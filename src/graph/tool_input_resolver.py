"""Resolve only Router-selected structured Tool arguments from a question."""

from __future__ import annotations

import csv
from enum import Enum
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Literal, Mapping, Protocol
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent.contracts import MetricArguments, PlannerUsage, TrainingLogArguments


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALIASES_PATH = PROJECT_ROOT / "config/exercise_aliases_v1.csv"
RESOLVER_VERSION = "tool_input_resolver_v1"
STRUCTURED_TOOL_ORDER = ("query_training_log", "compute_metrics")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResolverExecutionStatus(str, Enum):
    READY = "ready"
    NOT_REQUIRED = "not_required"
    FAILURE = "failure"


class ResolverErrorCode(str, Enum):
    INVALID_ROUTE = "invalid_route"
    MISSING_REQUIRED_ARGUMENT = "missing_required_argument"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    UNKNOWN_EXERCISE = "unknown_exercise"
    UNGROUNDED_ARGUMENT = "ungrounded_argument"
    SCHEMA_FAILURE = "schema_failure"
    PROVIDER_FAILURE = "provider_failure"


class ToolInputResolverRequest(StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    task_type: str = Field(min_length=1)
    selected_tools: list[str] = Field(max_length=3)
    execution_order: list[str] = Field(max_length=3)
    route: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_router_plan(self) -> "ToolInputResolverRequest":
        allowed = {
            "query_training_log",
            "compute_metrics",
            "search_literature",
        }
        if any(tool not in allowed for tool in self.selected_tools):
            raise ValueError("selected_tools contains an unknown Tool")
        if len(self.selected_tools) != len(set(self.selected_tools)):
            raise ValueError("selected_tools contains duplicates")
        if self.execution_order != self.selected_tools:
            raise ValueError("Resolver cannot change Router execution order")
        structured = [
            tool for tool in self.selected_tools if tool in STRUCTURED_TOOL_ORDER
        ]
        if "compute_metrics" in structured and (
            not structured or structured[0] != "query_training_log"
        ):
            raise ValueError("compute_metrics requires query_training_log first")
        return self

    @property
    def structured_tools(self) -> list[str]:
        return [
            tool for tool in self.selected_tools if tool in STRUCTURED_TOOL_ORDER
        ]


class ToolInputExtractionDraft(StrictModel):
    """Provider proposal; final validation is deterministic."""

    query_training_log: TrainingLogArguments | None = None
    compute_metrics: MetricArguments | None = None


class ResolverError(StrictModel):
    code: ResolverErrorCode
    message: str = Field(min_length=1)


class ResolverProvenance(StrictModel):
    resolver_version: Literal["tool_input_resolver_v1"] = RESOLVER_VERSION
    method: Literal["not_required", "prebound", "deterministic", "openai"]
    selected_tools: list[str]
    resolved_tools: list[str] = Field(default_factory=list)
    canonical_exercise_name: str | None = None
    model: str | None = None
    prompt_sha256: str | None = None
    config_sha256: str | None = None
    response_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    usage: PlannerUsage = Field(default_factory=PlannerUsage)


class ToolInputResolverResult(StrictModel):
    execution_status: ResolverExecutionStatus
    requested_tools: list[str]
    tool_inputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    provenance: ResolverProvenance
    error: ResolverError | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "ToolInputResolverResult":
        failed = self.execution_status == ResolverExecutionStatus.FAILURE
        if failed != (self.error is not None):
            raise ValueError("only failure results contain an error")
        if not failed and set(self.tool_inputs) != set(self.requested_tools):
            if self.execution_status != ResolverExecutionStatus.NOT_REQUIRED:
                raise ValueError("ready result must contain every requested Tool input")
        return self


class ResolverProviderResult(StrictModel):
    raw_inputs: dict[str, Any] | None = None
    model: str
    prompt_sha256: str
    config_sha256: str
    response_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    usage: PlannerUsage = Field(default_factory=PlannerUsage)
    error: str | None = None


class ToolInputProvider(Protocol):
    def invoke(self, request: ToolInputResolverRequest) -> ResolverProviderResult: ...


class CanonicalExerciseRepository(Protocol):
    def resolve_canonical_exercise(self, name: str) -> str | None: ...


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _load_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    aliases: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("decision") or "") != "merge":
                continue
            raw = str(row.get("raw_name") or "").strip()
            canonical = str(row.get("canonical_name") or "").strip()
            if raw and canonical:
                aliases[_normalize(raw)] = canonical
                aliases[_normalize(canonical)] = canonical
    return aliases


