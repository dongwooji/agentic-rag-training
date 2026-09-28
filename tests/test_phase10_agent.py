from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
import importlib.metadata
import json

import pytest
from pydantic import ValidationError

from src.agent.contracts import PlannerCallResult, PlannerDraft, PlannerUsage
from src.agent.baseline import (
    CHECKPOINT_VERSION,
    MIN_REQUEST_START_INTERVAL_SECONDS,
    load_or_create_plan_checkpoint,
    produce_agent_plans,
    run_agent_baseline,
)
from src.agent.executor import DeterministicToolExecutor
from src.agent.graph import PlanningAgentWorkflow
from src.agent.planner import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    OpenAIPlannerBackend,
    load_agent_config,
    sha256_file,
)
from src.tools.contracts import success_response


ROOT = Path(__file__).resolve().parents[1]


def log_step() -> dict[str, Any]:
    return {
        "tool": "query_training_log",
        "reason": "Personal Bench Press records are required.",
        "subtask": "Read Bench Press records.",
        "inputs": {
            "operation": "exercise_records",
            "canonical_exercise_name": "Bench Press",
            "limit": 500,
            "include_lineage": True,
        },
    }


def metric_step() -> dict[str, Any]:
    return {
        "tool": "compute_metrics",
        "reason": "The question requests a deterministic e1RM trend.",
        "subtask": "Compute first and last median e1RM.",
        "inputs": {
            "operation": "first_last_n_session_median_e1rm",
            "records_source": "query_training_log",
            "canonical_exercise_name": "Bench Press",
            "n_sessions": 5,
        },
    }


def literature_step() -> dict[str, Any]:
    return {
        "tool": "search_literature",
        "reason": "Scientific evidence is requested.",
        "subtask": "Retrieve evidence about progressive overload.",
        "inputs": {
            "operation": "search",
            "query": "progressive overload resistance training strength",
            "top_k": 10,
        },
    }


def hybrid_plan() -> PlannerDraft:
    return PlannerDraft.model_validate(
        {
            "task_type": "hybrid",
            "unsupported": False,
            "unsupported_reason": None,
            "planner_reasoning_summary": "Personal metrics and literature are both needed.",
            "steps": [log_step(), metric_step(), literature_step()],
        }
    )


class FakeBackend:
    def __init__(self, result: PlannerCallResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def invoke(self, question: str) -> PlannerCallResult:
        self.calls.append(question)
        return self.result


class SequenceBackend:
    def __init__(self, results: list[PlannerCallResult]) -> None:
        self.results = list(results)
        self.calls: list[str] = []

    def invoke(self, question: str) -> PlannerCallResult:
        self.calls.append(question)
        return self.results.pop(0)


def backend_result(plan: PlannerDraft | None, error: str | None = None) -> PlannerCallResult:
    return PlannerCallResult(
        raw_plan=plan.model_dump(mode="json") if plan else None,
        model="fake-model",
        response_id="resp_test",
        latency_ms=1.25,
        usage=PlannerUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        error=error,
    )


def test_preregistered_prompt_and_config_hashes_are_pinned() -> None:
    config, prompt, prompt_path = load_agent_config()
    assert config["agent_version"] == "agent_baseline_v1"
    assert config["model"] == "gpt-5.4-mini-2026-03-17"
    assert sha256_file(ROOT / "config/agent_planner_v1.json") == EXPECTED_CONFIG_SHA256
    assert sha256_file(prompt_path) == EXPECTED_PROMPT_SHA256
    assert "HYB-008" not in prompt
    assert "LIT-001" not in prompt


def test_http_transport_brotli_version_satisfies_httpx2_contract() -> None:
    version = importlib.metadata.version("Brotli")
    assert tuple(int(item) for item in version.split(".")) >= (1, 2, 0)


def test_valid_hybrid_plan_exposes_typed_order_and_inputs() -> None:
    plan = hybrid_plan()
    assert plan.selected_tools == [
        "query_training_log",
        "compute_metrics",
        "search_literature",
    ]
    assert plan.execution_order == plan.selected_tools
    assert plan.tool_inputs["compute_metrics"]["records_source"] == "query_training_log"


def test_structured_output_schema_uses_supported_anyof_not_oneof() -> None:
    schema = PlannerDraft.model_json_schema()
    serialized = json.dumps(schema)
    assert "oneOf" not in serialized
    assert "anyOf" in schema["properties"]["steps"]["items"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["steps"].append(value["steps"][0]),
        lambda value: value.update(steps=[metric_step(), log_step(), literature_step()]),
        lambda value: value.update(task_type="literature_only"),
    ],
)
def test_contract_rejects_duplicate_wrong_order_and_task_set(mutator: Any) -> None:
    value = hybrid_plan().model_dump(mode="json")
    mutator(value)
    with pytest.raises(ValidationError):
        PlannerDraft.model_validate(value)


