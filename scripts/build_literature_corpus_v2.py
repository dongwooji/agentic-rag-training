"""Build the pinned 26-paper selection with the unchanged parser/chunker."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import platform
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.build_v2 import (acquire_sources, build_once, check_evidence,
    compare_builds, load_selection, validate_build, validate_manifest)
from src.literature.corpus import sha256_file, write_jsonl


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "data/literature/v2/build_freeze_v1")
    parser.add_argument("--reports", type=Path, default=ROOT / "reports/diagnostics/literature_corpus_v2_build_validation")
    parser.add_argument("--protected-snapshot", type=Path, required=True,
                        help="Pre-task path->SHA256 map of all protected local files")
    args = parser.parse_args()
    output, reports = args.output.resolve(), args.reports.resolve()
    # A versioned build must never overwrite an existing build or historical data.
    if not output.is_relative_to(ROOT / "data/literature/v2"):
        raise ValueError("Build output must be under data/literature/v2")
    if not reports.is_relative_to(ROOT / "reports/diagnostics"):
        raise ValueError("Reports must be under reports/diagnostics")
    if output.exists() or reports.exists():
        raise ValueError("Output/report already exists; use a new version, never overwrite")
    config_path = ROOT / "config/literature_corpus_v2_build_v1.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    selection_path = ROOT / config["selection_manifest"]
    manifest = load_selection(selection_path)  # Hard-pinned gate before acquisition/output.
    rows = manifest["paper_selection"]
    if config["chunking"] != json.loads((ROOT / config["chunking_policy_reference"]).read_text(encoding="utf-8"))["chunking"]:
        raise ValueError("Chunking policy differs from current project policy")
    parser_path, chunker_path = ROOT / "src/literature/jats.py", ROOT / "src/literature/corpus.py"
    if any(sha256_file(parser_path) != r["parser"]["sha256"] for r in rows):
        raise ValueError("Parser differs from selection preflight")
    protected = json.loads(args.protected_snapshot.read_text(encoding="utf-8"))
    changed = [p for p, h in protected.items() if not (ROOT / p).is_file() or sha256_file(ROOT / p) != h]
    if changed:
        raise ValueError(f"Protected inputs changed before build: {changed}")
    sources = acquire_sources(ROOT, rows, ROOT / "data/literature/v2/sources/build_v1", args.allow_download)
    first = build_once(ROOT, rows, sources, config)
    second = build_once(ROOT, rows, sources, config)
    determinism = compare_builds(first, second)
    validation = validate_build(rows, first, config)
    evidence = check_evidence(first, config["evidence_preservation_checks"])
    changed = [p for p, h in protected.items() if not (ROOT / p).is_file() or sha256_file(ROOT / p) != h]
    integrity = {"protected_files": len(protected), "changed_files": changed,
                 "corpus_v1_changed_files": [p for p in changed if not p.startswith("data/literature/v2")],
                 "existing_frozen_artifact_changed_files": len(changed),
                 "all_checks_passed": not changed, "selection_sha256": sha256_file(selection_path)}
    reports.mkdir(parents=True)
    save(reports / "protected_hashes_before.json", protected)
    save(reports / "protected_hashes_after.json", {p: sha256_file(ROOT / p) for p in protected if (ROOT / p).exists()})
    save(reports / "paper_parse_status.json", first["parse_status"])
    save(reports / "paragraph_statistics.json", validation["paragraph_statistics"])
    save(reports / "chunk_statistics.json", validation["chunk_statistics"])
    save(reports / "provenance_validation.json", validation)
    save(reports / "selection_build_consistency.json", validation["consistency"])
    save(reports / "determinism_check.json", determinism)
    save(reports / "integrity.json", integrity)
    save(reports / "must_support_evidence_preservation.json", evidence)
    acquisition = {"local_pinned_sources": sum(bool(r["source"].get("local_path")) for r in rows),
        "remote_pinned_sources": [{"pmid": r["pmid"], "pmcid": r["pmcid"],
             "path": sources[r["pmcid"]].relative_to(ROOT).as_posix(),
             "article_sha256": sha256_file(sources[r["pmcid"]]),
             "pinned_article_sha256": r["source"]["source_article_sha256"]}
             for r in rows if not r["source"].get("local_path")],
        "serialization": "Pinned Windows ElementTree.write UTF-8 with declaration and CRLF",
        "initial_issue": "Bytes serialization used LF, so hash verification correctly blocked acquisition. "
                         "Reproducing preflight Windows CRLF yielded exact pins for both articles; "
                         "no selection/source content replacement."}
    save(reports / "source_acquisition.json", acquisition)
    lines = ["# MUST_SUPPORT evidence preservation", "",
             "Post-build paragraph/chunk preservation audit only; no scientific verdict or Gold.", ""]
    for check in evidence["checks"]:
        lines.append(f"## PMID {check['pmid']}: {check['requirement']} — {'PASS' if check['passed'] else 'FAIL'}")
        for match in check["matches"]:
            lines += [f"- Section: {match['section']}", f"- Paragraph: `{match['paragraph_id']}`",
                      f"- Chunks: {', '.join('`'+c+'`' for c in match['chunk_ids'])}"]
        lines.append("")
    (reports / "must_support_evidence_preservation.md").write_text("\n".join(lines), encoding="utf-8")
    success = validation["all_checks_passed"] and evidence["all_checks_passed"] and not changed and determinism["result"] == "PASS"
    summary = {"status": "BUILD_FROZEN" if success else "VALIDATION_FAILED_NO_FREEZE",
               "paper_count": len(first["papers"]), "paragraph_count": len(first["paragraphs"]),
               "chunk_count": len(first["chunks"]), "sections": validation["sections"],
               "unique_pmid": validation["unique_pmid"], "unique_pmcid": validation["unique_pmcid"],
               "exact_duplicate_chunk_texts": validation["chunk_statistics"]["exact_duplicate_texts"],
               "empty_chunks": validation["issues"]["empty_chunks"],
               "duplicate_chunk_ids": validation["issues"]["duplicate_chunk_ids"],
               "orphan_chunks": validation["issues"]["orphan_chunks"],
               "missing_chunk_provenance": validation["issues"]["missing_chunk_provenance"],
               "parse_status": {status: sum(r["parse_status"] == status for r in first["parse_status"])
                                for status in ("PARSE_OK", "PARSE_WARNING", "PARSE_FAILED")},
               "determinism": determinism["result"], "corpus_content_hash": determinism["corpus_content_hashes"][0],
               "selection_sha256": sha256_file(selection_path)}
    save(reports / "summary.json", summary)
    limitations = """
