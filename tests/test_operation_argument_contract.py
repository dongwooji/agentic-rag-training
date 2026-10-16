"""Post-hoc correctness cases, newly synthetic; never frozen acceptance data."""
from copy import deepcopy
from datetime import date, datetime
from unittest.mock import Mock

import pytest

from src.agent.executor import DeterministicToolExecutor
from src.graph.interpretation_binding import bind_interpretation
from src.interpretation.contracts import InterpretationDraft
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.operation_arguments import load_contract, required_user_arguments
from src.interpretation.provider import InterpreterProviderResult
from src.routing.interpretation_router import InterpretationRouter
from src.tools.metric import MetricOperation, MetricTool
from src.tools.training_log import TrainingLogOperation, TrainingLogTool
from graph_test_support import build_workflow
from test_question_interpretation import attach
from test_phase_a_semantics import SyntheticFinalLayer, api_for


LIFT = "Synthetic Flare (Cable)"
OTHER = "Synthetic Flare (Machine)"


class Repository:
    def list_canonical_exercises(self):
        return [LIFT, OTHER]

    def resolve_canonical_exercise(self, name):
        return name if name in self.list_canonical_exercises() else None

    def record_date_bounds(self):
        return date(2034, 1, 1), date(2034, 11, 22)

    def list_sessions(self, **kwargs):
        assert kwargs["canonical_name"] in {LIFT, None}
        return [dict(session_id=f"synthetic-{day}", started_at=datetime(2034, 5, day, 9),
                     exercise_names=[LIFT]) for day in (1, 11, 25)]


def draft(question, operation="weekly_frequency", **updates):
    raw = dict(personal_record_requested=True, literature_requested=False,
        exercise_mention=LIFT, canonical_exercise_name=LIFT, candidate_exercises=[LIFT],
        exercise_resolution_status="resolved", record_operation="exercise_records", session_id=None,
        time_condition=dict(scope="all_records", start_date=None, end_date=None, source_text=""),
        requested_analyses=[] if operation is None else [dict(operation=operation, n_sessions=None, source_text=question)],
        literature_subquestion=None, user_assumptions=[], unresolved_fields=[], clarification_required=False,
        evidence_need=dict(answer_type="descriptive", training_records_needed=True,
            literature_needed_for_answer=False, explicit_literature_request="none", source_spans=[question],
            purpose_unresolved=False, requires_current_window=False, out_of_scope_reason=None))
    raw.update(updates)
    return raw


def run(question, raw):
    provider = Mock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=deepcopy(raw))
    component = QuestionInterpreter(exercise_repository=Repository(), provider=provider, semantics_enabled=True)
    result = component.interpret(question)
    provider.invoke.assert_called_once()
    return result


def issue(field):
    return dict(field=field, reason="Synthetic proposed input is unavailable")


def test_config_covers_all_existing_operations_without_enabling_new_ones():
    config = load_contract()
    assert set(config["record_operations"]) == {x.value for x in TrainingLogOperation}
    assert set(config["metric_operations"]) == {x.value for x in MetricOperation}
    assert config["automatically_supplied_arguments"] == ["records_source"]
    assert config["record_operations"]["get_session"]["required_arguments"] == ["session_id"]


@pytest.mark.parametrize("operation,expected", [
    ("estimated_1rm", {"canonical_exercise_name"}),
    ("first_last_n_session_median_e1rm", {"canonical_exercise_name", "n_sessions"}),
    ("weekly_volume", {"canonical_exercise_name"}), ("weekly_frequency", set()),
    ("training_gap", set()), ("plateau_candidates", {"canonical_exercise_name"})])
def test_required_arguments_follow_upstream_binding(operation, expected):
    raw = draft("Synthetic execution contract", operation)
    parsed = InterpretationDraft.model_validate({k: v for k, v in raw.items() if k != "evidence_need"})
    assert required_user_arguments(parsed, load_contract()) == expected


@pytest.mark.parametrize("operation,expected", [
    ("exercise_records", {"canonical_exercise_name"}),
    ("exercise_first_last", {"canonical_exercise_name"}),
    ("get_session", {"session_id"}), ("list_sessions", set())])
def test_record_operation_contract(operation, expected):
    raw = draft("Synthetic record contract", None, record_operation=operation)
    parsed = InterpretationDraft.model_validate({k: v for k, v in raw.items() if k != "evidence_need"})
    assert required_user_arguments(parsed, load_contract()) == expected


