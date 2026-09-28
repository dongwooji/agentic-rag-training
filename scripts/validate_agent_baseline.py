"""Read-only validation of frozen agent_baseline_v1 artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agent.baseline import (
    CORPUS_CHUNKS_SHA256,
    CORPUS_MANIFEST_SHA256,
    HYBRID_MANIFEST_SHA256,
    ROUTER_MANIFEST_SHA256,
    build_comparison,
)
from src.agent.planner import EXPECTED_CONFIG_SHA256, EXPECTED_PROMPT_SHA256
from src.evaluation.routing_metrics import evaluate_routing
from src.routing.baseline import load_frozen_routing_evaluation, sha256_file


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> None:
    baseline = PROJECT_ROOT / "reports/baselines/agent_baseline_v1"
    if not baseline.exists():
        raise SystemExit("agent_baseline_v1 has not been created")
    manifest = read_json(baseline / "manifest.json")
    checks: dict[str, bool] = {
        "manifest_frozen": manifest.get("status") == "frozen",
        "first_configuration": manifest.get("configuration_status")
        == "preregistered_first_evaluation",
    }
    for artifact in manifest["artifacts"]:
        checks[f"artifact_hash:{artifact['path']}"] = (
            sha256_file(baseline / artifact["path"]) == artifact["sha256"]
        )

    inputs = load_frozen_routing_evaluation(PROJECT_ROOT)
    plans = read_jsonl(baseline / "plans.jsonl")
    stored_metrics = read_json(baseline / "metrics.json")
    predictions = [item["prediction"] for item in plans]
    recomputed_metrics = evaluate_routing(inputs["cases"], predictions)
    checks["case_count"] = len(plans) == 30
    checks["metrics_reproducible"] = recomputed_metrics == stored_metrics
    checks["question_only_input"] = all(
        item.get("planner_input_fields") == ["question"] for item in plans
    )
    checks["plan_only_results"] = all(
        result.get("status") == "not_executed_plan_only"
        for item in plans
        for result in item.get("tool_results", [])
    )

    router_metrics = read_json(
        PROJECT_ROOT / "reports/baselines/router_baseline_v1/metrics.json"
    )
    recomputed_comparison = build_comparison(
        agent_metrics=recomputed_metrics,
        router_reference={"metrics": router_metrics},
    )
    checks["comparison_reproducible"] = recomputed_comparison == read_json(
        baseline / "comparison.json"
    )
    checks["config_snapshot"] = (
        sha256_file(baseline / "config_snapshot.json") == EXPECTED_CONFIG_SHA256
    )
    checks["prompt_snapshot"] = (
        sha256_file(baseline / "prompt.md") == EXPECTED_PROMPT_SHA256
    )

    reproduction = read_json(baseline / "reproducibility.json")
    checks.update(
        {
            "eval_hash": reproduction.get("eval_dataset_sha256")
            == inputs["eval_sha256"],
            "router_manifest_hash": reproduction.get("router_manifest_sha256")
            == ROUTER_MANIFEST_SHA256,
            "hybrid_manifest_hash": reproduction.get("hybrid_manifest_sha256")
            == HYBRID_MANIFEST_SHA256,
            "corpus_chunks_hash": reproduction.get("corpus_chunks_sha256")
            == CORPUS_CHUNKS_SHA256,
            "corpus_manifest_hash": reproduction.get("corpus_manifest_sha256")
            == CORPUS_MANIFEST_SHA256,
        }
    )
    controls = reproduction.get("controls", {})
    checks["no_gold_to_planner"] = (
        controls.get("case_id_exposed_to_planner") is False
        and controls.get("category_exposed_to_planner") is False
        and controls.get("required_tools_exposed_to_planner") is False
        and controls.get("gold_exposed_to_planner") is False
    )
    checks["no_tool_execution_or_answer"] = (
        controls.get("tool_execution_performed") is False
        and controls.get("answer_generation_performed") is False
    )
    checks["no_tuning_retry_or_phase11"] = (
        controls.get("post_result_tuning_performed") is False
        and controls.get("grader_retry_or_rewrite_used") is False
        and controls.get("abstention_workflow_used") is False
        and controls.get("phase11_started") is False
    )
    failed = sorted(name for name, passed in checks.items() if not passed)
    print(json.dumps({"all_checks_passed": not failed, "checks": checks}, indent=2))
    if failed:
        raise SystemExit("Agent baseline validation failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
