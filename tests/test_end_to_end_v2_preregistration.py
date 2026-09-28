from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from src.evaluation.end_to_end_runner import (
    PROJECT_ROOT,
    load_frozen_cases,
    load_protocol_config,
)
from src.evaluation.end_to_end_v2 import (
    V2_BASELINE_VERSION,
    V2_CONFIG_PATH,
    V2_OUTPUT_DIR,
    V2_PREREGISTRATION_DIR,
    V2_PROTOCOL,
    run_end_to_end_baseline_v2,
)
from src.evaluation.end_to_end_v2_metrics import build_v1_v2_comparison


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_frozen_baseline(
    *,
    output_dir,
    baseline_version: str,
    manifest_sha256: str,
) -> dict:
    manifest_path = output_dir / "manifest.json"
    assert output_dir.is_dir()
    assert manifest_path.is_file()
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == manifest_sha256

    manifest = _read_json(manifest_path)
    assert manifest["baseline_version"] == baseline_version
    assert manifest["status"] == "FROZEN_COMPLETE"
    assert manifest["case_count"] == 30
    assert manifest["post_result_tuning"] is False
    for relative, expected_sha256 in manifest["artifact_hashes"].items():
        artifact = output_dir / relative
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected_sha256
    return manifest


def test_v2_reuses_the_exact_frozen_v1_evaluation_input_and_gold() -> None:
    v1 = load_protocol_config()
    v2 = load_protocol_config(
        V2_CONFIG_PATH,
        expected_baseline_version=V2_BASELINE_VERSION,
    )
    assert v2["evaluation_dataset"] == v1["evaluation_dataset"]
    assert v2["evaluation_manifest"] == v1["evaluation_manifest"]
    dataset = PROJECT_ROOT / v2["evaluation_dataset"]
    manifest = PROJECT_ROOT / v2["evaluation_manifest"]
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == (
        "1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509"
    )
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == (
        "4abd207041c4dd69df73e85e6d50b9288fe9f0d9c19f1ce33d06ae62e008d972"
    )
    assert len(load_frozen_cases(config=v2)) == 30


def test_v2_changes_only_the_three_preregistered_integration_boundaries() -> None:
    v2 = load_protocol_config(
        V2_CONFIG_PATH,
        expected_baseline_version=V2_BASELINE_VERSION,
    )
    execution = v2["execution"]
    assert execution["list_sessions_metric_adapter"].startswith(
        "successful result.sessions"
    )
    assert "Router matched-pattern" in execution["literature_subquestion_policy"]
    assert execution["final_answer_training_log_payload"] == {
        "policy": "bounded_training_log_payload_v1",
        "max_items_per_sequence": 20,
        "selection": "first_10_and_last_10",
        "graph_state_retains_full_tool_result": True,
        "metric_result_is_not_compacted": True,
    }


def test_v2_preserves_v1_retrieval_retry_and_metric_contracts() -> None:
    v1 = load_protocol_config()
    v2 = load_protocol_config(
        V2_CONFIG_PATH,
        expected_baseline_version=V2_BASELINE_VERSION,
    )
    for key in (
        "literature_top_k",
        "max_retry",
        "fusion_policy",
        "fusion_rrf_k",
        "initial_query_policy",
        "provider_automatic_retries",
        "api_min_interval_seconds",
        "recursion_limit",
        "result_driven_tuning",
    ):
        assert v2["execution"][key] == v1["execution"][key]
    assert v2["metrics"]["same_as_v1"] is True
    assert v2["comparison"]["remediation_targets"] == {
        "LOG-002": "list_sessions_to_training_gap_adapter",
        "HYB-002": "list_sessions_to_training_gap_adapter",
        "LOG-004": "bounded_final_answer_training_log_payload",
    }


