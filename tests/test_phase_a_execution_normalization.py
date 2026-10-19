"""New synthetic post-hoc regressions; no acceptance inputs or providers."""
from copy import deepcopy

import pytest

from src.interpretation.messages import clarification_questions
from src.routing.interpretation_router import InterpretationRouter
from test_operation_argument_contract import LIFT, OTHER, draft, issue, run


def test_identical_execution_with_different_grounded_provenance_is_deduplicated():
    q = f"{LIFT}의 weekly frequency를 집계해서 빈도 표로 보여주세요."
    raw = draft(q)
    raw["requested_analyses"].append(dict(operation="weekly_frequency", n_sessions=None, source_text="빈도 표"))
    r = run(q, raw)
    assert r.execution_status == "ready"
    assert len(r.interpretation.requested_analyses) == 1
    assert "identical_metric_execution_deduplication_v1" in r.interpretation.applied_policies
    assert len(raw["requested_analyses"]) == 2


def test_duplicate_does_not_hide_ungrounded_source():
    q = f"{LIFT}의 weekly frequency를 집계해주세요."
    raw = draft(q)
    raw["requested_analyses"].append(dict(operation="weekly_frequency", n_sessions=None, source_text="absent synthetic span"))
    assert run(q, raw).error_code == "ungrounded_interpretation"


def test_same_operation_with_different_n_is_not_merged():
    q = f"{LIFT}의 처음 2세션과 최근 2세션, 처음 3세션과 최근 3세션 median e1RM을 비교해주세요."
    raw = draft(q, "first_last_n_session_median_e1rm")
    raw["requested_analyses"][0]["n_sessions"] = 2
    raw["requested_analyses"].append(dict(operation="first_last_n_session_median_e1rm", n_sessions=3, source_text=q))
    r = run(q, raw)
    assert r.execution_status == "clarification_required"
    assert [x.n_sessions for x in r.interpretation.requested_analyses] == [2, 3]


def test_real_multiple_metrics_remain_blocking():
    q = f"{LIFT}의 weekly frequency와 weekly volume을 모두 산출해주세요."
    raw = draft(q)
    raw["requested_analyses"].append(dict(operation="weekly_volume", n_sessions=None, source_text="weekly volume"))
    r = run(q, raw)
    assert r.execution_status == "clarification_required"
    assert not InterpretationRouter().route(r.interpretation).selected_tools
    assert "먼저 확인할 지표" in " ".join(clarification_questions(r.interpretation))


def test_e1rm_based_plateau_is_a_valid_single_analysis():
    q = f"2034년 7월 {LIFT}의 추정 1RM 정체 후보 구간을 표시해주세요."
    r = run(q, draft(q, "plateau_candidates"))
    assert r.execution_status == "ready"
    assert r.interpretation.requested_analyses[0].operation.value == "plateau_candidates"


def test_unrelated_metric_does_not_satisfy_explicit_e1rm_request():
    q = f"{LIFT}의 추정 1RM 추세를 보여주세요."
    assert run(q, draft(q)).execution_status == "clarification_required"


@pytest.mark.parametrize("scope", ["explicit", "unresolved", "all_records"])
def test_omitted_calendar_scope_is_resolved_from_all_record_policy(scope):
    q = f"{LIFT}의 training gap 중 최장 구간을 반환해주세요."
    raw = draft(q, "training_gap", time_condition=dict(scope=scope, start_date=None, end_date=None, source_text=""),
                unresolved_fields=[issue("time_condition")], clarification_required=True)
    r = run(q, raw)
    assert r.execution_status == "ready"
    assert r.interpretation.time_condition.scope == "all_records"
    assert not r.phase_a_semantics.default_time_window.applied


@pytest.mark.parametrize("scope", ["explicit", "unresolved"])
def test_session_comparison_needs_n_but_no_calendar_dates(scope):
    q = f"{LIFT}의 처음 4세션과 최근 4세션 median e1RM을 대조해주세요."
    raw = draft(q, "first_last_n_session_median_e1rm",
        time_condition=dict(scope=scope, start_date=None, end_date=None, source_text="처음 4세션과 최근 4세션"),
        unresolved_fields=[issue("time_condition")], clarification_required=True)
    raw["requested_analyses"][0]["n_sessions"] = 4
    r = run(q, raw)
    assert r.execution_status == "ready"
    assert r.interpretation.time_condition.scope == "all_records"
    assert not r.phase_a_semantics.default_time_window.applied


