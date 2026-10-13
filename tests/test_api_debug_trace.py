"""New synthetic trace observations through the real API, no live providers."""
from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from src.api.app import create_app
from src.api.dependencies import get_workflow
from src.api.debug_trace import build_debug_trace
from src.answer.contracts import FinalResponse, FinalResponseProvenance, FinalResponseStatus, ChannelMode
from graph_test_support import build_workflow, literature_hit, recovery_payload
from test_api_query import FakeWorkflow, _state
from test_date_clarification import workflow
from test_date_grounding import draft


SYNTHETIC_QUERY = "훈련 경험자에게 VBT를 적용했을 때 반복 수행량이 어떻게 달라지는지 연구 문헌을 설명해주세요."


class FinalLayer:
    def respond(self, state):
        ids = [x["chunk_id"] for x in state.get("fused_evidence", [])]
        status = FinalResponseStatus(state["final_status"])
        return FinalResponse(final_status=status, answer_text="합성 문헌의 관찰 결과입니다.", used_literature_chunk_ids=ids,
            provenance=FinalResponseProvenance(graph_terminal_status=status, channel_mode=ChannelMode.LITERATURE_ONLY,
                available_literature_chunk_ids=ids, used_literature_chunk_ids=ids,
                used_literature_sources=[{key: hit[key] for key in ("chunk_id", "paper_id", "title", "section")} for hit in state.get("fused_evidence", [])]))


def pipeline_state():
    question = SYNTHETIC_QUERY
    graph, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[[literature_hit("source-alpha", 1)], [literature_hit("source-beta", 1)], [literature_hit("source-gamma", 1)]],
        grader_results=[False, False, True], recovery_payloads=[recovery_payload("VBT repetition performance mechanisms"), recovery_payload("VBT repetition performance training protocol")],
        final_response_layer=FinalLayer())
    generator = Mock()
    generator.prepare.return_value = dict(dense_query="VBT repetition performance research", bm25_query="VBT repetition performance research",
        step2=dict(actual_query="VBT 반복 수행량 문헌", fallback=False),
        step3=dict(actual_query="VBT repetition performance research", fallback=False))
    graph.nodes.query_mode = "translated_both"
    graph.nodes.query_generator = generator
    state = graph.invoke(question)
    assert state["final_status"] == "answer_ready", (state.get("errors"), state.get("final_response"))
    assert len(grader.calls) == 3 and len(recovery.calls) == 2 and len(literature.calls) == 3
    return state, (generator, literature, grader, recovery)


def test_debug_trace_covers_queries_retrieval_each_grade_recovery_and_fusion():
    state, ports = pipeline_state()
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: FakeWorkflow(state)
    with TestClient(app) as client:
        regular = client.post("/query", json={"question": "합성 검색 결과를 확인해주세요."}).json()
        debug = client.post("/query", json={"question": "합성 검색 결과를 확인해주세요.", "debug": True}).json()
    assert "response_metadata" not in regular
    assert {k: v for k, v in debug.items() if k not in {"response_metadata", "latency_ms"}} == {k: v for k, v in regular.items() if k != "latency_ms"}
    trace = debug["response_metadata"]["trace"]
    assert len(trace["request_id"]) == 32
    assert trace["initial_queries"]["korean"] == "VBT 반복 수행량 문헌"
    assert trace["initial_queries"]["english"] == "VBT repetition performance research"
    assert [x["evidence_sufficient"] for x in trace["graders"]] == [False, False, True]
    assert trace["graders"][0]["missing_components"][0]["component_id"] == "C1"
    assert [x["attempt"] for x in trace["recovery"]] == [1, 2]
    assert [x["new_evidence"][0]["chunk_id"] for x in trace["recovery"]] == ["source-beta", "source-gamma"]
    assert len(trace["retrievals"]) == 3 and len(trace["fusions"]) == 2
    assert {x["chunk_id"] for x in trace["final_evidence"]} == {"source-alpha", "source-beta", "source-gamma"}
    assert all(x["title"] and x["section"] for x in trace["final_evidence"])
    assert ports[0].prepare.call_count == 1 and len(ports[1].calls) == 3


def test_debug_interpreter_summary_and_date_validation_preserve_clarification():
    graph, provider, executor = workflow(draft(time_condition=dict(scope="explicit", start_date="2034-01-01", end_date="2034-12-31", source_text="")))
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: graph
    with TestClient(app) as client:
        body = client.post("/query", json={"question": "내 Fixture Pull 기록을 확인해주세요.", "debug": True}).json()
    metadata = body["response_metadata"]
    assert metadata["clarification_required"] is True
    assert metadata["trace"]["interpreter"]["output_summary"]["time_condition"]["start_date"] == "2034-01-01"
    assert metadata["trace"]["interpreter"]["validation"]["check_id"] == "date_grounding"
    assert metadata["trace"]["interpreter"]["validation"]["normalized_summary"]["time_condition"]["start_date"] is None
    assert provider.invoke.call_count == 1
    executor.execute.assert_not_called()


