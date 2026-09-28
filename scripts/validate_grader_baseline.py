"""Read-only validation of frozen grader_baseline_v1 artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.baseline import (
    AGENT_MANIFEST_SHA256,
    CORPUS_CHUNKS_SHA256,
    CORPUS_MANIFEST_SHA256,
    DENSE_MANIFEST_SHA256,
    EVAL_DATASET_SHA256,
    EVAL_MANIFEST_SHA256,
    GRADER_INPUT_FIELDS,
    HYBRID_MANIFEST_SHA256,
    HYBRID_RESULTS_SHA256,
    ROUTER_MANIFEST_SHA256,
    build_case_results,
    load_frozen_grader_inputs,
)
from src.grading.contracts import EvidenceGrade
from src.grading.evaluation import evaluate_grader
from src.grading.provider import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    sha256_file,
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for item in value.values():
            keys.update(all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(all_keys(item))
    return keys


def main() -> None:
    baseline = PROJECT_ROOT / "reports/baselines/grader_baseline_v1"
    if not baseline.exists():
        raise SystemExit("grader_baseline_v1 has not been created")
    manifest = read_json(baseline / "manifest.json")
    checks: dict[str, bool] = {
        "manifest_frozen": manifest.get("status") == "frozen",
        "first_configuration": manifest.get("configuration_status")
        == "preregistered_first_evaluation",
        "artifact_count": len(manifest.get("artifacts", [])) == 9,
    }
    for artifact in manifest["artifacts"]:
        checks[f"artifact_hash:{artifact['path']}"] = (
            sha256_file(baseline / artifact["path"]) == artifact["sha256"]
        )

    frozen = load_frozen_grader_inputs(PROJECT_ROOT)
    stored_inputs = read_jsonl(baseline / "grader_inputs.jsonl")
    outputs = read_jsonl(baseline / "grader_outputs.jsonl")
    stored_cases = read_jsonl(baseline / "case_results.jsonl")
    stored_metrics = read_json(baseline / "metrics.json")
    reproduction = read_json(baseline / "reproducibility.json")
    checks["case_count"] = len(stored_inputs) == len(outputs) == len(stored_cases) == 18
    checks["inputs_reproducible"] = stored_inputs == frozen["grader_inputs"]
    checks["input_bundle_hash"] = (
        reproduction.get("input_bundle_sha256")
        == frozen["input_bundle_sha256"]
    )
    banned_keys = {
        "case_id",
        "category",
        "gold",
        "required_tools",
        "answer_criteria",
        "limitations",
        "literature_evidence_groups",
        "chunk_ids",
        "complete_evidence@10",
    }
    checks["no_gold_in_model_input"] = all(
        not (all_keys(item["grader_input"]) & banned_keys)
        and item.get("grader_input_fields") == GRADER_INPUT_FIELDS
        and len(item["grader_input"].get("retrieved_evidence", [])) == 10
        for item in stored_inputs
    )
    checks["typed_outputs"] = all(
        EvidenceGrade.model_validate(item["validated_grade"])
        and item.get("grader_input_fields") == GRADER_INPUT_FIELDS
        for item in outputs
    )
    recomputed_cases = build_case_results(
        cases=frozen["cases"],
        grader_inputs=stored_inputs,
        grader_outputs=outputs,
    )
    checks["case_results_reproducible"] = recomputed_cases == stored_cases
    checks["metrics_reproducible"] = (
        evaluate_grader(recomputed_cases) == stored_metrics
    )
    checks["config_snapshot"] = (
        sha256_file(baseline / "config_snapshot.json") == EXPECTED_CONFIG_SHA256
    )
    checks["prompt_snapshot"] = (
        sha256_file(baseline / "prompt.md") == EXPECTED_PROMPT_SHA256
    )
    checks.update(
        {
            "eval_hash": reproduction.get("eval_dataset_sha256")
            == EVAL_DATASET_SHA256,
            "eval_manifest_hash": reproduction.get("eval_manifest_sha256")
            == EVAL_MANIFEST_SHA256,
            "hybrid_manifest_hash": reproduction.get("hybrid_manifest_sha256")
            == HYBRID_MANIFEST_SHA256,
            "hybrid_results_hash": reproduction.get("hybrid_results_sha256")
            == HYBRID_RESULTS_SHA256,
            "corpus_chunks_hash": reproduction.get("corpus_chunks_sha256")
            == CORPUS_CHUNKS_SHA256,
            "corpus_manifest_hash": reproduction.get("corpus_manifest_sha256")
            == CORPUS_MANIFEST_SHA256,
            "dense_manifest_reference": reproduction.get("dense_manifest_sha256")
            == DENSE_MANIFEST_SHA256,
            "router_manifest_reference": reproduction.get("router_manifest_sha256")
            == ROUTER_MANIFEST_SHA256,
            "agent_manifest_reference": reproduction.get("agent_manifest_sha256")
            == AGENT_MANIFEST_SHA256,
        }
    )
    controls = reproduction.get("controls", {})
    checks["gold_applied_offline"] = (
        controls.get("gold_exposed_to_grader") is False
        and controls.get("case_id_exposed_to_grader") is False
        and controls.get("category_exposed_to_grader") is False
        and controls.get("answer_criteria_exposed_to_grader") is False
        and controls.get("limitations_labels_exposed_to_grader") is False
        and controls.get("gold_applied_after_all_predictions") is True
    )
    checks["no_retrieval_mutation_or_recovery"] = (
        controls.get("retrieval_rerun") is False
        and controls.get("query_rewrite") is False
        and controls.get("retry_loop") is False
        and controls.get("re_retrieval") is False
        and controls.get("replan") is False
        and controls.get("recovery_agent") is False
    )
    checks["no_answer_abstention_or_phase11b"] = (
        controls.get("answer_generation") is False
        and controls.get("abstention_workflow") is False
        and controls.get("post_result_tuning") is False
        and controls.get("phase11b_started") is False
    )
    failed = sorted(name for name, passed in checks.items() if not passed)
    print(json.dumps({"all_checks_passed": not failed, "checks": checks}, indent=2))
    if failed:
        raise SystemExit("Grader baseline validation failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()