def test_contract_rejects_unknown_tool_and_missing_required_input() -> None:
    value = hybrid_plan().model_dump(mode="json")
    value["steps"][0]["tool"] = "made_up_tool"
    with pytest.raises(ValidationError):
        PlannerDraft.model_validate(value)
    value = hybrid_plan().model_dump(mode="json")
    value["steps"][0]["inputs"]["canonical_exercise_name"] = None
    with pytest.raises(ValidationError):
        PlannerDraft.model_validate(value)


def test_unsupported_plan_requires_reason_and_no_tools() -> None:
    valid = {
        "task_type": "unsupported",
        "unsupported": True,
        "unsupported_reason": "The personal log has no sleep data.",
        "planner_reasoning_summary": "Required personal data is absent.",
        "steps": [],
    }
    assert PlannerDraft.model_validate(valid).selected_tools == []
    invalid = dict(valid, unsupported_reason=None)
    with pytest.raises(ValidationError):
        PlannerDraft.model_validate(invalid)


def test_plan_only_graph_never_calls_configured_tools() -> None:
    class FailIfCalled:
        def execute(self, request: Any) -> Any:
            raise AssertionError("Tool must not be called in plan-only evaluation")

    backend = FakeBackend(backend_result(hybrid_plan()))
    executor = DeterministicToolExecutor(
        training_log_tool=FailIfCalled(),
        metric_tool=FailIfCalled(),
        literature_tool=FailIfCalled(),
    )
    workflow = PlanningAgentWorkflow(
        backend=backend, executor=executor, execute_tools=False
    )
    state = workflow.invoke("내 Bench Press 추세와 progressive overload 근거를 비교해줘")
    assert state["final_status"] == "planned"
    assert state["selected_tools"] == hybrid_plan().selected_tools
    assert [item["status"] for item in state["tool_results"]] == [
        "not_executed_plan_only",
        "not_executed_plan_only",
        "not_executed_plan_only",
    ]


def test_graph_has_only_preregistered_acyclic_edges() -> None:
    workflow = PlanningAgentWorkflow(
        backend=FakeBackend(backend_result(hybrid_plan())), execute_tools=False
    )
    graph = workflow.graph.get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert edges == {
        ("__start__", "planner"),
        ("planner", "validate_plan"),
        ("validate_plan", "execute_plan"),
        ("execute_plan", "finalize"),
        ("finalize", "__end__"),
    }


def test_graph_handles_planner_error_and_malformed_input_without_execution() -> None:
    backend = FakeBackend(backend_result(None, error="provider unavailable"))
    workflow = PlanningAgentWorkflow(backend=backend, execute_tools=False)
    failed = workflow.invoke("valid question")
    assert failed["final_status"] == "planner_error"
    assert failed["selected_tools"] == []
    malformed = workflow.invoke("   ")
    assert malformed["final_status"] == "invalid_input"
    assert backend.calls == ["valid question"]


def test_baseline_aborts_without_freezing_provider_error() -> None:
    workflow = PlanningAgentWorkflow(
        backend=FakeBackend(backend_result(None, error="invalid credentials")),
        execute_tools=False,
    )
    with pytest.raises(RuntimeError, match="aborted before freeze"):
        produce_agent_plans(
            [{"id": "TEST-001", "question": "a valid question"}], workflow
        )


def test_semantically_invalid_model_plan_is_recorded_not_repaired() -> None:
    invalid = hybrid_plan().model_dump(mode="json")
    invalid["steps"][0]["inputs"]["canonical_exercise_name"] = None
    backend = FakeBackend(
        PlannerCallResult(
            raw_plan=invalid,
            model="fake-model",
            response_id="resp_invalid",
            latency_ms=2.0,
            usage=PlannerUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )
    )
    workflow = PlanningAgentWorkflow(backend=backend, execute_tools=False)
    plans = produce_agent_plans(
        [{"id": "TEST-INVALID", "question": "a valid question"}], workflow
    )
    assert plans[0]["final_status"] == "validation_error"
    assert plans[0]["prediction"]["task_type"] == "invalid"
    assert plans[0]["prediction"]["selected_tools"] == []
    assert plans[0]["raw_plan"] == invalid
    assert plans[0]["planner_metrics"]["usage"]["total_tokens"] == 30


