"""Synthetic failure logs and correlation, no actual provider or DB."""
import json
import logging
from unittest.mock import Mock
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.api.dependencies import get_workflow
from src.observability import log_failure, request_context, REQUEST_ID
from test_date_clarification import workflow
from test_date_grounding import draft
from test_api_query import FakeWorkflow, _state


def events(caplog):
    return [json.loads(x.message) for x in caplog.records if x.name == "agentic_rag.diagnostics"]


def test_date_failure_logs_check_and_field_but_no_secret_or_public_reason(caplog):
    caplog.set_level(logging.WARNING, logger="agentic_rag.diagnostics")
    graph, _, _ = workflow(draft(time_condition=dict(scope="explicit", start_date="2034-01-01", end_date="2034-12-31", source_text="")))
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: graph
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "내 Fixture Pull 기록을 보여주세요."})
    event = next(x for x in events(caplog) if x["check_id"] == "date_grounding")
    assert event["stage"] == "interpretation_validation"
    assert event["field"] == "time_condition.start_date"
    assert event["exception_type"] == "DateGroundingError" and len(event["request_id"]) == 32
    assert event["reason"] == "date_not_supported_by_question"
    assert "date is absent" not in caplog.text and event["request_id"] not in response.text
    assert response.json()["response_metadata"]["clarification_required"] is True


def test_exceptions_and_record_payloads_are_excluded_from_logs_and_response(caplog):
    caplog.set_level(logging.WARNING, logger="agentic_rag.diagnostics")
    state = _state("execution_failure")
    state["errors"] = [dict(stage="initial_tools", code="tool_execution_failure", message="ValueError: API_KEY=private-secret DB_PASSWORD=hidden-pass raw_record=private-record")]
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: FakeWorkflow(state)
    with TestClient(app) as client:
        first = client.post("/query", json={"question": "측정 자료를 확인해주세요."})
        second = client.post("/query", json={"question": "다른 측정 자료를 확인해주세요."})
    logged = events(caplog)
    assert len(logged) == 2 and logged[0]["request_id"] != logged[1]["request_id"]
    assert all(x["exception_type"] == "ValueError" and x["stage"] == "initial_tools" for x in logged)
    for text in (caplog.text, first.text, second.text):
        assert all(secret not in text for secret in ("private-secret", "hidden-pass", "private-record"))


def test_api_exception_keeps_sanitized_response_and_logs_type(caplog):
    caplog.set_level(logging.WARNING, logger="agentic_rag.diagnostics")
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: FakeWorkflow(error=RuntimeError("private-provider-detail"))
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "연구 요약을 보여주세요."})
    assert response.status_code == 500
    assert events(caplog)[0]["exception_type"] == "RuntimeError"
    assert "private-provider-detail" not in caplog.text + response.text


def test_request_context_restores_and_logging_failure_cannot_change_execution(monkeypatch):
    before = REQUEST_ID.get()
    with request_context() as first:
        assert REQUEST_ID.get() == first
        with request_context() as second:
            assert second != first
        assert REQUEST_ID.get() == first
        logger = Mock()
        logger.warning.side_effect = RuntimeError("handler problem")
        monkeypatch.setattr("src.observability.LOGGER", logger)
        log_failure(stage="validator", check_id="date_grounding", exception=ValueError("secret"))
    assert REQUEST_ID.get() == before
