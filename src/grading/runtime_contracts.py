"""Typed contracts for the production Runtime Evidence Grader."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class RuntimeComponentStatus(str, Enum):
    SUPPORTED = "supported"
    MISSING = "missing"


class RuntimeExecutionStatus(str, Enum):
    COMPLETED = "completed"
    FAILURE = "failure"


class RuntimeErrorCode(str, Enum):
    PROVIDER_FAILURE = "provider_failure"
    SCHEMA_FAILURE = "schema_failure"
    FINALIZER_FAILURE = "finalizer_failure"
    INPUT_FAILURE = "input_failure"


class RuntimeRetrievalProvenance(RuntimeStrictModel):
    rank: int = Field(ge=1, le=100)
    dense_rank: int | None = Field(default=None, ge=1)
    bm25_rank: int | None = Field(default=None, ge=1)
    rrf_score: float | None = Field(default=None, ge=0.0)


class RuntimeLiteratureChunk(RuntimeStrictModel):
    chunk_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    pmcid: str | None = None
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    text: str = Field(min_length=1)
    retrieval: RuntimeRetrievalProvenance
    corpus_version: str = Field(min_length=1)


class RuntimeEvidenceGraderInput(RuntimeStrictModel):
    question: str = Field(min_length=1, max_length=2000)
    literature_subquestion: str = Field(min_length=1, max_length=1500)
    evidence_channel: Literal["literature_only"] = "literature_only"
    structured_channel_policy: Literal[
        "log_and_metric_evidence_excluded_and_evaluated_separately"
    ] = "log_and_metric_evidence_excluded_and_evaluated_separately"
    supplied_chunks: list[RuntimeLiteratureChunk] = Field(
        min_length=1, max_length=10
    )

    @model_validator(mode="after")
    def validate_supplied_chunks(self) -> "RuntimeEvidenceGraderInput":
        chunk_ids = [item.chunk_id for item in self.supplied_chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("supplied chunk IDs must be unique")
        ranks = [item.retrieval.rank for item in self.supplied_chunks]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("supplied chunks must use contiguous retrieval ranks")
        corpus_versions = {item.corpus_version for item in self.supplied_chunks}
        if len(corpus_versions) != 1:
            raise ValueError("supplied chunks must use one corpus version")
        return self


class RuntimeRequiredComponentDraft(RuntimeStrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    requirement: str = Field(min_length=1, max_length=600)
    status: RuntimeComponentStatus
    supporting_chunk_ids: list[str] = Field(max_length=20)


class RuntimeEvidenceAssessmentDraft(RuntimeStrictModel):
    components: list[RuntimeRequiredComponentDraft] = Field(
        min_length=1, max_length=8
    )

    @model_validator(mode="after")
    def validate_component_order(self) -> "RuntimeEvidenceAssessmentDraft":
        component_ids = [item.component_id for item in self.components]
        expected = [f"C{index}" for index in range(1, len(component_ids) + 1)]
        if component_ids != expected:
            raise ValueError("components must use contiguous C1..Cn order")
        return self


class RuntimeTokenUsage(RuntimeStrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class RuntimeFinalizedComponent(RuntimeStrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    requirement: str = Field(min_length=1, max_length=600)
    model_status: RuntimeComponentStatus
    status: RuntimeComponentStatus
    proposed_chunk_ids: list[str]
    supporting_chunk_ids: list[str]
    rejected_chunk_ids: list[str]
    duplicate_anchor_count: int = Field(ge=0)
    supporting_evidence: list[RuntimeLiteratureChunk]


class RuntimeGraderProvenance(RuntimeStrictModel):
    grader_name: Literal["runtime_evidence_grader"] = (
        "runtime_evidence_grader"
    )
    model: str = Field(min_length=1)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    config_sha256: str = Field(min_length=64, max_length=64)
    corpus_version: str = Field(min_length=1)
    supplied_chunk_ids: list[str]
    accepted_chunk_ids: list[str]
    rejected_chunk_ids: list[str]
    component_support: dict[str, list[str]]
    response_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    token_usage: RuntimeTokenUsage = Field(default_factory=RuntimeTokenUsage)


class RuntimeEvidenceGrade(RuntimeStrictModel):
    execution_status: RuntimeExecutionStatus
    evidence_sufficient: bool
    components: list[RuntimeFinalizedComponent]
    missing_component_ids: list[str]
    error_code: RuntimeErrorCode | None = None
    error_detail: str | None = None
    provenance: RuntimeGraderProvenance

    @model_validator(mode="after")
    def validate_final_semantics(self) -> "RuntimeEvidenceGrade":
        if self.execution_status == RuntimeExecutionStatus.FAILURE:
            if self.evidence_sufficient:
                raise ValueError("failure must be fail-closed")
            if self.components or self.missing_component_ids:
                raise ValueError("failure cannot claim finalized components")
            if self.error_code is None:
                raise ValueError("failure requires an error code")
            return self

        if self.error_code is not None or self.error_detail is not None:
            raise ValueError("completed grade cannot contain an error")
        missing = [
            item.component_id
            for item in self.components
            if item.status == RuntimeComponentStatus.MISSING
        ]
        if self.missing_component_ids != missing:
            raise ValueError("missing_component_ids must match component states")
        if self.evidence_sufficient != (not missing):
            raise ValueError("evidence_sufficient must use all-required semantics")
        return self
