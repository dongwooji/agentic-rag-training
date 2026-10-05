"""Compare namespace-compatible parsing with the previous parser, read-only.

Frozen artifacts are never rebuilt; only a new diagnostic directory is written.
Historical Gold/eval/baseline data are read as opaque bytes for hashes only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.literature import jats
from scripts.diagnose_jats_special_blocks import (
    EXCLUDED, SPECIAL, coverage_index, generate_chunks, historical_parser,
    inventory, sha, source_spans, spans_preserved, traced_extract,
)
from scripts.diagnose_short_section_chunking import Coverage, duplicates, read_jsonl, stats


def run(root, output, source, protected_hashes):
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / "reports/diagnostics"):
        raise ValueError("Output must be under reports/diagnostics")
    if output.exists():
        raise FileExistsError("Refusing to overwrite diagnostic output")
    hashes = json.loads(protected_hashes.read_text(encoding="utf-8"))

    def check_hashes():
        actual = {p: sha(root / p) for p in hashes}
        changed = [p for p in hashes if hashes[p] != actual[p]]
        if changed:
            raise AssertionError(f"Protected files changed: {changed}")
        return actual

    check_hashes()
    old = historical_parser(source)
    papers = read_jsonl(root / "data/literature/processed/papers.jsonl")
    frozen = read_jsonl(root / "data/literature/processed/chunks.jsonl")
    config = json.loads((root / "config/literature_corpus_v1.json").read_text(encoding="utf-8"))["chunking"]
    audit = root / "reports/diagnostics/jats_parser_v1"
    audit_rows = read_jsonl(audit / "block_audit.jsonl")
    ordinary = [r for r in audit_rows if r["ordinary_body_narrative"]]
    special = [r for r in audit_rows if r["status"] == "parser_omitted" and r["block_type"] in
               {"list", "list-item", "boxed-text", "def-list", "def-item"}]
    losses = [(p["pmcid"], loss) for p in json.loads((audit / "per_paper.json").read_text(encoding="utf-8"))
              for loss in p["chunking_loss_candidates"]]
    assert (len(papers), len(frozen), len(ordinary), len(losses), len(special)) == (22, 488, 811, 105, 77)
    per_paper, snapshots, body_checks, loss_checks, special_checks = [], [], [], [], []
    chunks_all = {"before": [], "after": []}
    for paper in sorted(papers, key=lambda p: p["pmcid"]):
        pmc = paper["pmcid"]
        path = root / "data/literature/raw" / f"{pmc}.xml"
        tree = ET.parse(path)
        root_node = tree.getroot()
        assert root_node.tag == "article"
        assert root_node.find(".//body") is not None and root_node.find(".//article-meta") is not None
        paths, parents, selected = inventory(root_node)
        nodes = {p: node for node, p in paths.items()}
        sections, emissions, metadata, chunks, chunk_index, parser_index = {}, {}, {}, {}, {}, {}
        for phase, module in (("before", old), ("after", jats)):
            sections[phase], emissions[phase] = traced_extract(module, path, tree, selected, paths)
            metadata[phase] = module.paper_metadata(path)
            chunks[phase] = generate_chunks(paper, sections[phase], config)
            chunks_all[phase].extend(chunks[phase])
            chunk_index[phase] = coverage_index(chunks[phase])
            parser_index[phase] = coverage_index([
                {"section": s.section, "text": text, "chunk_id": f"{i}:{n}"}
                for i, s in enumerate(sections[phase]) for n, text in enumerate(s.paragraphs)])
        projection = lambda values: [{"section": s.section, "paragraphs": list(s.paragraphs)} for s in values]
        same_sections = projection(sections["before"]) == projection(sections["after"])
        same_metadata = metadata["before"] == metadata["after"]
        same_chunks = chunks["before"] == chunks["after"]
        same_sources = dict(emissions["before"]) == dict(emissions["after"])
        assert same_sections and same_metadata and same_chunks and same_sources, f"Unexpected output change: {pmc}"
        duplicate_count = sum(max(0, len(v)-1) for v in emissions["after"].values())
        empty_count = sum(not p.strip() for s in sections["after"] for p in s.paragraphs)
        assert duplicate_count == empty_count == 0

        def owner(node):
            while node is not None:
                if node.tag.rsplit("}", 1)[-1] in EXCLUDED:
                    return None
                if node in selected:
                    return node
                node = parents.get(node)
            return None

        for row in (r for r in ordinary if r["pmcid"] == pmc):
            result = {"pmcid": pmc, "source_path": row["source_path"], "section": row["section_path"]}
            for phase in sections:
                key = row["section_path"]
                result[phase] = {
                    "parser": parser_index[phase].get(key, Coverage([])).match(row["comparison_text"])["complete"],
                    "chunks": chunk_index[phase].get(key, Coverage([])).match(row["comparison_text"])["complete"],
                    "source_selected_once": len(emissions[phase].get(row["source_path"], [])) == 1}
            body_checks.append(result)
        for _, loss in (x for x in losses if x[0] == pmc):
            loss_checks.append({"pmcid": pmc, "section": loss["section"],
                               **{phase: chunk_index[phase].get(loss["section"], Coverage([])).match(loss["text"])["complete"]
                                  for phase in sections}})
        for row in (r for r in special if r["pmcid"] == pmc):
            node = nodes[row["source_path"]]
            owning = owner(node)
            assert owning is not None
            origin, section = paths[owning], selected[owning]
            checks = {}
            for phase in sections:
                emitted = emissions[phase].get(origin, [])
                checks[phase] = len(emitted) == 1 and spans_preserved(node, emitted[0]) and all(
                    chunk_index[phase].get(section, Coverage([])).match(span)["complete"] for span in source_spans(node))
            special_checks.append({"pmcid": pmc, "source_path": paths[node], "type": row["block_type"], **checks})
        excluded_lists = [r for r in audit_rows if r["pmcid"] == pmc and r["block_type"] in {"list", "list-item"}
                          and r["status"] == "intentionally_excluded"]
        assert all(owner(nodes[r["source_path"]]) is None for r in excluded_lists)
        special_outputs = {paths[node]: emissions["after"].get(paths[node], []) for node in selected if node.tag in SPECIAL}
        per_paper.append({"pmcid": pmc, "unqualified_article_root": True, "body_and_metadata_found": True,
                          "metadata_identical": same_metadata, "sections_paths_paragraphs_text_identical": same_sections,
                          "special_block_outputs_identical": same_sources, "memory_chunks_identical": same_chunks,
                          "section_count": len(sections["after"]),
                          "paragraph_count": sum(len(s.paragraphs) for s in sections["after"]),
                          "logical_special_block_count": len(special_outputs), "memory_chunk_count": len(chunks["after"]),
                          "duplicate_source_emissions": duplicate_count, "empty_blocks": empty_count})
        snapshots.append({"pmcid": pmc, "identical_before_after": True, "metadata": metadata["after"],
                          "sections": projection(sections["after"]), "special_source_outputs": special_outputs})
    assert all(v for r in body_checks for phase in ("before", "after") for v in r[phase].values())
    assert all(r["before"] and r["after"] for r in loss_checks + special_checks)
    recovered = dict(Counter(r["type"] for r in special_checks if r["after"]))
    assert recovered == {"list": 10, "list-item": 37, "boxed-text": 2, "def-list": 2, "def-item": 26}
    assert len(chunks_all["after"]) == 584 and chunks_all["before"] == chunks_all["after"]
    metrics = {"scope": "preventive namespace compatibility; no corpus rebuild or retrieval evaluation",
               "paper_count": 22, "frozen_chunk_count": len(frozen), "body_metadata_found": 22,
               "metadata_exact_equality": 22, "sections_paths_paragraphs_text_exact_equality": 22,
               "special_block_output_exact_equality": 22, "memory_chunks_exact_equality": 22,
               "ordinary_body_parser_and_chunks_preserved": 811, "short_section_recoveries_retained": 105,
               "scientific_list_box_omissions": 0, "special_recovery_retained": recovered,
               "duplicate_source_emissions": 0, "empty_blocks": 0,
               "chunk_statistics": {phase: stats(values) for phase, values in chunks_all.items()},
               "chunk_duplicates": {phase: duplicates(values) for phase, values in chunks_all.items()},
               "protected_files_unchanged": len(hashes), "protected_hash_changes": [],
               "historical_parser_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
               "current_parser_sha256": sha(root / "src/literature/jats.py"),
               "unchanged_chunker_sha256": sha(root / "src/literature/corpus.py"),
               "comparison_method": "Exact structured equality for all 22 metadata/sections/chunks/source emissions; section-local 5-gram and source ownership for historical preservation targets",
               "historical_audit_limit": "Frozen audit uses bare-name XPath for namespace flags; do not use that old flag to diagnose the new parser. Namespace support is verified by new positive/equality tests."}
    after_hashes = check_hashes()
    output.mkdir(parents=True)
    for name, data in (("metrics", metrics), ("per_paper", per_paper), ("identical_parser_outputs", snapshots),
                       ("ordinary_body_preservation", body_checks), ("short_section_preservation", loss_checks),
                       ("special_recovery_preservation", special_checks), ("protected_hashes_before", hashes),
                       ("protected_hashes_after", after_hashes)):
        (output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/diagnostics/jats_namespace_v1")
    parser.add_argument("--protected-hashes", type=Path, required=True)
    before = parser.add_mutually_exclusive_group(required=True)
    before.add_argument("--before-source", type=Path)
    before.add_argument("--before-ref")
    args = parser.parse_args()
    source = args.before_source.read_text(encoding="utf-8") if args.before_source else subprocess.check_output(
        ["git", "show", f"{args.before_ref}:src/literature/jats.py"], cwd=args.root).decode("utf-8")
    print(json.dumps(run(args.root, args.output, source, args.protected_hashes), ensure_ascii=True))


if __name__ == "__main__":
    main()