@pytest.mark.parametrize("constraint", ["지난 분기", "지난달", "최근 6주", "before 2030", "현재"])
def test_unresolved_original_time_constraint_is_never_discarded(constraint):
    q = f"{constraint} {LIFT}의 training gap을 정리해주세요."
    raw = draft(q, "training_gap", time_condition=dict(scope="unresolved", start_date=None, end_date=None, source_text=""))
    assert run(q, raw).execution_status == "clarification_required"


def test_session_comparison_preserves_explicit_year():
    q = f"2034년 {LIFT}의 처음 4세션과 최근 4세션 median e1RM을 대조해주세요."
    raw = draft(q, "first_last_n_session_median_e1rm")
    raw["requested_analyses"][0]["n_sessions"] = 4
    r = run(q, raw)
    assert r.execution_status == "ready"
    assert r.interpretation.time_condition.start_date == "2034-01-01"
    assert r.interpretation.time_condition.end_date == "2034-12-31"


def test_session_comparison_without_n_stays_clarification():
    q = f"{LIFT}의 처음과 마지막 세션 median e1RM을 비교해주세요."
    r = run(q, draft(q, "first_last_n_session_median_e1rm"))
    assert r.execution_status == "clarification_required"
    assert "n_sessions" in r.phase_a_semantics.argument_resolution.unresolved_fields


def test_current_window_cannot_be_silently_replaced_by_all_records():
    q = f"{LIFT}의 weekly frequency를 현재 수준 관점에서 평가해주세요."
    raw = draft(q, time_condition=dict(scope="unresolved", start_date=None, end_date=None, source_text=""))
    raw["evidence_need"].update(answer_type="evaluative", requires_current_window=True)
    r = run(q, raw)
    assert r.phase_a_semantics.default_time_window.applied
    assert r.interpretation.time_condition.scope == "explicit"


def test_grounding_and_catalog_ambiguity_remain_blocking():
    q = "Synthetic Flare의 training gap을 나열해주세요."
    raw = draft(q, "training_gap", exercise_mention="Synthetic Flare", canonical_exercise_name=None,
                candidate_exercises=[LIFT, OTHER], exercise_resolution_status="ambiguous")
    r = run(q, raw)
    assert r.execution_status == "clarification_required"
    assert "canonical_exercise_name" in r.phase_a_semantics.argument_resolution.unresolved_fields


def test_single_metric_uncertainty_is_not_blindly_cleared_and_message_is_accurate():
    q = f"{LIFT}의 수치를 확인해주세요."
    r = run(q, draft(q, unresolved_fields=[issue("requested_analyses")]))
    assert r.execution_status == "clarification_required"
    text = " ".join(clarification_questions(r.interpretation))
    assert "어떤 지표" in text and "한 번에 하나" not in text
    copy = deepcopy(r.interpretation)
    copy.unresolved_fields[0].field = "comparison"
    assert clarification_questions(copy) == ["어떤 대상이나 구간을 서로 비교할까요?"]


def test_normalized_time_runs_through_real_rest_boundary_with_mock_tools():
    from unittest.mock import Mock
    from src.interpretation.interpreter import QuestionInterpreter
    from src.interpretation.provider import InterpreterProviderResult
    from test_operation_argument_contract import Repository
    from test_phase_a_semantics import SyntheticFinalLayer, api_for
    from test_question_interpretation import attach
    from graph_test_support import build_workflow

    q = f"{LIFT}의 training gap 목록을 반환해주세요."
    raw = draft(q, "training_gap", time_condition=dict(scope="unresolved", start_date=None, end_date=None, source_text=""))
    provider = Mock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=raw)
    interpreter = QuestionInterpreter(exercise_repository=Repository(), provider=provider, semantics_enabled=True)
    legacy, *_ = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[],
                               final_response_layer=SyntheticFinalLayer())
    with api_for(attach(legacy, interpreter)) as api:
        body = api.post("/query", json={"question": q, "debug": True}).json()
    assert body["response_metadata"]["trace"]["interpreter"]["validation"]["status"] == "ready"
    assert body["route"] == "log_metric"
    assert body["tools_used"] == ["query_training_log", "compute_metrics"]
