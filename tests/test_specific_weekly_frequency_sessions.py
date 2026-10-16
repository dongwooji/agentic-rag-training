"""New synthetic weekly-frequency cases; no evaluation data, DB, or LLM."""
from datetime import date, datetime

import pytest

from src.agent.contracts import PlannerDraft
from src.agent.executor import DeterministicToolExecutor
from src.tools.metric import MetricInput, MetricTool
from src.tools.training_log import PsycopgTrainingRepository, TrainingLogTool

LIFT = "Synthetic Rotation (Cable)"
OTHER = "Synthetic Rotation (Machine)"


def sessions():
    return [dict(session_id=f"s-{day}", started_at=datetime(2035, 1, day, 10), exercise_names=names)
            for day, names in [(2, [LIFT, OTHER]), (3, [LIFT]), (8, [LIFT]), (9, [OTHER]), (15, [LIFT])]]


def test_specific_frequency_counts_unique_matching_sessions_per_week():
    rows = sessions()
    response = MetricTool().execute(MetricInput(operation="weekly_frequency",
        canonical_exercise_name=LIFT, records=rows + [rows[0], rows[2]]))
    assert response.success and response.status.value == "success"
    assert response.result["weeks"] == [
        dict(week_start=date(2035, 1, 1), frequency=2),
        dict(week_start=date(2035, 1, 8), frequency=1),
        dict(week_start=date(2035, 1, 15), frequency=1)]
    assert rows == sessions()


def test_specific_frequency_preserves_inclusive_date_bounds():
    response = MetricTool().execute(MetricInput(operation="weekly_frequency", records=sessions(),
        canonical_exercise_name=LIFT, start_date="2035-01-03", end_date="2035-01-08"))
    assert response.success
    assert response.result["weeks"] == [
        dict(week_start=date(2035, 1, 1), frequency=1),
        dict(week_start=date(2035, 1, 8), frequency=1)]


def test_global_frequency_still_counts_all_sessions():
    response = MetricTool().execute(MetricInput(operation="weekly_frequency", records=sessions()))
    assert response.result["weeks"] == [
        dict(week_start=date(2035, 1, 1), frequency=2),
        dict(week_start=date(2035, 1, 8), frequency=2),
        dict(week_start=date(2035, 1, 15), frequency=1)]


@pytest.mark.parametrize("names", [[], [OTHER], LIFT, None])
def test_frequency_does_not_accept_unrelated_or_malformed_session_membership(names):
    response = MetricTool().execute(MetricInput(operation="weekly_frequency", canonical_exercise_name=LIFT,
        records=[dict(session_id="unrelated", started_at="2035-01-02", exercise_names=names)]))
    assert response.success and response.status.value == "empty"
    assert response.result["record_count"] == 0


def test_frequency_keeps_existing_scalar_set_filter_and_deduplication():
    rows = [dict(session_id="same", started_at="2035-01-02", exercise_name=LIFT),
            dict(session_id="same", started_at="2035-01-02", exercise_name=LIFT),
            dict(session_id="other", started_at="2035-01-03", exercise_name=OTHER, exercise_names=[LIFT])]
    response = MetricTool().execute(MetricInput(operation="weekly_frequency", records=rows,
        canonical_exercise_name=LIFT))
    assert response.result["weeks"] == [dict(week_start=date(2035, 1, 1), frequency=1)]


def test_frequency_repository_tool_executor_boundary_uses_actual_session_shape(monkeypatch):
    repo = PsycopgTrainingRepository.__new__(PsycopgTrainingRepository)
    calls = []

    def fetch(sql, params=()):
        calls.append((sql, params))
        if "SELECT canonical_name" in sql:
            return [{"canonical_name": LIFT}]
        assert "AS exercise_names" in sql and "e.canonical_name = %s" in sql
        assert params[1] == LIFT
        return [row for row in sessions() if LIFT in row["exercise_names"]]

    monkeypatch.setattr(repo, "_fetch_all", fetch)
    plan = PlannerDraft.model_validate(dict(task_type="log_metric", unsupported=False,
        planner_reasoning_summary="Synthetic fixed weekly frequency", steps=[
            dict(tool="query_training_log", reason="Read selected sessions", subtask="Read sessions",
                 inputs=dict(operation="list_sessions", canonical_exercise_name=LIFT)),
            dict(tool="compute_metrics", reason="Count unique sessions by week", subtask="Weekly frequency",
                 inputs=dict(operation="weekly_frequency", canonical_exercise_name=LIFT))]))
    output = DeterministicToolExecutor(training_log_tool=TrainingLogTool(repo),
        metric_tool=MetricTool()).execute(plan, enabled=True)
    assert [row["status"] for row in output] == ["success", "success"]
    assert output[1]["result"]["weeks"] == [
        dict(week_start="2035-01-01", frequency=2),
        dict(week_start="2035-01-08", frequency=1),
        dict(week_start="2035-01-15", frequency=1)]
    assert len(calls) == 2
