"""Synthetic date rejection -> public clarification, with no Tool execution."""
from unittest.mock import Mock
import json
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.api.dependencies import get_workflow
from src.graph.interpreted_workflow import InterpretedWorkflow
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult
from test_date_grounding import Repository, draft


def workflow(raw):
    provider = Mock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=raw)
    executor = Mock()
    graph = InterpretedWorkflow(interpreter=QuestionInterpreter(exercise_repository=Repository(), provider=provider),
        tool_executor=executor, literature_tool=Mock(), runtime_grader=Mock(), recovery_agent=Mock())
    return graph, provider, executor


@pytest.mark.parametrize("period", ["2031년 2월 29일", "2034년 15월", "2034년 11월~3월", "4월"])
def test_malformed_source_period_returns_clarification_without_retry(period):
    graph, provider, executor = workflow(draft())
    state = graph.invoke(f"내 Fixture Pull의 {period} 기록을 보여주세요.")
    assert state["interpretation_status"] == "clarification_required"
    assert state["final_status"] == "abstain_ready" and state["errors"] == []
    assert state["final_response"]["provenance"]["response_metadata"]["clarification_required"]
    assert "어느 기간을 볼까요?" in state["final_response"]["answer_text"]
    provider.invoke.assert_called_once()
    executor.execute.assert_not_called()


def test_invented_period_is_rejected_as_public_question_without_internal_reason():
    graph, provider, executor = workflow(draft(time_condition=dict(scope="explicit", start_date="2034-01-01", end_date="2034-12-31", source_text="")))
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: graph
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "내 Fixture Pull 기록을 보여주세요."})
    assert response.status_code == 200
    body = response.json()
    assert body["response_metadata"]["clarification_required"] is True
    assert body["response_metadata"]["response_mode"] == "clarification_required"
    assert "error" not in body and body["final_status"] == "abstain_ready"
    assert "date is absent" not in json.dumps(body) and "GroundingError" not in json.dumps(body)
    assert "어느 기간을 볼까요?" in body["answer_text"]
    provider.invoke.assert_called_once()
    executor.execute.assert_not_called()


def test_model_reason_is_never_echoed_in_public_clarification():
    graph, _, _ = workflow(draft(time_condition=dict(scope="all_records", start_date=None, end_date=None, source_text=""),
        unresolved_fields=[dict(field="question", reason="Internal model reasoning with provider secret token")], clarification_required=True))
    state = graph.invoke("내 Fixture Pull 기록을 확인해 주세요.")
    assert "Internal model reasoning" not in state["final_response"]["answer_text"]
    assert "구체적으로 알려주세요" in state["final_response"]["answer_text"]


def test_date_clarification_does_not_bypass_numeric_grounding():
    graph, provider, executor = workflow(draft(time_condition=dict(scope="explicit", start_date="2034-01-01", end_date="2034-12-31", source_text=""),
        requested_analyses=[dict(operation="weekly_volume", n_sessions=5, source_text="weekly volume")]))
    state = graph.invoke("내 Fixture Pull의 weekly volume을 알려주세요.")
    assert state["final_status"] == "execution_failure"
    assert state["interpretation_trace"]["error_code"] == "ungrounded_interpretation"
    provider.invoke.assert_called_once()
    executor.execute.assert_not_called()
