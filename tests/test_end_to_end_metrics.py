from __future__ import annotations

from copy import deepcopy

from src.evaluation.end_to_end_metrics import (
    aggregate_end_to_end_metrics,
    evaluate_end_to_end_case,
    evidence_metrics_at_10,
)


def _case(*, expected_behavior: str = "answer") -> dict:
    return {
        "id": "DEV-LIT-001",
        "category": "literature_only",
        "question": "question",
        "expected_behavior": expected_behavior,
        "required_tools": ["search_literature"] if expected_behavior == "answer" else [],
        "gold": {
            "literature_evidence_groups": [
                {
                    "id": "G1",
                    "required": True,
                    "match": "any",
                    "chunk_ids": ["c1", "c1-alt"],
                },
                {
                    "id": "G2",
                    "required": True,
                    "match": "all",
                    "chunk_ids": ["c2", "c3"],
                },
            ]
        },
    }


def _hit(chunk_id: str, rank: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_id": "P1",
        "title": "Title",
        "section": "Results",
        "text": f"Text {chunk_id}",
        "rank": rank,
        "corpus_version": "literature_corpus_v1",
    }


def _answer_response(used: list[str]) -> dict:
    return {
        "final_status": "answer_ready",
        "answer_text": "grounded",
        "used_tool_results": [],
        "used_literature_chunk_ids": used,
        "limitations": [],
        "provenance": {
            "used_tool_result_ids": [],
            "used_literature_chunk_ids": used,
        },
    }


def _state(initial: list[str], final: list[str], status: str = "answer_ready") -> dict:
    return {
        "route": {
            "selected_tools": ["search_literature"],
            "task_type": "literature_only",
        },
        "selected_tools": ["search_literature"],
        "tool_results": [
            {
                "tool": "search_literature",
                "status": "success",
                "result": {"hits": [_hit(item, rank) for rank, item in enumerate(initial, 1)]},
            }
        ],
        "literature_evidence": [_hit(item, rank) for rank, item in enumerate(initial, 1)],
        "fused_evidence": [_hit(item, rank) for rank, item in enumerate(final, 1)],
        "retry_count": int(initial != final),
        "errors": [],
        "final_status": status,
        "final_response": (
            _answer_response(final[:1])
            if status == "answer_ready"
            else {
                "final_status": status,
                "answer_text": "abstain",
                "used_tool_results": [],
                "used_literature_chunk_ids": [],
                "limitations": [],
                "provenance": {
                    "used_tool_result_ids": [],
                    "used_literature_chunk_ids": [],
                },
            }
        ),
    }


def test_evidence_metrics_reuse_any_and_all_semantics() -> None:
    incomplete = evidence_metrics_at_10(_case(), ["c1", "c2"])
    complete = evidence_metrics_at_10(_case(), ["c1-alt", "c2", "c3"])
    assert incomplete == {
        "evidence_group_recall@10": 0.5,
        "complete_evidence@10": 0.0,
    }
    assert complete == {
        "evidence_group_recall@10": 1.0,
        "complete_evidence@10": 1.0,
    }


def test_recovery_success_and_node_level_api_accounting() -> None:
    state = _state(["c1", "c2"], ["c1", "c2", "c3"])
    api_calls = [
        {
            "component": "runtime_evidence_grader",
            "latency_ms": 10,
            "token_usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "estimated_cost_usd": 0.01},
        },
        {
            "component": "evidence_recovery_agent",
            "latency_ms": 20,
            "token_usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10, "estimated_cost_usd": 0.02},
        },
        {
            "component": "runtime_evidence_grader",
            "latency_ms": 11,
            "token_usage": {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13, "estimated_cost_usd": 0.01},
        },
        {
            "component": "final_answer",
            "latency_ms": 30,
            "token_usage": {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12, "estimated_cost_usd": 0.03},
        },
    ]
    result = evaluate_end_to_end_case(
        _case(),
        state,
        api_calls=api_calls,
        tool_calls=[{"tool": "search_literature"}, {"tool": "search_literature"}],
        grader_calls=[
            {"execution_status": "completed", "evidence_sufficient": False},
            {"execution_status": "completed", "evidence_sufficient": True},
        ],
        recovery_calls=[{"execution_status": "query_ready"}],
        case_latency_ms=100,
    )
    assert result["recovery"]["triggered"] is True
    assert result["recovery"]["executed"] is True
    assert result["recovery"]["success"] is True
    assert result["recovery"]["new_gold_chunk_ids"] == ["c3"]
    assert result["grounding"]["valid"] is True
    assert result["operations"]["api"]["by_node"]["runtime_grader"]["request_count"] == 2
    assert result["operations"]["api"]["total"]["request_count"] == 4
    assert result["operations"]["api"]["total"]["total_tokens"] == 47


