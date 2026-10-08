"""Public CI-safe pinned build/validation regressions; no local corpus required."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from src.literature.build_v2 import (acquire_sources, build_once, check_evidence,
    compare_builds, content_hash, load_selection, serialize_pinned_article,
    validate_build, validate_manifest, _tokens_preserved)
from src.literature.corpus import sha256_file
from src.literature.jats import extract_sections
from scripts import build_literature_corpus_v2 as runner


@pytest.fixture
def minimal(tmp_path):
    text = " ".join(f"word{i}" for i in range(400))
    xml = f'''<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>
    <article-id pub-id-type="pmc">PMC456</article-id><title-group><article-title>Fixture</article-title>
    </title-group><pub-date pub-type="epub"><year>2024</year></pub-date><abstract><p>Abstract</p></abstract>
    </article-meta></front><body><sec><title>Results</title><p>{text}</p><p>Short result.</p></sec>
    <sec><title>Results</title><p>Independent repeated section title.</p></sec>
    <sec><title>Funding</title><p>Excluded funding.</p></sec></body></article>'''
    path = tmp_path / "PMC456.xml"
    path.write_text(xml, encoding="utf-8")
    signatures = [{"section_index": i, "section": s.section, "paragraphs": [
        {"paragraph_index": j, "sha256": hashlib.sha256(t.encode()).hexdigest()}
        for j, t in enumerate(s.paragraphs)]} for i, s in enumerate(extract_sections(path))]
    row = {"pmid": "123", "pmcid": "PMC456", "selection_role": "MAIN", "source_type": "JATS",
           "source": {"local_path": path.name, "source_article_sha256": sha256_file(path),
                      "source_url": "https://example.test/PMC456"},
           "provenance": {"section_paragraph_index_and_text_hashes": signatures}}
    config = {"version": "literature_corpus_v2", "chunking": {"target_words": 350, "minimum_words": 80,
              "maximum_words": 550, "overlap_words": 60, "excluded_sections": ["funding"]}}
    sources = acquire_sources(tmp_path, [row], tmp_path / "sources")
    built = build_once(tmp_path, [row], sources, config)
    return tmp_path, row, config, sources, built


def test_two_independent_builds_and_repeated_section_ownership(minimal):
    root, row, config, sources, built = minimal
    again = build_once(root, [row], sources, config)
    assert compare_builds(built, again)["result"] == "PASS"
    assert validate_build([row], built, config)["all_checks_passed"]
    assert [c["section_index"] for c in built["chunks"] if c["section"] == "Results"] == [1, 2]
    assert all(p["paragraph_id"] not in c["source_paragraph_ids"] for p in built["paragraphs"]
               if p["section"] == "Funding" for c in built["chunks"])


@pytest.mark.parametrize("mutation,issue", [
    (lambda b: b["chunks"].append(deepcopy(b["chunks"][0])), "duplicate_chunk_ids"),
    (lambda b: b["chunks"][0].update(text=""), "empty_chunks"),
    (lambda b: b["chunks"][0].pop("source_sha256"), "missing_chunk_provenance"),
    (lambda b: b["chunks"][0].update(pmid=""), "missing_chunk_pmid"),
    (lambda b: b["chunks"][0].update(pmcid=""), "missing_chunk_pmcid"),
    (lambda b: b["chunks"][0].update(paper_id="orphan"), "orphan_chunks"),
    (lambda b: b["chunks"][0].update(source_paragraph_ids=["missing"]), "orphan_chunk_spans"),
    (lambda b: b["chunks"][0].update(section="Results > "), "malformed_chunk_section_path"),
    (lambda b: b["chunks"][0].update(chunk_id="wrong"), "malformed_chunk_metadata"),
    (lambda b: b["chunks"][0].update(text_sha256="f"*64), "malformed_chunk_metadata"),
    (lambda b: b["paragraphs"][0].update(paragraph_index=0), "malformed_paragraph_metadata"),
    (lambda b: b["paragraphs"][0].update(paper_id="orphan"), "orphan_paragraphs"),
    (lambda b: b["papers"][0].update(year="2024"), "malformed_paper_metadata"),
])
def test_structural_errors_block_acceptance(minimal, mutation, issue):
    _, row, config, _, built = minimal
    bad = deepcopy(built)
    mutation(bad)
    result = validate_build([row], bad, config)
    assert not result["all_checks_passed"]
    assert result["issues"][issue] > 0


def test_selection_build_missing_and_unexpected_detection(minimal):
    _, row, config, _, built = minimal
    row = deepcopy(row)
    row.update(pmid="999", pmcid="PMC999")
    result = validate_build([row], built, config)
    assert result["consistency"]["missing_selected_papers"] == [("999", "PMC999")]
    assert result["consistency"]["unexpected_papers"] == [("123", "PMC456")]
    assert not result["all_checks_passed"]


def test_source_hash_identity_and_no_implicit_download(minimal):
    root, row, _, _, _ = minimal
    bad = deepcopy(row)
    bad["source"]["source_article_sha256"] = "f"*64
    with pytest.raises(ValueError, match="hash mismatch"):
        acquire_sources(root, [bad], root / "sources")
    bad = deepcopy(row)
    bad["pmid"] = "999"
    with pytest.raises(ValueError, match="identity mismatch"):
        acquire_sources(root, [bad], root / "sources")
    bad["source"]["local_path"] = None
    with pytest.raises(ValueError, match="unavailable"):
        acquire_sources(root, [bad], root / "sources")


def test_preflight_paragraph_change_blocks_parse(minimal):
    root, row, config, sources, _ = minimal
    changed = deepcopy(row)
    changed["provenance"]["section_paragraph_index_and_text_hashes"][0]["paragraphs"][0]["sha256"] = "f"*64
    assert build_once(root, [changed], sources, config)["parse_status"][0]["parse_status"] == "PARSE_FAILED"


def test_source_changed_between_acquisition_and_build_blocks_parse(minimal):
    root, row, config, sources, _ = minimal
    sources[row["pmcid"]].write_text('<article/>', encoding="utf-8")
    assert build_once(root, [row], sources, config)["parse_status"][0]["parse_status"] == "PARSE_FAILED"


def test_unsupported_wrapper_loss_is_reported(minimal):
    root, row, config, sources, _ = minimal
    path = sources[row["pmcid"]]
    tree = ET.parse(path);body = tree.getroot().find("body")
    ET.SubElement(ET.SubElement(body, "unexpected-wrapper"), "p").text = "Lost ordinary paragraph"
    tree.write(path, encoding="utf-8")
    row["source"]["source_article_sha256"] = sha256_file(path)
    assert build_once(root, [row], sources, config)["parse_status"][0]["parse_status"] == "PARSE_FAILED"


@pytest.mark.parametrize("existing", ["output", "reports"])
def test_runner_refuses_existing_paths_before_reading_corpus(tmp_path, monkeypatch, existing):
    output = tmp_path / "data/literature/v2/build_freeze_v1"
    reports = tmp_path / "reports/diagnostics/build_validation"
    (output if existing == "output" else reports).mkdir(parents=True)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["build", "--output", str(output), "--reports", str(reports),
                                  "--protected-snapshot", str(tmp_path / "unused.json")])
    with pytest.raises(ValueError, match="already exists"):
        runner.main()


def test_selection_manifest_hash_roles_identity_validation(minimal):
    root, row, _, _, _ = minimal
    manifest = {"status": "SELECTION_FROZEN", "MAIN_count": 1, "paper_selection": [row]}
    path = root / "selection.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_selection(path, sha256_file(path), 1) == manifest
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        load_selection(path, "f"*64, 1)
    for field, value in [("selection_role", "BACKUP"), ("source_type", "PDF"), ("pmcid", "456")]:
        bad = deepcopy(manifest); bad["paper_selection"][0][field] = value
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid selection"):
            load_selection(path, sha256_file(path), 1)


def test_evidence_requires_body_and_associated_chunk(minimal):
    _, _, _, _, built = minimal
    checks = [{"pmid": "123", "requirement": "result", "patterns": ["Short result"]}]
    assert check_evidence(built, checks)["all_checks_passed"]
    assert not check_evidence(built, [{**checks[0], "patterns": ["Abstract"]}])["all_checks_passed"]
    built["chunks"] = []
    assert not check_evidence(built, checks)["all_checks_passed"]


def test_content_and_chunk_identity_detect_mutation(minimal):
    _, _, _, _, built = minimal
    assert content_hash(built) == content_hash(deepcopy(built))
    bad = deepcopy(built); bad["chunks"][0]["text"] += " lost"
    assert compare_builds(built, bad)["result"] == "FAIL"


def test_ordered_conservation_handles_repetition_and_rejects_omission():
    assert _tokens_preserved(["a", "a"], [{"text": "a"}, {"text": "a"}], 60)
    assert _tokens_preserved(["a", "b", "c"], [{"text": "a b"}, {"text": "b c"}], 60)
    assert not _tokens_preserved(["a", "b", "c"], [{"text": "a"}, {"text": "c"}], 60)


def test_pinned_serialization_reproduces_windows_elementtree_bytes():
    article = ET.fromstring('<article><p>First\nsecond</p></article>')
    result = serialize_pinned_article(article)
    assert result == b"<?xml version='1.0' encoding='utf-8'?>\r\n<article><p>First\r\nsecond</p></article>"


def test_build_manifest_validation():
    reference = {"path": "fixture", "sha256": "a"*64}
    manifest = {"corpus_version": "v2", "status": "BUILD_FROZEN", "selection_manifest": reference,
                "parser": reference, "chunker": reference, "config": reference,
                "selected_paper_count": 1, "built_paper_count": 1,
                "papers": [{"pmid": "123", "pmcid": "PMC456", "source_sha256": "a"*64}],
                "paragraph_count": 2, "chunk_count": 1, "corpus_content_hash": "a"*64,
                "determinism": "PASS", "build_timestamp": "2026-10-08T12:00:00+09:00",
                "output_files": {"chunks": reference}}
    validate_manifest(manifest)
    for field, value in [("selected_paper_count", 2), ("corpus_content_hash", "invalid"),
                         ("determinism", "FAIL"), ("parser", {"path": "x", "sha256": "bad"})]:
        bad = deepcopy(manifest); bad[field] = value
        with pytest.raises(ValueError):
            validate_manifest(bad)
