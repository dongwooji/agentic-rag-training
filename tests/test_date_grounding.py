"""New synthetic calendar and record-coverage contracts; no evaluation files."""
from datetime import date
from unittest.mock import Mock
import pytest

from src.graph.interpreted_workflow import InterpretedWorkflow
from src.graph.tool_input_resolver import ExerciseCanonicalizer
from src.interpretation.date_ranges import explicit_period, DateExpressionError
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult
from src.interpretation.validator import validate_interpretation, DateGroundingError


class Repository:
    def resolve_canonical_exercise(self, name):
        return name if name == "Fixture Pull" else None
    def list_canonical_exercises(self):
        return ["Fixture Pull"]
    def record_date_bounds(self):
        return date(2030, 1, 4), date(2035, 6, 20)


def draft(**changes):
    raw = dict(personal_record_requested=True, literature_requested=False, exercise_mention="Fixture Pull",
        canonical_exercise_name="Fixture Pull", exercise_resolution_status="resolved", candidate_exercises=["Fixture Pull"],
        time_condition=dict(scope="unresolved", start_date=None, end_date=None, source_text=""),
        record_operation="exercise_records", session_id=None, requested_analyses=[], literature_subquestion=None,
        user_assumptions=[], unresolved_fields=[], clarification_required=False)
    raw.update(changes)
    return raw


def validate(question, raw):
    return validate_interpretation(question, raw, source="llm", canonicalizer=ExerciseCanonicalizer(Repository()),
                                   canonical_exercises=("Fixture Pull",))


@pytest.mark.parametrize("text,start,end", [
    ("2032년", "2032-01-01", "2032-12-31"),
    ("2033년 2월", "2033-02-01", "2033-02-28"),
    ("2032년 2월", "2032-02-01", "2032-02-29"),
    ("2030년부터 2032년까지", "2030-01-01", "2032-12-31"),
    ("2034년 9월~12월", "2034-09-01", "2034-12-31"),
    ("2034년 9월부터 12월까지", "2034-09-01", "2034-12-31"),
    ("2034년 11월부터 2035년 2월까지", "2034-11-01", "2035-02-28"),
    ("2034-09-01부터2034-12-31까지", "2034-09-01", "2034-12-31"),
    ("2034년 9월 3일", "2034-09-03", "2034-09-03"),
    ("2034-09-03", "2034-09-03", "2034-09-03"),
])
def test_calendar_bounds(text, start, end):
    result = validate(f"내 Fixture Pull 기록을 {text} 기간으로 보여주세요.", draft())
    assert (result.time_condition.start_date, result.time_condition.end_date) == (start, end)
    assert not result.clarification_required
    assert result.date_range_normalization.mismatch


@pytest.mark.parametrize("scope,start,end,mismatch", [
    ("explicit", "2034-09-01", "2034-12-31", False),
    ("explicit", "2034-10-01", "2034-11-30", True),
    ("explicit", "2035-01-01", "2033-12-31", True),
    ("all_records", None, None, True),
])
def test_supplied_period_is_normalized_to_source_without_blocking(scope, start, end, mismatch):
    raw = draft(time_condition=dict(scope=scope, start_date=start, end_date=end, source_text=""),
                unresolved_fields=[dict(field="time_condition", reason="internal model reasoning")], clarification_required=True)
    result = validate("내 Fixture Pull의 2034년 9월~12월 기록을 보여주세요.", raw)
    assert (result.time_condition.start_date, result.time_condition.end_date) == ("2034-09-01", "2034-12-31")
    assert result.date_range_normalization.mismatch is mismatch
    assert result.date_range_normalization.supplied_start_date == start
    assert not result.clarification_required


@pytest.mark.parametrize("text", ["2033년 2월 29일", "2034년 13월", "2034년 12월~9월", "2035년부터2034년까지", "9월", "2034년과2035년"])
def test_invalid_or_ambiguous_question_period_is_not_invented(text):
    with pytest.raises(DateExpressionError):
        explicit_period(text)


def test_period_absent_from_question_still_rejected():
    with pytest.raises(DateGroundingError, match="absent"):
        validate("내 Fixture Pull 기록을 보여주세요.", draft(time_condition=dict(scope="explicit", start_date="2034-01-01", end_date="2034-12-31", source_text="")))


def test_normalization_preserves_unrelated_grounding_errors():
    raw = draft(requested_analyses=[dict(operation="weekly_volume", n_sessions=5, source_text="weekly volume")])
    with pytest.raises(ValueError, match="N is absent"):
        validate("내 Fixture Pull의 2034년 weekly volume을 보여주세요.", raw)


@pytest.mark.parametrize("period", ["2029년", "2036년"])
def test_disjoint_stored_coverage_returns_no_records_without_tools(period):
    class Provider:
        def invoke(self, question, *, canonical_exercises):
            return InterpreterProviderResult(raw_interpretation=draft())
    executor = Mock()
    workflow = InterpretedWorkflow(interpreter=QuestionInterpreter(exercise_repository=Repository(), provider=Provider()),
        tool_executor=executor, literature_tool=Mock(), runtime_grader=Mock(), recovery_agent=Mock())
    state = workflow.invoke(f"내 Fixture Pull의 {period} 기록을 보여주세요.")
    assert state["final_status"] == "abstain_ready"
    assert state["record_coverage_status"] == "no_records"
    assert "해당 기간 기록이 없습니다" in state["final_response"]["answer_text"]
    assert state["errors"] == [] and state["tool_results"] == []
    executor.execute.assert_not_called()


def test_repository_coverage_query_is_dynamic_and_read_only():
    from src.tools.training_log import PsycopgTrainingRepository
    repository = object.__new__(PsycopgTrainingRepository)
    repository._fetch_all = Mock(return_value=[dict(first_date=date(2030, 1, 4), last_date=date(2035, 6, 20))])
    assert repository.record_date_bounds() == (date(2030, 1, 4), date(2035, 6, 20))
    sql, parameters = repository._fetch_all.call_args.args
    assert sql.startswith("SELECT min(s.started_at)") and "JOIN training.workout_sets" in sql
    assert parameters == ("preprocessing_v1",)
    assert "2015" not in sql


def test_partially_overlapping_period_is_not_clamped_to_record_coverage():
    from graph_test_support import build_workflow
    legacy, training, _, _, _, _ = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    class Provider:
        def invoke(self, question, *, canonical_exercises):
            return InterpreterProviderResult(raw_interpretation=draft())
    workflow = InterpretedWorkflow(interpreter=QuestionInterpreter(exercise_repository=Repository(), provider=Provider()),
        tool_executor=legacy.nodes.tool_executor, literature_tool=legacy.nodes.literature_tool,
        runtime_grader=legacy.nodes.runtime_grader, recovery_agent=legacy.nodes.recovery_agent)
    state = workflow.invoke("내 Fixture Pull의 2029년부터 2031년까지 기록을 보여주세요.")
    assert state["final_status"] == "answer_ready" and len(training.calls) == 1
    assert training.calls[0].start_date == "2029-01-01"
    assert training.calls[0].end_date == "2031-12-31"
