"""Typed contracts for runtime literature-evidence query recovery."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_RETRY = 2


class RecoveryStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class RecoveryExecutionStatus(str, Enum):
    QUERY_READY = "query_ready"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILURE = "failure"


class RecoveryErrorCode(str, Enum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_FAILURE = "provider_failure"
    SCHEMA_FAILURE = "schema_failure"
    INVALID_TARGET_COMPONENT = "invalid_target_component"
    EMPTY_QUERY = "empty_query"
    DUPLICATE_QUERY = "duplicate_query"
    PRESERVED_TERM_MISSING = "preserved_term_missing"
    VALIDATION_FAILURE = "validation_failure"


class MissingLiteratureComponent(RecoveryStrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    requirement: str = Field(min_length=1, max_length=600)


class EvidenceRecoveryInput(RecoveryStrictModel):
    original_question: str = Field(min_length=1, max_length=2000)
    literature_subquestion: str = Field(min_length=1, max_length=1500)
    missing_components: list[MissingLiteratureComponent] = Field(
        min_length=1,
        max_length=8,
    )
    previous_query: str = Field(min_length=1, max_length=1500)
    query_history: list[str] = Field(default_factory=list, max_length=20)
    retry_count: int = Field(ge=0)
    max_retry: Literal[2] = MAX_RETRY
    evidence_channel: Literal["literature_only"] = "literature_only"
    structured_channel_policy: Literal[
        "log_and_metric_evidence_excluded"
    ] = "log_and_metric_evidence_excluded"

    @model_validator(mode="after")
    def validate_recovery_context(self) -> "EvidenceRecoveryInput":
        component_ids = [item.component_id for item in self.missing_components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("missing component IDs must be unique")
        if any(not item.strip() for item in self.query_history):
            raise ValueError("query history entries must be non-empty")
        return self


class RecoveryQueryDraft(RecoveryStrictModel):
    """The complete model-owned output; execution state is code-owned."""

    target_component_ids: list[str] = Field(max_length=8)
    recovery_query: str = Field(max_length=1500)
    preserved_terms: list[str] = Field(default_factory=list, max_length=20)


class RecoveryTokenUsage(RecoveryStrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class RecoveryError(RecoveryStrictModel):
    code: RecoveryErrorCode
    detail: str = Field(min_length=1, max_length=2000)


class RecoveryProvenance(RecoveryStrictModel):
    agent_name: Literal["evidence_recovery_agent"] = "evidence_recovery_agent"
    model: str = Field(min_length=1)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    config_sha256: str = Field(min_length=64, max_length=64)
    evidence_channel: Literal["literature_only"] = "literature_only"
    available_missing_component_ids: list[str]
    previous_query: str
    query_history: list[str]
    retry_count: int = Field(ge=0)
    max_retry: Literal[2] = MAX_RETRY
    normalized_recovery_query: str | None = None
    response_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    token_usage: RecoveryTokenUsage = Field(default_factory=RecoveryTokenUsage)
    response_metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecoveryResult(RecoveryStrictModel):
    target_component_ids: list[str]
    recovery_query: str | None
    preserved_terms: list[str]
    execution_status: RecoveryExecutionStatus
    error: RecoveryError | None
    provenance: RecoveryProvenance

    @model_validator(mode="after")
    def validate_result_semantics(self) -> "EvidenceRecoveryResult":
        if self.execution_status == RecoveryExecutionStatus.QUERY_READY:
            if not self.target_component_ids:
                raise ValueError("query_ready requires target components")
            if not self.recovery_query or not self.recovery_query.strip():
                raise ValueError("query_ready requires a recovery query")
            if self.error is not None:
                raise ValueError("query_ready cannot contain an error")
            return self

        if self.target_component_ids or self.recovery_query or self.preserved_terms:
            raise ValueError("non-ready results cannot expose a recovery query")
        if self.error is None:
            raise ValueError("non-ready results require a structured error")
        if (
            self.execution_status == RecoveryExecutionStatus.BUDGET_EXHAUSTED
            and self.error.code != RecoveryErrorCode.BUDGET_EXHAUSTED
        ):
            raise ValueError("budget exhaustion requires the matching error code")
        return self