def test_request_pacing_prevents_rpm_burst_without_retry() -> None:
    current_time = [0.0]
    sleep_calls: list[float] = []

    def clock() -> float:
        return current_time[0]

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        current_time[0] += seconds

    workflow = PlanningAgentWorkflow(
        backend=FakeBackend(backend_result(hybrid_plan())), execute_tools=False
    )
    plans = produce_agent_plans(
        [
            {"id": "PACE-001", "question": "first"},
            {"id": "PACE-002", "question": "second"},
            {"id": "PACE-003", "question": "third"},
        ],
        workflow,
        min_request_interval_seconds=MIN_REQUEST_START_INTERVAL_SECONDS,
        clock=clock,
        sleep_fn=fake_sleep,
    )
    assert sleep_calls == [
        MIN_REQUEST_START_INTERVAL_SECONDS,
        MIN_REQUEST_START_INTERVAL_SECONDS,
    ]
    assert [item["request_pacing_wait_ms"] for item in plans] == [
        0.0,
        7000.0,
        7000.0,
    ]


def test_provider_failure_checkpoint_resumes_without_repeating_completed_cases(
    tmp_path: Path,
) -> None:
    cases = [
        {"id": "CHECK-001", "question": "first question"},
        {"id": "CHECK-002", "question": "second question"},
        {"id": "CHECK-003", "question": "third question"},
    ]
    checkpoint_dir = tmp_path / ".agent_baseline_v1_checkpoint"
    metadata = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "model": "fake-model",
        "case_count": len(cases),
    }
    initial = load_or_create_plan_checkpoint(
        checkpoint_dir=checkpoint_dir,
        cases=cases,
        expected_metadata=metadata,
    )
    first_backend = SequenceBackend(
        [
            backend_result(hybrid_plan()),
            backend_result(hybrid_plan()),
            backend_result(None, error="rate limit reached"),
        ]
    )
    first_workflow = PlanningAgentWorkflow(
        backend=first_backend, execute_tools=False
    )
    with pytest.raises(RuntimeError, match="provider failed at CHECK-003"):
        produce_agent_plans(
            cases,
            first_workflow,
            initial_plans=initial,
            checkpoint_path=checkpoint_dir / "plans.jsonl",
        )
    assert first_backend.calls == [
        "first question",
        "second question",
        "third question",
    ]

    preserved = load_or_create_plan_checkpoint(
        checkpoint_dir=checkpoint_dir,
        cases=cases,
        expected_metadata=metadata,
    )
    assert [item["case_id"] for item in preserved] == ["CHECK-001", "CHECK-002"]
    second_backend = FakeBackend(backend_result(hybrid_plan()))
    second_workflow = PlanningAgentWorkflow(
        backend=second_backend, execute_tools=False
    )
    completed = produce_agent_plans(
        cases,
        second_workflow,
        initial_plans=preserved,
        checkpoint_path=checkpoint_dir / "plans.jsonl",
    )
    assert second_backend.calls == ["third question"]
    assert [item["case_id"] for item in completed] == [
        "CHECK-001",
        "CHECK-002",
        "CHECK-003",
    ]


def test_deterministic_executor_preserves_phase8_contract_and_dependency_order() -> None:
    calls: list[tuple[str, Any]] = []

    class FakeTraining:
        def execute(self, request: Any) -> Any:
            calls.append(("training", request))
            return success_response(
                operation="exercise_records",
                result={
                    "records": [
                        {
                            "set_id": "S1",
                            "session_id": "SESSION1",
                            "started_at": "2025-01-01T10:00:00",
                            "exercise_name": "Bench Press",
                            "weight": 100,
                            "reps": 5,
                            "set_order": 1,
                            "include_in_e1rm": True,
                            "include_in_volume_metrics": True,
                        }
                    ]
                },
                provenance={"tool": "training_log_tool_v1"},
            )

    class FakeMetric:
        def execute(self, request: Any) -> Any:
            calls.append(("metric", request))
            assert len(request.records) == 1
            return success_response(
                operation="first_last_n_session_median_e1rm",
                result={"values": []},
                provenance={"tool": "metric_tool_v1"},
            )

    class FakeLiterature:
        def execute(self, request: Any) -> Any:
            calls.append(("literature", request))
            return success_response(
                operation="search",
                result={"hits": []},
                provenance={"tool": "literature_tool_v1"},
            )

    executor = DeterministicToolExecutor(
        training_log_tool=FakeTraining(),
        metric_tool=FakeMetric(),
        literature_tool=FakeLiterature(),
    )
    results = executor.execute(hybrid_plan(), enabled=True)
    assert [name for name, _ in calls] == ["training", "metric", "literature"]
    assert [item["status"] for item in results] == ["success", "success", "success"]
    assert all(item["provenance"] for item in results)