@pytest.mark.parametrize("field", ["n_sessions", "current_set_count", "increase_in_sets_amount", "records_source"])
def test_unused_or_tool_derived_or_answer_output_is_not_missing_input(field):
    question = f"{LIFT}의 weekly frequency를 집계하고 세트 증량을 제안해주세요."
    raw = draft(question, unresolved_fields=[issue(field)], clarification_required=True)
    raw["evidence_need"].update(answer_type="advisory", literature_needed_for_answer=True)
    raw["literature_subquestion"] = "훈련 빈도와 수행량 조절에 관한 일반 근거"
    result = run(question, raw)
    assert result.execution_status == "ready"
    assert result.interpretation.unresolved_fields == []
    diagnostic = result.interpretation.argument_contract_normalization
    assert diagnostic.ignored_llm_fields == [field]
    assert result.phase_a_semantics.evidence_need.required_sources == ["training_records", "literature"]
    args = bind_interpretation(result.interpretation, InterpretationRouter().route(result.interpretation))
    assert args["query_training_log"]["operation"] == "list_sessions"
    assert args["compute_metrics"]["operation"] == "weekly_frequency"


@pytest.mark.parametrize("operation", ["training_gap", "weekly_frequency"])
def test_session_operations_can_use_global_records_without_llm_exercise_input(operation):
    question = f"보관된 모든 기록의 {operation.replace('_', ' ')} 집계를 요청합니다."
    raw = draft(question, operation, exercise_mention=None, canonical_exercise_name=None,
        candidate_exercises=[], exercise_resolution_status="not_found", record_operation=None,
        unresolved_fields=[issue("exercise"), issue("n_sessions")], clarification_required=True)
    result = run(question, raw)
    assert result.execution_status == "ready"
    assert result.interpretation.canonical_exercise_name is None
    assert result.interpretation.argument_contract_normalization.required_user_arguments == []


@pytest.mark.parametrize("operation", ["training_gap", "weekly_frequency", "weekly_volume"])
def test_ambiguous_explicit_exercise_is_never_widened_to_all_records(operation):
    question = f"Synthetic Flare의 {operation.replace('_', ' ')}를 집계해주세요."
    raw = draft(question, operation, exercise_mention="Synthetic Flare", canonical_exercise_name=None,
        candidate_exercises=[LIFT, OTHER], exercise_resolution_status="ambiguous")
    result = run(question, raw)
    assert result.execution_status == "clarification_required"
    assert "canonical_exercise_name" in result.phase_a_semantics.argument_resolution.unresolved_fields
    assert not InterpretationRouter().route(result.interpretation).selected_tools


@pytest.mark.parametrize("operation", ["training_gap", "weekly_frequency"])
def test_omitted_exercise_extraction_does_not_drop_explicit_catalog_constraint(operation):
    question = f"{LIFT}의 {operation.replace('_', ' ')}를 정리해주세요."
    raw = draft(question, operation, exercise_mention=None, canonical_exercise_name=None,
                candidate_exercises=[], exercise_resolution_status="not_found")
    result = run(question, raw)
    assert result.execution_status == "clarification_required"
    assert "canonical_exercise_name" in result.phase_a_semantics.argument_resolution.unresolved_fields


def test_actual_n_requirement_is_not_replaced_by_tool_default():
    question = f"{LIFT}의 첫 세션과 마지막 세션 median e1RM을 비교해주세요."
    result = run(question, draft(question, "first_last_n_session_median_e1rm"))
    assert result.execution_status == "clarification_required"
    assert "n_sessions" in result.phase_a_semantics.argument_resolution.unresolved_fields


def test_explicit_n_constraint_is_not_ignored_for_an_operation_without_n():
    question = f"{LIFT}의 마지막 4세션만 사용해 weekly frequency를 집계해주세요."
    result = run(question, draft(question, unresolved_fields=[issue("n_sessions")]))
    assert result.execution_status == "clarification_required"
    assert "n_sessions" in result.phase_a_semantics.argument_resolution.unresolved_fields


@pytest.mark.parametrize("period", ["2034년 4월", "2034-02-30"])
def test_explicit_dates_still_normalize_or_clarify(period):
    question = f"{period} {LIFT}의 weekly frequency를 집계해주세요."
    raw = draft(question)
    result = run(question, raw)
    if period.endswith("30"):
        assert result.execution_status == "clarification_required"
        assert result.interpretation.date_validation.check_id == "invalid_calendar_date"
    else:
        assert result.execution_status == "ready"
        assert result.interpretation.time_condition.start_date == "2034-04-01"
        assert result.interpretation.time_condition.end_date == "2034-04-30"


def test_invented_date_is_still_a_date_clarification():
    question = f"{LIFT}의 weekly frequency를 집계해주세요."
    raw = draft(question, time_condition=dict(scope="explicit", start_date="2034-03-01",
        end_date="2034-03-31", source_text=""))
    result = run(question, raw)
    assert result.execution_status == "clarification_required"
    assert result.interpretation.date_validation.check_id == "date_grounding"


def test_stagnation_word_does_not_force_a_metric():
    question = f"{LIFT}의 추정 1RM 추세로 정체 현상을 살펴봐주세요."
    result = run(question, draft(question, "estimated_1rm"))
    assert result.execution_status == "ready"
    assert [x.operation.value for x in result.interpretation.requested_analyses] == ["estimated_1rm"]