def test_v1_to_v2_fixture_comparison_tracks_targets_and_hybrid_deltas() -> None:
    v1_dir = PROJECT_ROOT / "reports/baselines/end_to_end_baseline_v1"
    v1_metrics = _read_json(v1_dir / "case_metrics.json")
    v1_results = _read_json(v1_dir / "case_results.json")
    v2_metrics = deepcopy(v1_metrics)
    v2_results = deepcopy(v1_results)

    metrics_by_id = {item["case_id"]: item for item in v2_metrics}
    results_by_id = {item["case_id"]: item for item in v2_results}
    for case_id in ("LOG-002", "HYB-002"):
        result = results_by_id[case_id]
        metric_result = next(
            item
            for item in result["state"]["tool_results"]
            if item["tool"] == "compute_metrics"
        )
        metric_result["status"] = "success"
        result["state"]["errors"] = [
            item
            for item in result["state"].get("errors", [])
            if item.get("code") != "tool_execution_failure"
        ]
    log004 = results_by_id["LOG-004"]
    log004["remediation_diagnostics"] = {
        "final_answer_payload": {
            "training_log_provider_list_counts": {"records": 20},
            "training_log_payload_policy": {
                "policy": "bounded_training_log_payload_v1"
            },
            "metric_result_preserved": True,
            "serialized_input_characters": 1000,
        }
    }
    metrics_by_id["LOG-004"]["final_status"] = "answer_ready"
    hybrid = metrics_by_id["HYB-001"]
    hybrid["final_status"] = "answer_ready"

    comparison = build_v1_v2_comparison(
        v1_case_metrics=v1_metrics,
        v2_case_metrics=v2_metrics,
        v1_case_results=v1_results,
        v2_case_results=v2_results,
    )
    assert comparison["summary"]["remediation_targets_resolved"] == 3
    assert comparison["summary"]["hybrid_answer_ready_delta"] == 1
    assert comparison["remediation_targets"]["LOG-002"]["resolved"] is True
    assert comparison["remediation_targets"]["HYB-002"]["resolved"] is True
    assert comparison["remediation_targets"]["LOG-004"]["resolved"] is True
    assert len(comparison["hybrid"]["v2"]["literature_side_missing_components_by_case"]) == 8


def test_v2_entrypoint_delegates_to_common_runner_with_v2_protocol(monkeypatch) -> None:
    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"output_dir": "unused", "manifest_sha256": "x", "metrics": {}}

    monkeypatch.setattr("src.evaluation.end_to_end_v2.run_end_to_end_baseline", fake_runner)
    result = run_end_to_end_baseline_v2(
        database_config=object(),
        postgres_password="db-secret",
        openai_api_key="api-secret",
        model_cache=PROJECT_ROOT / "data/models/huggingface",
    )
    assert result["manifest_sha256"] == "x"
    assert captured["protocol"] == V2_PROTOCOL


def test_frozen_v1_and_v2_baselines_are_preserved() -> None:
    v1_output_dir = PROJECT_ROOT / "reports/baselines/end_to_end_baseline_v1"
    v1 = _assert_frozen_baseline(
        output_dir=v1_output_dir,
        baseline_version="end_to_end_baseline_v1",
        manifest_sha256=(
            "fd6d45414010033d61971fbddbea26fcd19c485aa51b2cb64c2ef0caff3478e6"
        ),
    )
    v2 = _assert_frozen_baseline(
        output_dir=V2_OUTPUT_DIR,
        baseline_version=V2_BASELINE_VERSION,
        manifest_sha256=(
            "c1e5c4c2defee539edb9980c62b79da003b33b4db2e1186314fb884706904225"
        ),
    )
    assert v1["preregistration_sha256"] == (
        "ac643e85527c83fc1653ccb69397c110fb5fd0727715407d4fa0101604ef8e11"
    )
    assert v2["preregistration_sha256"] == (
        "3d844f89bc18a6fb5cfcbbecd87e674073210eda35b3d59a2b371b0372198438"
    )


def test_v2_preregistration_and_completed_output_manifests_are_locked() -> None:
    preregistration_manifest = V2_PREREGISTRATION_DIR / "manifest.json"
    preregistration_lock = V2_PREREGISTRATION_DIR / "manifest.sha256"
    expected_preregistration_sha256 = (
        "3d844f89bc18a6fb5cfcbbecd87e674073210eda35b3d59a2b371b0372198438"
    )
    assert preregistration_lock.read_text(encoding="utf-8").strip() == (
        expected_preregistration_sha256
    )
    assert hashlib.sha256(preregistration_manifest.read_bytes()).hexdigest() == (
        expected_preregistration_sha256
    )
    preregistration = _read_json(preregistration_manifest)
    assert preregistration["baseline_version"] == V2_BASELINE_VERSION
    assert preregistration["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert preregistration["case_count"] == 30

    assert hashlib.sha256(
        (V2_OUTPUT_DIR / "manifest.json").read_bytes()
    ).hexdigest() == (
        "c1e5c4c2defee539edb9980c62b79da003b33b4db2e1186314fb884706904225"
    )
