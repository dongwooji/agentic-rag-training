"""Synthetic session-shape regressions; no DB, evaluation input, or provider."""
from datetime import datetime

import pytest

from src.agent.contracts import PlannerDraft
from src.agent.executor import DeterministicToolExecutor
from src.tools.metric import MetricInput, MetricTool
from src.tools.training_log import PsycopgTrainingRepository, TrainingLogTool


LIFT = "Synthetic Pullover (Cable)"
OTHER = "Synthetic Pullover (Machine)"


def sessions():
    return [dict(session_id=f"s-{day}", started_at=datetime(2033, 4, day, 9),
                 exercise_names=names)
            for day, names in [(1, [LIFT, OTHER]), (5, [OTHER]), (10, [LIFT]), (20, [LIFT])]]


def test_specific_gap_accepts_repository_session_shape_and_deduplicates():
    rows = sessions()
    response = MetricTool().execute(MetricInput(operation="training_gap",
        canonical_exercise_name=LIFT, records=rows + [rows[2]]))
    assert response.success and response.status.value == "success"
    assert response.result["session_count"] == 3
    assert response.result["gap_count"] == 2
    assert [g["gap_days"] for g in response.result["gaps"]] == [10.0, 9.0]
    assert rows == sessions()  # Do not mutate repository data.


def test_specific_gap_retains_inclusive_date_filter():
    response = MetricTool().execute(MetricInput(operation="training_gap", records=sessions(),
        canonical_exercise_name=LIFT, start_date="2033-04-10", end_date="2033-04-20"))
    assert response.success
    assert response.result["session_count"] == 2
    assert response.result["gaps"][0]["gap_days"] == 10.0


def test_global_gap_keeps_all_session_memberships():
    response = MetricTool().execute(MetricInput(operation="training_gap", records=sessions()))
    assert response.result["session_count"] == 4
    assert [g["gap_days"] for g in response.result["gaps"]] == [10.0, 5.0, 4.0]


@pytest.mark.parametrize("names", [[], [OTHER], LIFT, None])
def test_specific_gap_does_not_accept_missing_or_malformed_membership(names):
    rows = [dict(session_id="unrelated", started_at="2033-04-01", exercise_names=names)]
    response = MetricTool().execute(MetricInput(operation="training_gap", records=rows,
        canonical_exercise_name=LIFT))
    assert response.success and response.status.value == "empty"
    assert response.result["record_count"] == 0


def test_existing_set_rows_keep_scalar_exercise_filter():
    rows = [dict(session_id=f"s-{i}", started_at=f"2033-04-{i:02}", exercise_name=LIFT)
            for i in (1, 11)]
    rows.append(dict(session_id="other", started_at="2033-04-05", exercise_name=OTHER,
                     exercise_names=[LIFT]))
    response = MetricTool().execute(MetricInput(operation="training_gap", records=rows,
        canonical_exercise_name=LIFT))
    assert response.result["session_count"] == 2
    assert response.result["gaps"][0]["gap_days"] == 10.0


@pytest.mark.parametrize("operation", ["estimated_1rm", "first_last_n_session_median_e1rm",
    "weekly_volume", "weekly_frequency", "plateau_candidates"])
def test_set_metrics_keep_their_filter_and_frequency_accepts_sessions(operation):
    response = MetricTool().execute(MetricInput(operation=operation, records=sessions(),
        canonical_exercise_name=LIFT))
    if operation == "weekly_frequency":
        assert response.success and response.status.value == "success"
        assert [week["frequency"] for week in response.result["weeks"]] == [1, 1, 1]
    else:
        assert response.success and response.status.value == "empty"
        assert response.result["record_count"] == 0


def test_repository_tool_executor_metric_boundary_uses_actual_session_shape(monkeypatch):
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
        planner_reasoning_summary="Synthetic fixed execution contract", steps=[
            dict(tool="query_training_log", reason="Read selected sessions", subtask="Read sessions",
                 inputs=dict(operation="list_sessions", canonical_exercise_name=LIFT)),
            dict(tool="compute_metrics", reason="Calculate stored session intervals", subtask="Calculate gap",
                 inputs=dict(operation="training_gap", canonical_exercise_name=LIFT))]))
    output = DeterministicToolExecutor(training_log_tool=TrainingLogTool(repo),
        metric_tool=MetricTool()).execute(plan, enabled=True)
    assert [row["status"] for row in output] == ["success", "success"]
    assert output[1]["result"]["session_count"] == 3
    assert [g["gap_days"] for g in output[1]["result"]["gaps"]] == [10.0, 9.0]
    assert len(calls) == 2
