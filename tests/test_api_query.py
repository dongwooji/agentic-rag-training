from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.dependencies import get_workflow


class FakeWorkflow:
    def __init__(self, state: dict[str, Any] | None = None, error: Exception | None = None):
        self.state = state
        self.error = error
        self.questions: list[str] = []

    def invoke(self, question: str) -> dict[str, Any]:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        assert self.state is not None
        return self.state


def _provenance(
    status: str,
    *,
    used_tool_ids: list[str],
    used_chunks: list[str],
) -> dict[str, Any]:
    return {
        "component": "final_answer_layer_v1",
        "graph_terminal_status": status,
        "channel_mode": (
            "hybrid"
            if used_tool_ids and used_chunks
            else "structured_only"
            if used_tool_ids
            else "literature_only"
            if used_chunks
            else "no_evidence"
        ),
        "available_tool_result_ids": used_tool_ids,
        "used_tool_result_ids": used_tool_ids,
        "available_literature_chunk_ids": used_chunks,
        "used_literature_chunk_ids": used_chunks,
        "used_literature_sources": [
            {"chunk_id": chunk_id, "paper_id": "PAPER-001"}
            for chunk_id in used_chunks
        ],
        "model": "test-model",
        "prompt_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "response_id": "response-test",
        "latency_ms": 1.0,
        "token_usage": {
            "input_tokens": 1,
            "cached_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_tokens": 0,
            "total_tokens": 2,
            "estimated_cost_usd": 0.0,
        },
        "response_metadata": {},
        "internal_graph_errors": [],
        "internal_error_detail": None,
    }


def _state(status: str) -> dict[str, Any]:
    used_chunks = ["PAPER-001::chunk-003"] if status == "answer_ready" else []
    used_tool_ids = ["compute_metrics:1"] if status == "answer_ready" else []
    response: dict[str, Any] = {
        "final_status": status,
        "answer_text": {
            "answer_ready": "기록과 문헌 근거를 구분해 답변했습니다.",
            "abstain_ready": "현재 근거만으로는 요청한 결론을 낼 수 없습니다.",
            "execution_failure": "요청을 안전하게 처리하지 못했습니다.",
        }[status],
        "used_tool_results": [
            {
                "result_id": "compute_metrics:1",
                "tool": "compute_metrics",
                "phase": "initial",
                "requested_operation": "training_gap",
                "result": {"days": 10},
                "provenance": {"metric_version": "v1"},
            }
        ] if used_tool_ids else [],
        "used_literature_chunk_ids": used_chunks,
        "limitations": ["현재 제공된 근거 범위로 제한됩니다."],
        "provenance": _provenance(
            status,
            used_tool_ids=used_tool_ids,
            used_chunks=used_chunks,
        ),
        "error": (
            {
                "code": "graph_execution_failure",
                "user_message": "일시적인 내부 실행 문제로 요청을 완료하지 못했습니다.",
            }
            if status == "execution_failure"
            else None
        ),
    }
    return {
        "route": {"route": "hybrid", "task_type": "hybrid"},
        "task_type": "hybrid",
        "tool_results": [
            {"tool": "query_training_log", "status": "success"},
            {"tool": "compute_metrics", "status": "success"},
            {"tool": "search_literature", "status": "success"},
        ],
        "retry_count": 1 if status == "abstain_ready" else 0,
        "recovery_result": (
            {"execution_status": "query_ready"}
            if status == "abstain_ready"
            else None
        ),
        "fusion_history": ([{"policy": "retry_evidence_fusion_v1"}]
                           if status == "abstain_ready" else []),
        "final_status": status,
        "final_response": response,
    }


@pytest.mark.parametrize(
    ("final_status", "expected_recovery"),
    [
        ("answer_ready", False),
        ("abstain_ready", True),
        ("execution_failure", False),
    ],
)
def test_query_returns_all_graph_terminal_states_as_http_200(
    final_status: str,
    expected_recovery: bool,
) -> None:
    workflow = FakeWorkflow(_state(final_status))
    application = create_app()
    application.dependency_overrides[get_workflow] = lambda: workflow

    with TestClient(application) as client:
        response = client.post("/query", json={"question": "  테스트 질문  "})

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_status"] == final_status
    assert payload["route"] == "hybrid"
    assert payload["tools_used"] == [
        "query_training_log",
        "compute_metrics",
        "search_literature",
    ]
    assert payload["recovery_used"] is expected_recovery
    assert payload["retry_count"] == (1 if expected_recovery else 0)
    assert payload["latency_ms"] >= 0
    assert workflow.questions == ["테스트 질문"]
    if final_status == "answer_ready":
        assert payload["used_literature_chunk_ids"] == [
            "PAPER-001::chunk-003"
        ]
        assert "error" not in payload
    elif final_status == "execution_failure":
        assert payload["error"] == {
            "code": "graph_execution_failure",
            "message": "일시적인 내부 실행 문제로 요청을 완료하지 못했습니다.",
        }


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   \t\n"},
        {"question": 123},
        {"question": "ok", "unexpected": True},
    ],
)
def test_invalid_question_returns_validation_error_without_calling_workflow(
    body: dict[str, Any],
) -> None:
    workflow = FakeWorkflow(_state("answer_ready"))
    application = create_app()
    application.dependency_overrides[get_workflow] = lambda: workflow

    with TestClient(application) as client:
        response = client.post("/query", json=body)

    assert response.status_code == 422
    assert workflow.questions == []


def test_internal_exception_returns_sanitized_http_error(caplog) -> None:
    secret = "sk-never-expose-this-value"
    workflow = FakeWorkflow(error=RuntimeError(f"provider failed with {secret}"))
    application = create_app()
    application.dependency_overrides[get_workflow] = lambda: workflow

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/query", json={"question": "정상 질문"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "The request could not be processed safely."
    }
    assert secret not in response.text
    assert secret not in caplog.text


def test_public_query_response_does_not_expose_internal_provenance_or_secrets() -> None:
    state = _state("execution_failure")
    secret = "database-password-never-expose"
    state["errors"] = [{"stage": "tool", "message": secret}]
    state["final_response"]["provenance"]["internal_error_detail"] = secret
    state["final_response"]["provenance"]["internal_graph_errors"] = [
        {"message": secret}
    ]
    workflow = FakeWorkflow(state)
    application = create_app()
    application.dependency_overrides[get_workflow] = lambda: workflow

    with TestClient(application) as client:
        response = client.post("/query", json={"question": "정상 질문"})

    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert response.status_code == 200
    assert secret not in serialized
    assert "provenance" not in response.json()
    assert "used_tool_results" not in response.json()
