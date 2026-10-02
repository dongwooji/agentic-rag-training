from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from answer_test_support import FakeFinalAnswerProvider, answer_state, draft_payload
from graph_test_support import (
    build_workflow,
    hybrid_tool_inputs,
    literature_hit,
    recovery_payload,
)
from src.agent.contracts import PlannerDraft
from src.agent.executor import DeterministicToolExecutor
from src.answer.contracts import EVIDENCE_BUDGET
from src.answer.generator import (
    TRAINING_LOG_PAYLOAD_POLICY,
    TRAINING_LOG_SEQUENCE_LIMIT,
    FinalResponseLayer,
)
from src.recovery.contracts import MAX_RETRY
from src.recovery.evidence_fusion import (
    EVIDENCE_BUDGET as FUSION_EVIDENCE_BUDGET,
    FUSION_POLICY,
)
from src.retrieval.rrf import DEFAULT_RRF_K
from src.tools.contracts import success_response
from src.tools.metric import MetricTool


ROOT = Path(__file__).resolve().parents[1]
EVAL_DATASET_PATH = ROOT / "data/evaluation/eval_dataset_v1.json"


def load_frozen_cases() -> list[dict[str, Any]]:
    """Read frozen eval_dataset_v1 cases; only their question text is used here."""

    payload = json.loads(EVAL_DATASET_PATH.read_text(encoding="utf-8"))
    return [dict(case) for case in payload["cases"]]


class SessionSummaryTrainingTool:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute(self, request: Any) -> Any:
        self.calls.append(request)
        sessions = [
            {
                "session_id": "session-1",
                "started_at": "2024-01-01T10:00:00",
                "workout_name": "A",
                "set_count": 3,
                "exercise_count": 1,
                "exercise_names": ["Bench Press (Barbell)"],
                "preprocessing_version": "preprocessing_v1",
            },
            {
                "session_id": "session-2",
                "started_at": "2024-01-11T10:00:00",
                "workout_name": "B",
                "set_count": 4,
                "exercise_count": 2,
                "exercise_names": ["Bench Press (Barbell)", "Squat (Barbell)"],
                "preprocessing_version": "preprocessing_v1",
            },
        ]
        return success_response(
            operation="list_sessions",
            result={
                "canonical_exercise_name": None,
                "session_count": len(sessions),
                "sessions": sessions,
                "truncated_at_limit": False,
            },
            provenance={"tool": "training_log_tool_v1"},
        )


class EmptyLiteratureTool:
    def execute(self, request: Any) -> Any:
        return success_response(
            operation="search",
            result={"query": request.query, "top_k": request.top_k, "hits": []},
            provenance={"tool": "literature_tool_v1"},
            empty=True,
        )


def _plan(*, hybrid: bool) -> PlannerDraft:
    steps: list[dict[str, Any]] = [
        {
            "tool": "query_training_log",
            "reason": "Read session summaries.",
            "subtask": "List sessions.",
            "inputs": {
                "operation": "list_sessions",
                "limit": 5000,
                "include_lineage": False,
            },
        },
        {
            "tool": "compute_metrics",
            "reason": "Calculate the deterministic longest gap.",
            "subtask": "Compute training gap from listed sessions.",
            "inputs": {
                "operation": "training_gap",
                "records_source": "query_training_log",
            },
        },
    ]
    if hybrid:
        steps.append(
            {
                "tool": "search_literature",
                "reason": "Retrieve literature evidence.",
                "subtask": "Run unchanged Hybrid retrieval.",
                "inputs": {
                    "operation": "search",
                    "query": "detraining limitations",
                    "top_k": 10,
                },
            }
        )
    return PlannerDraft.model_validate(
        {
            "task_type": "hybrid" if hybrid else "log_metric",
            "unsupported": False,
            "unsupported_reason": None,
            "planner_reasoning_summary": "Regression fixture.",
            "steps": steps,
        }
    )


@pytest.mark.parametrize("hybrid", [False, True])
def test_list_sessions_feeds_training_gap_for_log_and_hybrid(hybrid: bool) -> None:
    executor = DeterministicToolExecutor(
        training_log_tool=SessionSummaryTrainingTool(),
        metric_tool=MetricTool(),
        literature_tool=EmptyLiteratureTool(),
    )
    results = executor.execute(_plan(hybrid=hybrid), enabled=True)

    metric = next(item for item in results if item["tool"] == "compute_metrics")
    assert metric["status"] == "success"
    assert metric["error"] is None
    assert metric["result"]["session_count"] == 2
    assert metric["result"]["gaps"][0]["calendar_day_difference"] == 10
    assert all(item["status"] != "dependency_error" for item in results)


