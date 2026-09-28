"""One-shot Phase 7 BM25 and Dense+BM25+RRF comparison pipeline."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import shutil
from statistics import mean, median
from typing import Any, Sequence
import uuid

import numpy as np

from src.evaluation.retrieval_metrics import (
    evaluate_retrieval,
    required_gold_chunk_ids,
    required_literature_groups,
)

from .baseline import ALL_KS, FrozenRetrievalInputs, load_frozen_retrieval_inputs, sha256_file
from .bm25 import BM25Index, DEFAULT_B, DEFAULT_K1, TOKENIZER_VERSION
from .rrf import DEFAULT_RRF_K, reciprocal_rank_fusion


PHASE7_VERSION = "hybrid_baseline_v1"
TOP_K = 10
PRIMARY_METRICS = (
    "evidence_group_recall@5",
    "evidence_group_recall@10",
    "complete_evidence@5",
    "complete_evidence@10",
    "chunk_recall@5",
    "chunk_recall@10",
    "mrr",
)
DIAGNOSTIC_METRICS = (
    "evidence_group_recall@1",
    "evidence_group_recall@3",
    "complete_evidence@1",
    "complete_evidence@3",
    "chunk_recall@1",
    "chunk_recall@3",
)
METHODS = ("dense", "bm25", "hybrid")

# Cohorts are copied from the frozen Phase 6 FAILURE_ANALYSIS.md before Phase 7
# results are produced. They are diagnostic strata, not tuning targets.
PHASE6_COHORTS = {
    "same_paper_wrong_chunk": (
        "LIT-001",
        "LIT-002",
        "LIT-004",
        "LIT-008",
        "HYB-002",
        "HYB-004",
        "HYB-006",
    ),
    "gold_rank_6_10": ("LIT-004", "LIT-005", "LIT-007", "LIT-009", "LIT-010"),
    "gold_not_in_top10": (
        "LIT-001",
        "HYB-001",
        "HYB-002",
        "HYB-003",
        "HYB-004",
        "HYB-006",
        "HYB-007",
    ),
    "hybrid_cases": tuple(f"HYB-{index:03d}" for index in range(1, 9)),
    "multi_evidence_incomplete": (
        "LIT-001",
        "LIT-002",
        "LIT-004",
        "LIT-008",
        "HYB-002",
    ),
    "dense_success_top5": ("LIT-003", "LIT-006", "HYB-005", "HYB-008"),
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _ranked_ids(result: dict[str, Any]) -> list[str]:
    return [str(item["chunk_id"]) for item in result["retrieved"]]


def _validate_ranked_results(
    results: Sequence[dict[str, Any]], cases: Sequence[dict[str, Any]], *, label: str
) -> None:
    expected = {case["id"]: case["question"] for case in cases}
    if {item.get("case_id") for item in results} != set(expected):
        raise RuntimeError(f"{label} result case IDs do not match frozen evaluation cases")
    if len(results) != len(expected):
        raise RuntimeError(f"{label} results contain duplicate cases")
    for result in results:
        case_id = result["case_id"]
        if result.get("question") != expected[case_id]:
            raise RuntimeError(f"{label} query differs from frozen question for {case_id}")
        hits = result.get("retrieved", [])
        if len(hits) != TOP_K:
            raise RuntimeError(f"{label} must store exactly Top-{TOP_K} for {case_id}")
        ranks = [hit.get("rank") for hit in hits]
        ids = [hit.get("chunk_id") for hit in hits]
        if ranks != list(range(1, TOP_K + 1)) or len(ids) != len(set(ids)):
            raise RuntimeError(f"{label} has invalid ranks or duplicates for {case_id}")


def load_frozen_dense_reference(
    project_root: str | Path, inputs: FrozenRetrievalInputs
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    baseline_dir = root / "reports/baselines/dense_baseline_v1"
    manifest_path = baseline_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("baseline_version") != "dense_baseline_v1" or manifest.get("status") != "frozen":
        raise RuntimeError("Phase 6 Dense reference is not the frozen dense_baseline_v1")
    artifact_hashes: dict[str, str] = {}
    for artifact in manifest["artifacts"]:
        path = baseline_dir / artifact["path"]
        actual = sha256_file(path)
        if actual != artifact["sha256"]:
            raise RuntimeError(f"Dense frozen artifact hash mismatch: {path.name}")
        artifact_hashes[artifact["path"]] = actual
    reproducibility = _read_json(baseline_dir / "reproducibility.json")
    if reproducibility["inputs"]["corpus_chunks_sha256"] != inputs.corpus_chunks_sha256:
        raise RuntimeError("Dense reference corpus hash differs from Phase 7 corpus")
    if reproducibility["inputs"]["eval_dataset_sha256"] != inputs.eval_dataset_sha256:
        raise RuntimeError("Dense reference eval hash differs from Phase 7 evaluation set")

    results = _read_jsonl(baseline_dir / "retrieval_results.jsonl")
    _validate_ranked_results(results, inputs.cases, label="Dense")
    metrics = _read_json(baseline_dir / "metrics.json")
    recomputed = evaluate_retrieval(
        inputs.cases,
        {result["case_id"]: _ranked_ids(result) for result in results},
        ks=ALL_KS,
    )
    for metric, value in metrics["macro"].items():
        if not math.isclose(float(value), float(recomputed["macro"][metric]), abs_tol=1e-12):
            raise RuntimeError(f"Dense stored metric differs from frozen ranks: {metric}")
    failure_path = baseline_dir / "FAILURE_ANALYSIS.md"
    return {
        "directory": baseline_dir,
        "manifest": manifest,
        "manifest_sha256": sha256_file(manifest_path),
        "artifact_hashes": artifact_hashes,
        "reproducibility": reproducibility,
        "metrics": recomputed,
        "stored_metrics": metrics,
        "results": results,
        "failure_analysis_sha256": (
            sha256_file(failure_path) if failure_path.is_file() else None
        ),
    }


def run_bm25_retrieval(
    chunks: Sequence[dict[str, Any]], question_records: Sequence[dict[str, str]]
) -> tuple[BM25Index, list[dict[str, Any]]]:
    """Rank frozen questions using only frozen text, never Gold labels."""

    index = BM25Index(chunks, k1=DEFAULT_K1, b=DEFAULT_B)
    results: list[dict[str, Any]] = []
    for record in question_records:
        response = index.search(record["question"], top_k=TOP_K)
        results.append(
            {
                "case_id": record["case_id"],
                "question": record["question"],
                "retrieved": [
                    {"rank": rank, "chunk_id": hit.chunk_id, "score": hit.score}
                    for rank, hit in enumerate(response.hits, 1)
                ],
                "latency_ms": {"bm25_search": response.latency_ms},
                "query_diagnostics": {
                    "query_token_count": response.query_token_count,
                    "matched_query_terms": response.matched_query_terms,
                },
            }
        )
    return index, results


def run_rrf_fusion(
    dense_results: Sequence[dict[str, Any]],
    bm25_results: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fuse stored rankings without accepting Gold labels or case metadata."""

    dense_by_case = {result["case_id"]: result for result in dense_results}
    hybrid_results: list[dict[str, Any]] = []
    for bm25 in bm25_results:
        case_id = bm25["case_id"]
        dense = dense_by_case[case_id]
        response = reciprocal_rank_fusion(
            _ranked_ids(dense),
            _ranked_ids(bm25),
            rrf_k=DEFAULT_RRF_K,
            top_k=TOP_K,
        )
        hybrid_results.append(
            {
                "case_id": case_id,
                "question": bm25["question"],
                "retrieved": [
                    {
                        "rank": rank,
                        "chunk_id": hit.chunk_id,
                        "score": hit.score,
                        "dense_rank": hit.dense_rank,
                        "bm25_rank": hit.bm25_rank,
                        "dense_contribution": hit.dense_contribution,
                        "bm25_contribution": hit.bm25_contribution,
                    }
                    for rank, hit in enumerate(response.hits, 1)
                ],
                "latency_ms": {"rrf_fusion": response.latency_ms},
                "candidate_count": response.candidate_count,
            }
        )
    return hybrid_results


