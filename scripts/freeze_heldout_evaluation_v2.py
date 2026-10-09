"""Validate authored semantic Gold, map frozen parents, then publish a new freeze.

No retrieval implementation is imported or called. Local/private case contents
remain ignored research artifacts. Public tests use invented sources only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.heldout_v2 import (content_hash, map_source_span, runtime_isolation_audit,
                                       sha256_file, validate_dataset, verify_references)
from src.literature.jats import extract_sections

EXPECTED_CORPUS_HASH = "ecdbef3098c28b72c20b9c4962b728b2294f6116b6fd4e808099efb424102e2f"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def reference(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}


def verify_corpus(root: Path, build: Path, expected_hash: str = EXPECTED_CORPUS_HASH) -> tuple[dict, dict]:
    manifest = load(build / "build_manifest.json")
    integrity = load(build / "freeze_integrity.json")
    refs = verify_references(root, manifest) + verify_references(root, integrity)
    tables = {name: [json.loads(line) for line in (root / ref["path"]).read_text(encoding="utf-8").splitlines()]
              for name, ref in manifest["output_files"].items()}
    if (manifest["corpus_content_hash"] != expected_hash or integrity["corpus_content_hash"] != expected_hash
            or content_hash(tables) != expected_hash):
        raise ValueError("Corpus content hash mismatch; stop before evaluation design")
    if manifest.get("status") != "BUILD_FROZEN":
        # Current build uses FROZEN_BUILD: accept only the exact frozen spelling.
        if manifest.get("status") != "FROZEN_BUILD":
            raise ValueError("Corpus build is not frozen")
    for paper in tables["papers"]:
        if sha256_file(root / paper["source_path"]) != paper["source_sha256"]:
            raise ValueError("frozen source hash mismatch")
    return manifest, {**tables, "references_checked": refs}


def verify_source_spans(root: Path, gold: dict) -> int:
    """Re-read pinned XML before adding any paragraph/chunk identifiers."""
    sections = {}
    for group in gold["evidence_groups"]:
        source = group["source"]
        path = root / source["source_path"]
        if sha256_file(path) != source["source_sha256"]:
            raise ValueError("source hash mismatch")
        if path not in sections:
            sections[path] = extract_sections(path)
        section = sections[path][source["section_index"]]
        paragraph = section.paragraphs[source["paragraph_index"] - 1]
        if (section.section != source["section"]
                or hashlib.sha256(paragraph.encode()).hexdigest() != source["paragraph_sha256"]
                or paragraph[source["span_start"]:source["span_end"]] != source["span_text"]):
            raise ValueError("authored source locator mismatch")
        for index in (source["span_start"], source["span_end"]):
            if 0 < index < len(paragraph) and not (paragraph[index].isspace() or paragraph[index - 1].isspace()):
                raise ValueError("source span boundary is not word-aligned")
    return len(gold["evidence_groups"])


def verify_authoring(root: Path, draft: Path, authoring: dict) -> None:
    """Bind the declaration to the actual authored bytes, not just any reference."""
    if (authoring.get("stage") != "SOURCE_REVIEWED_SEMANTIC_GOLD_BEFORE_CHUNK_MAPPING"
            or authoring.get("retrieval_results_used") is not False
            or authoring.get("chunk_ids_used") is not False):
        raise ValueError("invalid authoring independence declaration")
    verify_references(root, authoring)
    for field, name in (("cases", "evaluation_cases_authored.json"),
                        ("semantic_gold", "gold_semantic_authored.json")):
        if sha256_file(draft / name) != authoring[field]["sha256"]:
            raise ValueError("authored input hash mismatch")
    if sha256_file(draft / "authoring_questions.json") != authoring["question_stage"]["sha256"]:
        raise ValueError("initial question hash mismatch")
    if authoring["question_stage"].get("retrieval_results_used") is not False:
        raise ValueError("question authoring used retrieval")


def map_gold(gold: dict, tables: dict) -> dict:
    mapped = deepcopy(gold)
    for group in mapped["evidence_groups"]:
        source = group["source"]
        matches = [p for p in tables["paragraphs"] if p["pmid"] == source["pmid"]
                   and p["section_index"] == source["section_index"] and p["paragraph_index"] == source["paragraph_index"]]
        if len(matches) != 1 or matches[0]["text_sha256"] != source["paragraph_sha256"]:
            raise ValueError("source paragraph cannot be mapped uniquely")
        source["paragraph_id"] = matches[0]["paragraph_id"]
        group["parent_mapping"] = map_source_span(source, tables["paragraphs"], tables["chunks"])
    return mapped


def protected_changes(root: Path, protected: dict) -> list[str]:
    return [p for p, digest in protected.items() if not (root / p).is_file() or sha256_file(root / p) != digest]


def validate_leakage_review(review: dict) -> None:
    """Reject contradictory PASS declarations; semantic review stays explicit."""
    if (review.get("status") != "PASS" or review.get("retrieval_outputs_consulted") is not False
            or review.get("old_gold_copied") is not False
            or review.get("retrieval_derived_gold_count") != 0
            or review.get("new_case_id_duplicates") != 0
            or review.get("exact_question_duplicates") != []
            or not review.get("manual_semantic_overlap_review")):
        raise ValueError("incomplete or contradictory leakage review")


def publish_freeze(root: Path, output: Path, reports: Path, cases: dict, gold: dict,
                   scope_path: Path, build_path: Path, authoring: dict, audits: dict, protected: dict) -> dict:
    """Stage all files before rename; existing versions are never overwritten."""
    for target in (output, reports):
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("output must remain in repository")
        if target.exists():
            raise FileExistsError(f"freeze/report version already exists: {target}")
    if protected_changes(root, protected):
        raise ValueError("protected artifacts changed")
    validate_leakage_review(audits["leakage"])
    if any(audits[name]["status"] != "PASS" for name in ("leakage", "integrity", "provenance")):
        raise ValueError("freeze audit failed")
    temporary = output.with_name(output.name + ".staging")
    report_temporary = reports.with_name(reports.name + ".staging")
    # Never reuse leftovers from an interrupted publication.
    temporary.mkdir(parents=True, exist_ok=False)
    report_temporary.mkdir(parents=True, exist_ok=False)
    write_json(temporary / "evaluation_cases.json", cases)
    write_json(temporary / "gold_evidence.json", gold)
    # Preserve the byte-identical pre-mapping snapshots inside the freeze itself.
    authoring = deepcopy(authoring)
    for field, filename in (("cases", "authored_cases_before_mapping.json"),
                            ("semantic_gold", "semantic_gold_before_mapping.json"),
                            ("initial_questions", "initial_questions.json")):
        ref = authoring[field]
        destination = temporary / filename
        destination.write_bytes((root / ref["path"]).read_bytes())
        if sha256_file(destination) != ref["sha256"]:
            raise ValueError("authored snapshot changed during publication")
        ref["path"] = (output / filename).relative_to(root).as_posix()
    write_json(temporary / "authoring_record.json", authoring)
    for name, value in (("gold_provenance_audit", audits["provenance"]),
                        ("leakage_audit", audits["leakage"]), ("integrity", audits["integrity"])):
        write_json(report_temporary / (name + ".json"), value)
    for name in ("sampling_report.md", "case_review.md"):
        (report_temporary / name).write_text(audits[name], encoding="utf-8", newline="\n")
    # References contain FINAL paths with hashes of the staged bytes.
    def final_ref(staged: Path, final: Path):
        return {"path": final.relative_to(root).as_posix(), "sha256": sha256_file(staged)}
    manifest = {"schema_version": 1, "version": cases["dataset_version"],
                "created_at": datetime.now(timezone.utc).isoformat(), "status": "FROZEN",
                "freeze_relationship": "Evaluation inputs and semantic Gold are frozen together; neither is a runtime configuration.",
                "review_authority": "Agent source review under user instruction; human review pending, not claimed approved.",
                "corpus_build_manifest": reference(root, build_path / "build_manifest.json"),
                "corpus_content_sha256": EXPECTED_CORPUS_HASH,
                "scope_reference": reference(root, scope_path),
                "validation_code": [reference(root, root / p) for p in
                                    ("src/evaluation/heldout_v2.py", "scripts/freeze_heldout_evaluation_v2.py",
                                     "src/evaluation/retrieval_metrics.py", "src/metrics/definitions.py",
                                     "tests/test_heldout_v2.py", "docs/specs/HELDOUT_EVALUATION_V2.md")
                                    if (root / p).is_file()],
                "files": {n: final_ref(temporary / n, output / n) for n in ("evaluation_cases.json", "gold_evidence.json", "authoring_record.json", "authored_cases_before_mapping.json", "semantic_gold_before_mapping.json", "initial_questions.json")},
                "reports": {p.name: final_ref(p, reports / p.name) for p in sorted(report_temporary.iterdir())},
                "summary": audits["provenance"], "leakage_audit": audits["leakage"]["status"],
                "integrity": audits["integrity"]["status"],
                "representation": "683 evidence-preserving parent chunks; no assumption of Dense input-length compatibility",
                "metrics_contract": "Only summary.retrieval_eligible_case_count cases; required any/all groups for recall/complete. Existing MRR is first required-chunk hit, not complete evidence rank.",
                "next_phase": "Retrieval v2 has not started; corpus/evaluation/Gold changes require new versions."}
    write_json(temporary / "manifest.json", manifest)
    receipt = {"status": "PASS", "manifest": final_ref(temporary / "manifest.json", output / "manifest.json"),
               "files": manifest["files"], "reports": manifest["reports"]}
    write_json(temporary / "freeze_integrity.json", receipt)
    if protected_changes(root, protected):
        raise ValueError("protected artifacts changed during publication")
    report_temporary.rename(reports)
    temporary.rename(output)
    verify_references(root, receipt)
    verify_references(root, manifest)
    verify_references(root, authoring)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    build = ROOT / "data/literature/v2/build_freeze_v1"
    scope_path = ROOT / "data/literature/v2/manifests/selection_freeze_v1/supported_scope.json"
    _, tables = verify_corpus(ROOT, build)
    scope = load(scope_path)
    verify_references(ROOT, scope)
    cases = load(args.draft_dir / "evaluation_cases_authored.json")
    semantic = load(args.draft_dir / "gold_semantic_authored.json")
    authoring = load(args.draft_dir / "semantic_stage.json")
    verify_authoring(ROOT, args.draft_dir, authoring)
    validate_dataset(cases, semantic, scope, tables["papers"], [], [], mapped=False)
    verify_source_spans(ROOT, semantic)
    gold = map_gold(semantic, tables)
    result = validate_dataset(cases, gold, scope, tables["papers"], tables["paragraphs"], tables["chunks"])
    isolation = runtime_isolation_audit(ROOT)
    if isolation["status"] != "PASS":
        raise ValueError(f"runtime isolation failed: {isolation['violations']}")
    if args.validate_only:
        print(json.dumps({**result, "runtime_isolation": isolation["status"],
                          "mapping_modes": dict(Counter(g["parent_mapping"]["match"] for g in gold["evidence_groups"]))}, indent=2))
        return
    audits = load(args.draft_dir / "review_audits.json")
    audits["provenance"] = {**result, "direct_xml_spans_verified": len(gold["evidence_groups"]),
                            "mapping_modes": dict(Counter(g["parent_mapping"]["match"] for g in gold["evidence_groups"])),
                            "mapped_groups": len(gold["evidence_groups"])}
    audits["leakage"]["runtime_isolation"] = isolation
    if audits["leakage"]["status"] != "PASS" or isolation["status"] != "PASS":
        raise ValueError("leakage audit failed")
    protected = load(args.draft_dir / "protected_before.json")
    audits["integrity"] = {"status": "PASS" if not protected_changes(ROOT, protected) else "FAIL",
                           "protected_file_count": len(protected), "changed_protected_files": protected_changes(ROOT, protected),
                           "corpus_content_sha256": EXPECTED_CORPUS_HASH, "corpus_references_checked": tables["references_checked"]}
    authoring["mapping_stage"] = {"created_at": datetime.now(timezone.utc).isoformat(),
                                  "semantic_gold_sha256_before_mapping": authoring["semantic_gold"]["sha256"],
                                  "policy": "source span coverage, no ranks/scores", "child_chunks_created": 0}
    authoring["initial_questions"] = reference(ROOT, (args.draft_dir / "authoring_questions.json").resolve())
    receipt = publish_freeze(ROOT, args.output.resolve(), args.reports.resolve(), cases, gold, scope_path,
                             build, authoring, audits, protected)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
