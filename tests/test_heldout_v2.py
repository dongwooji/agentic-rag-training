"""Public synthetic checks: never load the private held-out questions or Gold."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.freeze_heldout_evaluation_v2 import (map_gold, protected_changes, publish_freeze,
                                                verify_authoring, verify_corpus, verify_source_spans,
                                                validate_leakage_review, write_json)
from src.evaluation.heldout_v2 import (content_hash, map_source_span, retrieval_metric_cases,
                                     runtime_isolation_audit, sha256_file, validate_dataset,
                                     verify_references)
from src.evaluation.retrieval_metrics import (complete_evidence_at_k, evidence_group_recall_at_k,
                                            reciprocal_rank)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def example():
    text = "Invented source supports a narrow comparison and uncertain difference."
    paper = dict(pmid="FAKE-PMID", pmcid="FAKE-PMC", source_path="invented.xml", source_sha256="source-hash")
    paragraph = dict(**paper, section="Results", section_index=1, paragraph_index=1,
                     paragraph_id="FAKE-PARA", included_in_chunks=True, text=text, text_sha256=digest(text))
    chunk = dict(**paper, section_index=1, source_paragraph_ids=["FAKE-PARA"],
                 chunk_id="FAKE-CHUNK", text=text)
    source = dict(**paper, section="Results", section_index=1, paragraph_index=1,
                  paragraph_id="FAKE-PARA", paragraph_sha256=digest(text), span_start=0,
                  span_end=len(text), span_text=text)
    case = dict(id="FAKE-CASE", question="Which comparison was measured?", question_family_id="FAKE-FAMILY",
                scope_class="REQUIRED_LIMITED", task_type="literature_only", category="literature_only",
                expected_behavior="partial_answer", requires_log=False, requires_literature=True,
                retrieval_metric_eligible=True, notes="Invented fixture.", scope_boundary_reference="Narrow comparison.")
    expected = dict(case_id=case["id"], expected_behavior=case["expected_behavior"],
                    supported_conclusion="Narrow comparison.", required_qualification="Uncertain difference.",
                    prohibited_overgeneralization="Universal superiority.", unsupported_extension="Personal cause.",
                    boundary_expectation="Explain limitations.")
    group = dict(evidence_group_id="FAKE-E1", case_id=case["id"], requirement="Comparison and uncertainty.",
                 semantic_claim="The invented comparison is uncertain.", required=True,
                 qualification="Narrow comparison only.", notes="Synthetic source.", source=source,
                 parent_mapping={"match": "any", "chunk_ids": [chunk["chunk_id"]]})
    return dict(cases={"dataset_version": "fake-v1", "cases": [case]},
                gold={"dataset_version": "fake-v1", "cases": [expected], "evidence_groups": [group]},
                scope={"questions": [dict(question_id="FAKE-FAMILY", scope="REQUIRED_LIMITED",
                                          human_approved_scope=True, NOT_DECIDED=False,
                                          approved_scope_boundary="Narrow comparison.")]},
                papers=[paper], paragraphs=[paragraph], chunks=[chunk])


def test_valid_synthetic_schema(example):
    result = validate_dataset(**example)
    assert result["status"] == "PASS"
    assert result["case_count"] == result["required_group_count"] == 1


@pytest.mark.parametrize("mutation,reason", [
    ("duplicate_case", "duplicate id"), ("duplicate_gold_case", "duplicate case_id"),
    ("duplicate_group", "duplicate evidence_group_id"), ("unknown_family", "unknown family"),
    ("invalid_scope", "invalid scope class"), ("scope_mismatch", "invalid scope class"),
    ("undecided", "unresolved family"), ("changed_scope_boundary", "frozen scope boundary mismatch"),
    ("missing_provenance", "missing source provenance"), ("missing_span", "missing provenance span_text"),
    ("source_outside", "source outside MAIN corpus"), ("bad_identity", "source identity mismatch"),
    ("bad_paragraph", "paragraph provenance mismatch"), ("bad_text", "paragraph text hash mismatch"),
    ("bad_chunk", "invalid chunk membership"), ("wrong_span", "source span not in paragraph"),
    ("missing_boundary", "missing answer boundary"), ("missing_expected", "missing case expected_behavior"),
    ("bad_behavior", "invalid expected behavior"), ("version_mismatch", "version mismatch"),
    ("no_required", "eligibility/group mismatch"), ("ranking_gold", "ranking-derived Gold"),
    ("gold_in_case", "Gold or ranking fields"), ("missing_hybrid", "missing synthetic hybrid"),
    ("non_bool", "invalid boolean"), ("orphan_group", "orphan evidence group"),
    ("excluded_para", "missing or excluded paragraph"), ("bad_required", "required must be boolean"),
])
def test_reject_invalid_dataset(example, mutation, reason):
    case = example["cases"]["cases"][0]; group = example["gold"]["evidence_groups"][0]
    expected = example["gold"]["cases"][0]
    if mutation == "duplicate_case": example["cases"]["cases"].append(deepcopy(case))
    elif mutation == "duplicate_gold_case": example["gold"]["cases"].append(deepcopy(expected))
    elif mutation == "duplicate_group": example["gold"]["evidence_groups"].append(deepcopy(group))
    elif mutation == "unknown_family": case["question_family_id"] = "UNKNOWN"
    elif mutation == "invalid_scope": case["scope_class"] = "NOT_DECIDED"
    elif mutation == "scope_mismatch": case["scope_class"] = "OPTIONAL"
    elif mutation == "undecided": example["scope"]["questions"][0]["NOT_DECIDED"] = True
    elif mutation == "changed_scope_boundary": case["scope_boundary_reference"] = "Expanded."
    elif mutation == "missing_provenance": del group["source"]
    elif mutation == "missing_span": del group["source"]["span_text"]
    elif mutation == "source_outside": group["source"]["pmid"] = "OUTSIDE"
    elif mutation == "bad_identity": group["source"]["source_sha256"] = "different"
    elif mutation == "bad_paragraph": group["source"]["paragraph_sha256"] = "different"
    elif mutation == "bad_text": example["paragraphs"][0]["text"] = "Changed."
    elif mutation == "bad_chunk": group["parent_mapping"]["chunk_ids"] = ["UNKNOWN"]
    elif mutation == "wrong_span": group["source"]["span_text"] = "X" * group["source"]["span_end"]
    elif mutation == "missing_boundary": del expected["required_qualification"]
    elif mutation == "missing_expected": del case["expected_behavior"]
    elif mutation == "bad_behavior": case["expected_behavior"] = "always_answer"
    elif mutation == "version_mismatch": example["gold"]["dataset_version"] = "different"
    elif mutation == "no_required": group["required"] = False
    elif mutation == "ranking_gold": group["retrieval_score"] = 1.0
    elif mutation == "gold_in_case": case["nested"] = {"gold": {}}
    elif mutation == "missing_hybrid": case.update(task_type="hybrid", category="hybrid", requires_log=True)
    elif mutation == "non_bool": case["requires_log"] = 0
    elif mutation == "orphan_group": group["case_id"] = "UNKNOWN"
    elif mutation == "excluded_para": example["paragraphs"][0]["included_in_chunks"] = False
    elif mutation == "bad_required": group["required"] = 1
    with pytest.raises(ValueError, match=reason): validate_dataset(**example)


def test_semantic_gold_precedes_mapping(example):
    group = example["gold"]["evidence_groups"][0]
    del group["parent_mapping"]; del group["source"]["paragraph_id"]
    assert validate_dataset(**example, mapped=False)["status"] == "PASS"
    mapped = map_gold(example["gold"], example)
    assert "parent_mapping" not in group
    assert mapped["evidence_groups"][0]["parent_mapping"]["chunk_ids"] == ["FAKE-CHUNK"]


def test_pre_mapping_schema_rejects_premature_chunk_ids(example):
    with pytest.raises(ValueError, match="already contains chunk mapping"):
        validate_dataset(**example, mapped=False)


def test_missing_version_and_midword_span_are_rejected(example):
    del example["cases"]["dataset_version"]
    with pytest.raises(ValueError, match="missing dataset version"): validate_dataset(**example)
    example["cases"]["dataset_version"] = "fake-v1"
    source = example["gold"]["evidence_groups"][0]["source"]
    source.update(span_start=1, span_text=source["span_text"][1:])
    with pytest.raises(ValueError, match="word-aligned"): validate_dataset(**example)


def test_split_span_requires_all_parents_and_checks_actual_text(example):
    para = example["paragraphs"][0]; words = para["text"].split()
    first = {**example["chunks"][0], "chunk_id": "FAKE-A", "text": " ".join(words[:6])}
    second = {**example["chunks"][0], "chunk_id": "FAKE-B", "text": " ".join(words[4:])}
    example["chunks"] = [first, second]
    group = example["gold"]["evidence_groups"][0]
    group["parent_mapping"] = map_source_span(group["source"], [para], [first, second])
    assert group["parent_mapping"] == {"match": "all", "chunk_ids": ["FAKE-A", "FAKE-B"]}
    assert validate_dataset(**example)["status"] == "PASS"
    # A linked paragraph does not make the truncated first parent sufficient.
    group["parent_mapping"] = {"match": "any", "chunk_ids": ["FAKE-A"]}
    with pytest.raises(ValueError, match="does not cover"): validate_dataset(**example)
    second["text"] = "Unrelated invented words."
    with pytest.raises(ValueError, match="cannot be aligned"):
        map_source_span(group["source"], [para], [first, second])


def test_complete_alternatives_and_uncovered_span(example):
    source = example["gold"]["evidence_groups"][0]["source"]
    second = {**example["chunks"][0], "chunk_id": "FAKE-B"}
    # Distinct alternatives normally arise from overlapping parents, not duplicate sections.
    text = example["paragraphs"][0]["text"]
    source.update(span_start=text.index("comparison"), span_end=text.index("difference"),
                  span_text=text[text.index("comparison"):text.index("difference")])
    words = text.split()
    example["chunks"][0]["text"] = " ".join(words[:8])
    second["text"] = " ".join(words[4:])
    assert map_source_span(source, example["paragraphs"], [example["chunks"][0], second])["match"] == "any"
    source.update(span_start=0, span_end=len(text), span_text=text)
    with pytest.raises(ValueError, match="uncovered"):
        map_source_span(source, example["paragraphs"], [second])


def test_metrics_use_synthetic_rankings_only_and_exclude_boundary(example):
    gold = example["gold"]; cases = example["cases"]
    gold["evidence_groups"].append({**deepcopy(gold["evidence_groups"][0]), "evidence_group_id": "FAKE-E2",
                                    "parent_mapping": {"match": "all", "chunk_ids": ["FAKE-B", "FAKE-C"]}})
    gold["evidence_groups"].append({**deepcopy(gold["evidence_groups"][0]), "evidence_group_id": "FAKE-O",
                                    "required": False, "parent_mapping": {"match": "any", "chunk_ids": ["FAKE-OPTIONAL"]}})
    cases["cases"].append({**deepcopy(cases["cases"][0]), "id": "FAKE-BOUNDARY", "retrieval_metric_eligible": False})
    adapted, excluded = retrieval_metric_cases(cases, gold)
    assert excluded == ["FAKE-BOUNDARY"]
    ranks = ["FAKE-NOISE", "FAKE-CHUNK", "FAKE-B", "FAKE-C"]
    assert evidence_group_recall_at_k(adapted[0], ranks, 3) == .5
    assert complete_evidence_at_k(adapted[0], ranks, 3) == 0
    assert complete_evidence_at_k(adapted[0], ranks, 4) == 1
    assert reciprocal_rank(adapted[0], ranks) == .5


def test_boundary_case_has_no_forced_source(example):
    case = example["cases"]["cases"][0]; expected = example["gold"]["cases"][0]
    case.update(scope_class="OUT_OF_SCOPE", task_type="boundary", category="boundary",
                requires_literature=False, retrieval_metric_eligible=False, expected_behavior="abstain")
    expected["expected_behavior"] = "abstain"
    example["scope"]["questions"][0]["scope"] = "OUT_OF_SCOPE"
    example["gold"]["evidence_groups"] = []
    assert validate_dataset(**example)["required_group_count"] == 0


def test_xml_source_span_validation_and_corruption(tmp_path, example):
    path = tmp_path / "invented.xml"
    text = example["paragraphs"][0]["text"]
    path.write_text(f'<article><body><sec><title>Results</title><p>{text}</p></sec></body></article>', encoding="utf-8")
    source = example["gold"]["evidence_groups"][0]["source"]
    source.update(source_sha256=sha256_file(path), section_index=0)
    assert verify_source_spans(tmp_path, example["gold"]) == 1
    source["span_text"] = "Invented false claim."
    with pytest.raises(ValueError, match="locator mismatch"): verify_source_spans(tmp_path, example["gold"])


def test_hash_gate_and_nested_manifest_verification(tmp_path, example):
    tables = {k: example[k] for k in ("papers", "paragraphs", "chunks")}
    build = tmp_path / "build"; build.mkdir()
    (tmp_path / "invented.xml").write_text("invented", encoding="utf-8")
    tables["papers"][0]["source_sha256"] = sha256_file(tmp_path / "invented.xml")
    files = {}
    for name, rows in tables.items():
        p = build / (name + ".jsonl"); p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        files[name] = {"path": p.relative_to(tmp_path).as_posix(), "sha256": sha256_file(p)}
    manifest = dict(status="FROZEN_BUILD", corpus_content_hash=content_hash(tables), output_files=files)
    write_json(build / "build_manifest.json", manifest)
    write_json(build / "freeze_integrity.json", dict(corpus_content_hash=content_hash(tables), output_files=files))
    assert verify_corpus(tmp_path, build, content_hash(tables))[1]["references_checked"] == 6
    with pytest.raises(ValueError, match="Corpus content hash mismatch"): verify_corpus(tmp_path, build, "wrong")
    (build / "papers.jsonl").write_text("corruption", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"): verify_corpus(tmp_path, build, content_hash(tables))


def test_references_reject_escape_and_changed_protected_files(tmp_path):
    p = tmp_path / "protected.txt"; p.write_text("frozen", encoding="utf-8")
    refs = {"nested": [{"path": "protected.txt", "sha256": sha256_file(p)}]}
    assert verify_references(tmp_path, refs) == 1
    p.write_text("changed", encoding="utf-8")
    assert protected_changes(tmp_path, {"protected.txt": refs["nested"][0]["sha256"]}) == ["protected.txt"]
    with pytest.raises(ValueError, match="hash mismatch"): verify_references(tmp_path, refs)
    with pytest.raises(ValueError, match="escapes"): verify_references(tmp_path, {"path": "../outside", "sha256": "fake"})


@pytest.mark.parametrize("source", ["from src import evaluation", "from .. import evaluation",
                                    "def later():\n    import src.evaluation.heldout_v2"])
def test_runtime_isolation_includes_deferred_and_relative_imports(tmp_path, source):
    runtime = tmp_path / "src/tools"; runtime.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (runtime / "fake.py").write_text(source, encoding="utf-8")
    assert runtime_isolation_audit(tmp_path)["status"] == "FAIL"


def test_runtime_configuration_cannot_reference_gold(tmp_path):
    (tmp_path / "src").mkdir(); (tmp_path / "config").mkdir()
    config = tmp_path / "config/runtime.json"
    config.write_text('{"path":"data/evaluation/heldout_v2_v1/gold_evidence.json"}', encoding="utf-8")
    assert runtime_isolation_audit(tmp_path)["status"] == "FAIL"
    config.write_text('{"path":"config/runtime.json"}', encoding="utf-8")
    assert runtime_isolation_audit(tmp_path)["status"] == "PASS"


def test_authoring_hash_binds_actual_input_bytes(tmp_path):
    authoring = dict(stage="SOURCE_REVIEWED_SEMANTIC_GOLD_BEFORE_CHUNK_MAPPING", retrieval_results_used=False,
                     chunk_ids_used=False, question_stage={"retrieval_results_used": False})
    for key, name in (("cases", "evaluation_cases_authored.json"), ("semantic_gold", "gold_semantic_authored.json")):
        p = tmp_path / name; p.write_text("{}", encoding="utf-8")
        authoring[key] = {"path": name, "sha256": sha256_file(p)}
    p = tmp_path / "authoring_questions.json"; p.write_text("{}", encoding="utf-8")
    authoring["question_stage"]["sha256"] = sha256_file(p)
    verify_authoring(tmp_path, tmp_path, authoring)
    authoring["chunk_ids_used"] = True
    with pytest.raises(ValueError, match="independence"): verify_authoring(tmp_path, tmp_path, authoring)


def test_freeze_publication_is_immutable_and_self_verifying(tmp_path, example):
    scope = tmp_path / "scope.json"; write_json(scope, example["scope"])
    build = tmp_path / "build"; build.mkdir(); write_json(build / "build_manifest.json", {})
    protected = tmp_path / "protected"; protected.write_text("immutable", encoding="utf-8")
    before = {"protected": sha256_file(protected)}
    authoring = {}
    for key in ("cases", "semantic_gold", "initial_questions"):
        p = tmp_path / (key + ".json"); write_json(p, {})
        authoring[key] = {"path": p.name, "sha256": sha256_file(p)}
    output = tmp_path / "freeze"; reports = tmp_path / "reports"
    audits = {k: {"status": "PASS"} for k in ("leakage", "integrity", "provenance")}
    audits["leakage"].update(retrieval_outputs_consulted=False, old_gold_copied=False,
                            retrieval_derived_gold_count=0, new_case_id_duplicates=0,
                            exact_question_duplicates=[], manual_semantic_overlap_review=["Invented questions reviewed."])
    audits.update({"sampling_report.md": "Synthetic sampling.", "case_review.md": "Synthetic review."})
    receipt = publish_freeze(tmp_path, output, reports, example["cases"], example["gold"],
                             scope, build, authoring, audits, before)
    assert verify_references(tmp_path, receipt) == 12
    assert verify_references(tmp_path, json.loads((output / "authoring_record.json").read_text())) == 3
    with pytest.raises(FileExistsError):
        publish_freeze(tmp_path, output, reports, example["cases"], example["gold"], scope, build, authoring, audits, before)
    (output / "gold_evidence.json").write_text("corruption", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"): verify_references(tmp_path, receipt)


@pytest.mark.parametrize("key,value", [("retrieval_outputs_consulted", True), ("old_gold_copied", True),
                                      ("retrieval_derived_gold_count", 1), ("exact_question_duplicates", ["FAKE"]),
                                      ("manual_semantic_overlap_review", [])])
def test_freeze_rejects_contradictory_leakage_pass(key, value):
    review = dict(status="PASS", retrieval_outputs_consulted=False, old_gold_copied=False,
                  retrieval_derived_gold_count=0, new_case_id_duplicates=0,
                  exact_question_duplicates=[], manual_semantic_overlap_review=["Synthetic review."])
    review[key] = value
    with pytest.raises(ValueError, match="contradictory leakage"): validate_leakage_review(review)
