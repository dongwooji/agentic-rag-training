from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from graph_test_support import (
    HYBRID_QUESTION,
    build_workflow,
    literature_hit,
)
from src.agent.contracts import PlannerUsage
from src.graph.nodes import _build_tool_plan
from src.graph.tool_input_resolver import (
    ResolverErrorCode,
    ResolverExecutionStatus,
    ResolverProviderResult,
    ToolInputResolver,
    ToolInputResolverRequest,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeExerciseRepository:
    def __init__(self, names: list[str] | None = None) -> None:
        self.names = names or [
            "Deadlift (Barbell)",
            "Bench Press (Barbell)",
            "Squat (Barbell)",
        ]
        self.calls: list[str] = []

    def resolve_canonical_exercise(self, name: str) -> str | None:
        self.calls.append(name)
        matches = [item for item in self.names if item.casefold() == name.casefold()]
        return matches[0] if len(matches) == 1 else None


class FakeProvider:
    def __init__(
        self,
        raw_inputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.raw_inputs = raw_inputs
        self.error = error
        self.calls: list[ToolInputResolverRequest] = []

    def invoke(self, request: ToolInputResolverRequest) -> ResolverProviderResult:
        self.calls.append(request)
        return ResolverProviderResult(
            raw_inputs=self.raw_inputs,
            model="fixture-resolver",
            prompt_sha256="a" * 64,
            config_sha256="b" * 64,
            response_id="resolver-response-1",
            latency_ms=3.0,
            usage=PlannerUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            error=self.error,
        )


def _request(
    question: str,
    tools: list[str],
    *,
    task_type: str = "log_metric",
) -> ToolInputResolverRequest:
    return ToolInputResolverRequest(
        question=question,
        task_type=task_type,
        selected_tools=tools,
        execution_order=tools,
        route={"route": task_type, "selected_tools": tools},
    )


def _resolver(
    *,
    provider: FakeProvider | None = None,
    names: list[str] | None = None,
) -> ToolInputResolver:
    return ToolInputResolver(
        exercise_repository=FakeExerciseRepository(names),
        provider=provider,
    )


def test_resolves_deadlift_first_last_three_session_median_e1rm() -> None:
    result = _resolver().resolve(
        _request(
            "내 Deadlift (Barbell)의 처음 3-session과 최근 3-session median "
            "e1RM을 비교해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )

    assert result.execution_status == ResolverExecutionStatus.READY
    assert result.provenance.method == "deterministic"
    assert result.tool_inputs["query_training_log"] == {
        "operation": "exercise_records",
        "canonical_exercise_name": "Deadlift (Barbell)",
        "start_date": None,
        "end_date": None,
        "session_id": None,
        "limit": 5000,
        "include_lineage": False,
    }
    assert result.tool_inputs["compute_metrics"]["operation"] == (
        "first_last_n_session_median_e1rm"
    )
    assert result.tool_inputs["compute_metrics"]["n_sessions"] == 3
    assert result.tool_inputs["compute_metrics"]["records_source"] == (
        "query_training_log"
    )


@pytest.mark.parametrize(
    ("question", "exercise", "training_operation"),
    [
        (
            "내 운동 기록 전체에서 가장 긴 training gap을 계산해줘.",
            None,
            "list_sessions",
        ),
        (
            "내 Squat (Barbell)의 training gap을 계산해줘.",
            "Squat (Barbell)",
            "exercise_records",
        ),
    ],
)
def test_resolves_training_gap(
    question: str,
    exercise: str | None,
    training_operation: str,
) -> None:
    result = _resolver().resolve(
        _request(question, ["query_training_log", "compute_metrics"])
    )
    assert result.execution_status == ResolverExecutionStatus.READY
    assert result.tool_inputs["query_training_log"]["operation"] == training_operation
    assert result.tool_inputs["compute_metrics"]["operation"] == "training_gap"
    assert result.tool_inputs["compute_metrics"]["canonical_exercise_name"] == exercise


def test_resolves_weekly_volume_with_exercise_and_iso_date_range() -> None:
    result = _resolver().resolve(
        _request(
            "2024-01-01부터 2024-03-31까지 내 Squat (Barbell) 주간 volume을 계산해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )
    assert result.execution_status == ResolverExecutionStatus.READY
    training = result.tool_inputs["query_training_log"]
    metric = result.tool_inputs["compute_metrics"]
    assert training["operation"] == "exercise_records"
    assert training["start_date"] == metric["start_date"] == "2024-01-01"
    assert training["end_date"] == metric["end_date"] == "2024-03-31"
    assert metric["operation"] == "weekly_volume"


def test_resolves_weekly_frequency() -> None:
    result = _resolver().resolve(
        _request(
            "내 Bench Press (Barbell)의 weekly frequency를 보여줘.",
            ["query_training_log", "compute_metrics"],
        )
    )
    assert result.execution_status == ResolverExecutionStatus.READY
    assert result.tool_inputs["compute_metrics"]["operation"] == "weekly_frequency"


def test_hybrid_resolves_only_structured_inputs_and_keeps_literature_selected() -> None:
    tools = ["query_training_log", "compute_metrics", "search_literature"]
    result = _resolver().resolve(
        _request(
            "내 Deadlift (Barbell)의 최근 3-session과 처음 3-session median "
            "e1RM 변화와 progressive overload 문헌을 설명해줘.",
            tools,
            task_type="hybrid",
        )
    )
    assert result.execution_status == ResolverExecutionStatus.READY
    assert result.provenance.selected_tools == tools
    assert list(result.tool_inputs) == ["query_training_log", "compute_metrics"]
    assert "search_literature" not in result.tool_inputs


@pytest.mark.parametrize(
    ("task_type", "tools"),
    [
        ("literature_only", ["search_literature"]),
        ("unsupported", []),
    ],
)
def test_literature_and_unsupported_routes_do_not_call_provider(
    task_type: str,
    tools: list[str],
) -> None:
    provider = FakeProvider(error="must not be called")
    result = _resolver(provider=provider).resolve(
        _request("문헌 근거를 확인해줘", tools, task_type=task_type)
    )
    assert result.execution_status == ResolverExecutionStatus.NOT_REQUIRED
    assert result.tool_inputs == {}
    assert provider.calls == []


def test_missing_required_exercise_fails_closed() -> None:
    result = _resolver().resolve(
        _request(
            "내 운동 기록의 처음 3-session과 최근 3-session median e1RM을 비교해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )
    assert result.execution_status == ResolverExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == ResolverErrorCode.MISSING_REQUIRED_ARGUMENT
    assert result.tool_inputs == {}


def test_unknown_exercise_fails_before_tool_execution() -> None:
    result = _resolver(names=["Bench Press (Barbell)"]).resolve(
        _request(
            "내 Deadlift (Barbell)의 e1RM을 계산해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )
    assert result.execution_status == ResolverExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == ResolverErrorCode.UNKNOWN_EXERCISE


def test_provider_cannot_add_router_unselected_metric_tool() -> None:
    provider = FakeProvider(
        raw_inputs={
            "query_training_log": {
                "operation": "exercise_records",
                "canonical_exercise_name": "Deadlift (Barbell)",
            },
            "compute_metrics": {
                "operation": "estimated_1rm",
                "records_source": "query_training_log",
                "canonical_exercise_name": "Deadlift (Barbell)",
            },
        }
    )
    result = _resolver(provider=provider).resolve(
        _request(
            "내 최근 훈련 기록을 조회해줘.",
            ["query_training_log"],
            task_type="log_lookup",
        )
    )
    assert len(provider.calls) == 1
    assert result.execution_status == ResolverExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == ResolverErrorCode.INVALID_ROUTE


def test_provider_fallback_output_is_validated_and_preserves_provenance() -> None:
    provider = FakeProvider(
        raw_inputs={
            "query_training_log": {
                "operation": "exercise_records",
                "canonical_exercise_name": "Deadlift (Barbell)",
            },
            "compute_metrics": {
                "operation": "weekly_frequency",
                "records_source": "query_training_log",
                "canonical_exercise_name": "Deadlift (Barbell)",
            },
        }
    )
    result = _resolver(provider=provider).resolve(
        _request(
            "내 Deadlift (Barbell)의 주별 수행 횟수 추세를 계산해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )

    assert result.execution_status == ResolverExecutionStatus.READY
    assert result.provenance.method == "openai"
    assert result.provenance.response_id == "resolver-response-1"
    assert result.provenance.usage.total_tokens == 15
    assert result.tool_inputs["compute_metrics"]["operation"] == "weekly_frequency"


def test_provider_fallback_is_typed_and_fail_closed_on_provider_error() -> None:
    provider = FakeProvider(error="simulated provider failure")
    result = _resolver(provider=provider).resolve(
        _request(
            "내 Deadlift (Barbell)의 주별 수행 횟수 추세를 계산해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )
    assert len(provider.calls) == 1
    assert result.execution_status == ResolverExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == ResolverErrorCode.PROVIDER_FAILURE


def test_provider_schema_failure_is_structured_and_fail_closed() -> None:
    provider = FakeProvider(
        raw_inputs={
            "query_training_log": {
                "operation": "invented_operation",
                "canonical_exercise_name": "Deadlift (Barbell)",
            },
            "compute_metrics": None,
        }
    )
    result = _resolver(provider=provider).resolve(
        _request(
            "내 최근 훈련 기록을 조회해줘.",
            ["query_training_log"],
            task_type="log_lookup",
        )
    )

    assert result.execution_status == ResolverExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == ResolverErrorCode.SCHEMA_FAILURE
    assert result.tool_inputs == {}


def test_reversed_explicit_date_range_fails_closed() -> None:
    result = _resolver().resolve(
        _request(
            "2024-03-31부터 2024-01-01까지 내 Squat (Barbell) 주간 volume을 계산해줘.",
            ["query_training_log", "compute_metrics"],
        )
    )

    assert result.execution_status == ResolverExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == ResolverErrorCode.SCHEMA_FAILURE


def test_resolved_inputs_are_compatible_with_existing_planner_contract() -> None:
    question = (
        "내 Deadlift (Barbell)의 처음 3-session과 최근 3-session median e1RM을 비교해줘."
    )
    result = _resolver().resolve(
        _request(question, ["query_training_log", "compute_metrics"])
    )
    plan = _build_tool_plan(
        {
            "task_type": "log_metric",
            "execution_order": ["query_training_log", "compute_metrics"],
            "tool_inputs": result.tool_inputs,
            "initial_query": question,
        }
    )
    assert plan.selected_tools == ["query_training_log", "compute_metrics"]
    assert plan.tool_inputs == result.tool_inputs


def test_graph_inserts_resolver_between_router_and_initial_tools() -> None:
    workflow, training, metric, literature, grader, _ = build_workflow(
        literature_responses=[[literature_hit("initial", 1)]],
        grader_results=[True],
        recovery_payloads=[],
    )
    workflow.nodes.tool_input_resolver = _resolver()
    state = workflow.invoke(HYBRID_QUESTION)

    assert state["final_status"] == "answer_ready"
    assert state["tool_input_resolution"]["execution_status"] == "ready"
    assert state["tool_input_resolution"]["provenance"]["method"] == "deterministic"
    assert len(training.calls) == len(metric.calls) == len(literature.calls) == 1
    assert len(grader.calls) == 1


def test_frozen_end_to_end_output_artifacts_remain_unchanged() -> None:
    expected = {
        "end_to_end_baseline_v1": (
            "fd6d45414010033d61971fbddbea26fcd19c485aa51b2cb64c2ef0caff3478e6"
        ),
        "end_to_end_baseline_v2": (
            "c1e5c4c2defee539edb9980c62b79da003b33b4db2e1186314fb884706904225"
        ),
    }
    for version, manifest_hash in expected.items():
        output = ROOT / "reports" / "baselines" / version
        manifest_path = output / "manifest.json"
        assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == manifest_hash
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "FROZEN_COMPLETE"
        for relative, artifact_hash in manifest["artifact_hashes"].items():
            assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == (
                artifact_hash
            )