def test_trace_excludes_raw_records_prompts_errors_and_redacts_configured_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "canary-openai-secret")
    monkeypatch.setenv("PGPASSWORD", "canary-db-password")
    state = _state("execution_failure")
    state.update(interpretation_trace=dict(provider=dict(raw_interpretation=draft(), raw_output="private-provider-prompt", error="private-provider-error")),
        tool_results=[dict(tool="query_training_log", result=dict(records=[dict(notes="private-record", password="canary-db-password")])),
                      dict(tool="search_literature", phase="initial", result=dict(hits=[]))],
        errors=[dict(stage="input", code="invalid_question", message="private-exception-trace")],
        initial_query="OPENAI_API_KEY=canary-openai-secret PGPASSWORD=canary-db-password")
    trace = build_debug_trace(state)
    encoded = json.dumps(trace)
    assert all(x not in encoded for x in ("canary-openai-secret", "canary-db-password", "private-record", "private-provider-prompt", "private-provider-error", "private-exception-trace"))
    assert "[REDACTED]" in encoded


@pytest.mark.parametrize("debug", [False, None, "true", 1])
def test_only_explicit_boolean_true_enables_trace(debug):
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: FakeWorkflow(_state("answer_ready"))
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "합성 결과를 설명해주세요.", "debug": debug})
    assert response.status_code == (200 if debug is False else 422)
    if response.status_code == 200:
        assert "response_metadata" not in response.json()


def test_trace_failure_does_not_change_terminal_or_leak_invalid_provider_fields():
    state = _state("execution_failure")
    state["interpretation_trace"] = dict(provider=dict(raw_interpretation=dict(time_condition=dict(scope=["private-provider-field"]))))
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: FakeWorkflow(state)
    with TestClient(app) as client:
        response = client.post("/query", json={"question": "실패한 합성 결과를 확인해주세요.", "debug": True})
    assert response.status_code == 200 and response.json()["final_status"] == "execution_failure"
    assert response.json()["response_metadata"]["trace"]["interpreter"]["validation"]["status"] == "unavailable"
    assert "private-provider-field" not in response.text


def test_failed_grader_and_recovery_attempts_remain_in_history_without_details():
    graph, _, _, _, _, _ = build_workflow(literature_responses=[[literature_hit("trace-source", 1)]],
        grader_results=[], recovery_payloads=[], final_response_layer=FinalLayer())
    graph.nodes.runtime_grader = Mock()
    graph.nodes.runtime_grader.grade.side_effect = RuntimeError("private-grader-provider-detail")
    state = graph.invoke(SYNTHETIC_QUERY)
    trace = build_debug_trace(state)
    assert trace["graders"][0]["execution_status"] == "failure"
    assert "private-grader-provider-detail" not in json.dumps(trace)

    graph, _, _, _, _, _ = build_workflow(literature_responses=[[literature_hit("trace-source", 1)]],
        grader_results=[False], recovery_payloads=[], final_response_layer=FinalLayer())
    graph.nodes.recovery_agent = Mock()
    graph.nodes.recovery_agent.generate.side_effect = RuntimeError("private-recovery-provider-detail")
    trace = build_debug_trace(graph.invoke(SYNTHETIC_QUERY))
    assert trace["recovery"][0]["status"] == "failure" and trace["recovery"][0]["attempt"] == 1
    assert "private-recovery-provider-detail" not in json.dumps(trace)


def test_failed_recovery_search_keeps_attempt_and_generated_query():
    state = _state("execution_failure")
    state.update(initial_query="first query", query_history=["first query"], recovery_history=[dict(attempt=1,
        result=dict(execution_status="query_ready", recovery_query="recovery query"))],
        tool_results=[dict(tool="search_literature", phase="recovery", retry_count=0, status="failure", result=None)])
    trace = build_debug_trace(state)
    assert trace["retrievals"][0]["attempt"] == 1 and trace["retrievals"][0]["query"] == "recovery query"
    assert trace["recovery"][0]["new_evidence"] == []


def test_non_literature_request_does_not_claim_a_search_query():
    trace = build_debug_trace(dict(initial_query="synthetic record request", tool_results=[dict(tool="query_training_log", status="success")]))
    assert trace["initial_queries"]["dense"] is None and trace["initial_queries"]["bm25"] is None
    assert trace["initial_queries"]["korean"] is None and trace["initial_queries"]["english"] is None
    assert trace["retrievals"] == []
