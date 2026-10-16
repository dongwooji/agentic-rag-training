"""New synthetic Phase A argument cases; no evaluation/Gold/DB/LLM access."""
from datetime import date, datetime
from unittest.mock import Mock

import pytest

from src.agent.executor import DeterministicToolExecutor
from src.graph.interpretation_binding import bind_interpretation
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult
from src.routing.interpretation_router import InterpretationRouter
from src.tools.metric import MetricTool
from src.tools.training_log import TrainingLogTool
from graph_test_support import build_workflow
from test_phase_a_semantics import SyntheticFinalLayer, api_for, payload
from test_question_interpretation import attach


LIFT = "Synthetic Arc (Cable)"
OTHER = "Synthetic Arc (Machine)"
PLAIN = "Synthetic Unbracketed Lift"


class Repository:
    def __init__(self):
        self.calls = []

    def list_canonical_exercises(self):
        return [LIFT, OTHER, PLAIN, "Deadlift (Barbell)"]

    def resolve_canonical_exercise(self, name):
        return name if name in self.list_canonical_exercises() else None

    def record_date_bounds(self):
        return date(2032, 1, 1), date(2032, 10, 30)

    def list_sessions(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["canonical_name"] is None
        # The existing production list_sessions shape, including exercise_names.
        return [dict(session_id=f"synthetic-{i}", started_at=datetime(2032, 10, day, 10),
                     exercise_names=[LIFT if i != 1 else OTHER])
                for i, day in enumerate([3, 8, 22])]


def draft(question, **updates):
    source = "training gap" if "training gap" in question else "훈련 공백"
    raw = payload(question, operation="training_gap", exercise_mention=None,
        canonical_exercise_name=None, candidate_exercises=[], exercise_resolution_status="not_found",
        record_operation=None)
    raw["requested_analyses"][0]["source_text"] = source
    raw.update(updates)
    return raw


def interpreter(raw):
    repo = Repository()
    provider = Mock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=raw)
    return QuestionInterpreter(exercise_repository=repo, provider=provider, semantics_enabled=True), provider, repo


@pytest.mark.parametrize("record_op", [None, "list_sessions", "exercise_records"])
@pytest.mark.parametrize("exercise_issues", [False, True])
def test_global_gap_does_not_require_an_exercise(record_op, exercise_issues):
    question = "보관된 모든 세션에서 training gap의 최댓값을 산출해주세요."
    raw = draft(question, record_operation=record_op)
    if exercise_issues:
        raw.update(unresolved_fields=[dict(field=f, reason="합성 운동 조건 없음") for f in (
            "canonical_exercise_name", "candidate_exercises", "exercise_resolution_status", "exercise")],
            clarification_required=True)
    component, provider, _ = interpreter(raw)
    result = component.interpret(question)
    assert result.execution_status == "ready"
    assert result.interpretation.unresolved_fields == []
    assert result.interpretation.canonical_exercise_name is None
    assert "global_training_gap_v1" in result.interpretation.applied_policies
    assert result.phase_a_semantics.evidence_need.required_sources == ["training_records"]
    arguments = bind_interpretation(result.interpretation, InterpretationRouter().route(result.interpretation))
    assert arguments["query_training_log"]["operation"] == "list_sessions"
    assert arguments["compute_metrics"]["operation"] == "training_gap"
    assert all(value["canonical_exercise_name"] is None for value in arguments.values())
    provider.invoke.assert_called_once()


def test_recent_global_gap_reuses_existing_record_anchored_window():
    question = "요즘 보관 세션의 training gap 값을 정리해주세요."
    raw = draft(question)
    raw["time_condition"].update(scope="unresolved", source_text="요즘")
    component, _, _ = interpreter(raw)
    result = component.interpret(question)
    assert result.execution_status == "ready"
    window = result.phase_a_semantics.default_time_window
    assert window.applied and window.recent_window_weeks == 8
    assert window.anchor_date == window.end_date == "2032-10-30"
    assert window.start_date == "2032-09-05"
    assert result.phase_a_semantics.argument_resolution.unresolved_fields == []


def test_global_gap_executes_existing_record_and_metric_tools_over_all_sessions():
    question = "보관 세션 전체의 training gap을 큰 값부터 정리해주세요."
    component, provider, repo = interpreter(draft(question))
    legacy, *ports = build_workflow(literature_responses=[], grader_results=[],
                                   recovery_payloads=[], final_response_layer=SyntheticFinalLayer())
    legacy.nodes.tool_executor = DeterministicToolExecutor(training_log_tool=TrainingLogTool(repo),
        metric_tool=MetricTool(), literature_tool=ports[2])
    graph = attach(legacy, component)
    state = graph.invoke(question)
    assert state["final_status"] == "answer_ready"
    assert [x["requested_operation"] for x in state["tool_results"]] == ["list_sessions", "training_gap"]
    assert all(x["status"] == "success" for x in state["tool_results"])
    result = state["tool_results"][1]["result"]
    assert result["session_count"] == 3 and result["gap_count"] == 2
    assert [x["gap_days"] for x in result["gaps"]] == [14.0, 5.0]
    assert len(repo.calls) == 1 and repo.calls[0]["canonical_name"] is None
    assert all(not p.calls for p in ports)
    provider.invoke.assert_called_once()


