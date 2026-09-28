"""Typed input/output contract for a non-executing deterministic Router."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class TaskType(str, Enum):
    LITERATURE_ONLY = "literature_only"
    LOG_LOOKUP = "log_lookup"
    LOG_METRIC = "log_metric"
    HYBRID = "hybrid"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


class RoutingStatus(str, Enum):
    PLANNED = "planned"
    UNSUPPORTED = "unsupported"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


@dataclass(frozen=True)
class RouterInput:
    question: str


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    normalized_question: str
    signals: Mapping[str, bool]
    matched_patterns: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class RoutingError:
    code: str
    message: str


@dataclass(frozen=True)
class RoutingPlan:
    router_version: str
    status: RoutingStatus
    route: str
    task_type: TaskType
    selected_tools: tuple[str, ...]
    execution_order: tuple[str, ...]
    routing_reason: str
    confidence: float
    rule_match: RuleMatch
    unsupported_reason: str | None = None
    error: RoutingError | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["task_type"] = self.task_type.value
        value["selected_tools"] = list(self.selected_tools)
        value["execution_order"] = list(self.execution_order)
        value["rule_match"]["matched_patterns"] = {
            key: list(items)
            for key, items in self.rule_match.matched_patterns.items()
        }
        return value