def _first_gold_rank(case: dict[str, Any], ranking: Sequence[str]) -> int | None:
    gold = required_gold_chunk_ids(case)
    return next((rank for rank, chunk_id in enumerate(ranking, 1) if chunk_id in gold), None)


def _group_completion_rank(group: dict[str, Any], ranking: Sequence[str]) -> int | None:
    ranks = {
        chunk_id: rank for rank, chunk_id in enumerate(ranking, 1) if chunk_id in group["chunk_ids"]
    }
    if group["match"] == "any":
        return min(ranks.values()) if ranks else None
    if group["match"] == "all":
        return max(ranks.values()) if len(ranks) == len(set(group["chunk_ids"])) else None
    raise ValueError(f"Unknown group match mode: {group['match']}")


def _metric_differences(metrics: dict[str, Any]) -> dict[str, Any]:
    comparisons = (
        ("bm25_minus_dense", "bm25", "dense"),
        ("hybrid_minus_dense", "hybrid", "dense"),
        ("hybrid_minus_bm25", "hybrid", "bm25"),
    )
    output: dict[str, Any] = {}
    for label, candidate, reference in comparisons:
        output[label] = {}
        for metric in PRIMARY_METRICS + DIAGNOSTIC_METRICS:
            candidate_value = float(metrics[candidate]["macro"][metric])
            reference_value = float(metrics[reference]["macro"][metric])
            absolute = candidate_value - reference_value
            output[label][metric] = {
                "absolute": absolute,
                "relative_percent": (
                    None if reference_value == 0 else absolute / reference_value * 100.0
                ),
            }
    return output


