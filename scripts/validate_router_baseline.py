"""Read-only validation of frozen router_baseline_v1 artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.routing_metrics import evaluate_routing
from src.routing.baseline import (
    EXPECTED_CONFIG_SHA256,
    FROZEN_EVAL_MANIFEST_SHA256,
    FROZEN_EVAL_SHA256,
    load_frozen_routing_evaluation,
    produce_routes,
    sha256_file,
)
from src.routing.deterministic import DeterministicRouter


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def main() -> None:
    baseline = PROJECT_ROOT / "reports/baselines/router_baseline_v1"
    manifest = _read_json(baseline / "manifest.json")
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
    checks.update(
        {
            "eval_hash": inputs["eval_sha256"] == FROZEN_EVAL_SHA256,
            "eval_manifest_hash": inputs["eval_manifest_sha256"]
            == FROZEN_EVAL_MANIFEST_SHA256,
            "router_config_hash": inputs["router_config_sha256"]
            == EXPECTED_CONFIG_SHA256,
        }
    )
    config = _read_json(inputs["config_path"])
    checks["config_snapshot"] = config == _read_json(
        baseline / "rule_config_snapshot.json"
    )
    stored_routes = _read_jsonl(baseline / "routes.jsonl")
    reproduced_routes = produce_routes(inputs["cases"], DeterministicRouter(config))
    checks["case_count"] = len(stored_routes) == len(reproduced_routes) == 30
    checks["routes_reproducible"] = stored_routes == reproduced_routes
    recomputed_metrics = evaluate_routing(inputs["cases"], stored_routes)
    checks["metrics_reproducible"] = recomputed_metrics == _read_json(
        baseline / "metrics.json"
    )
    reproduction = _read_json(baseline / "reproducibility.json")
    controls = reproduction.get("controls", {})
    checks["no_llm_or_tool_execution"] = (
        controls.get("llm_used") is False
        and controls.get("tool_execution_performed") is False
    )
    checks["no_post_result_tuning"] = (
        controls.get("post_result_tuning_performed") is False
    )
    checks["phase10_not_started"] = controls.get("phase10_started") is False
    failed = sorted(name for name, passed in checks.items() if not passed)
    print(json.dumps({"all_checks_passed": not failed, "checks": checks}, indent=2))
    if failed:
        raise SystemExit("Router baseline validation failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()