def test_openai_backend_uses_pinned_structured_request_and_records_usage() -> None:
    captured: dict[str, Any] = {}

    class FakeResponses:
        def create(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                id="resp_123",
                model="gpt-5.4-mini-2026-03-17",
                output_text=hybrid_plan().model_dump_json(),
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=10,
                    total_tokens=110,
                    input_tokens_details=SimpleNamespace(cached_tokens=20),
                ),
            )

    client = SimpleNamespace(responses=FakeResponses())
    backend = OpenAIPlannerBackend(client=client)
    result = backend.invoke("question text only")
    assert result.error is None
    assert result.response_id == "resp_123"
    assert result.usage.estimated_cost_usd == pytest.approx(0.0001065)
    assert captured["model"] == "gpt-5.4-mini-2026-03-17"
    assert captured["temperature"] == 0.0
    assert captured["reasoning"] == {"effort": "none"}
    assert captured["text"]["verbosity"] == "low"
    assert captured["text"]["format"]["name"] == "PlannerDraft"
    assert captured["text"]["format"]["strict"] is True
    assert "oneOf" not in json.dumps(captured["text"]["format"]["schema"])
    assert "verbosity" not in captured
    assert captured["store"] is False
    assert "tools" not in captured


def test_openai_backend_selects_final_answer_instead_of_aggregated_output_text() -> None:
    commentary_plan = hybrid_plan()
    final_plan = PlannerDraft.model_validate(
        {
            "task_type": "literature_only",
            "unsupported": False,
            "unsupported_reason": None,
            "planner_reasoning_summary": "Only scientific evidence is required.",
            "steps": [literature_step()],
        }
    )
    commentary_json = commentary_plan.model_dump_json()
    final_json = final_plan.model_dump_json()

    class FakeResponses:
        def create(self, **kwargs: Any) -> Any:
            del kwargs
            return SimpleNamespace(
                id="resp_phased",
                model="gpt-5.4-mini-2026-03-17",
                output_text=commentary_json + final_json,
                output=[
                    SimpleNamespace(
                        type="message",
                        phase="commentary",
                        content=[
                            SimpleNamespace(type="output_text", text=commentary_json)
                        ],
                    ),
                    SimpleNamespace(
                        type="message",
                        phase="final_answer",
                        content=[
                            SimpleNamespace(type="output_text", text=final_json)
                        ],
                    ),
                ],
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                ),
            )

    backend = OpenAIPlannerBackend(
        client=SimpleNamespace(responses=FakeResponses())
    )
    result = backend.invoke("question text only")
    assert result.error is None
    assert result.raw_plan == final_plan.model_dump(mode="json")


def test_openai_backend_does_not_silently_accept_ambiguous_unphased_json() -> None:
    concatenated = hybrid_plan().model_dump_json() * 2

    class FakeResponses:
        def create(self, **kwargs: Any) -> Any:
            del kwargs
            return SimpleNamespace(
                id="resp_ambiguous",
                model="gpt-5.4-mini-2026-03-17",
                output_text=concatenated,
                usage=None,
            )

    backend = OpenAIPlannerBackend(
        client=SimpleNamespace(responses=FakeResponses())
    )
    result = backend.invoke("question text only")
    assert result.raw_plan is None
    assert result.error is not None
    assert result.error.startswith("JSONDecodeError: Extra data")


def test_backend_requires_api_key_when_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIPlannerBackend()


def test_full_baseline_pipeline_freezes_once_with_fake_backend(tmp_path: Path) -> None:
    fake = FakeBackend(backend_result(hybrid_plan()))
    output = tmp_path / "agent_baseline_v1"
    manifest, metrics, comparison = run_agent_baseline(
        ROOT, output_dir=output, backend=fake
    )
    assert manifest["status"] == "frozen"
    assert metrics["aggregate"]["case_count"] == 30
    assert comparison["hyb_008"]["agent_tools"] == hybrid_plan().selected_tools
    assert len(fake.calls) == 30
    plans = [
        __import__("json").loads(line)
        for line in (output / "plans.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(item["planner_input_fields"] == ["question"] for item in plans)
    assert all(
        result["status"] == "not_executed_plan_only"
        for item in plans
        for result in item["tool_results"]
    )
    checkpoint_dir = tmp_path / ".agent_baseline_v1_checkpoint"
    checkpoint_metadata = json.loads(
        (checkpoint_dir / "metadata.json").read_text(encoding="utf-8")
    )
    assert checkpoint_metadata["status"] == "finalized"
    assert checkpoint_metadata["final_manifest_sha256"] == sha256_file(
        output / "manifest.json"
    )
    assert len(
        (checkpoint_dir / "plans.jsonl").read_text(encoding="utf-8").splitlines()
    ) == 30
    with pytest.raises(FileExistsError):
        run_agent_baseline(ROOT, output_dir=output, backend=fake)