def build_case_comparisons(
    cases: Sequence[dict[str, Any]],
    results: dict[str, Sequence[dict[str, Any]]],
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    result_maps = {
        method: {result["case_id"]: result for result in method_results}
        for method, method_results in results.items()
    }
    comparisons: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["id"]
        rankings = {
            method: _ranked_ids(result_maps[method][case_id]) for method in METHODS
        }
        first_ranks = {
            method: _first_gold_rank(case, rankings[method]) for method in METHODS
        }
        per_method = {
            method: {
                "first_gold_rank": first_ranks[method],
                **metrics[method]["per_case"][case_id],
            }
            for method in METHODS
        }
        groups = []
        for group in required_literature_groups(case):
            group_ranks = {
                method: _group_completion_rank(group, rankings[method])
                for method in METHODS
            }
            chunk_ranks = {
                chunk_id: {
                    method: (
                        rankings[method].index(chunk_id) + 1
                        if chunk_id in rankings[method]
                        else None
                    )
                    for method in METHODS
                }
                for chunk_id in group["chunk_ids"]
            }
            groups.append(
                {
                    "evidence_group_id": group["id"],
                    "claim": group["claim"],
                    "match": group["match"],
                    "gold_chunk_ids": group["chunk_ids"],
                    "completion_rank": group_ranks,
                    "gold_chunk_ranks": chunk_ranks,
                }
            )

        dense_c5 = bool(per_method["dense"]["complete_evidence@5"])
        bm25_c5 = bool(per_method["bm25"]["complete_evidence@5"])
        hybrid_c5 = bool(per_method["hybrid"]["complete_evidence@5"])
        labels: list[str] = []
        if not dense_c5 and hybrid_c5:
            labels.append("DENSE_FAILURE_RECOVERED_AT5")
        if not bool(per_method["hybrid"]["complete_evidence@10"]):
            labels.append("HYBRID_STILL_INCOMPLETE_AT10")
        if dense_c5 and not hybrid_c5:
            labels.append("DENSE_SUCCESS_DEGRADED_AT5")
        if bm25_c5 and not dense_c5:
            labels.append("BM25_ONLY_SUCCESS_AT5")
        if dense_c5 and not bm25_c5:
            labels.append("DENSE_ONLY_SUCCESS_AT5")
        if not dense_c5 and not bm25_c5 and hybrid_c5:
            labels.append("COMPLEMENTARY_SUCCESS_AT5")
        comparisons.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "question": case["question"],
                "per_method": per_method,
                "first_gold_rank_movement": first_ranks,
                "evidence_group_rank_movement": groups,
                "comparison_labels": labels,
            }
        )
    return comparisons


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("Latency list cannot be empty")
    return {
        "mean": mean(values),
        "median": median(values),
        "p95": float(np.percentile(np.asarray(values), 95)),
        "min": min(values),
        "max": max(values),
    }