def test_global_gap_public_response_does_not_ask_for_exercise():
    question = "내 훈련 공백의 최댓값을 세션 목록 전체에서 산출해주세요."
    component, _, _ = interpreter(draft(question))
    legacy, training, metric, *unused = build_workflow(literature_responses=[], grader_results=[],
        recovery_payloads=[], final_response_layer=SyntheticFinalLayer())
    with api_for(attach(legacy, component)) as api:
        response = api.post("/query", json=dict(question=question, debug=True))
    assert response.status_code == 200
    body = response.json()
    assert body["final_status"] == "answer_ready"
    trace = body["response_metadata"]["trace"]["interpreter"]["phase_a"]
    assert trace["argument_resolution"] == dict(status="ready", unresolved_fields=[])
    assert training.calls[0].canonical_exercise_name is None
    assert metric.calls[0].canonical_exercise_name is None
    assert all(not p.calls for p in unused)


def test_explicit_exercise_gap_keeps_canonical_binding():
    question = f"{LIFT} 수행 세션의 training gap을 정리해주세요."
    raw = draft(question, exercise_mention=LIFT, canonical_exercise_name=LIFT,
        candidate_exercises=[LIFT], exercise_resolution_status="resolved")
    component, _, _ = interpreter(raw)
    result = component.interpret(question)
    assert result.execution_status == "ready"
    assert "global_training_gap_v1" not in result.interpretation.applied_policies
    assert result.interpretation.canonical_exercise_name == LIFT
    args = bind_interpretation(result.interpretation, InterpretationRouter().route(result.interpretation))
    assert all(value["canonical_exercise_name"] == LIFT for value in args.values())


@pytest.mark.parametrize("mention", ["Synthetic Arc", "Synthetic Unlisted Lift"])
def test_ambiguous_or_unknown_exercise_gap_still_requires_resolution(mention):
    question = f"{mention}의 training gap을 확인해주세요."
    candidates = [LIFT, OTHER] if mention == "Synthetic Arc" else []
    component, _, _ = interpreter(draft(question, exercise_mention=mention,
        candidate_exercises=candidates, exercise_resolution_status="ambiguous" if candidates else "not_found"))
    result = component.interpret(question)
    assert result.execution_status == "clarification_required"
    assert "canonical_exercise_name" in [x.field for x in result.interpretation.unresolved_fields]
    assert InterpretationRouter().route(result.interpretation).selected_tools == ()


@pytest.mark.parametrize("mentioned", [LIFT, PLAIN, "데드리프트", "덤벨 데드리프트", "시티드 로우"])
def test_omitted_llm_exercise_field_does_not_broaden_specific_question(mentioned):
    question = f"{mentioned} 수행 기록의 training gap을 확인해주세요."
    component, _, _ = interpreter(draft(question))
    result = component.interpret(question)
    assert result.execution_status == "clarification_required"
    assert "canonical_exercise_name" in [x.field for x in result.interpretation.unresolved_fields]
    assert "global_training_gap_v1" not in result.interpretation.applied_policies


def test_global_gap_preserves_missing_period_and_other_arguments():
    question = "보관 세션의 training gap을 기간별로 비교해주세요."
    raw = draft(question, unresolved_fields=[dict(field="canonical_exercise_name", reason="불필요 운동"),
        dict(field="comparison", reason="별도 미확정 조건")])
    raw["time_condition"].update(scope="unresolved")
    component, _, _ = interpreter(raw)
    result = component.interpret(question)
    assert result.execution_status == "clarification_required"
    assert {x.field for x in result.interpretation.unresolved_fields} == {"comparison", "time_condition"}


@pytest.mark.parametrize("invalid", ["catalog", "numeric", "span"])
def test_global_gap_does_not_bypass_grounding(invalid):
    question = "보관 세션의 training gap을 산출해주세요."
    raw = draft(question)
    if invalid == "catalog":
        raw.update(exercise_mention="세션", candidate_exercises=["Synthetic Noncatalog Exercise"])
    elif invalid == "numeric":
        raw["requested_analyses"][0]["n_sessions"] = 9
    else:
        raw["requested_analyses"][0]["source_text"] = "not present"
    component, _, _ = interpreter(raw)
    result = component.interpret(question)
    assert result.execution_status == "failure"
    assert result.error_code == "ungrounded_interpretation"


def test_other_and_multiple_metrics_keep_existing_exercise_requirement():
    question = "세션 목록의 weekly volume과 training gap을 확인해주세요."
    raw = draft(question)
    raw["requested_analyses"].append(dict(operation="weekly_volume", n_sessions=None, source_text="weekly volume"))
    component, _, _ = interpreter(raw)
    result = component.interpret(question)
    assert result.execution_status == "clarification_required"
    assert {x.field for x in result.interpretation.unresolved_fields} >= {"canonical_exercise_name", "requested_analyses"}
    raw["requested_analyses"] = raw["requested_analyses"][1:]
    component, _, _ = interpreter(raw)
    assert "canonical_exercise_name" in [x.field for x in component.interpret(question).interpretation.unresolved_fields]
