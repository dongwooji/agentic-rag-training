"""Phase A.2 offline selection, resume, API and safety regressions."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from graph_test_support import build_workflow, literature_hit
from test_question_interpretation import attach, draft, FakeInterpreterProvider
from test_tool_input_resolver import FakeExerciseRepository
from test_runtime_exercise_relations import MemberTool, row
from src.api.app import create_app
from src.api.dependencies import get_workflow
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.clarification import ClarificationError, ClarificationStore
from src.interpretation.exercise_catalog import candidate_schema
from src.answer.generator import FinalResponseLayer
from answer_test_support import FakeFinalAnswerProvider, draft_payload


NAMES = ["Hammer Curl", "Hammer Curl (Dumbbell)", "Deadlift (Barbell)",
         "Seated Shoulder Press (Barbell)", "Seated Shoulder Press (Dumbbell)",
         "Lat Pulldown", "Lat Pulldown (Cable)", "Lat Pulldown Closegrip"]


def setup_flow(mention="해머컬", **updates):
    output = draft(exercise_mention=mention, canonical_exercise_name=None,
                   exercise_resolution_status="ambiguous", requested_analyses=[], **updates)
    provider = FakeInterpreterProvider(output)
    legacy, training, metric, literature, grader, recovery = build_workflow(
        literature_responses=[[literature_hit("a", 1)]], grader_results=[True], recovery_payloads=[],
        final_response_layer=FinalResponseLayer(FakeFinalAnswerProvider(draft_payload(
            record="선택된 기록을 확인했습니다.", tool_ids=["tool-result-01-query_training_log"]))))
    port = MemberTool({name: [row(name, name)] for name in NAMES})
    legacy.nodes.tool_executor._tools["query_training_log"] = port
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(list(NAMES)), provider=provider)
    workflow = attach(legacy, component)
    return workflow, provider, port, metric, literature, grader, recovery


def metadata(state):
    return state["final_response"]["provenance"]["response_metadata"]


def test_multiple_candidates_no_downstream_or_final_answer_calls():
    flow, provider, port, metric, literature, grader, recovery = setup_flow()
    final = MagicMock()
    flow.final_response_layer = final
    state = flow.invoke("내 해머컬 기록 보여줘.")
    assert state["final_status"] == "abstain_ready"
    assert metadata(state)["response_mode"] == "clarification"
    assert len(metadata(state)["options"]) == 3
    assert port.calls == metric.calls == literature.calls == grader.calls == recovery.calls == []
    assert final.mock_calls == []
    assert len(provider.calls) == 1


@pytest.mark.parametrize("option,expected", [("1", [NAMES[0]]), ("2", [NAMES[1]]), ("3", NAMES[:2])])
def test_resume_queries_only_selected_names_no_interpreter_recall(option, expected):
    flow, provider, port, *_ = setup_flow()
    state = flow.invoke("내 해머컬 기록 보여줘.")
    result = flow.clarify(metadata(state)["clarification_id"], option)
    assert result["final_status"] == "answer_ready"
    assert [x.canonical_exercise_name for x in port.calls] == expected
    assert len(provider.calls) == 1
    assert result["exercise_selection_provenance"]["selected_canonical_exercises"] == expected
    assert result["question_interpretation"]["original_question"] == "내 해머컬 기록 보여줘."
    if option == "3":
        payload = result["tool_results"][0]
        assert payload["result"]["stored_record_count"] == 2
        assert payload["result"]["cross_label_overlap_signatures"] == 1
        assert payload["limitations"]


def test_explicit_dumbbell_only_no_clarification_no_joint_lookup():
    flow, provider, port, *_ = setup_flow("덤벨 해머컬")
    result = flow.invoke("내 덤벨 해머컬 기록 보여줘.")
    assert result["final_status"] == "answer_ready"
    assert [x.canonical_exercise_name for x in port.calls] == [NAMES[1]]
    assert len(provider.calls) == 1


@pytest.mark.parametrize("mention,expected", [("시티드 숄더 프레스", NAMES[3:5]), ("랫풀다운", NAMES[5:])])
def test_other_actual_catalog_families_use_general_flow(mention, expected):
    flow, provider, port, *_ = setup_flow(mention)
    result = flow.invoke(f"내 {mention} 기록 보여줘.")
    assert [x["canonical_exercises"][0] for x in metadata(result)["options"]] == expected
    assert all(len(x["canonical_exercises"]) == 1 for x in metadata(result)["options"])
    flow.clarify(metadata(result)["clarification_id"], "2")
    assert [x.canonical_exercise_name for x in port.calls] == [expected[1]]
    assert len(provider.calls) == 1


def test_deadlift_single_candidate_rule_fast_path():
    flow, provider, port, *_ = setup_flow()
    result = flow.invoke("내 Deadlift (Barbell) 기록 보여줘.")
    assert result["final_status"] == "answer_ready"
    assert result["question_interpretation"]["parsing_source"] == "rule"
    assert [x.canonical_exercise_name for x in port.calls] == [NAMES[2]]
    assert provider.calls == []


def test_user_choice_preserves_other_unresolved_fields():
    flow, provider, port, *_ = setup_flow(time_condition={"scope": "unresolved", "start_date": None,
                                                        "end_date": None, "source_text": "요즘"})
    state = flow.invoke("내 요즘 해머컬 기록 보여줘.")
    result = flow.clarify(metadata(state)["clarification_id"], "2")
    assert result["interpretation_status"] == "clarification_required"
    assert port.calls == []
    assert len(provider.calls) == 1
    assert "분석 기간" in result["final_response"]["answer_text"]


def client(flow):
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: flow
    return TestClient(app)


def test_public_api_two_requests_and_replay_rejected():
    flow, provider, port, *_ = setup_flow()
    api = client(flow)
    first = api.post("/query", json={"question": "내 해머컬 기록 보여줘."})
    assert first.status_code == 200
    body = first.json()
    assert body["tools_used"] == []
    payload = {"clarification_id": body["response_metadata"]["clarification_id"], "selected_option": "2"}
    second = api.post("/query/clarify", json=payload)
    assert second.status_code == 200 and second.json()["final_status"] == "answer_ready"
    assert api.post("/query/clarify", json=payload).status_code == 404
    assert len(port.calls) == 1 and len(provider.calls) == 1
    assert "/query/clarify" in api.get("/openapi.json").json()["paths"]


def test_invalid_option_does_not_consume_or_execute():
    flow, _, port, *_ = setup_flow()
    api = client(flow)
    state = flow.invoke("내 해머컬 기록 보여줘.")
    identifier = metadata(state)["clarification_id"]
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "Deadlift (Barbell)"}).status_code in {400, 422}
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "9"}).status_code == 400
    assert port.calls == []
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "1"}).status_code == 200


def test_expired_and_unknown_api_requests_no_execution():
    flow, _, port, *_ = setup_flow()
    now = [0]
    flow.clarification_store = ClarificationStore(clock=lambda: now[0], ttl_seconds=10)
    identifier = metadata(flow.invoke("내 해머컬 기록 보여줘."))["clarification_id"]
    now[0] = 11
    api = client(flow)
    assert api.post("/query/clarify", json={"clarification_id": identifier, "selected_option": "1"}).status_code == 410
    assert api.post("/query/clarify", json={"clarification_id": "missing", "selected_option": "1"}).status_code == 404
    assert port.calls == []


def test_catalog_change_before_resume_rejected():
    flow, _, port, *_ = setup_flow()
    identifier = metadata(flow.invoke("내 해머컬 기록 보여줘."))["clarification_id"]
    flow.interpreter.exercise_catalog.repository.names.remove(NAMES[1])
    with pytest.raises(ClarificationError, match="candidate_no_longer_available"):
        flow.clarify(identifier, "2")
    assert port.calls == []


def test_single_use_store_is_atomic():
    flow, _, _, *_ = setup_flow()
    identifier = metadata(flow.invoke("내 해머컬 기록 보여줘."))["clarification_id"]
    def consume(_):
        try:
            flow.clarification_store.consume(identifier, "1")
            return True
        except ClarificationError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(consume, range(2))) == 1


def test_store_capacity_fail_closed():
    flow, _, port, *_ = setup_flow()
    flow.clarification_store = ClarificationStore(capacity=1)
    flow.invoke("내 해머컬 기록 보여줘.")
    result = flow.invoke("내 해머컬 기록 보여줘.")
    assert result["final_status"] == "execution_failure" and port.calls == []


def test_llm_candidate_not_in_catalog_rejected():
    flow, provider, port, *_ = setup_flow(candidate_exercises=["Cosmic Lift"])
    assert flow.invoke("내 해머컬 기록 보여줘.")["final_status"] == "execution_failure"
    assert port.calls == []


def test_dynamic_schema_candidate_list_is_catalog_enum():
    schema = candidate_schema(tuple(NAMES))
    value = draft(candidate_exercises=["Cosmic Lift"])
    with pytest.raises(ValueError):
        schema.model_validate(value)


def test_explicit_equipment_not_overridden_by_narrow_model_mention():
    flow, _, port, *_ = setup_flow("해머컬")
    result = flow.invoke("내 덤벨 해머컬 기록 보여줘.")
    assert result["final_status"] == "answer_ready"
    assert [x.canonical_exercise_name for x in port.calls] == [NAMES[1]]


def test_joint_choice_does_not_repeat_exercise_selection_for_missing_period():
    flow, provider, port, *_ = setup_flow(time_condition={"scope": "unresolved", "start_date": None,
                                                        "end_date": None, "source_text": "요즘"})
    first = flow.invoke("내 요즘 해머컬 기록 보여줘.")
    result = flow.clarify(metadata(first)["clarification_id"], "3")
    assert metadata(result).get("clarification_id") is None
    assert port.calls == [] and len(provider.calls) == 1


def test_known_exercise_cannot_offer_unrelated_catalog_candidates():
    flow, provider, port, *_ = setup_flow("데드리프트", candidate_exercises=["Deadlift (Barbell)", "Hammer Curl"])
    assert flow.invoke("내 데드리프트 기록 좀 확인해줘.")["final_status"] == "execution_failure"
    assert port.calls == []


def test_legacy_workflow_clarify_unavailable_without_execution():
    api = client(MagicMock(spec=["invoke"]))
    assert api.post("/query/clarify", json={"clarification_id": "x", "selected_option": "1"}).status_code == 409


def test_api_response_metadata_excludes_internal_provider_trace():
    flow, provider, *_ = setup_flow()
    result = client(flow).post("/query", json={"question": "내 해머컬 기록 보여줘."}).json()
    assert "interpretation_trace" not in result
    assert "provider" not in result["response_metadata"]
    assert "canonical_catalog_sha256" not in result["response_metadata"]


def test_single_model_candidate_with_unresolved_status_cannot_force_execution():
    flow, provider, port, *_ = setup_flow("데드리프트", candidate_exercises=["Deadlift (Barbell)"])
    result = flow.invoke("내 데드리프트 기록 좀 확인해줘.")
    assert result["interpretation_status"] == "clarification_required"
    assert port.calls == []
