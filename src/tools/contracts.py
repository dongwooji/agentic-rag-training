"""Shared JSON-serializable contracts for deterministic tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Mapping, TypeVar


class ToolStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    FAILURE = "failure"


class ToolErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    INVALID_DATE_RANGE = "invalid_date_range"
    UNKNOWN_EXERCISE = "unknown_exercise"
    NOT_FOUND = "not_found"
    DATA_INTEGRITY_ERROR = "data_integrity_error"
    DATABASE_ERROR = "database_error"
    CONFIGURATION_ERROR = "configuration_error"
    RETRIEVAL_ERROR = "retrieval_error"


@dataclass(frozen=True)
class ToolError:
    code: ToolErrorCode
    message: str
    details: Mapping[str, Any] | None = None


ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ToolResponse(Generic[ResultT]):
    """Stable envelope consumed later by the Router/Agent layers."""

    success: bool
    status: ToolStatus
    requested_operation: str
    result: ResultT | None
    provenance: Mapping[str, Any]
    limitations: tuple[str, ...] = ()
    error: ToolError | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def success_response(
    *,
    operation: str,
    result: ResultT,
    provenance: Mapping[str, Any],
    limitations: tuple[str, ...] = (),
    empty: bool = False,
) -> ToolResponse[ResultT]:
    return ToolResponse(
        success=True,
        status=ToolStatus.EMPTY if empty else ToolStatus.SUCCESS,
        requested_operation=operation,
        result=result,
        provenance=provenance,
        limitations=limitations,
    )


def failure_response(
    *,
    operation: str,
    error: ToolError,
    provenance: Mapping[str, Any],
    limitations: tuple[str, ...] = (),
) -> ToolResponse[Any]:
    return ToolResponse(
        success=False,
        status=ToolStatus.FAILURE,
        requested_operation=operation,
        result=None,
        provenance=provenance,
        limitations=limitations,
        error=error,
    )


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