Known limitations (unchanged parser/policy): direct p takes priority in mixed abstracts;
tables/figures/media/supplements are deliberately excluded, so table-only cells are unavailable;
some fn-group body footnotes are not ingested; no malformed XML recovery or DTD validation;
inline markup is flattened. Structured abstract without direct p is flattened.
Previously repaired short-section, special-block and namespace issues are historical,
not current known losses. No new parser regression: exact frozen paragraph signatures
and supported body-block multiplicities are checked, then all included section tokens
are compared to chunks in order. Excluded section paragraphs remain in the normalized
paragraph artifact with included_in_chunks=false. This does not certify all XML dialects,
external supplements, all MUST_SUPPORT families, or table/figure evidence completeness.
Study type/population absent from the pinned structured selection remain not_reported;
no values are inferred or copied from Gold. Exact repeated text is reported as a warning,
not silently removed. Source spans use section_index plus 1-based paragraph indices and
explicit IDs, including repeated section titles; XML IDs are not asserted to be available.
Two new MAIN paper requirements are checked in body paragraphs and associated chunks,
not abstract alone. Scientific conclusions and RTEV/RTUV conditions are not reinterpreted.
No DB, OpenAI, embeddings, index, retrieval experiment, evaluation or deployment occurs.
"""
    text = "# Corpus v2 build validation\n\n" + "\n".join(f"- {k}: `{v}`" for k, v in summary.items())
    text += "\n\n" + limitations.strip() + "\n"
    (reports / "build_summary.md").write_text(text, encoding="utf-8")
    if not success:
        print(json.dumps(summary)); raise SystemExit("Validation failed; no build freeze written. See reports.")
    output.mkdir(parents=True)
    output_files = {}
    for key in ("papers", "paragraphs", "chunks"):
        path = output / f"{key}.jsonl"
        write_jsonl(path, first[key])
        output_files[key] = {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path)}
    def reference(path: Path) -> dict:
        return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256_file(path)}
    build_manifest = {**summary, "schema_version": 1, "corpus_version": config["version"],
        "selected_paper_count": len(rows), "built_paper_count": len(first["papers"]),
        "selection_manifest": reference(selection_path), "parser": reference(parser_path),
        "chunker": reference(chunker_path), "config": reference(config_path),
        "orchestration": reference(ROOT / "src/literature/build_v2.py"),
        "builder_script": reference(Path(__file__).resolve()), "chunking_policy": config["chunking"],
        "papers": [{k: p[k] for k in ("paper_id", "pmid", "pmcid", "source_path", "source_sha256")}
                   for p in first["papers"]], "output_files": output_files,
        "build_timestamp": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "article_serialization_newline": "CRLF", "artifact_jsonl_newline": "LF"},
        "validation_reports": {p.name: reference(p) for p in sorted(reports.iterdir()) if p.is_file()},
        "freeze_policy": "Immutable; representation experiments require a separate version"}
    validate_manifest(build_manifest)
    save(output / "build_manifest.json", build_manifest)
    save(output / "freeze_integrity.json", {"build_manifest": reference(output / "build_manifest.json"),
         "corpus_content_hash": summary["corpus_content_hash"], "output_files": output_files})
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
