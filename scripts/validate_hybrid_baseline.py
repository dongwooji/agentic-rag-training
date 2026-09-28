"""Read-only validation of the frozen Phase 7 comparison artifacts."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.retrieval_metrics import evaluate_retrieval
from src.retrieval.baseline import ALL_KS, load_frozen_retrieval_inputs, sha256_file
from src.retrieval.hybrid_baseline import _ranked_ids, _read_jsonl, _validate_ranked_results
from src.retrieval.rrf import DEFAULT_RRF_K, reciprocal_rank_fusion


PHASE7_DIR = PROJECT_ROOT / "reports/baselines/hybrid_baseline_v1"
DENSE_DIR = PROJECT_ROOT / "reports/baselines/dense_baseline_v1"


def main() -> None:
    manifest = json.loads((PHASE7_DIR / "manifest.json").read_text(encoding="utf-8"))
    checks: dict[str, bool] = {
        "manifest_frozen": manifest.get("status") == "frozen",
        "first_configuration": manifest.get("configuration_status")
        == "untuned_first_configuration",
    }
    for artifact in manifest["artifacts"]:
        checks[f"hash_{artifact['path']}"] = (
            sha256_file(PHASE7_DIR / artifact["path"]) == artifact["sha256"]
        )

    inputs = load_frozen_retrieval_inputs(PROJECT_ROOT)
    dense = _read_jsonl(DENSE_DIR / "retrieval_results.jsonl")
    bm25 = _read_jsonl(PHASE7_DIR / "bm25_results.jsonl")
    hybrid = _read_jsonl(PHASE7_DIR / "hybrid_results.jsonl")
    case_comparison = _read_jsonl(PHASE7_DIR / "case_comparison.jsonl")
    _validate_ranked_results(dense, inputs.cases, label="Dense validation")
    _validate_ranked_results(bm25, inputs.cases, label="BM25 validation")
    _validate_ranked_results(hybrid, inputs.cases, label="Hybrid validation")
    checks["case_comparison_count"] = len(case_comparison) == 18

    dense_by_case = {result["case_id"]: result for result in dense}
    bm25_by_case = {result["case_id"]: result for result in bm25}
    hybrid_by_case = {result["case_id"]: result for result in hybrid}
    rrf_matches = True
    for case_id in dense_by_case:
        expected = reciprocal_rank_fusion(
            _ranked_ids(dense_by_case[case_id]),
            _ranked_ids(bm25_by_case[case_id]),
            rrf_k=DEFAULT_RRF_K,
            top_k=10,
        )
        stored = hybrid_by_case[case_id]["retrieved"]
        if [hit.chunk_id for hit in expected.hits] != [item["chunk_id"] for item in stored]:
            rrf_matches = False
            break
        for expected_hit, stored_hit in zip(expected.hits, stored, strict=True):
            if not math.isclose(expected_hit.score, stored_hit["score"], abs_tol=1e-15):
                rrf_matches = False
                break
    checks["stored_rrf_exactly_reproducible"] = rrf_matches

    comparison = json.loads((PHASE7_DIR / "comparison.json").read_text(encoding="utf-8"))
    results = {"dense": dense, "bm25": bm25, "hybrid": hybrid}
    metrics_match = True
    for method, method_results in results.items():
        actual = evaluate_retrieval(
            inputs.cases,
            {result["case_id"]: _ranked_ids(result) for result in method_results},
            ks=ALL_KS,
        )
        for metric, value in actual["macro"].items():
            if not math.isclose(
                float(value),
                float(comparison["metrics"][method]["macro"][metric]),
                abs_tol=1e-15,
            ):
                metrics_match = False
                break
    checks["stored_metrics_exactly_reproducible"] = metrics_match

    reproducibility = json.loads(
        (PHASE7_DIR / "reproducibility.json").read_text(encoding="utf-8")
    )
    checks["corpus_hash_matches"] = (
        inputs.corpus_chunks_sha256
        == reproducibility["inputs"]["corpus_chunks_sha256"]
    )
    checks["eval_hash_matches"] = (
        inputs.eval_dataset_sha256 == reproducibility["inputs"]["eval_dataset_sha256"]
    )
    checks["dense_not_rerun_recorded"] = (
        reproducibility["controls"]["dense_retrieval_rerun"] is False
    )
    checks["no_post_result_tuning_recorded"] = (
        reproducibility["controls"]["post_result_tuning_performed"] is False
    )
    checks["phase8_not_started_recorded"] = (
        reproducibility["controls"]["phase8_tools_started"] is False
    )
    failed = sorted(name for name, passed in checks.items() if not passed)
    result = {
        "phase7_version": manifest.get("phase7_version"),
        "all_checks_passed": not failed,
        "checks": checks,
        "failed": failed,
        "artifact_count": len(manifest["artifacts"]),
        "case_count": len(case_comparison),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

