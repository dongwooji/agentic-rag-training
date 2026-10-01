"""Exercise selection traverses the actual production wrapper; no DB/API."""

from unittest.mock import MagicMock

import pytest

from test_exercise_clarification import setup_flow, client
from src.api.dependencies import RuntimeWorkflow, WorkflowClarificationUnavailable


def wrapped():
    flow, provider, training, *_ = setup_flow()
    runtime = RuntimeWorkflow(workflow=flow, literature_tool=MagicMock())
    return runtime, provider, training


def pending(api):
    response = api.post("/query", json={"question": "내 해머컬 기록 보여줘."})
    assert response.status_code == 200
    assert response.json()["tools_used"] == []
    return response.json()["response_metadata"]["clarification_id"]


@pytest.mark.parametrize("option,expected", [
    ("1", ["Hammer Curl"]),
    ("2", ["Hammer Curl (Dumbbell)"]),
    ("3", ["Hammer Curl", "Hammer Curl (Dumbbell)"]),
])
def test_real_wrapper_public_selection_resume(option, expected):
    runtime, provider, training = wrapped()
    api = client(runtime)
    identifier = pending(api)
    assert training.calls == []
    response = api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": option})
    assert response.status_code == 200
    assert response.json()["final_status"] == "answer_ready"
    assert [call.canonical_exercise_name for call in training.calls] == expected
    assert len(provider.calls) == 1
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": option}).status_code == 404


def test_wrapper_preserves_invalid_selection_then_valid_resume():
    runtime, provider, training = wrapped()
    api = client(runtime)
    identifier = pending(api)
    invalid = api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "9"})
    assert invalid.status_code == 400 and training.calls == []
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "2"}).status_code == 200
    assert len(provider.calls) == 1


def test_wrapper_preserves_expired_and_unknown_requests():
    from src.interpretation.clarification import ClarificationStore
    runtime, _, training = wrapped()
    now = [0]
    runtime._workflow.clarification_store = ClarificationStore(clock=lambda: now[0], ttl_seconds=10)
    api = client(runtime)
    identifier = pending(api)
    now[0] = 11
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "2"}).status_code == 410
    assert api.post("/query/clarify", json={"clarification_id": "missing", "selected_option": "2"}).status_code == 404
    assert training.calls == []


def test_legacy_runtime_returns_documented_409():
    workflow = MagicMock(spec=["invoke"])
    runtime = RuntimeWorkflow(workflow=workflow, literature_tool=MagicMock())
    api = client(runtime)
    assert api.post("/query/clarify", json={"clarification_id": "x", "selected_option": "1"}).status_code == 409
    assert workflow.mock_calls == []
    assert "409" in api.get("/openapi.json").json()["paths"]["/query/clarify"]["post"]["responses"]
    with pytest.raises(WorkflowClarificationUnavailable):
        runtime.clarify("x", "1")


def test_closed_runtime_rejects_clarification_without_execution():
    runtime, provider, training = wrapped()
    runtime.close()
    with pytest.raises(RuntimeError, match="closed"):
        runtime.clarify("x", "1")
    assert training.calls == provider.calls == []
    assert client(runtime).post("/query/clarify", json={"clarification_id": "x", "selected_option": "1"}).status_code == 500
