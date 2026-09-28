"""Read-only validation of frozen Grader v2 held-out and baseline artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v2_baseline import (
    build_component_diagnostics,
    build_v2_case_results,
    build_v2_metrics,
    load_frozen_v2_model_inputs,
)
from src.grading.v2_contracts import EvidenceGradeV2
from src.grading.v2_freeze import validate_frozen_heldout
from src.grading.v2_review import read_json, read_jsonl, sha256_file


def main() -> None:
    baseline = PROJECT_ROOT / "reports/baselines/grader_v2_baseline"
    if not baseline.exists():
        raise SystemExit("grader_v2_baseline has not been created")
    frozen = validate_frozen_heldout(PROJECT_ROOT)
    loaded = load_frozen_v2_model_inputs(PROJECT_ROOT)
    manifest = read_json(baseline / "manifest.json")
    checks: dict[str, bool] = {
        "heldout_manifest_frozen": frozen["manifest"]["status"] == "frozen",
        "baseline_manifest_frozen": manifest.get("status") == "frozen",
        "first_configuration": manifest.get("configuration_status")
        == "preregistered_first_evaluation",
    }
    for artifact in manifest["artifacts"]:
        checks[f"artifact_hash:{artifact['path']}"] = (
            sha256_file(baseline / artifact["path"]) == artifact["sha256"]
        )
    stored_inputs = read_jsonl(baseline / "grader_inputs.jsonl")
    outputs = read_jsonl(baseline / "grader_outputs.jsonl")
    stored_cases = read_jsonl(baseline / "case_results.jsonl")
    stored_metrics = read_json(baseline / "metrics.json")
    stored_components = read_json(baseline / "component_diagnostics.json")
    checks["case_count"] = len(stored_inputs) == len(outputs) == len(stored_cases) == 14
    checks["model_inputs_reproducible"] = stored_inputs == loaded["grader_inputs"]
    checks["typed_outputs"] = all(
        EvidenceGradeV2.model_validate(item["validated_grade"]) for item in outputs
    )
    recomputed_cases = build_v2_case_results(
        frozen_dir=frozen["directory"],
        grader_inputs=stored_inputs,
        grader_outputs=outputs,
    )
    checks["case_results_reproducible"] = recomputed_cases == stored_cases
    checks["metrics_reproducible"] = build_v2_metrics(recomputed_cases) == stored_metrics
    checks["component_diagnostics_reproducible"] = (
        build_component_diagnostics(recomputed_cases) == stored_components
    )
    reproduction = read_json(baseline / "reproducibility.json")
    checks["heldout_manifest_reference"] = (
        reproduction["heldout_manifest_sha256"] == frozen["manifest_sha256"]
    )
    controls = reproduction["controls"]
    checks["gold_not_exposed"] = (
        controls["gold_exposed_to_grader"] is False
        and controls["case_id_exposed_to_grader"] is False
        and controls["category_exposed_to_grader"] is False
        and controls["diagnostic_tags_exposed_to_grader"] is False
        and controls["gold_loaded_after_all_predictions"] is True
    )
    checks["no_tuning_or_recovery"] = (
        controls["post_result_tuning"] is False
        and controls["query_rewrite"] is False
        and controls["retry_loop"] is False
        and controls["re_retrieval"] is False
        and controls["replan"] is False
        and controls["recovery_agent"] is False
        and controls["abstention_workflow"] is False
    )
    failed = sorted(name for name, passed in checks.items() if not passed)
    print(json.dumps({"all_checks_passed": not failed, "checks": checks}, indent=2))
    if failed:
        raise SystemExit("Grader v2 baseline validation failed: " + ", ".join(failed))


if __name__ == "__main__":
    main()