@pytest.mark.parametrize(
    ("case_id", "excluded_terms"),
    [
        (
            "HYB-005",
            ["Deadlift (Barbell)", "5-session", "e1RM 변화"],
        ),
        (
            "HYB-007",
            ["2017-02-04", "2,956", "956", "Squat"],
        ),
    ],
)
def test_hybrid_grader_receives_literature_only_scope(
    case_id: str,
    excluded_terms: list[str],
) -> None:
    case = next(item for item in load_frozen_cases() if item["id"] == case_id)
    workflow, _, _, _, grader, recovery = build_workflow(
        literature_responses=[[literature_hit("initial", 1)]],
        grader_results=[True],
        recovery_payloads=[],
    )
    state = workflow.invoke(
        case["question"],
        initial_query=case["question"],
        tool_inputs=hybrid_tool_inputs(),
    )

    assert state["final_status"] == "answer_ready"
    assert state["missing_components"] == []
    assert recovery.calls == []
    scope = grader.calls[0].literature_subquestion
    assert scope == state["literature_subquestion"]
    assert scope != case["question"]
    for term in excluded_terms:
        assert term.casefold() not in scope.casefold()


def test_clean_literature_scope_is_shared_with_recovery_agent() -> None:
    case = next(item for item in load_frozen_cases() if item["id"] == "HYB-005")
    recovery_query = "VBT autoregulation fixed loading maximal strength evidence"
    workflow, _, _, _, grader, recovery = build_workflow(
        literature_responses=[
            [literature_hit("initial", 1)],
            [literature_hit("recovery", 1)],
        ],
        grader_results=[False, True],
        recovery_payloads=[recovery_payload(recovery_query)],
    )
    state = workflow.invoke(
        case["question"],
        initial_query=case["question"],
        tool_inputs=hybrid_tool_inputs(),
    )

    assert state["final_status"] == "answer_ready"
    assert len(grader.calls) == 2
    assert len(recovery.calls) == 1
    assert recovery.calls[0].literature_subquestion == state["literature_subquestion"]
    assert "Deadlift (Barbell)" not in recovery.calls[0].literature_subquestion
    assert "5-session" not in recovery.calls[0].literature_subquestion


def test_large_training_log_is_bounded_only_in_final_answer_payload() -> None:
    state = answer_state(structured=True)
    raw_records = [
        {
            "set_id": f"set-{index:03d}",
            "started_at": f"2024-01-{index % 28 + 1:02d}T10:00:00",
            "weight": 100.0 + index,
            "reps": 5,
        }
        for index in range(630)
    ]
    state["tool_results"][0]["result"] = {
        "record_count": 630,
        "records": raw_records,
        "truncated_at_limit": False,
    }
    metric_result = {
        "eligible_session_count": 106,
        "requested_n_sessions": 5,
        "used_n_sessions": 5,
        "first_median_e1rm": 209.0,
        "last_median_e1rm": 291.3333333333,
        "change_pct": 39.3939393939,
    }
    state["tool_results"][1]["result"] = metric_result
    provider = FakeFinalAnswerProvider(
        draft_payload(
            record="첫 구간보다 마지막 구간 median e1RM이 높습니다.",
            tool_ids=[
                "tool-result-01-query_training_log",
                "tool-result-02-compute_metrics",
            ],
        )
    )
    response = FinalResponseLayer(provider).respond(state)

    assert response.final_status.value == "answer_ready"
    assert len(provider.calls) == 1
    provider_input = provider.calls[0]
    bounded_log = provider_input.structured_evidence[0].result
    assert len(bounded_log["records"]) == TRAINING_LOG_SEQUENCE_LIMIT
    assert bounded_log["records"][0]["set_id"] == "set-000"
    assert bounded_log["records"][-1]["set_id"] == "set-629"
    assert bounded_log["_answer_payload"] == {
        "policy": TRAINING_LOG_PAYLOAD_POLICY,
        "max_items_per_sequence": 20,
        "selection": "first_10_and_last_10",
        "omitted_items_by_path": {"result.records": 610},
    }
    assert provider_input.structured_evidence[1].result == metric_result
    assert provider_input.structured_evidence[0].provenance == {
        "tool": "training_log_tool_v1"
    }
    assert provider_input.structured_evidence[1].provenance["tool"] == (
        "metric_tool_v1"
    )
    assert len(state["tool_results"][0]["result"]["records"]) == 630


def test_fixed_retry_top_ten_and_fusion_policy_are_unchanged() -> None:
    assert EVIDENCE_BUDGET == FUSION_EVIDENCE_BUDGET == 10
    assert MAX_RETRY == 2
    assert FUSION_POLICY == "retry_evidence_fusion_v1"
    assert DEFAULT_RRF_K == 60