def test_unsafe_answer_when_final_gold_is_incomplete() -> None:
    state = _state(["c1"], ["c1"], "answer_ready")
    result = evaluate_end_to_end_case(
        _case(),
        state,
        grader_calls=[{"execution_status": "completed", "evidence_sufficient": True}],
    )
    assert result["answer_abstention"]["unsafe_answer"] is True
    assert "unsafe_answer" in result["failure_types"]
    assert "retrieval_insufficiency" in result["failure_types"]
    assert "grader_failure" in result["failure_types"]


def test_unnecessary_abstention_when_complete_and_tools_succeed() -> None:
    state = _state(["c1", "c2", "c3"], ["c1", "c2", "c3"], "abstain_ready")
    result = evaluate_end_to_end_case(
        _case(),
        state,
        grader_calls=[{"execution_status": "completed", "evidence_sufficient": True}],
    )
    assert result["answer_abstention"]["unnecessary_abstention"] is True
    assert "unnecessary_abstention" in result["failure_types"]


def test_unanswerable_abstention_and_no_tool_route_are_counted() -> None:
    case = {
        "id": "DEV-UNA-001",
        "category": "unanswerable",
        "question": "unsupported",
        "expected_behavior": "abstain",
        "required_tools": [],
        "gold": {"literature_evidence_groups": []},
    }
    state = {
        "route": {"selected_tools": [], "task_type": "unsupported"},
        "selected_tools": [],
        "tool_results": [],
        "literature_evidence": [],
        "fused_evidence": [],
        "retry_count": 0,
        "errors": [],
        "final_status": "abstain_ready",
        "final_response": {
            "final_status": "abstain_ready",
            "answer_text": "insufficient data",
            "used_tool_results": [],
            "used_literature_chunk_ids": [],
            "provenance": {
                "used_tool_result_ids": [],
                "used_literature_chunk_ids": [],
            },
        },
    }
    result = evaluate_end_to_end_case(case, state)
    assert result["answer_abstention"]["unanswerable_abstained"] is True
    assert result["initial_evidence"] is None
    assert result["failure_types"] == []


def test_recovery_structured_error_is_classified_without_claiming_success() -> None:
    state = _state(["c1"], ["c1"], "execution_failure")
    state["errors"] = [
        {"stage": "recovery_agent", "code": "provider_failure", "message": "closed"}
    ]
    result = evaluate_end_to_end_case(
        _case(),
        state,
        recovery_calls=[{"execution_status": "failure"}],
    )
    assert result["recovery"]["triggered"] is True
    assert result["recovery"]["success"] is False
    assert "recovery_failure" in result["failure_types"]
    assert "execution_failure" in result["failure_types"]


def test_grounding_rejects_chunk_not_in_terminal_evidence() -> None:
    state = _state(["c1", "c2", "c3"], ["c1", "c2", "c3"])
    state["final_response"] = _answer_response(["not-supplied"])
    result = evaluate_end_to_end_case(_case(), state)
    assert result["grounding"]["valid"] is False
    assert result["grounding"]["unknown_literature_chunk_ids"] == ["not-supplied"]
    assert "grounding_failure" in result["failure_types"]


def test_aggregate_reports_status_recovery_cost_and_task_type() -> None:
    success = evaluate_end_to_end_case(
        _case(),
        _state(["c1", "c2"], ["c1", "c2", "c3"]),
        recovery_calls=[{"execution_status": "query_ready"}],
        api_calls=[
            {
                "component": "evidence_recovery_agent",
                "latency_ms": 2,
                "token_usage": {"total_tokens": 5, "estimated_cost_usd": 0.5},
            }
        ],
    )
    failed_state = deepcopy(_state(["c1"], ["c1"], "execution_failure"))
    failed_state["final_response"]["final_status"] = "execution_failure"
    second_case = _case()
    second_case["id"] = "DEV-LIT-002"
    failed = evaluate_end_to_end_case(second_case, failed_state)
    aggregate = aggregate_end_to_end_metrics([success, failed])
    overall = aggregate["overall"]
    assert overall["case_count"] == 2
    assert overall["final_status"]["answer_ready"]["count"] == 1
    assert overall["final_status"]["execution_failure"]["count"] == 1
    assert overall["recovery"]["success_count"] == 1
    assert overall["operations"]["api"]["total"]["estimated_cost_usd"] == 0.5
    assert aggregate["by_task_type"]["literature_only"]["case_count"] == 2
