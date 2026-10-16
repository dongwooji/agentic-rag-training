"""Saved metadata only and new synthetic requests; no acceptance questions/Gold."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.app import create_app, query_response_from_state
from src.api.contracts import ClarificationMetadata
from src.api.dependencies import get_workflow
from src.interpretation.semantic_contracts import PhaseASemantics
from test_api_query import FakeWorkflow, _state
from test_phase_a_semantics import LIFT, api_for, graph_for, payload


SAVED = json.loads((Path(__file__).parent / "fixtures/clarification_window_metadata_v1.json").read_text(encoding="utf-8"))


def clarification_state():
    state = _state("abstain_ready")
    state.update(route={"route": "ambiguous"}, tool_results=[], retry_count=0,
                 recovery_result=None, fusion_history=[])
    metadata = deepcopy(SAVED["response_metadata"])
    state["final_response"]["provenance"]["response_metadata"] = metadata
    state["interpretation_trace"] = {"phase_a_semantics": PhaseASemantics(
        parsing_source="llm", default_time_window=metadata["default_time_window"]).model_dump(mode="json")}
    return state


def test_saved_metadata_reproduces_strict_public_schema_conflict():
    with pytest.raises(ValidationError) as caught:
        ClarificationMetadata.model_validate(SAVED["response_metadata"])
    assert [(e["loc"], e["type"]) for e in caught.value.errors()] == [
        (("default_time_window",), "extra_forbidden")]
    assert "default_time_window" not in ClarificationMetadata.model_fields
    assert ClarificationMetadata.model_config["extra"] == "forbid"


@pytest.mark.parametrize("endpoint", ["/query", "/query/clarify"])
@pytest.mark.parametrize("debug", [None, False, True], ids=["omitted", "false", "true"])
def test_saved_metadata_serializes_without_internal_fields(endpoint, debug):
    state = clarification_state()
    internal = state["final_response"]["provenance"]["response_metadata"]
    internal["server_only_diagnostic"] = "synthetic-private-value"
    internal["trace"] = {"untrusted_internal_trace": "must-not-pass-through"}

    class Workflow(FakeWorkflow):
        def clarify(self, clarification_id, selected_option):
            assert (clarification_id, selected_option) == ("synthetic-id", "1")
            return self.state

    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: Workflow(state)
    request = ({"question": "합성 입력의 확인할 조건을 알려주세요."} if endpoint == "/query" else
               {"clarification_id": "synthetic-id", "selected_option": "1"})
    if debug is not None:
        request["debug"] = debug
    with TestClient(app) as client:
        response = client.post(endpoint, json=request)
    assert response.status_code == 200
    body = response.json()
    metadata = body["response_metadata"]
    assert body["final_status"] == "abstain_ready"
    assert metadata["clarification_required"] is True
    assert "default_time_window" not in metadata
    assert "server_only_diagnostic" not in metadata
    assert "synthetic-private-value" not in json.dumps(body)
    assert "must-not-pass-through" not in json.dumps(body)
    if debug:
        semantics = metadata["trace"]["interpreter"]["phase_a"]
        assert semantics["version"] == "phase_a_semantics_v1"
        assert semantics["default_time_window"] == SAVED["response_metadata"]["default_time_window"]
    else:
        assert "trace" not in metadata
    # Projection must not strip the private state needed by debug/provenance.
    assert internal["default_time_window"] == SAVED["response_metadata"]["default_time_window"]


def test_projection_preserves_existing_exercise_selection_contract():
    state = clarification_state()
    fields = dict(clarification_id="synthetic-id", clarification_type="exercise_selection",
        options=[dict(id="1", label="Synthetic Lift", canonical_exercises=["Synthetic Lift"])],
        expires_at="2036-02-17T09:30:00Z")
    state["final_response"]["provenance"]["response_metadata"].update(fields)
    body = query_response_from_state(state, latency_ms=1).model_dump(mode="json", exclude_none=True)
    assert all(body["response_metadata"][k] == v for k, v in fields.items())
    assert set(body["response_metadata"]) == {"response_mode", "clarification_required", *fields}


def test_projection_still_validates_allowed_public_fields():
    state = clarification_state()
    state["final_response"]["provenance"]["response_metadata"]["options"] = "invalid"
    with pytest.raises(ValidationError) as caught:
        query_response_from_state(state, latency_ms=1)
    assert any(e["loc"] == ("options",) for e in caught.value.errors())


@pytest.mark.parametrize("debug", [False, True])
def test_synthetic_phase_a_recent_argument_clarification_returns_http_200(debug):
    question = f"요즘 {LIFT}의 weekly frequency 관측값을 다른 구간과 비교해주세요."
    raw = payload(question, operation="weekly_frequency", unresolved_fields=[
        dict(field="comparison", reason="합성 비교 대상 미확정")], clarification_required=True)
    raw["time_condition"].update(scope="unresolved", source_text="요즘")
    graph, provider, *ports = graph_for(raw)
    with api_for(graph) as api:
        response = api.post("/query", json=dict(question=question, debug=debug))
    assert response.status_code == 200
    metadata = response.json()["response_metadata"]
    assert metadata["clarification_required"] is True
    assert "default_time_window" not in metadata
    if debug:
        assert metadata["trace"]["interpreter"]["phase_a"]["default_time_window"]["applied"]
    else:
        assert "trace" not in metadata
    assert provider.invoke.call_count == 1
    assert all(not p.calls for p in ports)
