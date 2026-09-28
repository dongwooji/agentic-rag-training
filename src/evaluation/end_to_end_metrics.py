"""Preregistered metrics for the frozen End-to-End integration baseline.

The functions in this module are deliberately provider-free.  They compare a
completed runtime state with the frozen evaluation contract after execution;
Gold labels are never added to graph state or provider inputs.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from src.answer.generator import build_final_answer_input
from src.evaluation.retrieval_metrics import (
    complete_evidence_at_k,
    evidence_group_recall_at_k,
    required_gold_chunk_ids,
    required_literature_groups,
)
from src.evaluation.routing_metrics import TOOL_IDS, expected_task_type


EVIDENCE_K = 10
FINAL_STATUSES = ("answer_ready", "abstain_ready", "execution_failure")
FAILURE_TAXONOMY = (
    "routing_failure",
    "tool_execution_failure",
    "retrieval_insufficiency",
    "grader_failure",
    "recovery_failure",
    "grounding_failure",
    "execution_failure",
    "unnecessary_abstention",
    "unsafe_answer",
)
SUCCESS_TOOL_STATUSES = {"success", "empty"}


METRIC_DEFINITIONS: dict[str, str] = {
    "recovery_triggered": (
        "At least one recovery_agent provider request was made for the case."
    ),
    "recovery_executed": (
        "At least one new recovery Literature Tool search completed; equivalently "
        "the graph retry_count is greater than zero."
    ),
    "recovery_success": (
        "Among triggered cases whose required Gold evidence was incomplete in the "
        "initial Top-10, required Gold evidence is complete in the final Top-10."
    ),
    "unnecessary_recovery": (
        "Recovery was triggered although frozen required Gold evidence was already "
        "complete in the initial Top-10."
    ),
    "new_gold_evidence_acquired": (
        "At least one required Gold chunk absent from initial Top-10 is present in "
        "final Top-10. This is diagnostic and does not itself define recovery success."
    ),
    "unsafe_answer": (
        "Final status is answer_ready while a literature-bearing case has incomplete "
        "final required Gold evidence, or while Gold expected_behavior is abstain."
    ),
    "unnecessary_abstention": (
        "Final status is abstain_ready for a Gold-answerable case when required "
        "literature evidence is complete (if applicable), the required Tool set was "
        "selected, and all required Tool executions succeeded."
    ),
    "grounding_valid": (
        "Every reported used Tool-result ID and literature chunk ID is a member of "
        "the deterministic evidence inventory built from the terminal graph state, "
        "and response/provenance ID lists agree."
    ),
}


def _safe_rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
    return output


def _chunk_ids(records: Sequence[Mapping[str, Any]] | None) -> list[str]:
    return _dedupe(
        item.get("chunk_id", "")
        for item in records or []
        if isinstance(item, Mapping)
    )[:EVIDENCE_K]


def evidence_metrics_at_10(
    case: Mapping[str, Any], ranked_chunk_ids: Sequence[str]
) -> dict[str, float] | None:
    """Evaluate literature evidence or return ``None`` for non-literature cases."""

    case_dict = dict(case)
    if not required_literature_groups(case_dict):
        return None
    ranked = list(ranked_chunk_ids)[:EVIDENCE_K]
    return {
        "evidence_group_recall@10": evidence_group_recall_at_k(
            case_dict, ranked, EVIDENCE_K
        ),
        "complete_evidence@10": complete_evidence_at_k(
            case_dict, ranked, EVIDENCE_K
        ),
    }


def validate_answer_grounding(state: Mapping[str, Any]) -> dict[str, Any]:
    """Validate answer provenance against the terminal state's evidence inventory."""

    response = state.get("final_response")
    if not isinstance(response, Mapping):
        return {
            "applicable": False,
            "valid": True,
            "available_tool_result_ids": [],
            "available_literature_chunk_ids": [],
            "used_tool_result_ids": [],
            "used_literature_chunk_ids": [],
            "unknown_tool_result_ids": [],
            "unknown_literature_chunk_ids": [],
            "contract_mismatches": [],
        }

    try:
        inventory = build_final_answer_input(state)
        available_tools = [item.result_id for item in inventory.structured_evidence]
        available_chunks = [item.chunk_id for item in inventory.literature_evidence]
    except Exception as exc:
        return {
            "applicable": True,
            "valid": False,
            "available_tool_result_ids": [],
            "available_literature_chunk_ids": [],
            "used_tool_result_ids": [],
            "used_literature_chunk_ids": [],
            "unknown_tool_result_ids": [],
            "unknown_literature_chunk_ids": [],
            "contract_mismatches": [f"inventory_error:{type(exc).__name__}"],
        }

    response_used_tools = _dedupe(
        item.get("result_id", "")
        for item in response.get("used_tool_results", []) or []
        if isinstance(item, Mapping)
    )
    response_used_chunks = _dedupe(response.get("used_literature_chunk_ids", []) or [])
    provenance = response.get("provenance") or {}
    provenance_used_tools = _dedupe(
        provenance.get("used_tool_result_ids", [])
        if isinstance(provenance, Mapping)
        else []
    )
    provenance_used_chunks = _dedupe(
        provenance.get("used_literature_chunk_ids", [])
        if isinstance(provenance, Mapping)
        else []
    )
    mismatches: list[str] = []
    if response_used_tools != provenance_used_tools:
        mismatches.append("tool_result_ids_disagree_with_provenance")
    if response_used_chunks != provenance_used_chunks:
        mismatches.append("literature_chunk_ids_disagree_with_provenance")
    unknown_tools = [item for item in response_used_tools if item not in available_tools]
    unknown_chunks = [item for item in response_used_chunks if item not in available_chunks]
    return {
        "applicable": True,
        "valid": not mismatches and not unknown_tools and not unknown_chunks,
        "available_tool_result_ids": available_tools,
        "available_literature_chunk_ids": available_chunks,
        "used_tool_result_ids": response_used_tools,
        "used_literature_chunk_ids": response_used_chunks,
        "unknown_tool_result_ids": unknown_tools,
        "unknown_literature_chunk_ids": unknown_chunks,
        "contract_mismatches": mismatches,
    }


