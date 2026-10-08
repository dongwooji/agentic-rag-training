"""Offline held-out validation and representation mapping; never a runtime input.

Semantic claims and source spans are authoritative. Parent chunk IDs are a
versioned evaluation view. This module neither searches nor computes rankings.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


SCOPES = {"REQUIRED_MUST_SUPPORT", "REQUIRED_LIMITED", "OPTIONAL", "OUT_OF_SCOPE"}
BEHAVIORS = {"answer_with_qualification", "partial_answer", "abstain", "clarification_required"}
BANNED_KEYS = {"retrieval_rank", "retrieval_score", "retrieved_chunks", "baseline_output", "model_answer"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def verify_references(root: Path, value: Any) -> int:
    """Check every explicit file reference, including nested manifest references."""
    checked = 0
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            path = (root / value["path"]).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("reference escapes repository")
            if not path.is_file() or sha256_file(path) != value["sha256"]:
                raise ValueError(f"reference hash mismatch: {value['path']}")
            checked += 1
        for nested in value.values():
            checked += verify_references(root, nested)
    elif isinstance(value, list):
        for nested in value:
            checked += verify_references(root, nested)
    return checked


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value), set())
    return set()


def _unique(rows: list[dict], key: str) -> dict[str, dict]:
    values = [r.get(key) for r in rows]
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError(f"missing {key}")
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {key}")
    return dict(zip(values, rows))


def validate_dataset(cases: dict, gold: dict, scope: dict, papers: list[dict],
                     paragraphs: list[dict], chunks: list[dict], *, mapped: bool = True) -> dict:
    """Fail closed on invalid labels, provenance, boundaries, or mapping."""
    if _keys(cases) & (BANNED_KEYS | {"gold", "semantic_claim", "evidence_groups", "chunk_ids"}):
        raise ValueError("Gold or ranking fields in case input")
    if _keys(gold) & BANNED_KEYS:
        raise ValueError("ranking-derived Gold fields")
    if not isinstance(cases.get("dataset_version"), str) or not cases["dataset_version"].strip():
        raise ValueError("missing dataset version")
    if cases.get("dataset_version") != gold.get("dataset_version"):
        raise ValueError("case/Gold version mismatch")
    case_map = _unique(cases["cases"], "id")
    gold_map = _unique(gold["cases"], "case_id")
    if set(case_map) != set(gold_map):
        raise ValueError("case/Gold membership mismatch")
    families = _unique(scope["questions"], "question_id")
    paper_map = _unique(papers, "pmid")
    paragraph_map = _unique(paragraphs, "paragraph_id") if mapped else {}
    chunk_map = _unique(chunks, "chunk_id") if mapped else {}
    group_map = _unique(gold["evidence_groups"], "evidence_group_id")
    by_case: dict[str, list] = {key: [] for key in case_map}
    sources = set()
    for group in group_map.values():
        cid = group.get("case_id")
        if cid not in case_map:
            raise ValueError("orphan evidence group")
        if type(group.get("required")) is not bool:
            raise ValueError("required must be boolean")
        for key in ("requirement", "semantic_claim", "qualification", "notes"):
            if not isinstance(group.get(key), str) or not group[key].strip():
                raise ValueError(f"missing group {key}")
        source = group.get("source")
        if not isinstance(source, dict):
            raise ValueError("missing source provenance")
        paper = paper_map.get(source.get("pmid"))
        if paper is None or source.get("pmcid") != paper["pmcid"]:
            raise ValueError("source outside MAIN corpus")
        for key in ("source_path", "source_sha256"):
            if source.get(key) != paper[key]:
                raise ValueError("source identity mismatch")
        for key in ("section", "paragraph_sha256", "span_text"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                raise ValueError(f"missing provenance {key}")
        for key, minimum in (("section_index", 0), ("paragraph_index", 1), ("span_start", 0)):
            if type(source.get(key)) is not int or source[key] < minimum:
                raise ValueError(f"invalid provenance {key}")
        if type(source.get("span_end")) is not int or source["span_end"] <= source["span_start"]:
            raise ValueError("invalid source span")
        if source["span_end"] - source["span_start"] != len(source["span_text"]):
            raise ValueError("source span length mismatch")
        sources.add((source["pmid"], source["pmcid"]))
        if mapped:
            paragraph = paragraph_map.get(source.get("paragraph_id"))
            if paragraph is None or not paragraph["included_in_chunks"]:
                raise ValueError("missing or excluded paragraph")
            for src_key, para_key in (("paragraph_sha256", "text_sha256"), ("pmid", "pmid"),
                                      ("pmcid", "pmcid"), ("section", "section"),
                                      ("section_index", "section_index"), ("paragraph_index", "paragraph_index"),
                                      ("source_sha256", "source_sha256"), ("source_path", "source_path")):
                if source[src_key] != paragraph[para_key]:
                    raise ValueError("paragraph provenance mismatch")
            if hashlib.sha256(paragraph["text"].encode()).hexdigest() != source["paragraph_sha256"]:
                raise ValueError("paragraph text hash mismatch")
            if paragraph["text"][source["span_start"]:source["span_end"]] != source["span_text"]:
                raise ValueError("source span not in paragraph")
            for index in (source["span_start"], source["span_end"]):
                text = paragraph["text"]
                if 0 < index < len(text) and not (text[index].isspace() or text[index - 1].isspace()):
                    raise ValueError("source span boundary is not word-aligned")
            mapping = group.get("parent_mapping", {})
            ids = mapping.get("chunk_ids", [])
            if mapping.get("match") not in {"any", "all"} or not ids or len(ids) != len(set(ids)):
                raise ValueError("invalid parent mapping")
            for chunk_id in ids:
                chunk = chunk_map.get(chunk_id)
                if chunk is None or chunk["pmid"] != source["pmid"] or source["paragraph_id"] not in chunk["source_paragraph_ids"]:
                    raise ValueError("invalid chunk membership")
            # Recompute exact span coverage instead of accepting a merely related paragraph.
            expected = map_source_span(source, paragraphs, chunks)
            if mapping != expected:
                raise ValueError("parent mapping does not cover semantic source span")
        elif "parent_mapping" in group or "paragraph_id" in source:
            raise ValueError("semantic authoring already contains chunk mapping")
        by_case[cid].append(group)
    for cid, case in case_map.items():
        for key in ("question", "question_family_id", "scope_class", "task_type", "expected_behavior", "notes", "scope_boundary_reference"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f"missing case {key}")
        family = families.get(case["question_family_id"])
        if family is None:
            raise ValueError("unknown family")
        if case["scope_class"] not in SCOPES or family["scope"] != case["scope_class"]:
            raise ValueError("invalid scope class")
        if family.get("NOT_DECIDED") is not False or family.get("human_approved_scope") is not True:
            raise ValueError("unresolved family")
        if case["scope_boundary_reference"] != family["approved_scope_boundary"]:
            raise ValueError("frozen scope boundary mismatch")
        if case["expected_behavior"] not in BEHAVIORS:
            raise ValueError("invalid expected behavior")
        if case["task_type"] not in {"literature_only", "hybrid", "boundary"} or case.get("category") != case["task_type"]:
            raise ValueError("invalid task type")
        for key in ("requires_log", "requires_literature", "retrieval_metric_eligible"):
            if type(case.get(key)) is not bool:
                raise ValueError(f"invalid boolean {key}")
        if case["requires_log"] != (case["task_type"] == "hybrid"):
            raise ValueError("hybrid/log mismatch")
        if case["requires_literature"] != (case["task_type"] != "boundary"):
            raise ValueError("task/literature mismatch")
        expected = gold_map[cid]
        for key in ("supported_conclusion", "required_qualification", "prohibited_overgeneralization", "unsupported_extension", "boundary_expectation"):
            if not isinstance(expected.get(key), str) or not expected[key].strip():
                raise ValueError(f"missing answer boundary {key}")
        if expected.get("expected_behavior") != case["expected_behavior"]:
            raise ValueError("expected behavior disagreement")
        if case["requires_log"]:
            rows = case.get("log_input", {}).get("records", [])
            if case.get("log_input", {}).get("source_type") != "synthetic" or not rows or not expected.get("log_expectations"):
                raise ValueError("missing synthetic hybrid log/provenance")
        elif case.get("log_input") or expected.get("log_expectations"):
            raise ValueError("log fields on non-hybrid case")
        required = [g for g in by_case[cid] if g["required"]]
        if case["retrieval_metric_eligible"] != bool(required):
            raise ValueError("retrieval eligibility/group mismatch")
        if required and not case["requires_literature"]:
            raise ValueError("literature Gold on boundary case")
        if case["scope_class"] == "REQUIRED_MUST_SUPPORT" and not required:
            raise ValueError("MUST case lacks required evidence")
        if case["scope_class"] == "OUT_OF_SCOPE" and (by_case[cid] or case["expected_behavior"] not in {"abstain", "clarification_required"}):
            raise ValueError("forced source/answer on out-of-scope case")
    return {"status": "PASS", "case_count": len(case_map),
            "scope_distribution": dict(Counter(c["scope_class"] for c in case_map.values())),
            "task_distribution": dict(Counter(c["task_type"] for c in case_map.values())),
            "family_count": len({c["question_family_id"] for c in case_map.values()}),
            "evidence_group_count": len(group_map),
            "required_group_count": sum(g["required"] for g in group_map.values()),
            "optional_group_count": sum(not g["required"] for g in group_map.values()),
            "unique_sources": [{"pmid": p, "pmcid": c} for p, c in sorted(sources)],
            "retrieval_eligible_case_count": sum(c["retrieval_metric_eligible"] for c in case_map.values()),
            "missing_provenance": 0, "sources_outside_main": 0}


def map_source_span(source: dict, paragraphs: list[dict], chunks: list[dict]) -> dict:
    """Map a source span to complete parent alternatives or a covering union.

    Token offsets are reconstructed from the frozen section, in source order.
    A paragraph membership link alone is never sufficient for an answer hit.
    """
    section_paras = sorted((p for p in paragraphs if p["pmid"] == source["pmid"]
                            and p["section_index"] == source["section_index"] and p["included_in_chunks"]),
                           key=lambda p: p["paragraph_index"])
    tokens: list[str] = []
    target = None
    for para in section_paras:
        if para["paragraph_index"] == source["paragraph_index"]:
            start = len(tokens) + len(para["text"][:source["span_start"]].split())
            end = start + len(source["span_text"].split())
            target = (start, end)
        tokens.extend(para["text"].split())
    if target is None:
        raise ValueError("source paragraph missing from mapping")
    intervals = []
    cursor = 0
    for chunk in chunks:
        if chunk["pmid"] != source["pmid"] or chunk["section_index"] != source["section_index"]:
            continue
        words = chunk["text"].split()
        positions = [i for i in range(cursor, len(tokens) - len(words) + 1)
                     if tokens[i:i + len(words)] == words]
        if not positions:
            raise ValueError("chunk text cannot be aligned to frozen section")
        start = positions[0]
        cursor = start + 1
        if source["paragraph_id"] in chunk["source_paragraph_ids"]:
            intervals.append((start, start + len(words), chunk["chunk_id"]))
    start, end = target
    complete = [cid for a, b, cid in intervals if a <= start and b >= end]
    if complete:
        return {"match": "any", "chunk_ids": complete}
    selected = []
    cursor = start
    while cursor < end:
        covering = [(b, cid) for a, b, cid in intervals if a <= cursor < b]
        if not covering:
            raise ValueError("uncovered semantic source span")
        cursor, cid = max(covering)
        selected.append(cid)
    return {"match": "all", "chunk_ids": selected}


def retrieval_metric_cases(cases: dict, gold: dict) -> tuple[list[dict], list[str]]:
    """Evaluation-only adapter for the existing any/all retrieval metrics.

    Non-retrieval cases are excluded explicitly, never counted as zero recall.
    MRR remains the existing first required-chunk hit metric, not completion rank.
    Call validate_dataset before adapting artifacts loaded from disk.
    """
    groups = _unique(gold["evidence_groups"], "evidence_group_id")
    result, excluded = [], []
    for case in cases["cases"]:
        if not case["retrieval_metric_eligible"]:
            excluded.append(case["id"])
            continue
        selected = [{"id": g["evidence_group_id"], "required": g["required"],
                     **g["parent_mapping"]} for g in groups.values() if g["case_id"] == case["id"]]
        result.append({"id": case["id"], "gold": {"literature_evidence_groups": selected}})
    return result, excluded


def runtime_isolation_audit(root: Path) -> dict:
    """Static import/path guard for all runtime source packages and configuration.

    This is supplemented by the existing API import-closure tests. It does not
    claim an OS access-control boundary against arbitrary malicious file reads.
    """
    excluded = {"evaluation", "literature", "smoke"}
    files = [p for p in (root / "src").rglob("*.py")
             if p.relative_to(root / "src").parts[0] not in excluded]
    violations = []
    for path in files:
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        package = module.rpartition(".")[0] if path.name != "__init__.py" else module.removesuffix(".__init__")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                names = [base] + [base + "." + alias.name for alias in node.names]
            if any(n == "src.evaluation" or n.startswith("src.evaluation.") for n in names):
                violations.append({"path": path.relative_to(root).as_posix(), "line": node.lineno, "reason": "evaluation import"})
    configs = list((root / "config").glob("*"))
    for path in files + [p for p in configs if p.is_file()]:
        text = path.read_text(encoding="utf-8-sig").lower().replace("\\", "/")
        if any(v in text for v in ("heldout_v2", "gold_evidence.json", "data/evaluation/")):
            violations.append({"path": path.relative_to(root).as_posix(), "reason": "held-out/Gold path"})
    return {"status": "FAIL" if violations else "PASS", "source_files_checked": len(files),
            "config_files_checked": len(configs), "violations": violations,
            "limitation": "Static code/config audit; API import closure is tested separately."}
