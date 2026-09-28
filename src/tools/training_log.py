"""Deterministic read-only access to normalized PostgreSQL training records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
import hashlib
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.database.config import DatabaseConfig

from .contracts import (
    ToolError,
    ToolErrorCode,
    ToolResponse,
    failure_response,
    success_response,
)


PREPROCESSING_VERSION = "preprocessing_v1"
TOOL_VERSION = "training_log_tool_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / "config/preprocessing_v1.json"


class TrainingLogOperation(str, Enum):
    EXERCISE_RECORDS = "exercise_records"
    EXERCISE_FIRST_LAST = "exercise_first_last"
    GET_SESSION = "get_session"
    LIST_SESSIONS = "list_sessions"


@dataclass(frozen=True)
class TrainingLogInput:
    operation: TrainingLogOperation | str
    canonical_exercise_name: str | None = None
    start_date: date | datetime | str | None = None
    end_date: date | datetime | str | None = None
    session_id: str | None = None
    limit: int = 500
    include_lineage: bool = True


class TrainingRepository(Protocol):
    def resolve_canonical_exercise(self, name: str) -> str | None: ...

    def exercise_records(
        self,
        canonical_name: str,
        *,
        start_at: datetime | None,
        end_before: datetime | None,
        limit: int,
        include_lineage: bool,
    ) -> list[dict[str, Any]]: ...

    def exercise_first_last(
        self, canonical_name: str, *, include_lineage: bool
    ) -> dict[str, Any] | None: ...

    def get_session(
        self, session_id: str, *, include_lineage: bool
    ) -> dict[str, Any] | None: ...

    def list_sessions(
        self,
        *,
        canonical_name: str | None,
        start_at: datetime | None,
        end_before: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]: ...


class PsycopgTrainingRepository:
    """Parameterized, read-only SQL adapter for the Phase 3 schema."""

    def __init__(self, *, config: DatabaseConfig, password: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Training Log Tool requires psycopg") from exc
        self._psycopg = psycopg
        self._connect_args = {
            "host": config.host,
            "port": config.port,
            "dbname": config.database,
            "user": config.user,
            "password": password,
            "autocommit": True,
            "row_factory": dict_row,
            "options": "-c default_transaction_read_only=on",
        }

    def _fetch_all(
        self, sql: str, params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        with self._psycopg.connect(**self._connect_args) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def resolve_canonical_exercise(self, name: str) -> str | None:
        rows = self._fetch_all(
            """
            SELECT canonical_name
            FROM training.exercises
            WHERE lower(canonical_name) = lower(%s)
            ORDER BY canonical_name
            """,
            (name,),
        )
        return str(rows[0]["canonical_name"]) if len(rows) == 1 else None

    @staticmethod
    def _set_select(include_lineage: bool) -> str:
        lineage = (
            "COALESCE((SELECT jsonb_agg(jsonb_build_object("
            "'source_row', rl.source_row, 'row_status', rl.row_status, "
            "'raw_record_id', rl.raw_record_id) ORDER BY rl.source_row) "
            "FROM training.row_lineage AS rl WHERE rl.set_id = ws.set_id), '[]'::jsonb)"
            if include_lineage
            else "NULL::jsonb"
        )
        return f"""
            SELECT ws.set_id, ws.session_id, s.started_at, s.workout_name,
                   e.canonical_name AS exercise_name, ws.raw_exercise_name,
                   ws.set_order, ws.weight, ws.weight_unit, ws.reps,
                   ws.distance, ws.distance_unit, ws.seconds, ws.notes,
                   ws.workout_notes, ws.is_outlier, ws.outlier_reason,
                   ws.has_set_order_collision, ws.include_in_volume_metrics,
                   ws.include_in_e1rm, ws.e1rm_formula_version,
                   ws.validation_reason, ws.representative_source_row,
                   ws.raw_row_count, ws.exact_duplicate_extra_count,
                   ws.alias_shadow_count, ws.source_file_sha256,
                   ws.preprocessing_version, {lineage} AS lineage
            FROM training.workout_sets AS ws
            JOIN training.workout_sessions AS s USING (session_id)
            JOIN training.exercises AS e USING (exercise_id)
        """

    def exercise_records(
        self,
        canonical_name: str,
        *,
        start_at: datetime | None,
        end_before: datetime | None,
        limit: int,
        include_lineage: bool,
    ) -> list[dict[str, Any]]:
        conditions = ["e.canonical_name = %s", "ws.preprocessing_version = %s"]
        params: list[Any] = [canonical_name, PREPROCESSING_VERSION]
        if start_at is not None:
            conditions.append("s.started_at >= %s")
            params.append(start_at)
        if end_before is not None:
            conditions.append("s.started_at < %s")
            params.append(end_before)
        params.append(limit)
        return self._fetch_all(
            self._set_select(include_lineage)
            + " WHERE "
            + " AND ".join(conditions)
            + " ORDER BY s.started_at, ws.set_order, ws.set_id LIMIT %s",
            params,
        )

    def exercise_first_last(
        self, canonical_name: str, *, include_lineage: bool
    ) -> dict[str, Any] | None:
        base = self._set_select(include_lineage)
        params = (canonical_name, PREPROCESSING_VERSION)
        where = " WHERE e.canonical_name = %s AND ws.preprocessing_version = %s"
        first = self._fetch_all(
            base + where + " ORDER BY s.started_at, ws.set_order, ws.set_id LIMIT 1",
            params,
        )
        last = self._fetch_all(
            base
            + where
            + " ORDER BY s.started_at DESC, ws.set_order DESC, ws.set_id DESC LIMIT 1",
            params,
        )
        if not first:
            return None
        return {"first_record": first[0], "last_record": last[0]}

    def get_session(
        self, session_id: str, *, include_lineage: bool
    ) -> dict[str, Any] | None:
        sessions = self._fetch_all(
            """
            SELECT session_id, started_at, workout_name, preprocessing_version
            FROM training.workout_sessions
            WHERE session_id = %s AND preprocessing_version = %s
            """,
            (session_id, PREPROCESSING_VERSION),
        )
        if not sessions:
            return None
        sets = self._fetch_all(
            self._set_select(include_lineage)
            + " WHERE ws.session_id = %s AND ws.preprocessing_version = %s"
            + " ORDER BY e.canonical_name, ws.set_order, ws.set_id",
            (session_id, PREPROCESSING_VERSION),
        )
        return {**sessions[0], "sets": sets}

    def list_sessions(
        self,
        *,
        canonical_name: str | None,
        start_at: datetime | None,
        end_before: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        conditions = ["s.preprocessing_version = %s"]
        params: list[Any] = [PREPROCESSING_VERSION]
        if canonical_name is not None:
            conditions.append("e.canonical_name = %s")
            params.append(canonical_name)
        if start_at is not None:
            conditions.append("s.started_at >= %s")
            params.append(start_at)
        if end_before is not None:
            conditions.append("s.started_at < %s")
            params.append(end_before)
        params.append(limit)
        return self._fetch_all(
            """
            SELECT s.session_id, s.started_at, s.workout_name,
                   count(DISTINCT ws.set_id) AS set_count,
                   count(DISTINCT ws.exercise_id) AS exercise_count,
                   array_agg(DISTINCT e.canonical_name ORDER BY e.canonical_name)
                       AS exercise_names,
                   s.preprocessing_version
            FROM training.workout_sessions AS s
            JOIN training.workout_sets AS ws USING (session_id)
            JOIN training.exercises AS e USING (exercise_id)
            WHERE """
            + " AND ".join(conditions)
            + " GROUP BY s.session_id, s.started_at, s.workout_name, s.preprocessing_version"
            + " ORDER BY s.started_at, s.session_id LIMIT %s",
            params,
        )


class TrainingLogTool:
    def __init__(self, repository: TrainingRepository) -> None:
        self._repository = repository
        self._policy_sha256 = hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()

    @property
    def provenance(self) -> dict[str, Any]:
        return {
            "tool": TOOL_VERSION,
            "data_source": "PostgreSQL training schema (read-only queries)",
            "preprocessing_version": PREPROCESSING_VERSION,
            "preprocessing_policy_sha256": self._policy_sha256,
        }

    @staticmethod
    def _operation(value: TrainingLogOperation | str) -> TrainingLogOperation | None:
        try:
            return TrainingLogOperation(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date_bounds(
        start: date | datetime | str | None,
        end: date | datetime | str | None,
    ) -> tuple[datetime | None, datetime | None]:
        def parse(value: date | datetime | str | None) -> date | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            if isinstance(value, str):
                return date.fromisoformat(value)
            raise ValueError("dates must be ISO YYYY-MM-DD strings or date values")

        start_date = parse(start)
        end_date = parse(end)
        if start_date and end_date and start_date > end_date:
            raise ValueError("start_date must be on or before end_date")
        return (
            datetime.combine(start_date, time.min) if start_date else None,
            datetime.combine(end_date + timedelta(days=1), time.min)
            if end_date
            else None,
        )

    def execute(self, request: TrainingLogInput) -> ToolResponse[Any]:
        raw_operation = (
            request.operation.value
            if isinstance(request.operation, TrainingLogOperation)
            else str(request.operation)
        )
        operation = self._operation(request.operation)
        if operation is None:
            return failure_response(
                operation=raw_operation,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    "Unsupported Training Log operation",
                    {"supported": [item.value for item in TrainingLogOperation]},
                ),
                provenance=self.provenance,
            )
        if not isinstance(request.limit, int) or not 1 <= request.limit <= 5_000:
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT, "limit must be between 1 and 5000"
                ),
                provenance=self.provenance,
            )
        try:
            start_at, end_before = self._date_bounds(
                request.start_date, request.end_date
            )
        except (TypeError, ValueError) as exc:
            return failure_response(
                operation=operation.value,
                error=ToolError(ToolErrorCode.INVALID_DATE_RANGE, str(exc)),
                provenance=self.provenance,
            )

        requires_exercise = operation in {
            TrainingLogOperation.EXERCISE_RECORDS,
            TrainingLogOperation.EXERCISE_FIRST_LAST,
        }
        name = (request.canonical_exercise_name or "").strip()
        if requires_exercise and not name:
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    "canonical_exercise_name is required for this operation",
                ),
                provenance=self.provenance,
            )
        try:
            canonical = (
                self._repository.resolve_canonical_exercise(name) if name else None
            )
            if name and canonical is None:
                return failure_response(
                    operation=operation.value,
                    error=ToolError(
                        ToolErrorCode.UNKNOWN_EXERCISE,
                        "No exact canonical exercise name matched",
                        {"requested": name},
                    ),
                    provenance=self.provenance,
                )

            if operation is TrainingLogOperation.EXERCISE_RECORDS:
                records = self._repository.exercise_records(
                    canonical or "",
                    start_at=start_at,
                    end_before=end_before,
                    limit=request.limit,
                    include_lineage=request.include_lineage,
                )
                result = {
                    "canonical_exercise_name": canonical,
                    "date_range": {
                        "start_date": request.start_date,
                        "end_date": request.end_date,
                    },
                    "record_count": len(records),
                    "records": records,
                    "truncated_at_limit": len(records) == request.limit,
                }
                return success_response(
                    operation=operation.value,
                    result=result,
                    provenance=self.provenance,
                    limitations=(
                        "Weight uses the source's unverified unit label.",
                        "Records are returned as stored; no imputation or correction is applied.",
                    ),
                    empty=not records,
                )

            if operation is TrainingLogOperation.EXERCISE_FIRST_LAST:
                bounds = self._repository.exercise_first_last(
                    canonical or "", include_lineage=request.include_lineage
                )
                return success_response(
                    operation=operation.value,
                    result={"canonical_exercise_name": canonical, "records": bounds},
                    provenance=self.provenance,
                    empty=bounds is None,
                )

            if operation is TrainingLogOperation.GET_SESSION:
                session_id = (request.session_id or "").strip()
                if not session_id:
                    return failure_response(
                        operation=operation.value,
                        error=ToolError(
                            ToolErrorCode.INVALID_INPUT,
                            "session_id is required for get_session",
                        ),
                        provenance=self.provenance,
                    )
                session = self._repository.get_session(
                    session_id, include_lineage=request.include_lineage
                )
                if session is None:
                    return failure_response(
                        operation=operation.value,
                        error=ToolError(
                            ToolErrorCode.NOT_FOUND,
                            "Session was not found",
                            {"session_id": session_id},
                        ),
                        provenance=self.provenance,
                    )
                return success_response(
                    operation=operation.value,
                    result=session,
                    provenance=self.provenance,
                )

            sessions = self._repository.list_sessions(
                canonical_name=canonical,
                start_at=start_at,
                end_before=end_before,
                limit=request.limit,
            )
            return success_response(
                operation=operation.value,
                result={
                    "canonical_exercise_name": canonical,
                    "session_count": len(sessions),
                    "sessions": sessions,
                    "truncated_at_limit": len(sessions) == request.limit,
                },
                provenance=self.provenance,
                empty=not sessions,
            )
        except Exception as exc:  # Adapter boundary: preserve a structured failure.
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.DATABASE_ERROR,
                    "PostgreSQL training query failed",
                    {"exception_type": type(exc).__name__, "message": str(exc)},
                ),
                provenance=self.provenance,
            )