def build_latency_comparison(
    dense_results: Sequence[dict[str, Any]],
    bm25_results: Sequence[dict[str, Any]],
    hybrid_results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    dense_by_case = {result["case_id"]: result for result in dense_results}
    bm25_by_case = {result["case_id"]: result for result in bm25_results}
    hybrid_by_case = {result["case_id"]: result for result in hybrid_results}
    case_ids = [result["case_id"] for result in dense_results]
    dense = [float(dense_by_case[case_id]["latency_ms"]["end_to_end"]) for case_id in case_ids]
    bm25 = [float(bm25_by_case[case_id]["latency_ms"]["bm25_search"]) for case_id in case_ids]
    fusion = [float(hybrid_by_case[case_id]["latency_ms"]["rrf_fusion"]) for case_id in case_ids]
    estimated_serial = [
        dense_value + bm25_value + fusion_value
        for dense_value, bm25_value, fusion_value in zip(dense, bm25, fusion, strict=True)
    ]
    return {
        "dense_stored_end_to_end": _latency_summary(dense),
        "bm25_search": _latency_summary(bm25),
        "rrf_fusion_only": _latency_summary(fusion),
        "hybrid_estimated_serial": _latency_summary(estimated_serial),
        "caveat": (
            "Dense timings are stored Phase 6 measurements; BM25/RRF timings are Phase 7 "
            "measurements. Hybrid serial latency is their per-case sum, not a same-process "
            "wall-clock measurement, and no parallel execution is assumed."
        ),
    }


def build_hypothesis_checks(case_comparisons: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["case_id"]: item for item in case_comparisons}

    def cohort(name: str) -> list[str]:
        return [case_id for case_id in PHASE6_COHORTS[name] if case_id in by_id]

    def complete(case_id: str, method: str, k: int) -> bool:
        return bool(by_id[case_id]["per_method"][method][f"complete_evidence@{k}"])

    def first(case_id: str, method: str) -> int | None:
        return by_id[case_id]["first_gold_rank_movement"][method]

    intra_rows = []
    for case_id in cohort("same_paper_wrong_chunk"):
        dense_rank = first(case_id, "dense")
        hybrid_rank = first(case_id, "hybrid")
        intra_rows.append(
            {
                "case_id": case_id,
                "dense_first_gold_rank": dense_rank,
                "hybrid_first_gold_rank": hybrid_rank,
                "hybrid_first_rank_improved": hybrid_rank is not None
                and (dense_rank is None or hybrid_rank < dense_rank),
                "dense_evidence_group_recall@10": by_id[case_id]["per_method"]["dense"]["evidence_group_recall@10"],
                "hybrid_evidence_group_recall@10": by_id[case_id]["per_method"]["hybrid"]["evidence_group_recall@10"],
            }
        )
    late = [
        case_id
        for case_id in cohort("gold_rank_6_10")
        if complete(case_id, "hybrid", 5)
    ]
    recovered_no_gold = [
        case_id
        for case_id in cohort("gold_not_in_top10")
        if first(case_id, "hybrid") is not None
    ]
    hybrid_cases = cohort("hybrid_cases")
    dilution = {
        method: {
            "no_gold_in_top10": sum(first(case_id, method) is None for case_id in hybrid_cases),
            "complete@5": sum(complete(case_id, method, 5) for case_id in hybrid_cases),
            "complete@10": sum(complete(case_id, method, 10) for case_id in hybrid_cases),
        }
        for method in METHODS
    }
    multi_cases = cohort("multi_evidence_incomplete")
    multi = {
        method: {
            "complete@5": sum(complete(case_id, method, 5) for case_id in multi_cases),
            "complete@10": sum(complete(case_id, method, 10) for case_id in multi_cases),
        }
        for method in METHODS
    }
    degraded = [
        case_id
        for case_id in cohort("dense_success_top5")
        if not complete(case_id, "hybrid", 5)
    ]
    rank_worse = [
        case_id
        for case_id in cohort("dense_success_top5")
        if first(case_id, "hybrid") is None
        or (
            first(case_id, "dense") is not None
            and first(case_id, "hybrid") > first(case_id, "dense")
        )
    ]
    return {
        "phase6_cohorts": {key: list(value) for key, value in PHASE6_COHORTS.items()},
        "intra_paper_chunk_discrimination": intra_rows,
        "gold_rank_6_10_promoted_to_complete@5": late,
        "gold_not_in_top10_recovered_by_hybrid": recovered_no_gold,
        "hybrid_query_dilution": dilution,
        "multi_evidence_completeness": multi,
        "dense_success_degraded_at5": degraded,
        "dense_success_first_gold_rank_worsened": rank_worse,
    }


def build_comparison(
    *,
    cases: Sequence[dict[str, Any]],
    dense_results: Sequence[dict[str, Any]],
    bm25_results: Sequence[dict[str, Any]],
    hybrid_results: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_results = {
        "dense": dense_results,
        "bm25": bm25_results,
        "hybrid": hybrid_results,
    }
    metrics = {
        method: evaluate_retrieval(
            list(cases),
            {result["case_id"]: _ranked_ids(result) for result in results},
            ks=ALL_KS,
        )
        for method, results in raw_results.items()
    }
    case_comparisons = build_case_comparisons(cases, raw_results, metrics)
    label_counts = Counter(
        label for case in case_comparisons for label in case["comparison_labels"]
    )
    comparison = {
        "phase7_version": PHASE7_VERSION,
        "case_count": len(cases),
        "primary_metrics": list(PRIMARY_METRICS),
        "diagnostic_metrics": list(DIAGNOSTIC_METRICS),
        "metrics": metrics,
        "metric_differences": _metric_differences(metrics),
        "case_comparison_label_counts": dict(sorted(label_counts.items())),
        "hypothesis_checks": build_hypothesis_checks(case_comparisons),
        "latency_ms": build_latency_comparison(
            dense_results, bm25_results, hybrid_results
        ),
    }
    return comparison, case_comparisons


def _format_rank(value: int | None) -> str:
    return "not in Top-10" if value is None else str(value)


def _format_relative(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}%"


def build_report(
    *,
    comparison: dict[str, Any],
    case_comparisons: Sequence[dict[str, Any]],
    dense_results: Sequence[dict[str, Any]],
    bm25_results: Sequence[dict[str, Any]],
    hybrid_results: Sequence[dict[str, Any]],
    reproducibility: dict[str, Any],
) -> str:
    metrics = comparison["metrics"]
    differences = comparison["metric_differences"]
    by_result = {
        "dense": {item["case_id"]: item for item in dense_results},
        "bm25": {item["case_id"]: item for item in bm25_results},
        "hybrid": {item["case_id"]: item for item in hybrid_results},
    }
    by_case = {item["case_id"]: item for item in case_comparisons}
    labels = {
        label: [
            item["case_id"]
            for item in case_comparisons
            if label in item["comparison_labels"]
        ]
        for label in (
            "DENSE_FAILURE_RECOVERED_AT5",
            "HYBRID_STILL_INCOMPLETE_AT10",
            "DENSE_SUCCESS_DEGRADED_AT5",
            "BM25_ONLY_SUCCESS_AT5",
            "DENSE_ONLY_SUCCESS_AT5",
            "COMPLEMENTARY_SUCCESS_AT5",
        )
    }
    lines = [
        "# Phase 7 Hybrid Retrieval Comparison",
        "",
        "> Immutable first configuration: frozen Dense vs BM25 standalone vs Dense+BM25+RRF. No post-result tuning was performed.",
        "",
        "## Frozen inputs and fixed configuration",
        "",
        f"- Corpus: `{reproducibility['inputs']['corpus_version']}` / `{reproducibility['inputs']['corpus_chunks_sha256']}`",
        f"- Evaluation: `{reproducibility['inputs']['eval_dataset_version']}` / `{reproducibility['inputs']['eval_dataset_sha256']}`",
        f"- Dense reference: `dense_baseline_v1` retrieval results / `{reproducibility['dense_reference']['retrieval_results_sha256']}`",
        f"- BM25: `k1={DEFAULT_K1}`, `b={DEFAULT_B}`, tokenizer `{TOKENIZER_VERSION}`, Top-{TOP_K}",
        "- BM25 formula: `Σ idf(t) × tf(t,d)(k1+1) / (tf(t,d)+k1(1-b+b·dl/avgdl))`",
        "- BM25 IDF: `ln(1 + (N-df+0.5)/(df+0.5))`; Latin/Hangul boundaries split; no stemming or stopword removal",
        f"- RRF: equal weights, source depth {TOP_K}, `k={DEFAULT_RRF_K}`, `score(d)=Σ 1/({DEFAULT_RRF_K}+rank_i(d))`",
        "- RRF candidate pool: union of the frozen Dense Top-10 and first-run BM25 Top-10",
        "- Queries: unchanged frozen `question` fields; no rewrite or case-specific parameters",
        "",
        "## Aggregate primary metrics",
        "",
        "Absolute and relative differences use the method named after `minus` as the reference.",
        "",
        "| Metric | Dense | BM25 | Hybrid | BM25−Dense abs / rel | Hybrid−Dense abs / rel | Hybrid−BM25 abs / rel |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for metric in PRIMARY_METRICS:
        bd = differences["bm25_minus_dense"][metric]
        hd = differences["hybrid_minus_dense"][metric]
        hb = differences["hybrid_minus_bm25"][metric]
        lines.append(
            f"| `{metric}` | {metrics['dense']['macro'][metric]:.6f} | "
            f"{metrics['bm25']['macro'][metric]:.6f} | {metrics['hybrid']['macro'][metric]:.6f} | "
            f"{bd['absolute']:+.6f} / {_format_relative(bd['relative_percent'])} | "
            f"{hd['absolute']:+.6f} / {_format_relative(hd['relative_percent'])} | "
            f"{hb['absolute']:+.6f} / {_format_relative(hb['relative_percent'])} |"
        )

    lines.extend(
        [
            "",
            "## Diagnostic @1/@3 metrics",
            "",
            "| Metric | Dense | BM25 | Hybrid |",
            "|---|---:|---:|---:|",
        ]
    )
    for metric in DIAGNOSTIC_METRICS:
        lines.append(
            f"| `{metric}` | {metrics['dense']['macro'][metric]:.6f} | "
            f"{metrics['bm25']['macro'][metric]:.6f} | {metrics['hybrid']['macro'][metric]:.6f} |"
        )

    lines.extend(
        [
            "",
            "## Case-level CompleteEvidence and first-Gold movement",
            "",
            "| Case | Dense C@5/10 | BM25 C@5/10 | Hybrid C@5/10 | First Gold Dense → BM25 → Hybrid |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for item in case_comparisons:
        method = item["per_method"]
        ranks = item["first_gold_rank_movement"]
        lines.append(
            f"| `{item['case_id']}` | {int(method['dense']['complete_evidence@5'])}/"
            f"{int(method['dense']['complete_evidence@10'])} | "
            f"{int(method['bm25']['complete_evidence@5'])}/{int(method['bm25']['complete_evidence@10'])} | "
            f"{int(method['hybrid']['complete_evidence@5'])}/{int(method['hybrid']['complete_evidence@10'])} | "
            f"{_format_rank(ranks['dense'])} → {_format_rank(ranks['bm25'])} → {_format_rank(ranks['hybrid'])} |"
        )

    lines.extend(["", "## Outcome slices", ""])
    slice_labels = (
        ("Dense failure recovered by Hybrid at Top-5", "DENSE_FAILURE_RECOVERED_AT5"),
        ("Hybrid still incomplete at Top-10", "HYBRID_STILL_INCOMPLETE_AT10"),
        ("Dense Top-5 success degraded by Hybrid", "DENSE_SUCCESS_DEGRADED_AT5"),
        ("BM25-only Top-5 success", "BM25_ONLY_SUCCESS_AT5"),
        ("Dense-only Top-5 success", "DENSE_ONLY_SUCCESS_AT5"),
        ("Complementary Top-5 success", "COMPLEMENTARY_SUCCESS_AT5"),
    )
    for title, label in slice_labels:
        lines.append(f"- **{title}:** {', '.join(labels[label]) if labels[label] else 'none'}")

    checks = comparison["hypothesis_checks"]
    lines.extend(
        [
            "",
            "## Phase 6 hypothesis checks",
            "",
            "### Intra-paper chunk discrimination",
            "",
            "| Case | Dense first Gold | Hybrid first Gold | EGR@10 Dense → Hybrid | First rank improved |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in checks["intra_paper_chunk_discrimination"]:
        lines.append(
            f"| `{row['case_id']}` | {_format_rank(row['dense_first_gold_rank'])} | "
            f"{_format_rank(row['hybrid_first_gold_rank'])} | "
            f"{row['dense_evidence_group_recall@10']:.3f} → {row['hybrid_evidence_group_recall@10']:.3f} | "
            f"{'yes' if row['hybrid_first_rank_improved'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            f"- **GOLD_RANK_6_10 promoted to complete Top-5:** {', '.join(checks['gold_rank_6_10_promoted_to_complete@5']) or 'none'}",
            f"- **GOLD_NOT_IN_TOP10 recovered by Hybrid:** {', '.join(checks['gold_not_in_top10_recovered_by_hybrid']) or 'none'}",
            f"- **Dense success degraded at Top-5:** {', '.join(checks['dense_success_degraded_at5']) or 'none'}",
            f"- **Dense-success first Gold rank worsened:** {', '.join(checks['dense_success_first_gold_rank_worsened']) or 'none'}",
            "",
            "### Hybrid-query dilution cohort (HYB-001–008)",
            "",
            "| Method | No Gold in Top-10 | Complete@5 | Complete@10 |",
            "|---|---:|---:|---:|",
        ]
    )
    for method in METHODS:
        values = checks["hybrid_query_dilution"][method]
        lines.append(
            f"| {method} | {values['no_gold_in_top10']}/8 | {values['complete@5']}/8 | {values['complete@10']}/8 |"
        )
    lines.extend(
        [
            "",
            "### Phase 6 multi-evidence-incomplete cohort",
            "",
            "| Method | Complete@5 | Complete@10 |",
            "|---|---:|---:|",
        ]
    )
    for method in METHODS:
        values = checks["multi_evidence_completeness"][method]
        lines.append(
            f"| {method} | {values['complete@5']}/5 | {values['complete@10']}/5 |"
        )

    latency = comparison["latency_ms"]
    lines.extend(
        [
            "",
            "## Retrieval latency",
            "",
            "| Measurement | Mean ms | Median ms | P95 ms |",
            "|---|---:|---:|---:|",
        ]
    )
    for label, values in latency.items():
        if label == "caveat":
            continue
        lines.append(
            f"| `{label}` | {values['mean']:.3f} | {values['median']:.3f} | {values['p95']:.3f} |"
        )
    lines.extend(["", f"Latency caveat: {latency['caveat']}", ""])

    lines.extend(["## Per-case rank movement and Top-10", ""])
    for case in case_comparisons:
        case_id = case["case_id"]
        lines.extend(
            [
                f"### {case_id}",
                "",
                f"- Query: {case['question']}",
                f"- First Gold: Dense {_format_rank(case['first_gold_rank_movement']['dense'])}; "
                f"BM25 {_format_rank(case['first_gold_rank_movement']['bm25'])}; "
                f"Hybrid {_format_rank(case['first_gold_rank_movement']['hybrid'])}",
                f"- Comparison labels: {', '.join(f'`{label}`' for label in case['comparison_labels']) or 'none'}",
                "- Evidence-group completion ranks (`any`/`all` preserved):",
            ]
        )
        for group in case["evidence_group_rank_movement"]:
            ranks = group["completion_rank"]
            lines.append(
                f"  - `{group['evidence_group_id']}` (`{group['match']}`): "
                f"Dense {_format_rank(ranks['dense'])}; BM25 {_format_rank(ranks['bm25'])}; "
                f"Hybrid {_format_rank(ranks['hybrid'])}"
            )
            for chunk_id, chunk_ranks in group["gold_chunk_ranks"].items():
                lines.append(
                    f"    - `{chunk_id}` — Dense {_format_rank(chunk_ranks['dense'])}; "
                    f"BM25 {_format_rank(chunk_ranks['bm25'])}; Hybrid {_format_rank(chunk_ranks['hybrid'])}"
                )
        for method in METHODS:
            lines.append(f"- {method.upper()} Top-10:")
            for hit in by_result[method][case_id]["retrieved"]:
                if method == "hybrid":
                    component = (
                        f"; Dense rank {_format_rank(hit['dense_rank'])}; "
                        f"BM25 rank {_format_rank(hit['bm25_rank'])}"
                    )
                else:
                    component = ""
                lines.append(
                    f"  {hit['rank']}. `{hit['chunk_id']}` — score `{hit['score']:.8f}`{component}"
                )
        lines.append("")

    lines.extend(
        [
            "## Reproducibility and close-out",
            "",
            f"- BM25 tokenized corpus fingerprint: `{reproducibility['bm25']['tokenized_corpus_sha256']}`",
            f"- Dense manifest SHA-256: `{reproducibility['dense_reference']['manifest_sha256']}`",
            f"- Phase 6 failure analysis SHA-256: `{reproducibility['dense_reference']['failure_analysis_sha256']}`",
            f"- Python / NumPy: `{reproducibility['environment']['python_version']}` / `{reproducibility['environment']['numpy_version']}`",
            "- Gold labels entered only after all BM25 and Hybrid rankings were fixed.",
            "- No parameter was changed after observing evaluation results.",
            "- No reranker, answer generation, Agent, LangGraph, grader, or Phase 8 Tool was implemented.",
            "",
        ]
    )
    return "\n".join(lines)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_immutable_phase7_artifacts(
    *,
    output_dir: str | Path,
    index_metadata: dict[str, Any],
    bm25_results: Sequence[dict[str, Any]],
    hybrid_results: Sequence[dict[str, Any]],
    comparison: dict[str, Any],
    case_comparisons: Sequence[dict[str, Any]],
    reproducibility: dict[str, Any],
    report: str,
) -> dict[str, Any]:
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Phase 7 artifacts: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.{uuid.uuid4().hex}.staging"
    staging.mkdir()
    try:
        (staging / "bm25_index_metadata.json").write_text(
            _json_text(index_metadata), encoding="utf-8"
        )
        _write_jsonl(staging / "bm25_results.jsonl", bm25_results)
        _write_jsonl(staging / "hybrid_results.jsonl", hybrid_results)
        (staging / "comparison.json").write_text(
            _json_text(comparison), encoding="utf-8"
        )
        _write_jsonl(staging / "case_comparison.jsonl", case_comparisons)
        (staging / "reproducibility.json").write_text(
            _json_text(reproducibility), encoding="utf-8"
        )
        (staging / "REPORT.md").write_text(report, encoding="utf-8")
        artifact_names = (
            "bm25_index_metadata.json",
            "bm25_results.jsonl",
            "hybrid_results.jsonl",
            "comparison.json",
            "case_comparison.jsonl",
            "reproducibility.json",
            "REPORT.md",
        )
        manifest = {
            "phase7_version": PHASE7_VERSION,
            "status": "frozen",
            "configuration_status": "untuned_first_configuration",
            "created_at_utc": reproducibility["created_at_utc"],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(staging / name),
                    "bytes": (staging / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite hybrid_baseline_v1; create a new version for any later run."
            ),
        }
        (staging / "manifest.json").write_text(
            _json_text(manifest), encoding="utf-8"
        )
        staging.replace(target)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def execute_phase7_once(
    *, project_root: str | Path, output_dir: str | Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"Immutable Phase 7 output already exists: {target}")
    root = Path(project_root).resolve()
    inputs = load_frozen_retrieval_inputs(root)
    dense = load_frozen_dense_reference(root, inputs)
    question_records = [
        {"case_id": case["id"], "question": case["question"]}
        for case in inputs.cases
    ]

    # Ranking boundary: only IDs/questions and frozen corpus text enter BM25/RRF.
    index, bm25_results = run_bm25_retrieval(inputs.chunks, question_records)
    _validate_ranked_results(bm25_results, inputs.cases, label="BM25")
    hybrid_results = run_rrf_fusion(dense["results"], bm25_results)
    _validate_ranked_results(hybrid_results, inputs.cases, label="Hybrid")
    inputs.assert_unchanged()

    # Gold labels first enter Phase 7 here, after all rankings are fixed.
    comparison, case_comparisons = build_comparison(
        cases=inputs.cases,
        dense_results=dense["results"],
        bm25_results=bm25_results,
        hybrid_results=hybrid_results,
    )
    created_at = datetime.now(timezone.utc).isoformat()
    reproducibility = {
        "phase7_version": PHASE7_VERSION,
        "status": "untuned_first_configuration",
        "created_at_utc": created_at,
        "inputs": {
            "corpus_version": inputs.corpus_version,
            "corpus_chunks_sha256": inputs.corpus_chunks_sha256,
            "corpus_manifest_sha256": inputs.corpus_manifest_sha256,
            "corpus_chunk_count": len(inputs.chunks),
            "eval_dataset_version": inputs.eval_dataset_version,
            "eval_dataset_sha256": inputs.eval_dataset_sha256,
            "eval_manifest_sha256": inputs.eval_manifest_sha256,
            "literature_bearing_case_count": len(inputs.cases),
        },
        "dense_reference": {
            "version": "dense_baseline_v1",
            "manifest_sha256": dense["manifest_sha256"],
            "retrieval_results_sha256": dense["artifact_hashes"]["retrieval_results.jsonl"],
            "failure_analysis_sha256": dense["failure_analysis_sha256"],
            "rerun": False,
        },
        "bm25": {
            **index.metadata,
            "top_k": TOP_K,
        },
        "rrf": {
            "implementation": "project_native_equal_weight_rrf_v1",
            "formula": f"score(d) = sum(1 / ({DEFAULT_RRF_K} + rank_i(d)))",
            "rrf_k": DEFAULT_RRF_K,
            "weights": {"dense": 1.0, "bm25": 1.0},
            "source_depth": {"dense": TOP_K, "bm25": TOP_K},
            "candidate_pool": "union of Dense Top-10 and BM25 Top-10",
            "top_k": TOP_K,
            "tie_breaker": "score desc, best source rank, sum source ranks, chunk_id",
        },
        "evaluation": {
            "contract": "frozen eval_dataset_v1 evidence-group any/all semantics",
            "primary_metrics": list(PRIMARY_METRICS),
            "diagnostic_ks": [1, 3],
            "gold_application_stage": "after BM25 and Hybrid rankings were fixed",
        },
        "environment": {
            "python_version": platform.python_version(),
            "numpy_version": np.__version__,
            "platform": platform.platform(),
        },
        "controls": {
            "frozen_queries_changed": False,
            "dense_retrieval_rerun": False,
            "dense_configuration_changed": False,
            "gold_used_for_ranking_or_parameter_selection": False,
            "post_result_tuning_performed": False,
            "reranker_used": False,
            "llm_generation_or_agent_used": False,
            "phase8_tools_started": False,
        },
    }
    report = build_report(
        comparison=comparison,
        case_comparisons=case_comparisons,
        dense_results=dense["results"],
        bm25_results=bm25_results,
        hybrid_results=hybrid_results,
        reproducibility=reproducibility,
    )
    manifest = write_immutable_phase7_artifacts(
        output_dir=target,
        index_metadata=index.metadata,
        bm25_results=bm25_results,
        hybrid_results=hybrid_results,
        comparison=comparison,
        case_comparisons=case_comparisons,
        reproducibility=reproducibility,
        report=report,
    )
    inputs.assert_unchanged()
    return manifest, comparison