def _tool_execution_summary(
    case: Mapping[str, Any], state: Mapping[str, Any]
) -> dict[str, Any]:
    expected = _dedupe(case.get("required_tools", []) or [])
    route = state.get("route") or {}
    selected = _dedupe(
        route.get("selected_tools", state.get("selected_tools", []))
        if isinstance(route, Mapping)
        else state.get("selected_tools", [])
    )
    unknown = sorted((set(expected) | set(selected)).difference(TOOL_IDS))
    if unknown:
        raise ValueError(f"Unknown Tool IDs for {case.get('id')}: {unknown}")
    expected_set = set(expected)
    selected_set = set(selected)
    records = [
        item
        for item in state.get("tool_results", []) or []
        if isinstance(item, Mapping)
    ]
    succeeded = {
        str(item.get("tool"))
        for item in records
        if str(item.get("status")) in SUCCESS_TOOL_STATUSES
    }
    failed = [
        {
            "tool": str(item.get("tool")),
            "status": str(item.get("status")),
            "error": item.get("error"),
        }
        for item in records
        if str(item.get("status")) not in SUCCESS_TOOL_STATUSES
    ]
    return {
        "expected_tools": expected,
        "predicted_tools": selected,
        "correct_tool_set": expected_set == selected_set,
        "missing_tools": sorted(expected_set - selected_set),
        "unnecessary_tools": sorted(selected_set - expected_set),
        "successful_tools": sorted(succeeded),
        "required_tools_executed_successfully": expected_set.issubset(succeeded),
        "failed_tool_records": failed,
    }


def _normalized_api_node(value: Any) -> str:
    aliases = {
        "runtime_evidence_grader": "runtime_grader",
        "evidence_recovery_agent": "recovery_agent",
    }
    cleaned = str(value or "unknown")
    return aliases.get(cleaned, cleaned)