class ExerciseCanonicalizer:
    """Apply the frozen alias map and confirm the exact DB canonical label."""

    def __init__(self, repository: CanonicalExerciseRepository) -> None:
        self.repository = repository
        self.aliases = _load_aliases()

    def canonicalize(self, value: str) -> str | None:
        candidate = self.aliases.get(_normalize(value), value.strip())
        return self.repository.resolve_canonical_exercise(candidate)

    def mentioned_candidate(self, question: str) -> str | None:
        normalized = _normalize(question)
        matches = [
            (len(alias), canonical)
            for alias, canonical in self.aliases.items()
            if alias in normalized
        ]
        if matches:
            return max(matches)[1]
        parenthesized = re.findall(
            r"[A-Za-z][A-Za-z0-9 '&/+.-]{1,70}\s*\([^()]{1,40}\)",
            question,
        )
        if len(parenthesized) == 1:
            return parenthesized[0].strip()
        return None

    def is_grounded(self, question: str, proposed: str) -> bool:
        if _normalize(proposed) in _normalize(question):
            return True
        normalized_question = _normalize(question)
        proposed_canonical = self.aliases.get(_normalize(proposed), proposed)
        return any(
            alias in normalized_question and canonical == proposed_canonical
            for alias, canonical in self.aliases.items()
        )


_SESSION_N = re.compile(
    r"(?:처음|첫|최근|마지막|first|last|latest)\s*(\d+)\s*[- ]?\s*session",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)")


def _dates(question: str) -> tuple[str | None, str | None] | None:
    values = list(dict.fromkeys(_ISO_DATE.findall(question)))
    if len(values) > 2:
        return None
    if len(values) == 2:
        return values[0], values[1]
    if len(values) == 1:
        return values[0], values[0]
    return None, None


def _metric_operation(question: str) -> str | None:
    text = _normalize(question)
    if "e1rm" in text or "estimated 1rm" in text:
        if _SESSION_N.search(question) and any(
            term in text for term in ("처음", "첫", "최근", "마지막", "first", "last")
        ):
            return "first_last_n_session_median_e1rm"
    if any(term in text for term in ("weekly volume", "주간 volume", "주간 볼륨")):
        return "weekly_volume"
    if any(term in text for term in ("weekly frequency", "주간 frequency", "주간 빈도")):
        return "weekly_frequency"
    if any(term in text for term in ("training gap", "훈련 공백", "session 간 공백")):
        return "training_gap"
    if any(term in text for term in ("plateau candidate", "정체 구간")):
        return "plateau_candidates"
    if "e1rm" in text or "estimated 1rm" in text:
        return "estimated_1rm"
    return None


def _n_sessions(question: str) -> int | None:
    values = {int(value) for value in _SESSION_N.findall(question)}
    return next(iter(values)) if len(values) == 1 else None


def _deterministic_draft(
    request: ToolInputResolverRequest,
    canonicalizer: ExerciseCanonicalizer,
) -> ToolInputExtractionDraft | None:
    selected = request.structured_tools
    if not selected:
        return ToolInputExtractionDraft()
    question = request.question
    exercise = canonicalizer.mentioned_candidate(question)
    date_values = _dates(question)
    if date_values is None:
        return None
    start_date, end_date = date_values

    metric: MetricArguments | None = None
    if "compute_metrics" in selected:
        operation = _metric_operation(question)
        if operation is None:
            return None
        n_sessions = _n_sessions(question)
        if operation == "first_last_n_session_median_e1rm" and n_sessions is None:
            return None
        metric = MetricArguments(
            operation=operation,
            records_source="query_training_log",
            canonical_exercise_name=exercise,
            start_date=start_date,
            end_date=end_date,
            n_sessions=n_sessions or 5,
        )

    if metric is not None:
        set_required = metric.operation in {
            "estimated_1rm",
            "first_last_n_session_median_e1rm",
            "weekly_volume",
            "plateau_candidates",
        }
        if set_required and not exercise:
            return None
        training_operation = "exercise_records" if exercise else "list_sessions"
    else:
        text = _normalize(question)
        session_match = re.search(
            r"\b(?:session[_ -]?id)\s*[:=#]?\s*([A-Za-z0-9_-]+)",
            question,
            re.IGNORECASE,
        )
        if session_match:
            return ToolInputExtractionDraft(
                query_training_log=TrainingLogArguments(
                    operation="get_session",
                    session_id=session_match.group(1),
                    include_lineage=True,
                )
            )
        if exercise and any(
            term in text for term in ("최초", "처음 기록", "최근 기록", "first record", "last record")
        ):
            training_operation = "exercise_first_last"
        elif exercise:
            training_operation = "exercise_records"
        elif any(term in text for term in ("session 목록", "session list", "운동 기록")):
            training_operation = "list_sessions"
        else:
            return None

    training = TrainingLogArguments(
        operation=training_operation,
        canonical_exercise_name=exercise,
        start_date=start_date,
        end_date=end_date,
        limit=5000,
        include_lineage=False,
    )
    return ToolInputExtractionDraft(
        query_training_log=training,
        compute_metrics=metric,
    )


