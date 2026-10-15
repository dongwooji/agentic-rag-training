"""New synthetic contracts, never acceptance/evaluation inputs or live providers."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.dependencies import get_workflow, RuntimeWorkflow
from src.api.debug_trace import build_debug_trace
from src.interpretation.contracts import SemanticInterpretationDraft
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult, OpenAIQuestionInterpreterProvider
from src.routing.interpretation_router import InterpretationRouter
from src.answer.contracts import FinalResponse, FinalResponseProvenance, ChannelMode
from graph_test_support import build_workflow, literature_hit
from test_question_interpretation import attach

LIFT = "Synthetic Arc (Cable)"
OTHER = "Synthetic Arc (Machine)"
PAIRS = json.loads((Path(__file__).parent / "fixtures/phase_a_semantics_synthetic_v1.json").read_text(encoding="utf-8"))["pairs"]


class Repository:
    def __init__(self, bounds=(date(2032, 1, 1), date(2032, 10, 30))):
        self.bounds = bounds
        self.bounds_calls = 0

    def list_canonical_exercises(self):
        return [LIFT, OTHER, "Deadlift (Barbell)"]

    def resolve_canonical_exercise(self, name):
        return name if name in self.list_canonical_exercises() else None

    def record_date_bounds(self):
        self.bounds_calls += 1
        return self.bounds


def payload(question, *, operation=None, kind="descriptive", personal=True, needed=False, explicit="none", **updates):
    value = dict(personal_record_requested=personal, literature_requested=needed or explicit != "none",
        exercise_mention=LIFT if personal else None, canonical_exercise_name=LIFT if personal else None,
        exercise_resolution_status="resolved" if personal else None, candidate_exercises=[LIFT] if personal else [],
        time_condition=dict(scope="all_records", start_date=None, end_date=None, source_text=""),
        record_operation="exercise_records" if personal else None, session_id=None,
        requested_analyses=[dict(operation=operation, n_sessions=None, source_text=question)] if operation else [],
        literature_subquestion="저항훈련의 수행량과 적응에 관한 일반 과학 근거" if needed or explicit != "none" else None,
        user_assumptions=[], unresolved_fields=[], clarification_required=False,
        evidence_need=dict(answer_type=kind, training_records_needed=personal, literature_needed_for_answer=needed,
            explicit_literature_request=explicit, source_spans=[question], purpose_unresolved=kind is None,
            requires_current_window=False, out_of_scope_reason="unsupported_domain" if kind == "out_of_scope" else None))
    value.update(updates)
    return value


def component(value, *, repo=None, error=None):
    provider = MagicMock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=deepcopy(value), error=error, api_requests=1)
    return QuestionInterpreter(exercise_repository=repo or Repository(), provider=provider, semantics_enabled=True), provider


@pytest.mark.parametrize("pair", PAIRS, ids=lambda x: x["id"])
@pytest.mark.parametrize("judgment", [False, True], ids=["descriptive", "judgment"])
def test_frozen_new_synthetic_pairs_select_channels_independently(pair, judgment):
    q = pair["judgment" if judgment else "descriptive"]
    raw = payload(q, operation=pair["operation"], kind=pair["type"] if judgment else "descriptive", needed=judgment)
    c, provider = component(raw)
    result = c.interpret(q)
    assert result.execution_status == "ready"
    need = result.phase_a_semantics.evidence_need
    assert need.required_sources == (["training_records", "literature"] if judgment else ["training_records"])
    route = InterpretationRouter().route(result.interpretation)
    assert route.route == ("hybrid" if judgment else "log_metric")
    assert provider.invoke.call_count == 1


@pytest.mark.parametrize("explicit,needed,required,optional", [
    ("none", False, ["training_records"], []),
    ("none", True, ["training_records", "literature"], []),
    ("optional", False, ["training_records"], ["literature"]),
    ("optional", True, ["training_records", "literature"], []),
    ("required", False, ["training_records", "literature"], []),
])
def test_code_derives_required_optional_and_compatibility_boolean(explicit, needed, required, optional):
    q = f"{LIFT} 항목을 확인하고 부가 설명을 해주세요."
    raw = payload(q, explicit=explicit, needed=needed)
    raw["literature_requested"] = False
    c, _ = component(raw)
    result = c.interpret(q)
    assert result.execution_status == "ready"
    assert result.phase_a_semantics.evidence_need.required_sources == required
    assert result.phase_a_semantics.evidence_need.optional_sources == optional
    assert result.interpretation.literature_requested == (needed or explicit != "none")


@pytest.mark.parametrize("field", ["canonical_exercise_name", "n_sessions", "time_condition", "requested_analyses"])
def test_known_purpose_preserved_for_argument_clarification(field):
    q = f"{LIFT} 수행량을 평가해주세요."
    raw = payload(q, kind="evaluative", needed=True, unresolved_fields=[dict(field=field, reason="합성 미확정 인자"),
        dict(field="question", reason="부당한 목적 확인")], clarification_required=True)
    if field == "canonical_exercise_name":
        raw.update(exercise_mention="Synthetic Arc", candidate_exercises=[LIFT, OTHER], canonical_exercise_name=None, exercise_resolution_status="ambiguous")
    if field == "time_condition":
        raw["time_condition"]["scope"] = "unresolved"
    c, _ = component(raw)
    result = c.interpret(q)
    assert result.execution_status == "clarification_required"
    assert result.phase_a_semantics.evidence_need.answer_type == "evaluative"
    assert result.phase_a_semantics.evidence_need.status == "resolved"
    assert result.phase_a_semantics.evidence_need.required_sources == ["training_records", "literature"]
    assert field in result.phase_a_semantics.argument_resolution.unresolved_fields
    assert "question" not in [x.field for x in result.interpretation.unresolved_fields]


def test_unknown_purpose_is_separate_from_missing_arguments():
    q = "어떤 내용을 확인할지 아직 정하지 못했어요."
    c, _ = component(payload(q, kind=None, personal=False))
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert r.phase_a_semantics.evidence_need.status == "clarification_required"
    assert r.phase_a_semantics.argument_resolution.status == "not_started"
    assert r.phase_a_semantics.scope.out_of_scope is None


@pytest.mark.parametrize("declared_unresolved", [False, True])
def test_known_type_but_unknown_core_evidence_need_is_purpose_clarification(declared_unresolved):
    q = "확인하려는 대상은 아직 정하지 못했지만 값을 설명받고 싶어요."
    raw = payload(q, kind="descriptive", personal=False)
    raw["evidence_need"]["purpose_unresolved"] = declared_unresolved
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert r.phase_a_semantics.evidence_need.answer_type == "descriptive"
    assert r.phase_a_semantics.evidence_need.status == "clarification_required"
    assert r.phase_a_semantics.argument_resolution.status == "not_started"


@pytest.mark.parametrize("kind,personal", [("general", False), ("evaluative", True), ("causal", True), ("advisory", True)])
def test_answer_type_alone_does_not_choose_sources(kind, personal):
    q = f"{LIFT}에 관한 요청을 설명해주세요."
    c, _ = component(payload(q, kind=kind, personal=personal, needed=not personal))
    r = c.interpret(q)
    assert r.execution_status == "ready"
    assert r.phase_a_semantics.evidence_need.required_sources == (["training_records"] if personal else ["literature"])


def test_record_need_survives_missing_operation_and_false_personal_flag():
    q = f"{LIFT}의 보관 항목을 확인해주세요."
    raw = payload(q, record_operation=None, personal_record_requested=False)
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.interpretation.personal_record_requested is True
    assert r.interpretation.personal_record_normalization.mismatch
    assert r.phase_a_semantics.evidence_need.required_sources == ["training_records"]
    assert r.phase_a_semantics.argument_resolution.unresolved_fields == ["record_operation"]


def test_existing_personal_signal_also_preserves_need_before_recent_resolution():
    q = "내 최근 Synthetic Arc (Cable) 항목을 확인해주세요."
    raw = payload(q, personal_record_requested=False, record_operation=None)
    raw["evidence_need"]["training_records_needed"] = False
    raw["time_condition"].update(scope="unresolved", source_text="최근")
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert r.phase_a_semantics.evidence_need.required_sources == ["training_records"]
    assert r.phase_a_semantics.default_time_window.applied
    assert r.phase_a_semantics.argument_resolution.unresolved_fields == ["record_operation"]


@pytest.mark.parametrize("text", ["요즘", "최근", "recent"])
def test_recent_dates_computed_by_code_from_last_record(text):
    q = f"{text} {LIFT}의 weekly volume 관측값을 정리해주세요."
    raw = payload(q, operation="weekly_volume")
    raw["time_condition"].update(scope="unresolved", source_text=text)
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "ready"
    window = r.phase_a_semantics.default_time_window
    assert window.applied and window.trigger == "relative_recent"
    assert window.anchor_date == window.end_date == "2032-10-30"
    assert window.start_date == "2032-09-05"
    assert r.interpretation.time_condition.start_date == window.start_date


@pytest.mark.parametrize("operation,applied", [("weekly_volume", True), ("weekly_frequency", True),
    ("estimated_1rm", True), ("plateau_candidates", True), ("training_gap", False)])
def test_only_approved_operations_get_current_assessment_default(operation, applied):
    q = f"{LIFT}의 {operation.replace('_', ' ')}를 현재 수준 관점에서 평가해주세요."
    if operation == "estimated_1rm":
        q += " e1RM"
    raw = payload(q, operation=operation, kind="evaluative", needed=True)
    raw["evidence_need"]["requires_current_window"] = True
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "ready"
    assert r.phase_a_semantics.default_time_window.applied is applied


@pytest.mark.parametrize("qualifier", ["전체 기록", "최고 기록", "처음", "2032년 6월"])
def test_self_contained_or_explicit_time_wins_over_recent(qualifier):
    q = f"{qualifier} {LIFT}의 weekly volume을 최근 현황과 함께 평가해주세요."
    raw = payload(q, operation="weekly_volume", kind="evaluative", needed=True)
    raw["evidence_need"]["requires_current_window"] = True
    c, _ = component(raw)
    r = c.interpret(q)
    assert not r.phase_a_semantics.default_time_window.applied


def test_first_last_n_does_not_get_recent_default():
    q = f"{LIFT}의 처음 4-session과 최근 4-session median e1RM을 비교해주세요."
    raw = payload(q, operation="first_last_n_session_median_e1rm")
    raw["requested_analyses"][0]["n_sessions"] = 4
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "ready" and not r.phase_a_semantics.default_time_window.applied


def test_recent_n_sessions_is_not_reinterpreted_as_recent_weeks():
    q = f"최근 4-session {LIFT} 항목을 확인해주세요."
    raw = payload(q, time_condition=dict(scope="unresolved", start_date=None, end_date=None, source_text="최근 4-session"))
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert not r.phase_a_semantics.default_time_window.applied


@pytest.mark.parametrize("bounds", [None, (date(2032, 1, 1), date(2032, 10, 30))])
def test_missing_bounds_and_unsupported_relative_period_are_not_invented(bounds):
    q = f"지난 분기의 {LIFT} 항목을 확인해주세요."
    raw = payload(q)
    raw["time_condition"].update(scope="unresolved", source_text="지난 분기")
    c, _ = component(raw, repo=Repository(bounds))
    r = c.interpret(q)
    assert r.execution_status == "clarification_required" and not r.phase_a_semantics.default_time_window.applied


def test_no_records_cannot_supply_recent_anchor():
    q = f"요즘 {LIFT} 항목을 확인해주세요."
    raw = payload(q)
    raw["time_condition"].update(scope="unresolved", source_text="요즘")
    c, _ = component(raw, repo=Repository(None))
    r = c.interpret(q)
    assert r.execution_status == "clarification_required" and not r.phase_a_semantics.default_time_window.applied


@pytest.mark.parametrize("kind", ["catalog", "numeric", "span", "date"])
def test_grounding_regressions_remain_blocking(kind):
    q = f"{LIFT} 항목을 확인해주세요."
    raw = payload(q)
    if kind == "catalog":
        raw.update(candidate_exercises=["Invented Variant"], canonical_exercise_name="Invented Variant")
    elif kind == "numeric":
        raw["requested_analyses"] = [dict(operation="first_last_n_session_median_e1rm", n_sessions=11, source_text=q)]
    elif kind == "span":
        raw["evidence_need"]["source_spans"] = ["not in question"]
    else:
        raw["time_condition"].update(scope="explicit", start_date="2031-01-01", end_date="2031-02-01")
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == ("clarification_required" if kind == "date" else "failure")
    if kind == "date":
        assert r.phase_a_semantics.argument_resolution.unresolved_fields == ["time_condition"]


@pytest.mark.parametrize("period", ["2033년 2월 29일", "2032년 15월", "2032년 11월~3월", "5월"])
def test_malformed_original_dates_keep_existing_date_clarification(period):
    q = f"{period} {LIFT} 항목을 확인해주세요."
    c, _ = component(payload(q))
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert r.phase_a_semantics.evidence_need.status == "resolved"
    assert not r.phase_a_semantics.default_time_window.applied
    assert r.phase_a_semantics.argument_resolution.unresolved_fields == ["time_condition"]


def test_multi_metric_still_clarifies_without_losing_evidence_need():
    q = f"{LIFT}의 weekly volume과 weekly frequency를 평가해주세요."
    raw = payload(q, operation="weekly_volume", kind="evaluative", needed=True)
    raw["requested_analyses"].append(dict(operation="weekly_frequency", n_sessions=None, source_text=q))
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert r.phase_a_semantics.evidence_need.required_sources == ["training_records", "literature"]


def graph_for(raw, *, grades=(True,), recovery=()):
    c, provider = component(raw)
    old, training, metric, literature, grader, recovery_agent = build_workflow(
        literature_responses=[[literature_hit(f"synthetic-{i}", 1)] for i in range(len(grades))],
        grader_results=list(grades), recovery_payloads=list(recovery), final_response_layer=SyntheticFinalLayer())
    return attach(old, c), provider, training, metric, literature, grader, recovery_agent


class SyntheticFinalLayer:
    def respond(self, state):
        terminal = state["final_status"]
        return FinalResponse(final_status=terminal, answer_text="합성 결과를 확인했습니다.",
            provenance=FinalResponseProvenance(graph_terminal_status=terminal, channel_mode=ChannelMode.NO_EVIDENCE))


def api_for(graph):
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: graph
    return TestClient(app)


def test_out_of_scope_abstains_without_exercise_or_period_clarification():
    q = "통신 장비 구매와 설치를 대신 결정해주세요."
    raw = payload(q, kind="out_of_scope", personal=False)
    raw["unresolved_fields"] = [dict(field="time_condition", reason="irrelevant")]
    graph, provider, *ports = graph_for(raw)
    body = api_for(graph).post("/query", json=dict(question=q, debug=True)).json()
    assert body["final_status"] == "abstain_ready" and body["tools_used"] == []
    s = body["response_metadata"]["trace"]["interpreter"]["phase_a"]
    assert s["scope"] == dict(out_of_scope=True, reason_code="unsupported_domain")
    assert s["argument_resolution"]["status"] == "not_applicable"
    assert "clarification_required" not in body["response_metadata"]
    assert all(p.calls == [] for p in ports) and provider.invoke.call_count == 1


def test_default_window_notice_trace_and_tool_arguments_match():
    q = f"요즘 {LIFT}의 weekly volume 수치를 정리해주세요."
    raw = payload(q, operation="weekly_volume")
    raw["time_condition"].update(scope="unresolved", source_text="요즘")
    graph, _, training, metric, *_ = graph_for(raw)
    state = graph.invoke(q)
    assert state["final_status"] == "answer_ready"
    window = state["question_interpretation"]["phase_a_semantics"]["default_time_window"]
    assert window["applied"]
    assert "마지막 저장 기록일(2032-10-30) 기준 최근 8주" in state["final_response"]["answer_text"]
    assert training.calls[0].start_date == metric.calls[0].start_date == window["start_date"]
    assert state["final_response"]["provenance"]["response_metadata"]["default_time_window"] == window


def test_recent_literature_does_not_narrow_timeless_personal_records():
    q = f"보관한 {LIFT} 항목을 모두 나열하고 최근 운동학 문헌의 측정 원리를 설명해주세요."
    raw = payload(q, needed=True)
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "ready"
    assert r.phase_a_semantics.evidence_need.required_sources == ["training_records", "literature"]
    assert not r.phase_a_semantics.default_time_window.applied
    assert r.interpretation.time_condition.scope == "all_records"


def test_literature_time_word_does_not_block_approved_current_assessment_window():
    q = f"{LIFT}의 weekly volume을 현재 수준 관점에서 평가하고 이번 학술지의 측정 원리를 설명해주세요."
    raw = payload(q, operation="weekly_volume", kind="evaluative", needed=True)
    raw["evidence_need"]["requires_current_window"] = True
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "ready"
    assert r.phase_a_semantics.default_time_window.applied
    assert r.phase_a_semantics.default_time_window.trigger == "operation_default"


def test_ungrounded_recent_time_span_never_creates_dates():
    q = f"{LIFT} 항목의 분석 기간을 확인해주세요."
    raw = payload(q)
    raw["time_condition"].update(scope="unresolved", source_text="최근")
    c, _ = component(raw)
    r = c.interpret(q)
    assert r.execution_status == "clarification_required"
    assert not r.phase_a_semantics.default_time_window.applied


def test_optional_literature_reuses_existing_blocking_grader_and_recovery():
    q = f"{LIFT} 항목을 정리하고 관련 자료가 있으면 덧붙여주세요."
    raw = payload(q, explicit="optional")
    graph, _, training, _, literature, grader, recovery = graph_for(raw, grades=(False, False, False),
        recovery=(dict(target_component_ids=["C1"], recovery_query="synthetic adaptation dose", preserved_terms=[]),
                  dict(target_component_ids=["C1"], recovery_query="synthetic adaptation uncertainty", preserved_terms=[])))
    state = graph.invoke(q)
    assert state["question_interpretation"]["phase_a_semantics"]["evidence_need"]["optional_sources"] == ["literature"]
    assert len(training.calls) == 1 and len(literature.calls) == len(grader.calls) == 3
    assert len(recovery.calls) == 2
    assert state["final_status"] == "abstain_ready"


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("initial_debug", [False, True])
def test_clarify_real_wrapper_preserves_need_and_updates_argument_trace(debug, initial_debug):
    q = "Synthetic Arc에서 장비가 불명확한 항목을 확인해주세요."
    raw = payload(q, exercise_mention="Synthetic Arc", canonical_exercise_name=None, exercise_resolution_status="ambiguous", candidate_exercises=[LIFT, OTHER])
    graph, provider, training, *_ = graph_for(raw)
    api = api_for(RuntimeWorkflow(workflow=graph, literature_tool=MagicMock()))
    first = api.post("/query", json=dict(question=q, debug=initial_debug)).json()
    if initial_debug:
        before = first["response_metadata"]["trace"]["interpreter"]["phase_a"]
        assert before["argument_resolution"]["status"] == "clarification_required"
    else:
        assert "trace" not in first["response_metadata"]
    body = api.post("/query/clarify", json=dict(clarification_id=first["response_metadata"]["clarification_id"],
                                               selected_option="1", debug=debug)).json()
    assert body["final_status"] == "answer_ready" and len(training.calls) == 1
    if debug:
        after = body["response_metadata"]["trace"]["interpreter"]["phase_a"]
        assert after["version"] == "phase_a_semantics_v1" and after["parsing_source"] == "llm"
        assert after["evidence_need"]["required_sources"] == ["training_records"]
        if initial_debug:
            assert after["evidence_need"] == before["evidence_need"]
        assert after["argument_resolution"] == dict(status="ready", unresolved_fields=[])
    else:
        assert "response_metadata" not in body
    assert provider.invoke.call_count == 1


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_clarify_debug_is_strict_boolean(value):
    graph, _, *_ = graph_for(payload("합성 요청"))
    assert api_for(graph).post("/query/clarify", json=dict(clarification_id="x", selected_option="1", debug=value)).status_code == 422


def test_rule_path_emits_same_semantics_without_provider():
    c, p = component(None)
    r = c.interpret("내 Deadlift (Barbell)의 기록을 보여줘.")
    assert r.execution_status == "ready" and r.phase_a_semantics.parsing_source == "rule"
    assert r.phase_a_semantics.evidence_need.required_sources == ["training_records"]
    p.invoke.assert_not_called()


def test_failed_provider_keeps_versioned_trace_without_details():
    c, _ = component(None, error="private-provider-detail")
    r = c.interpret("합성 해석 입력입니다.")
    trace = build_debug_trace(dict(interpretation_status=r.execution_status, interpretation_trace=r.model_dump(mode="json")))
    assert trace["interpreter"]["phase_a"]["parsing_source"] == "llm"
    assert trace["interpreter"]["phase_a"]["argument_resolution"]["status"] == "failure"
    assert "private-provider-detail" not in json.dumps(trace)


def test_semantic_trace_redacts_secret_spans(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "synthetic-db-secret")
    q = "synthetic-db-secret 관련 설명"
    c, _ = component(payload(q, personal=False, needed=True, kind="general"))
    r = c.interpret(q)
    trace = build_debug_trace(dict(question_interpretation=r.interpretation.model_dump(mode="json")))
    assert "synthetic-db-secret" not in json.dumps(trace)
    assert trace["interpreter"]["phase_a"]["evidence_need"]["source_spans"] == ["[REDACTED] 관련 설명"]


def test_new_provider_uses_same_single_call_with_versioned_typed_schema():
    q = "합성 일반 과학 설명"
    raw = payload(q, personal=False, needed=True, kind="general")
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=SemanticInterpretationDraft.model_validate(raw), usage=None)
    provider = OpenAIQuestionInterpreterProvider(client=client,
        config_path=Path(__file__).resolve().parents[1] / "config/question_interpreter_semantics_v1.json")
    result = provider.invoke(q, canonical_exercises=(LIFT, OTHER))
    assert result.error is None and result.api_requests == 1
    assert "evidence_need" in client.responses.parse.call_args.kwargs["text_format"].model_fields
    client.responses.parse.assert_called_once()


def test_real_service_factory_enables_semantics_only_for_phase_a(monkeypatch):
    from src.api import dependencies
    monkeypatch.setenv("PGPASSWORD", "synthetic-password")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("AGENTIC_RAG_QUESTION_INTERPRETATION", "phase_a")
    monkeypatch.delenv("AGENTIC_RAG_RETRIEVAL_CONFIG", raising=False)
    monkeypatch.setattr(dependencies, "PsycopgTrainingRepository", lambda **kwargs: Repository())
    monkeypatch.setattr(dependencies, "TrainingLogTool", MagicMock())
    monkeypatch.setattr(dependencies, "MetricTool", MagicMock())
    literature = MagicMock()
    monkeypatch.setattr(dependencies.LiteratureTool, "from_postgres", MagicMock(return_value=literature))
    for name in ("OpenAIRuntimeEvidenceProvider", "OpenAIEvidenceRecoveryProvider", "OpenAIFinalAnswerProvider", "OpenAILiteratureQueryProvider"):
        monkeypatch.setattr(dependencies, name, MagicMock())
    provider = MagicMock()
    monkeypatch.setattr(dependencies, "OpenAIQuestionInterpreterProvider", provider)
    runtime = dependencies._build_runtime_workflow()
    try:
        assert runtime._workflow.interpreter.semantics_enabled
        assert provider.call_args.kwargs["config_path"].name == "question_interpreter_semantics_v1.json"
        assert runtime._workflow.interpreter.provider is provider.return_value
        provider.return_value.invoke.assert_not_called()
    finally:
        runtime.close()
