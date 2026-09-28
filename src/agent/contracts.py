"""Strict Phase 10 planning contracts.

The language model can only propose a plan. These models enforce the Phase 8
Tool names, dependencies, global execution order, and typed Tool inputs before
anything can be executed.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ToolId = Literal[
    "query_training_log",
    "compute_metrics",
    "search_literature",
]
TaskType = Literal[
    "literature_only",
    "log_lookup",
    "log_metric",
    "hybrid",
    "unsupported",
    "ambiguous",
]
TOOL_ORDER = (
    "query_training_log",
    "compute_metrics",
    "search_literature",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _validate_iso_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD") from exc
    return value


class TrainingLogArguments(StrictModel):
    operation: Literal[
        "exercise_records",
        "exercise_first_last",
        "get_session",
        "list_sessions",
    ]
    canonical_exercise_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    session_id: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)
    include_lineage: bool = True

    _start_date = field_validator("start_date")(_validate_iso_date)
    _end_date = field_validator("end_date")(_validate_iso_date)

    @field_validator("canonical_exercise_name", "session_id")
    @classmethod
    def nonempty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> "TrainingLogArguments":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if self.operation in {"exercise_records", "exercise_first_last"}:
            if not self.canonical_exercise_name:
                raise ValueError(
                    "canonical_exercise_name is required for exercise operations"
                )
        if self.operation == "get_session" and not self.session_id:
            raise ValueError("session_id is required for get_session")
        return self


class MetricArguments(StrictModel):
    operation: Literal[
        "estimated_1rm",
        "first_last_n_session_median_e1rm",
        "weekly_volume",
        "weekly_frequency",
        "training_gap",
        "plateau_candidates",
    ]
    records_source: Literal["query_training_log"] = "query_training_log"
    canonical_exercise_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    n_sessions: int = Field(default=5, ge=1, le=50)

    _start_date = field_validator("start_date")(_validate_iso_date)
    _end_date = field_validator("end_date")(_validate_iso_date)

    @field_validator("canonical_exercise_name")
    @classmethod
    def nonempty_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("canonical_exercise_name must not be blank")
        return value

    @model_validator(mode="after")
    def validate_dates(self) -> "MetricArguments":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        return self


class LiteratureArguments(StrictModel):
    operation: Literal["search"] = "search"
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=10)


class TrainingLogPlanStep(StrictModel):
    tool: Literal["query_training_log"]
    reason: str = Field(min_length=1, max_length=500)
    subtask: str = Field(min_length=1, max_length=500)
    inputs: TrainingLogArguments


class MetricPlanStep(StrictModel):
    tool: Literal["compute_metrics"]
    reason: str = Field(min_length=1, max_length=500)
    subtask: str = Field(min_length=1, max_length=500)
    inputs: MetricArguments


class LiteraturePlanStep(StrictModel):
    tool: Literal["search_literature"]
    reason: str = Field(min_length=1, max_length=500)
    subtask: str = Field(min_length=1, max_length=500)
    inputs: LiteratureArguments


PlanStep = Union[TrainingLogPlanStep, MetricPlanStep, LiteraturePlanStep]


class PlannerDraft(StrictModel):
    task_type: TaskType
    unsupported: bool
    unsupported_reason: str | None = Field(default=None, max_length=500)
    planner_reasoning_summary: str = Field(min_length=1, max_length=1000)
    steps: list[PlanStep] = Field(max_length=3)

    @field_validator("unsupported_reason")
    @classmethod
    def nonempty_reason(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("unsupported_reason must not be blank")
        return value

    @model_validator(mode="after")
    def validate_plan_semantics(self) -> "PlannerDraft":
        tools = [step.tool for step in self.steps]
        if len(tools) != len(set(tools)):
            raise ValueError("each Tool may appear at most once")
        positions = [TOOL_ORDER.index(tool) for tool in tools]
        if positions != sorted(positions):
            raise ValueError("Tool steps violate the fixed execution order")
        if "compute_metrics" in tools:
            metric_index = tools.index("compute_metrics")
            if "query_training_log" not in tools[:metric_index]:
                raise ValueError("compute_metrics requires a prior query_training_log")

        expected: dict[str, tuple[tuple[str, ...], ...]] = {
            "literature_only": (("search_literature",),),
            "log_lookup": (("query_training_log",),),
            "log_metric": (("query_training_log", "compute_metrics"),),
            "hybrid": (
                ("query_training_log", "search_literature"),
                (
                    "query_training_log",
                    "compute_metrics",
                    "search_literature",
                ),
            ),
            "unsupported": ((),),
            "ambiguous": ((),),
        }
        if tuple(tools) not in expected[self.task_type]:
            raise ValueError("Tool set does not match task_type")
        if self.task_type == "unsupported":
            if not self.unsupported or not self.unsupported_reason:
                raise ValueError(
                    "unsupported plans require unsupported=true and a reason"
                )
        elif self.unsupported:
            raise ValueError("unsupported=true is only valid for unsupported plans")
        elif self.unsupported_reason is not None:
            raise ValueError("unsupported_reason is only valid for unsupported plans")
        return self

    @property
    def selected_tools(self) -> list[str]:
        return [step.tool for step in self.steps]

    @property
    def execution_order(self) -> list[str]:
        return self.selected_tools.copy()

    @property
    def tool_inputs(self) -> dict[str, dict[str, Any]]:
        return {
            step.tool: step.inputs.model_dump(mode="json") for step in self.steps
        }

    def to_plan_dict(self) -> dict[str, Any]:
        value = self.model_dump(mode="json")
        value["selected_tools"] = self.selected_tools
        value["execution_order"] = self.execution_order
        value["tool_inputs"] = self.tool_inputs
        return value


class PlannerUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class PlannerCallResult(StrictModel):
    raw_plan: dict[str, Any] | None = None
    model: str
    response_id: str | None = None
    latency_ms: float = Field(ge=0.0)
    usage: PlannerUsage
    error: str | None = None
