"""Frozen v1-to-v2 comparison for the post-remediation E2E baseline."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


COMPARISON_VERSION = "end_to_end_v1_v2_comparison_v1"
REMEDIATION_TARGETS = {
    "LOG-002": "list_sessions_to_training_gap_adapter",
    "HYB-002": "list_sessions_to_training_gap_adapter",
    "LOG-004": "bounded_final_answer_training_log_payload",
}


def _by_case(values: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result = {str(item["case_id"]): item for item in values}
    if len(result) != len(values):
        raise ValueError("Comparison input contains duplicate case IDs")
    return result


def _tool_status(result: Mapping[str, Any], tool: str) -> str | None:
    state = result.get("state")
    records = state.get("tool_results", []) if isinstance(state, Mapping) else []
    for record in records:
        if isinstance(record, Mapping) and record.get("tool") == tool:
            return str(record.get("status") or "") or None
    return None


def _error_codes(result: Mapping[str, Any]) -> list[str]:
    state = result.get("state")
    errors = state.get("errors", []) if isinstance(state, Mapping) else []
    return [
        str(item.get("code"))
        for item in errors
        if isinstance(item, Mapping) and item.get("code")
    ]


def _hybrid_summary(
    metrics: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    case_ids = sorted(
        case_id
        for case_id, item in metrics.items()
        if item.get("category") == "hybrid"
    )
    missing: dict[str, list[dict[str, str]]] = {}
    for case_id in case_ids:
        state = results[case_id].get("state")
        components = (
            state.get("missing_components", []) if isinstance(state, Mapping) else []
        )
        missing[case_id] = [
            {
                "component_id": str(item.get("component_id") or ""),
                "requirement": str(item.get("requirement") or ""),
            }
            for item in components
            if isinstance(item, Mapping)
        ]
    return {
        "case_count": len(case_ids),
        "answer_ready_count": sum(
            metrics[case_id].get("final_status") == "answer_ready"
            for case_id in case_ids
        ),
        "unnecessary_recovery_count": sum(
            bool((metrics[case_id].get("recovery") or {}).get("unnecessary"))
            for case_id in case_ids
        ),
        "unnecessary_abstention_count": sum(
            bool(
                (metrics[case_id].get("answer_abstention") or {}).get(
                    "unnecessary_abstention"
                )
            )
            for case_id in case_ids
        ),
        "initial_complete_evidence_count": sum(
            bool(
                (metrics[case_id].get("initial_evidence") or {}).get(
                    "complete_evidence@10"
                )
            )
            for case_id in case_ids
        ),
        "final_complete_evidence_count": sum(
            bool(
                (metrics[case_id].get("final_evidence") or {}).get(
                    "complete_evidence@10"
                )
            )
            for case_id in case_ids
        ),
        "literature_side_missing_components_by_case": missing,
    }


def _target_snapshot(
    case_id: str,
    metrics: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    metric = metrics[case_id]
    result = results[case_id]
    diagnostics = result.get("remediation_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    payload = diagnostics.get("final_answer_payload")
    payload = payload if isinstance(payload, Mapping) else {}
    provider_counts = payload.get("training_log_provider_list_counts")
    provider_counts = provider_counts if isinstance(provider_counts, Mapping) else {}
    payload_policy = payload.get("training_log_payload_policy")
    bounded = bool(
        isinstance(payload_policy, Mapping)
        and payload_policy.get("policy") == "bounded_training_log_payload_v1"
        and all(int(value) <= 20 for value in provider_counts.values())
    )
    return {
        "final_status": metric.get("final_status"),
        "compute_metrics_status": _tool_status(result, "compute_metrics"),
        "error_codes": _error_codes(result),
        "training_log_payload_bounded": bounded,
        "metric_result_preserved": bool(payload.get("metric_result_preserved")),
        "provider_input_characters": payload.get("serialized_input_characters"),
    }


def build_v1_v2_comparison(
    *,
    v1_case_metrics: Sequence[Mapping[str, Any]],
    v2_case_metrics: Sequence[Mapping[str, Any]],
    v1_case_results: Sequence[Mapping[str, Any]],
    v2_case_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the preregistered comparison without any model-based judging."""

    v1_metrics = _by_case(v1_case_metrics)
    v2_metrics = _by_case(v2_case_metrics)
    v1_results = _by_case(v1_case_results)
    v2_results = _by_case(v2_case_results)
    expected_ids = set(v1_metrics)
    if not (
        expected_ids
        == set(v2_metrics)
        == set(v1_results)
        == set(v2_results)
    ):
        raise ValueError("v1 and v2 comparison case IDs differ")

    targets: dict[str, Any] = {}
    for case_id, target in REMEDIATION_TARGETS.items():
        before = _target_snapshot(case_id, v1_metrics, v1_results)
        after = _target_snapshot(case_id, v2_metrics, v2_results)
        if target == "list_sessions_to_training_gap_adapter":
            resolved = bool(
                after["compute_metrics_status"] == "success"
                and "tool_execution_failure" not in after["error_codes"]
            )
        else:
            resolved = bool(
                after["training_log_payload_bounded"]
                and after["metric_result_preserved"]
                and after["final_status"] != "execution_failure"
            )
        targets[case_id] = {
            "target": target,
            "v1": before,
            "v2": after,
            "resolved": resolved,
        }

    hybrid_v1 = _hybrid_summary(v1_metrics, v1_results)
    hybrid_v2 = _hybrid_summary(v2_metrics, v2_results)
    summary = {
        "remediation_targets_resolved": sum(
            bool(item["resolved"]) for item in targets.values()
        ),
        "remediation_target_count": len(targets),
        "hybrid_answer_ready_delta": (
            hybrid_v2["answer_ready_count"] - hybrid_v1["answer_ready_count"]
        ),
        "hybrid_unnecessary_recovery_delta": (
            hybrid_v2["unnecessary_recovery_count"]
            - hybrid_v1["unnecessary_recovery_count"]
        ),
        "hybrid_unnecessary_abstention_delta": (
            hybrid_v2["unnecessary_abstention_count"]
            - hybrid_v1["unnecessary_abstention_count"]
        ),
    }
    return {
        "comparison_version": COMPARISON_VERSION,
        "baseline_pair": ["end_to_end_baseline_v1", "end_to_end_baseline_v2"],
        "definitions": {
            "adapter_resolved": (
                "compute_metrics status is success and no tool_execution_failure "
                "is recorded for the target case"
            ),
            "payload_failure_resolved": (
                "provider-facing Training Log lists are capped at 20, the Metric "
                "result is preserved, and the case no longer ends execution_failure"
            ),
            "hybrid_missing_component": (
                "the terminal Runtime Grader missing component_id and requirement; "
                "no new semantic taxonomy or LLM judge is added"
            ),
        },
        "remediation_targets": targets,
        "hybrid": {"v1": hybrid_v1, "v2": hybrid_v2},
        "summary": summary,
    }