def _api_summary(api_calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_node: dict[str, dict[str, Any]] = {}
    totals = {
        "request_count": 0,
        "latency_ms": 0.0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    for record in api_calls:
        node = _normalized_api_node(record.get("node", record.get("component")))
        bucket = by_node.setdefault(
            node,
            {
                "request_count": 0,
                "latency_ms": 0.0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            },
        )
        usage = record.get("token_usage") or {}
        values = {
            "request_count": 1,
            "latency_ms": float(record.get("latency_ms", 0.0) or 0.0),
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "estimated_cost_usd": float(usage.get("estimated_cost_usd", 0.0) or 0.0),
        }
        for key, value in values.items():
            bucket[key] += value
            totals[key] += value
    return {"total": totals, "by_node": by_node}


def _grader_disagrees_with_gold(
    *,
    evidence_applicable: bool,
    initial_complete: bool | None,
    final_complete: bool | None,
    grader_calls: Sequence[Mapping[str, Any]],
) -> bool:
    if not evidence_applicable or not grader_calls:
        return False
    comparisons: list[tuple[bool, Mapping[str, Any]]] = []
    if initial_complete is not None:
        comparisons.append((initial_complete, grader_calls[0]))
    if len(grader_calls) > 1 and final_complete is not None:
        comparisons.append((final_complete, grader_calls[-1]))
    for expected, record in comparisons:
        if str(record.get("execution_status")) != "completed":
            return True
        if bool(record.get("evidence_sufficient")) != expected:
            return True
    return False


def evaluate_end_to_end_case(
    case: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    api_calls: Sequence[Mapping[str, Any]] = (),
    tool_calls: Sequence[Mapping[str, Any]] = (),
    grader_calls: Sequence[Mapping[str, Any]] = (),
    recovery_calls: Sequence[Mapping[str, Any]] = (),
    case_latency_ms: float = 0.0,
) -> dict[str, Any]:
    """Compute all preregistered case-level integration metrics."""

    initial_ids = _chunk_ids(state.get("literature_evidence", []) or [])
    final_ids = _chunk_ids(
        state.get("fused_evidence", [])
        or state.get("literature_evidence", [])
        or []
    )
    initial_metrics = evidence_metrics_at_10(case, initial_ids)
    final_metrics = evidence_metrics_at_10(case, final_ids)
    evidence_applicable = initial_metrics is not None
    initial_complete = (
        bool(initial_metrics["complete_evidence@10"])
        if initial_metrics is not None
        else None
    )
    final_complete = (
        bool(final_metrics["complete_evidence@10"])
        if final_metrics is not None
        else None
    )
    gold_ids = required_gold_chunk_ids(dict(case)) if evidence_applicable else set()
    newly_acquired = sorted((set(final_ids) - set(initial_ids)) & gold_ids)

    final_response = state.get("final_response") or {}
    status = str(
        final_response.get("final_status", state.get("final_status", "execution_failure"))
        if isinstance(final_response, Mapping)
        else state.get("final_status", "execution_failure")
    )
    if status not in FINAL_STATUSES:
        status = "execution_failure"
    expected_behavior = str(case.get("expected_behavior"))
    tools = _tool_execution_summary(case, state)
    grounding = validate_answer_grounding(state)
    normalized_api = [
        {**dict(item), "node": _normalized_api_node(item.get("node", item.get("component")))}
        for item in api_calls
    ]
    recovery_triggered = bool(recovery_calls) or any(
        item["node"] == "recovery_agent" for item in normalized_api
    )
    recovery_executed = int(state.get("retry_count", 0) or 0) > 0
    recovery_success = bool(
        evidence_applicable
        and recovery_triggered
        and initial_complete is False
        and final_complete is True
    )
    unnecessary_recovery = bool(
        evidence_applicable and recovery_triggered and initial_complete is True
    )

    unsafe_answer = status == "answer_ready" and (
        expected_behavior == "abstain"
        or (evidence_applicable and final_complete is False)
    )
    support_ready = (
        final_complete is True
        if evidence_applicable
        else tools["required_tools_executed_successfully"]
    )
    unnecessary_abstention = bool(
        status == "abstain_ready"
        and expected_behavior == "answer"
        and support_ready
        and tools["correct_tool_set"]
        and not tools["failed_tool_records"]
    )

    errors = [
        item
        for item in state.get("errors", []) or []
        if isinstance(item, Mapping)
    ]
    error_stages = {str(item.get("stage")) for item in errors}
    failures: set[str] = set()
    if not tools["correct_tool_set"]:
        failures.add("routing_failure")
    if tools["failed_tool_records"] or "initial_tools" in error_stages:
        failures.add("tool_execution_failure")
    if evidence_applicable and final_complete is False:
        failures.add("retrieval_insufficiency")
    if _grader_disagrees_with_gold(
        evidence_applicable=evidence_applicable,
        initial_complete=initial_complete,
        final_complete=final_complete,
        grader_calls=grader_calls,
    ) or "grader" in error_stages:
        failures.add("grader_failure")
    if error_stages & {"recovery_agent", "recovery_search", "fusion"}:
        failures.add("recovery_failure")
    if not grounding["valid"]:
        failures.add("grounding_failure")
    if status == "execution_failure":
        failures.add("execution_failure")
    if unnecessary_abstention:
        failures.add("unnecessary_abstention")
    if unsafe_answer:
        failures.add("unsafe_answer")

    return {
        "case_id": str(case["id"]),
        "category": str(case["category"]),
        "question": str(case["question"]),
        "expected_behavior": expected_behavior,
        "expected_task_type": expected_task_type(case),
        "final_status": status,
        "initial_chunk_ids": initial_ids,
        "final_chunk_ids": final_ids,
        "initial_evidence": initial_metrics,
        "final_evidence": final_metrics,
        "recovery": {
            "triggered": recovery_triggered,
            "executed": recovery_executed,
            "retry_count": int(state.get("retry_count", 0) or 0),
            "success": recovery_success,
            "unnecessary": unnecessary_recovery,
            "new_gold_evidence_acquired": bool(newly_acquired),
            "new_gold_chunk_ids": newly_acquired,
        },
        "answer_abstention": {
            "answerable_answered": expected_behavior == "answer" and status == "answer_ready",
            "unanswerable_abstained": expected_behavior == "abstain" and status == "abstain_ready",
            "unsafe_answer": unsafe_answer,
            "unnecessary_abstention": unnecessary_abstention,
        },
        "routing_tools": tools,
        "grounding": grounding,
        "operations": {
            "case_latency_ms": float(case_latency_ms),
            "api": _api_summary(normalized_api),
            "api_calls": normalized_api,
            "tool_call_count": len(tool_calls),
            "tool_calls": [dict(item) for item in tool_calls],
        },
        "failure_types": [item for item in FAILURE_TAXONOMY if item in failures],
        "structured_errors": [dict(item) for item in errors],
    }


def _aggregate_bucket(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(item["final_status"]) for item in rows)
    literature = [item for item in rows if item["initial_evidence"] is not None]
    recovery_triggered = [item for item in literature if item["recovery"]["triggered"]]
    recovery_opportunities = [
        item
        for item in recovery_triggered
        if not bool(item["initial_evidence"]["complete_evidence@10"])
    ]
    answerable = [item for item in rows if item["expected_behavior"] == "answer"]
    unanswerable = [item for item in rows if item["expected_behavior"] == "abstain"]

    api_totals: dict[str, dict[str, Any]] = {}
    total_api_calls: list[dict[str, Any]] = []
    for item in rows:
        total_api_calls.extend(item["operations"]["api_calls"])
    api = _api_summary(total_api_calls)
    api_totals.update(api["by_node"])

    return {
        "case_count": len(rows),
        "final_status": {
            status: {
                "count": status_counts.get(status, 0),
                "rate": _safe_rate(status_counts.get(status, 0), len(rows)),
            }
            for status in FINAL_STATUSES
        },
        "evidence": {
            "applicable_case_count": len(literature),
            "initial_evidence_group_recall@10": (
                mean(item["initial_evidence"]["evidence_group_recall@10"] for item in literature)
                if literature
                else None
            ),
            "final_evidence_group_recall@10": (
                mean(item["final_evidence"]["evidence_group_recall@10"] for item in literature)
                if literature
                else None
            ),
            "initial_complete_evidence@10": (
                mean(item["initial_evidence"]["complete_evidence@10"] for item in literature)
                if literature
                else None
            ),
            "final_complete_evidence@10": (
                mean(item["final_evidence"]["complete_evidence@10"] for item in literature)
                if literature
                else None
            ),
        },
        "recovery": {
            "denominator_literature_cases": len(literature),
            "trigger_count": len(recovery_triggered),
            "trigger_rate": _safe_rate(len(recovery_triggered), len(literature)),
            "success_denominator": len(recovery_opportunities),
            "success_count": sum(item["recovery"]["success"] for item in recovery_opportunities),
            "success_rate": _safe_rate(
                sum(item["recovery"]["success"] for item in recovery_opportunities),
                len(recovery_opportunities),
            ),
            "unnecessary_count": sum(item["recovery"]["unnecessary"] for item in recovery_triggered),
            "unnecessary_rate_among_triggers": _safe_rate(
                sum(item["recovery"]["unnecessary"] for item in recovery_triggered),
                len(recovery_triggered),
            ),
            "average_retry_count": (
                mean(item["recovery"]["retry_count"] for item in literature)
                if literature
                else 0.0
            ),
            "new_gold_evidence_case_count": sum(
                item["recovery"]["new_gold_evidence_acquired"] for item in literature
            ),
        },
        "answer_abstention": {
            "answerable_case_count": len(answerable),
            "answerable_answer_count": sum(
                item["answer_abstention"]["answerable_answered"] for item in answerable
            ),
            "answerable_answer_rate": _safe_rate(
                sum(item["answer_abstention"]["answerable_answered"] for item in answerable),
                len(answerable),
            ),
            "unanswerable_case_count": len(unanswerable),
            "unanswerable_abstention_count": sum(
                item["answer_abstention"]["unanswerable_abstained"] for item in unanswerable
            ),
            "unanswerable_abstention_rate": _safe_rate(
                sum(item["answer_abstention"]["unanswerable_abstained"] for item in unanswerable),
                len(unanswerable),
            ),
            "unsafe_answer_count": sum(
                item["answer_abstention"]["unsafe_answer"] for item in rows
            ),
            "unnecessary_abstention_count": sum(
                item["answer_abstention"]["unnecessary_abstention"] for item in rows
            ),
        },
        "routing_tools": {
            "correct_tool_set_count": sum(
                item["routing_tools"]["correct_tool_set"] for item in rows
            ),
            "correct_tool_set_accuracy": _safe_rate(
                sum(item["routing_tools"]["correct_tool_set"] for item in rows),
                len(rows),
            ),
            "missing_tool_count": sum(
                len(item["routing_tools"]["missing_tools"]) for item in rows
            ),
            "unnecessary_tool_count": sum(
                len(item["routing_tools"]["unnecessary_tools"]) for item in rows
            ),
        },
        "grounding": {
            "checked_case_count": sum(item["grounding"]["applicable"] for item in rows),
            "valid_case_count": sum(
                item["grounding"]["applicable"] and item["grounding"]["valid"]
                for item in rows
            ),
            "failure_count": sum(not item["grounding"]["valid"] for item in rows),
        },
        "operations": {
            "case_latency_ms_total": sum(item["operations"]["case_latency_ms"] for item in rows),
            "case_latency_ms_mean": (
                mean(item["operations"]["case_latency_ms"] for item in rows)
                if rows
                else 0.0
            ),
            "api": api,
            "tool_call_count": sum(item["operations"]["tool_call_count"] for item in rows),
            "tool_call_mean": (
                mean(item["operations"]["tool_call_count"] for item in rows)
                if rows
                else 0.0
            ),
        },
        "failure_distribution": dict(
            Counter(failure for item in rows for failure in item["failure_types"])
        ),
    }


def aggregate_end_to_end_metrics(
    case_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate all metrics overall and separately by frozen question category."""

    case_ids = [str(item["case_id"]) for item in case_results]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("End-to-End case results contain duplicate case IDs")
    categories = sorted({str(item["category"]) for item in case_results})
    return {
        "metric_definitions": METRIC_DEFINITIONS,
        "overall": _aggregate_bucket(case_results),
        "by_task_type": {
            category: _aggregate_bucket(
                [item for item in case_results if item["category"] == category]
            )
            for category in categories
        },
        "per_case": list(case_results),
    }
