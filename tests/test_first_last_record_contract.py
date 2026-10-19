"""New synthetic post-hoc regressions; no saved smoke/acceptance inputs."""
from datetime import datetime
from unittest.mock import Mock

import pytest

from src.agent.executor import DeterministicToolExecutor
from src.graph.interpretation_binding import bind_interpretation
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.operation_arguments import record_operation_for_metric
from src.interpretation.provider import InterpreterProviderResult
from src.routing.interpretation_router import InterpretationRouter
from src.tools.metric import MetricTool
from src.tools.training_log import TrainingLogTool
from graph_test_support import build_workflow
from test_operation_argument_contract import LIFT, Repository, draft, run
from test_phase_a_semantics import SyntheticFinalLayer, api_for
from test_question_interpretation import attach

METRIC = "first_last_n_session_median_e1rm"


def extraction(question, *, n=2, record_operation="exercise_first_last"):
    raw = draft(question, METRIC, record_operation=record_operation)
    raw["requested_analyses"][0]["n_sessions"] = n
    return raw


def test_first_last_metric_uses_the_existing_derived_record_contract():
    question = f"{LIFT}의 첫 두 세션과 마지막 두 세션 median e1RM을 대조해주세요."
    result = run(question, extraction(question))
    assert result.execution_status == "ready"
    assert result.interpretation.unresolved_fields == []
    inputs = bind_interpretation(result.interpretation, InterpretationRouter().route(result.interpretation))
    assert inputs["query_training_log"]["operation"] == record_operation_for_metric(METRIC) == "exercise_records"
    assert inputs["compute_metrics"]["operation"] == METRIC
    assert inputs["compute_metrics"]["n_sessions"] == 2


def test_missing_n_still_clarifies_without_a_spurious_record_operation_issue():
    question = f"{LIFT}의 첫 세션들과 마지막 세션들 median e1RM을 대조해주세요."
    result = run(question, extraction(question, n=None))
    assert result.execution_status == "clarification_required"
    assert {x.field for x in result.interpretation.unresolved_fields} == {"n_sessions"}
    assert not InterpretationRouter().route(result.interpretation).selected_tools


@pytest.mark.parametrize("metric,record_operation", [
    (None, "exercise_first_last"), (METRIC, "get_session"),
    ("weekly_volume", "exercise_first_last"), ("weekly_frequency", "exercise_first_last"),
    ("estimated_1rm", "exercise_first_last"), ("training_gap", "exercise_first_last"),
    ("plateau_candidates", "exercise_first_last"),
])
def test_no_other_record_operation_permission_is_expanded(metric, record_operation):
    question = f"{LIFT}의 첫 두 세션과 마지막 두 세션 median e1RM 항목을 확인해주세요."
    raw = draft(question, metric, record_operation=record_operation)
    if metric == METRIC:
        raw["requested_analyses"][0]["n_sessions"] = 2
    result = run(question, raw)
    assert result.execution_status == "clarification_required"
    assert "record_operation" in {x.field for x in result.interpretation.unresolved_fields}
    assert not InterpretationRouter().route(result.interpretation).selected_tools


def test_unknown_operation_still_fails_typed_validation():
    question = f"{LIFT}의 첫 두 세션과 마지막 두 세션 median e1RM을 대조해주세요."
    result = run(question, extraction(question, record_operation="unsupported_synthetic_operation"))
    assert result.execution_status == "failure"


class RecordsRepository(Repository):
    def __init__(self):
        self.calls = []

    def exercise_records(self, canonical_name, **kwargs):
        assert canonical_name == LIFT
        self.calls.append("exercise_records")
        return [dict(set_id=f"synthetic-set-{i}", session_id=f"synthetic-session-{i}",
            started_at=datetime(2034, 5, i, 9), exercise_name=LIFT, set_order=1,
            weight=weight, reps=3, weight_unit="unknown_source_unit", is_outlier=False,
            include_in_volume_metrics=True, include_in_e1rm=True)
            for i, weight in enumerate((30.0, 60.0, 90.0, 120.0), 1)]

    def exercise_first_last(self, *args, **kwargs):
        raise AssertionError("Metric needs all records, not only two endpoint records")


@pytest.mark.parametrize("missing_n", [False, True])
def test_rest_boundary_executes_real_synthetic_tools_or_clarifies_before_tools(missing_n):
    question = (f"{LIFT}의 첫 세션들과 마지막 세션들 median e1RM을 대조해주세요." if missing_n else
                f"{LIFT}의 첫 두 세션과 마지막 두 세션 median e1RM을 대조해주세요.")
    provider = Mock()
    provider.invoke.return_value = InterpreterProviderResult(raw_interpretation=extraction(question, n=None if missing_n else 2))
    repository = RecordsRepository()
    interpreter = QuestionInterpreter(exercise_repository=repository, provider=provider, semantics_enabled=True)
    legacy, *ports = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[],
                                   final_response_layer=SyntheticFinalLayer())

    class ObservedMetric(MetricTool):
        def execute(self, request):
            self.request, self.response = request, super().execute(request)
            return self.response

    metric = ObservedMetric()
    legacy.nodes.tool_executor = DeterministicToolExecutor(training_log_tool=TrainingLogTool(repository),
        metric_tool=metric, literature_tool=ports[2])
    with api_for(attach(legacy, interpreter)) as api:
        response = api.post("/query", json={"question": question, "debug": True})
    assert response.status_code == 200
    body = response.json()
    provider.invoke.assert_called_once()
    if missing_n:
        assert body["response_metadata"]["clarification_required"]
        assert body["tools_used"] == [] and repository.calls == []
        assert not hasattr(metric, "response")
    else:
        assert body["final_status"] == "answer_ready"
        assert body["tools_used"] == ["query_training_log", "compute_metrics"]
        assert repository.calls == ["exercise_records"]
        assert metric.request.n_sessions == 2
        result = metric.response.result
        assert result["eligible_session_count"] == 4
        assert result["used_n_sessions"] == 2
        assert result["first_median_e1rm"] == pytest.approx(49.5)
        assert result["last_median_e1rm"] == pytest.approx(115.5)