def _failure(
    request: ToolInputResolverRequest,
    *,
    method: Literal["deterministic", "openai"],
    code: ResolverErrorCode,
    message: str,
    started: float,
    provider: ResolverProviderResult | None = None,
) -> ToolInputResolverResult:
    return ToolInputResolverResult(
        execution_status=ResolverExecutionStatus.FAILURE,
        requested_tools=request.structured_tools,
        provenance=ResolverProvenance(
            method=method,
            selected_tools=request.selected_tools,
            model=provider.model if provider else None,
            prompt_sha256=provider.prompt_sha256 if provider else None,
            config_sha256=provider.config_sha256 if provider else None,
            response_id=provider.response_id if provider else None,
            latency_ms=(perf_counter() - started) * 1000.0,
            usage=provider.usage if provider else PlannerUsage(),
        ),
        error=ResolverError(code=code, message=message),
    )


def _finalize(
    request: ToolInputResolverRequest,
    draft: ToolInputExtractionDraft,
    canonicalizer: ExerciseCanonicalizer,
    *,
    method: Literal["deterministic", "openai"],
    started: float,
    provider: ResolverProviderResult | None = None,
) -> ToolInputResolverResult:
    requested = request.structured_tools
    proposed = {
        "query_training_log": draft.query_training_log,
        "compute_metrics": draft.compute_metrics,
    }
    for tool, value in proposed.items():
        if tool not in requested and value is not None:
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.INVALID_ROUTE,
                message="Resolver proposed input for a Tool not selected by Router",
                started=started,
                provider=provider,
            )
    if any(proposed[tool] is None for tool in requested):
        return _failure(
            request,
            method=method,
            code=ResolverErrorCode.MISSING_REQUIRED_ARGUMENT,
            message="Required structured Tool arguments could not be extracted",
            started=started,
            provider=provider,
        )

    training = proposed["query_training_log"]
    metric = proposed["compute_metrics"]
    assert training is not None
    canonical: str | None = None
    name = training.canonical_exercise_name
    if name:
        if not canonicalizer.is_grounded(request.question, name):
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.UNGROUNDED_ARGUMENT,
                message="Proposed exercise is not grounded in the question",
                started=started,
                provider=provider,
            )
        canonical = canonicalizer.canonicalize(name)
        if canonical is None:
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.UNKNOWN_EXERCISE,
                message="Exercise did not resolve to one exact canonical name",
                started=started,
                provider=provider,
            )

    for date_value in (training.start_date, training.end_date):
        if date_value and date_value not in request.question:
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.UNGROUNDED_ARGUMENT,
                message="Proposed date is not explicitly grounded in the question",
                started=started,
                provider=provider,
            )

    training_data = training.model_dump(mode="json")
    training_data["canonical_exercise_name"] = canonical
    training = TrainingLogArguments.model_validate(training_data)

    tool_inputs: dict[str, dict[str, Any]] = {
        "query_training_log": training.model_dump(mode="json")
    }
    if metric is not None:
        metric_name = metric.canonical_exercise_name
        if metric_name and canonicalizer.canonicalize(metric_name) != canonical:
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.UNKNOWN_EXERCISE,
                message="Metric and Training Log exercise names do not resolve identically",
                started=started,
                provider=provider,
            )
        required_exercise = metric.operation in {
            "estimated_1rm",
            "first_last_n_session_median_e1rm",
            "weekly_volume",
            "plateau_candidates",
        }
        if required_exercise and canonical is None:
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.MISSING_REQUIRED_ARGUMENT,
                message="The requested metric requires an explicit exercise",
                started=started,
                provider=provider,
            )
        if metric.operation == "first_last_n_session_median_e1rm":
            explicit_n = _n_sessions(request.question)
            if explicit_n is None or metric.n_sessions != explicit_n:
                return _failure(
                    request,
                    method=method,
                    code=ResolverErrorCode.MISSING_REQUIRED_ARGUMENT,
                    message="An explicit, consistent N-session value is required",
                    started=started,
                    provider=provider,
                )
        expected_training = "exercise_records" if canonical else "list_sessions"
        if training.operation != expected_training:
            return _failure(
                request,
                method=method,
                code=ResolverErrorCode.UNSUPPORTED_OPERATION,
                message="Training Log operation is incompatible with the metric inputs",
                started=started,
                provider=provider,
            )
        metric_data = metric.model_dump(mode="json")
        metric_data.update(
            {
                "canonical_exercise_name": canonical,
                "start_date": training.start_date,
                "end_date": training.end_date,
                "records_source": "query_training_log",
            }
        )
        validated_metric = MetricArguments.model_validate(metric_data)
        tool_inputs["compute_metrics"] = validated_metric.model_dump(mode="json")

    if set(tool_inputs) != set(requested):
        return _failure(
            request,
            method=method,
            code=ResolverErrorCode.INVALID_ROUTE,
            message="Resolved Tool inputs differ from Router-selected structured Tools",
            started=started,
            provider=provider,
        )
    return ToolInputResolverResult(
        execution_status=ResolverExecutionStatus.READY,
        requested_tools=requested,
        tool_inputs=tool_inputs,
        provenance=ResolverProvenance(
            method=method,
            selected_tools=request.selected_tools,
            resolved_tools=requested,
            canonical_exercise_name=canonical,
            model=provider.model if provider else None,
            prompt_sha256=provider.prompt_sha256 if provider else None,
            config_sha256=provider.config_sha256 if provider else None,
            response_id=provider.response_id if provider else None,
            latency_ms=(perf_counter() - started) * 1000.0,
            usage=provider.usage if provider else PlannerUsage(),
        ),
    )


