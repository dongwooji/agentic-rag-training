"""Deterministic executor for validated Phase 10 plans."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from src.tools.literature import LiteratureInput
from src.tools.metric import MetricInput
from src.tools.training_log import TrainingLogInput

from .contracts import PlannerDraft


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    status: str
    requested_operation: str
    result: Any | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    error: Any | None = None


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _records_from_training_result(value: Any) -> list[Mapping[str, Any]] | None:
    payload = _json_safe(value)
    if not isinstance(payload, Mapping) or not payload.get("success"):
        return None
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return None
    records = result.get("records")
    if isinstance(records, list):
        return [item for item in records if isinstance(item, Mapping)]
    if isinstance(records, Mapping):
        flattened = [
            item for item in records.values() if isinstance(item, Mapping)
        ]
        return flattened
    sessions = result.get("sessions")
    if isinstance(sessions, list):
        return [item for item in sessions if isinstance(item, Mapping)]
    sets = result.get("sets")
    if isinstance(sets, list):
        return [item for item in sets if isinstance(item, Mapping)]
    return None


class DeterministicToolExecutor:
    """Execute only already-validated plans using the unchanged Phase 8 APIs."""

    def __init__(
        self,
        *,
        training_log_tool: Any | None = None,
        metric_tool: Any | None = None,
        literature_tool: Any | None = None,
    ) -> None:
        self._tools = {
            "query_training_log": training_log_tool,
            "compute_metrics": metric_tool,
            "search_literature": literature_tool,
        }

    def execute(
        self, plan: PlannerDraft, *, enabled: bool
    ) -> list[dict[str, Any]]:
        if not enabled:
            return [
                ExecutionRecord(
                    tool=step.tool,
                    status="not_executed_plan_only",
                    requested_operation=step.inputs.operation,
                    limitations=[
                        "Phase 10 baseline evaluation measures planning only."
                    ],
                ).model_dump(mode="json")
                for step in plan.steps
            ]

        output: list[dict[str, Any]] = []
        training_response: Any | None = None
        for step in plan.steps:
            tool = self._tools.get(step.tool)
            operation = step.inputs.operation
            if tool is None:
                output.append(
                    ExecutionRecord(
                        tool=step.tool,
                        status="configuration_error",
                        requested_operation=operation,
                        error={
                            "code": "tool_not_configured",
                            "message": f"{step.tool} is not configured",
                        },
                    ).model_dump(mode="json")
                )
                continue
            if step.tool == "query_training_log":
                request = TrainingLogInput(**step.inputs.model_dump())
            elif step.tool == "compute_metrics":
                records = _records_from_training_result(training_response)
                if records is None:
                    output.append(
                        ExecutionRecord(
                            tool=step.tool,
                            status="dependency_error",
                            requested_operation=operation,
                            error={
                                "code": "training_records_unavailable",
                                "message": (
                                    "compute_metrics requires records from the prior "
                                    "query_training_log result"
                                ),
                            },
                        ).model_dump(mode="json")
                    )
                    continue
                inputs = step.inputs.model_dump(exclude={"records_source"})
                request = MetricInput(records=records, **inputs)
            else:
                request = LiteratureInput(**step.inputs.model_dump())
            try:
                response = tool.execute(request)
                if step.tool == "query_training_log":
                    training_response = response
                payload = _json_safe(response)
                output.append(
                    ExecutionRecord(
                        tool=step.tool,
                        status=str(payload.get("status", "unknown")),
                        requested_operation=str(
                            payload.get("requested_operation", operation)
                        ),
                        result=payload.get("result"),
                        provenance=dict(payload.get("provenance") or {}),
                        limitations=list(payload.get("limitations") or []),
                        error=payload.get("error"),
                    ).model_dump(mode="json")
                )
            except Exception as exc:
                output.append(
                    ExecutionRecord(
                        tool=step.tool,
                        status="execution_error",
                        requested_operation=operation,
                        error={
                            "code": type(exc).__name__,
                            "message": str(exc),
                        },
                    ).model_dump(mode="json")
                )
        return output
