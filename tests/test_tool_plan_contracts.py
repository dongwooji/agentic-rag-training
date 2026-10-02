"""Runtime tool-plan contract and deterministic executor tests.

Moved verbatim from the historical Phase 10 agent test module, which also
covered the LLM Planner baseline preserved in the ``legacy-pre-retrieval-v2`` tag.
"""

from __future__ import annotations

from typing import Any
import importlib.metadata
import json

import pytest
from pydantic import ValidationError

from src.agent.contracts import PlannerDraft
from src.agent.executor import DeterministicToolExecutor
from src.tools.contracts import success_response


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