def test_plateau_metric_remains_supported_without_inventing_opposite_mapping():
    question = f"{LIFT}의 plateau candidate 구간을 조회해주세요."
    result = run(question, draft(question, "plateau_candidates"))
    assert result.execution_status == "ready"
    assert result.interpretation.requested_analyses[0].operation.value == "plateau_candidates"


def test_explicitly_named_plateau_operation_is_still_preserved():
    question = f"{LIFT}의 plateau candidate 구간을 조회해주세요."
    result = run(question, draft(question, "estimated_1rm"))
    assert result.execution_status == "clarification_required"
    assert "requested_analyses" in result.phase_a_semantics.argument_resolution.unresolved_fields


def test_stagnation_word_alone_does_not_force_estimated_1rm_either():
    question = f"{LIFT}의 주간 빈도와 정체 현상의 관계를 설명해주세요."
    result = run(question, draft(question, "weekly_frequency"))
    assert result.execution_status == "ready"
    assert result.interpretation.requested_analyses[0].operation.value == "weekly_frequency"


def test_grounded_n_value_resolves_a_stale_llm_n_issue():
    question = f"{LIFT}의 첫 2세션과 마지막 2세션 median e1RM을 비교해주세요."
    raw = draft(question, "first_last_n_session_median_e1rm",
                unresolved_fields=[issue("n_sessions")], clarification_required=True)
    raw["requested_analyses"][0]["n_sessions"] = 2
    result = run(question, raw)
    assert result.execution_status == "ready"
    assert result.interpretation.requested_analyses[0].n_sessions == 2


def test_unused_comparison_without_original_constraint_is_not_blocking():
    question = f"{LIFT}의 weekly frequency 수치를 나열해주세요."
    result = run(question, draft(question, unresolved_fields=[issue("comparison")], clarification_required=True))
    assert result.execution_status == "ready"
    assert result.interpretation.argument_contract_normalization.ignored_llm_fields == ["comparison"]


def test_explicit_comparison_constraint_is_preserved():
    question = f"{LIFT}의 weekly frequency를 다른 구간과 비교해주세요."
    result = run(question, draft(question, unresolved_fields=[issue("comparison")], clarification_required=True))
    assert result.execution_status == "clarification_required"
    assert "comparison" in result.phase_a_semantics.argument_resolution.unresolved_fields


def test_actual_multiple_metrics_are_still_blocked():
    question = f"{LIFT}의 weekly volume과 weekly frequency를 함께 집계해주세요."
    raw = draft(question, "weekly_volume")
    raw["requested_analyses"].append(dict(operation="weekly_frequency", n_sessions=None, source_text="weekly frequency"))
    result = run(question, raw)
    assert result.execution_status == "clarification_required"
    assert "requested_analyses" in result.phase_a_semantics.argument_resolution.unresolved_fields


@pytest.mark.parametrize("kind", ["catalog", "number", "source_span"])
def test_existing_grounding_failures_are_not_filtered_away(kind):
    question = f"{LIFT}의 weekly frequency를 집계해주세요."
    raw = draft(question)
    if kind == "catalog":
        raw["candidate_exercises"] = ["Synthetic Catalog Missing"]
    elif kind == "number":
        raw["requested_analyses"][0]["n_sessions"] = 7
    else:
        raw["requested_analyses"][0]["source_text"] = "Synthetic absent source"
    result = run(question, raw)
    assert result.execution_status == "failure"
    assert result.error_code == "ungrounded_interpretation"


def test_specific_gap_still_executes_after_irrelevant_argument_is_removed():
    question = f"{LIFT}의 training gap을 산출해주세요."
    raw = draft(question, "training_gap", unresolved_fields=[issue("n_sessions")], clarification_required=True)
    provider = Mock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=raw)
    interpreter = QuestionInterpreter(exercise_repository=Repository(), provider=provider, semantics_enabled=True)
    legacy, *ports = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[],
                                   final_response_layer=SyntheticFinalLayer())
    class ObservedMetric(MetricTool):
        def execute(self, request):
            self.response = super().execute(request)
            return self.response

    metric = ObservedMetric()
    legacy.nodes.tool_executor = DeterministicToolExecutor(training_log_tool=TrainingLogTool(Repository()),
        metric_tool=metric, literature_tool=ports[2])
    with api_for(attach(legacy, interpreter)) as api:
        response = api.post("/query", json=dict(question=question, debug=True))
    assert response.status_code == 200
    body = response.json()
    assert body["final_status"] == "answer_ready"
    assert metric.response.result["session_count"] == 3
    assert [g["gap_days"] for g in metric.response.result["gaps"]] == [14.0, 10.0]
    provider.invoke.assert_called_once()