class ToolInputResolver:
    """Deterministic-first, provider-fallback structured argument resolver."""

    def __init__(
        self,
        *,
        exercise_repository: CanonicalExerciseRepository,
        provider: ToolInputProvider | None = None,
    ) -> None:
        self.canonicalizer = ExerciseCanonicalizer(exercise_repository)
        self.provider = provider

    def resolve(self, request: ToolInputResolverRequest) -> ToolInputResolverResult:
        started = perf_counter()
        if not request.structured_tools:
            return ToolInputResolverResult(
                execution_status=ResolverExecutionStatus.NOT_REQUIRED,
                requested_tools=[],
                provenance=ResolverProvenance(
                    method="not_required",
                    selected_tools=request.selected_tools,
                    latency_ms=(perf_counter() - started) * 1000.0,
                ),
            )

        try:
            draft = _deterministic_draft(request, self.canonicalizer)
        except Exception as exc:
            return _failure(
                request,
                method="deterministic",
                code=ResolverErrorCode.SCHEMA_FAILURE,
                message=f"Deterministic extraction failed: {type(exc).__name__}",
                started=started,
            )
        if draft is not None:
            return _finalize(
                request,
                draft,
                self.canonicalizer,
                method="deterministic",
                started=started,
            )

        if self.provider is None:
            return _failure(
                request,
                method="deterministic",
                code=ResolverErrorCode.MISSING_REQUIRED_ARGUMENT,
                message="Question lacks deterministically resolvable Tool arguments",
                started=started,
            )
        try:
            call = self.provider.invoke(request)
        except Exception as exc:
            return _failure(
                request,
                method="openai",
                code=ResolverErrorCode.PROVIDER_FAILURE,
                message=f"Tool input provider failed: {type(exc).__name__}",
                started=started,
            )
        if call.error or call.raw_inputs is None:
            return _failure(
                request,
                method="openai",
                code=ResolverErrorCode.PROVIDER_FAILURE,
                message="Tool input provider returned no usable output",
                started=started,
                provider=call,
            )
        try:
            draft = ToolInputExtractionDraft.model_validate(call.raw_inputs)
        except Exception:
            return _failure(
                request,
                method="openai",
                code=ResolverErrorCode.SCHEMA_FAILURE,
                message="Tool input provider output failed typed validation",
                started=started,
                provider=call,
            )
        return _finalize(
            request,
            draft,
            self.canonicalizer,
            method="openai",
            started=started,
            provider=call,
        )
