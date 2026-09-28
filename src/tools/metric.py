"""Deterministic metrics over records returned by the Training Log Tool."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
import hashlib
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from src.metrics.definitions import (
    E1RM_FORMULA_VERSION,
    E1RM_REP_MAX,
    E1RM_REP_MIN,
    PLATEAU_MAX_ABS_CHANGE_PCT,
    PLATEAU_MAX_RANGE_PCT,
    PLATEAU_MIN_DAYS,
    PLATEAU_WINDOW_SESSIONS,
    epley_e1rm,
)

from .contracts import (
    ToolError,
    ToolErrorCode,
    ToolResponse,
    failure_response,
    success_response,
)


TOOL_VERSION = "metric_tool_v1"
PREPROCESSING_VERSION = "preprocessing_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / "config/preprocessing_v1.json"


class MetricOperation(str, Enum):
    ESTIMATED_1RM = "estimated_1rm"
    FIRST_LAST_MEDIAN_E1RM = "first_last_n_session_median_e1rm"
    WEEKLY_VOLUME = "weekly_volume"
    WEEKLY_FREQUENCY = "weekly_frequency"
    TRAINING_GAP = "training_gap"
    PLATEAU_CANDIDATES = "plateau_candidates"


@dataclass(frozen=True)
class MetricInput:
    operation: MetricOperation | str
    records: Sequence[Mapping[str, Any]]
    canonical_exercise_name: str | None = None
    start_date: date | datetime | str | None = None
    end_date: date | datetime | str | None = None
    n_sessions: int = 5


def _parse_datetime(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO date/time") from exc
    raise ValueError(f"{field} must be a datetime or ISO date/time string")


def _parse_date(value: date | datetime | str | None, *, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field} must use YYYY-MM-DD") from exc
    raise ValueError(f"{field} must be a date or YYYY-MM-DD string")


def _week_start(value: datetime) -> date:
    return value.date() - timedelta(days=value.weekday())


class MetricTool:
    """Pure metric component: it neither queries PostgreSQL nor makes diagnoses."""

    def __init__(self) -> None:
        self._policy_sha256 = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "tool": TOOL_VERSION,
            "preprocessing_version": PREPROCESSING_VERSION,
            "preprocessing_policy_sha256": self._policy_sha256,
            "e1rm": {
                "formula_version": E1RM_FORMULA_VERSION,
                "formula": "weight * (1 + reps / 30)",
                "eligible_rep_range": [E1RM_REP_MIN, E1RM_REP_MAX],
                "eligibility_source": "stored include_in_e1rm flag",
            },
            "volume_eligibility_source": "stored include_in_volume_metrics flag",
            "plateau_rule": {
                "label": "performance plateau candidate",
                "minimum_sessions": PLATEAU_WINDOW_SESSIONS,
                "minimum_days": PLATEAU_MIN_DAYS,
                "maximum_absolute_change_pct": PLATEAU_MAX_ABS_CHANGE_PCT,
                "maximum_range_pct": PLATEAU_MAX_RANGE_PCT,
            },
        }

    @staticmethod
    def _operation(value: MetricOperation | str) -> MetricOperation | None:
        try:
            return MetricOperation(value)
        except (TypeError, ValueError):
            return None

    def _prepare_records(self, request: MetricInput) -> list[dict[str, Any]]:
        start = _parse_date(request.start_date, field="start_date")
        end = _parse_date(request.end_date, field="end_date")
        if start and end and start > end:
            raise ValueError("start_date must be on or before end_date")
        exercise = (request.canonical_exercise_name or "").strip()
        prepared: list[dict[str, Any]] = []
        for index, source in enumerate(request.records):
            if not isinstance(source, Mapping):
                raise ValueError(f"records[{index}] must be a mapping")
            record = dict(source)
            started_at = _parse_datetime(record.get("started_at"), field="started_at")
            if start and started_at.date() < start:
                continue
            if end and started_at.date() > end:
                continue
            record_exercise = str(record.get("exercise_name", "")).strip()
            if exercise and record_exercise != exercise:
                continue
            record["started_at"] = started_at
            prepared.append(record)
        return sorted(
            prepared,
            key=lambda item: (
                item["started_at"],
                int(item.get("set_order", 0)),
                str(item.get("set_id", "")),
            ),
        )

    @staticmethod
    def _validate_flag(record: Mapping[str, Any], flag: str) -> bool:
        value = record.get(flag)
        if not isinstance(value, bool):
            raise ValueError(f"Every record must contain boolean {flag}")
        return value

    @staticmethod
    def _numeric(record: Mapping[str, Any], field: str) -> float:
        value = record.get(field)
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric")
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be numeric") from exc
        if not math.isfinite(converted):
            raise ValueError(f"{field} must be finite")
        return converted

    def _e1rm_sets(
        self, records: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], Counter[str]]:
        included: list[dict[str, Any]] = []
        excluded: Counter[str] = Counter()
        for record in records:
            eligible = self._validate_flag(record, "include_in_e1rm")
            if not eligible:
                reason = str(
                    record.get("outlier_reason")
                    or record.get("validation_reason")
                    or "stored_include_in_e1rm_false"
                )
                excluded[reason] += 1
                continue
            weight = self._numeric(record, "weight")
            reps_value = self._numeric(record, "reps")
            reps = int(reps_value)
            if reps_value != reps or weight <= 0 or not E1RM_REP_MIN <= reps <= E1RM_REP_MAX:
                raise ValueError(
                    "Stored include_in_e1rm=true violates preprocessing_v1 eligibility"
                )
            formula_version = record.get("e1rm_formula_version", E1RM_FORMULA_VERSION)
            if formula_version != E1RM_FORMULA_VERSION:
                raise ValueError(
                    f"Unsupported stored e1rm_formula_version: {formula_version!r}"
                )
            included.append(
                {
                    "set_id": record.get("set_id"),
                    "session_id": record.get("session_id"),
                    "started_at": record["started_at"],
                    "exercise_name": record.get("exercise_name"),
                    "weight": weight,
                    "reps": reps,
                    "estimated_1rm": float(epley_e1rm(weight, reps)),
                    "formula_version": E1RM_FORMULA_VERSION,
                }
            )
        return included, excluded

    @staticmethod
    def _session_best(e1rm_sets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        by_session: dict[tuple[str, datetime], list[Mapping[str, Any]]] = defaultdict(list)
        for item in e1rm_sets:
            key = (str(item.get("session_id") or ""), item["started_at"])
            by_session[key].append(item)
        sessions = []
        for (session_id, started_at), items in sorted(
            by_session.items(), key=lambda pair: (pair[0][1], pair[0][0])
        ):
            best = max(items, key=lambda item: float(item["estimated_1rm"]))
            sessions.append(
                {
                    "session_id": session_id or None,
                    "started_at": started_at,
                    "session_best_e1rm": float(best["estimated_1rm"]),
                    "source_set_id": best.get("set_id"),
                    "eligible_set_count": len(items),
                }
            )
        return sessions

    @staticmethod
    def _plateau_candidates(sessions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        qualifying: list[tuple[int, int]] = []
        values = [float(item["session_best_e1rm"]) for item in sessions]
        dates = [item["started_at"] for item in sessions]
        for start in range(0, len(sessions) - PLATEAU_WINDOW_SESSIONS + 1):
            for end in range(start + PLATEAU_WINDOW_SESSIONS - 1, len(sessions)):
                span_days = (dates[end] - dates[start]).days
                if span_days < PLATEAU_MIN_DAYS:
                    continue
                window = values[start : end + 1]
                start_level = float(median(window[:3]))
                end_level = float(median(window[-3:]))
                median_level = float(median(window))
                if start_level <= 0 or median_level <= 0:
                    continue
                change_pct = 100.0 * (end_level / start_level - 1.0)
                range_pct = 100.0 * (max(window) - min(window)) / median_level
                if (
                    abs(change_pct) <= PLATEAU_MAX_ABS_CHANGE_PCT
                    and range_pct <= PLATEAU_MAX_RANGE_PCT
                ):
                    qualifying.append((start, end))
        qualifying.sort(
            key=lambda item: (
                -(item[1] - item[0] + 1),
                -(dates[item[1]] - dates[item[0]]).days,
                item[0],
            )
        )
        selected: list[tuple[int, int]] = []
        for start, end in qualifying:
            if not any(not (end < left or start > right) for left, right in selected):
                selected.append((start, end))
        output: list[dict[str, Any]] = []
        for start, end in sorted(selected):
            window = values[start : end + 1]
            start_level = float(median(window[:3]))
            end_level = float(median(window[-3:]))
            median_level = float(median(window))
            output.append(
                {
                    "start_date": dates[start],
                    "end_date": dates[end],
                    "duration_days": (dates[end] - dates[start]).days,
                    "session_count": end - start + 1,
                    "start_3_session_median_e1rm": start_level,
                    "end_3_session_median_e1rm": end_level,
                    "change_pct": 100.0 * (end_level / start_level - 1.0),
                    "within_period_range_pct": 100.0
                    * (max(window) - min(window))
                    / median_level,
                    "classification": "performance_plateau_candidate",
                }
            )
        return sorted(
            output,
            key=lambda item: (-item["duration_days"], -item["session_count"]),
        )

    def execute(self, request: MetricInput) -> ToolResponse[Any]:
        raw_operation = (
            request.operation.value
            if isinstance(request.operation, MetricOperation)
            else str(request.operation)
        )
        operation = self._operation(request.operation)
        if operation is None:
            return failure_response(
                operation=raw_operation,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    "Unsupported Metric operation",
                    {"supported": [item.value for item in MetricOperation]},
                ),
                provenance=self.provenance,
            )
        if not isinstance(request.records, Sequence) or isinstance(
            request.records, (str, bytes)
        ):
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT, "records must be a sequence of mappings"
                ),
                provenance=self.provenance,
            )
        if not isinstance(request.n_sessions, int) or not 1 <= request.n_sessions <= 50:
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    "n_sessions must be an integer between 1 and 50",
                ),
                provenance=self.provenance,
            )
        try:
            records = self._prepare_records(request)
        except (TypeError, ValueError) as exc:
            code = (
                ToolErrorCode.INVALID_DATE_RANGE
                if "date" in str(exc).lower()
                else ToolErrorCode.INVALID_INPUT
            )
            return failure_response(
                operation=operation.value,
                error=ToolError(code, str(exc)),
                provenance=self.provenance,
            )
        if not records:
            return success_response(
                operation=operation.value,
                result={"record_count": 0, "values": []},
                provenance=self.provenance,
                empty=True,
            )

        try:
            if operation in {
                MetricOperation.ESTIMATED_1RM,
                MetricOperation.FIRST_LAST_MEDIAN_E1RM,
                MetricOperation.PLATEAU_CANDIDATES,
            }:
                e1rm_sets, excluded = self._e1rm_sets(records)
                sessions = self._session_best(e1rm_sets)
                if operation is MetricOperation.ESTIMATED_1RM:
                    result = {
                        "eligible_set_count": len(e1rm_sets),
                        "excluded_set_count": len(records) - len(e1rm_sets),
                        "excluded_reasons": dict(excluded),
                        "set_e1rm": e1rm_sets,
                        "session_best_e1rm": sessions,
                    }
                elif operation is MetricOperation.FIRST_LAST_MEDIAN_E1RM:
                    if not sessions:
                        result = {"eligible_session_count": 0, "values": []}
                    else:
                        window = min(request.n_sessions, len(sessions))
                        first_value = float(
                            median(
                                item["session_best_e1rm"] for item in sessions[:window]
                            )
                        )
                        last_value = float(
                            median(
                                item["session_best_e1rm"] for item in sessions[-window:]
                            )
                        )
                        result = {
                            "eligible_session_count": len(sessions),
                            "requested_n_sessions": request.n_sessions,
                            "used_n_sessions": window,
                            "first_median_e1rm": first_value,
                            "last_median_e1rm": last_value,
                            "change_pct": 100.0 * (last_value / first_value - 1.0),
                            "first_window": sessions[:window],
                            "last_window": sessions[-window:],
                        }
                else:
                    candidates = self._plateau_candidates(sessions)
                    result = {
                        "eligible_session_count": len(sessions),
                        "candidate_count": len(candidates),
                        "candidates": candidates,
                    }
                return success_response(
                    operation=operation.value,
                    result=result,
                    provenance=self.provenance,
                    limitations=(
                        "e1RM is an estimate, not an observed 1RM.",
                        "No causal diagnosis is inferred from metric values.",
                    ),
                    empty=not e1rm_sets,
                )

            if operation is MetricOperation.WEEKLY_VOLUME:
                weekly: dict[date, dict[str, Any]] = {}
                excluded = 0
                for record in records:
                    if not self._validate_flag(record, "include_in_volume_metrics"):
                        excluded += 1
                        continue
                    weight = self._numeric(record, "weight")
                    reps = self._numeric(record, "reps")
                    if weight <= 0 or reps <= 0:
                        raise ValueError(
                            "Stored include_in_volume_metrics=true violates preprocessing_v1 eligibility"
                        )
                    week = _week_start(record["started_at"])
                    bucket = weekly.setdefault(
                        week,
                        {
                            "week_start": week,
                            "eligible_set_count": 0,
                            "rep_count": 0,
                            "volume_load": 0.0,
                        },
                    )
                    bucket["eligible_set_count"] += 1
                    bucket["rep_count"] += int(reps)
                    bucket["volume_load"] += weight * reps
                values = [weekly[key] for key in sorted(weekly)]
                result = {
                    "eligible_set_count": sum(
                        item["eligible_set_count"] for item in values
                    ),
                    "excluded_set_count": excluded,
                    "weeks": values,
                }
                return success_response(
                    operation=operation.value,
                    result=result,
                    provenance=self.provenance,
                    empty=not values,
                )

            if operation is MetricOperation.WEEKLY_FREQUENCY:
                weekly_sessions: dict[date, set[tuple[str, datetime]]] = defaultdict(set)
                for record in records:
                    week = _week_start(record["started_at"])
                    weekly_sessions[week].add(
                        (str(record.get("session_id") or ""), record["started_at"])
                    )
                values = [
                    {"week_start": week, "frequency": len(weekly_sessions[week])}
                    for week in sorted(weekly_sessions)
                ]
                return success_response(
                    operation=operation.value,
                    result={"weeks": values},
                    provenance=self.provenance,
                    empty=not values,
                )

            unique_sessions = sorted(
                {
                    (str(record.get("session_id") or ""), record["started_at"])
                    for record in records
                },
                key=lambda item: (item[1], item[0]),
            )
            gaps = []
            for previous, current in zip(unique_sessions, unique_sessions[1:]):
                gap_hours = (current[1] - previous[1]).total_seconds() / 3600.0
                gaps.append(
                    {
                        "previous_session_id": previous[0] or None,
                        "previous_session": previous[1],
                        "next_session_id": current[0] or None,
                        "next_session": current[1],
                        "gap_hours": gap_hours,
                        "gap_days": gap_hours / 24.0,
                        "calendar_day_difference": (
                            current[1].date() - previous[1].date()
                        ).days,
                    }
                )
            gaps.sort(key=lambda item: (-item["gap_hours"], item["previous_session"]))
            return success_response(
                operation=operation.value,
                result={
                    "session_count": len(unique_sessions),
                    "gap_count": len(gaps),
                    "gaps": gaps,
                },
                provenance=self.provenance,
                empty=not gaps,
            )
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            return failure_response(
                operation=operation.value,
                error=ToolError(ToolErrorCode.DATA_INTEGRITY_ERROR, str(exc)),
                provenance=self.provenance,
            )
