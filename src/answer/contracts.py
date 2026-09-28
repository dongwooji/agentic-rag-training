"""Typed contracts for converting a terminal graph state into a response."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EVIDENCE_BUDGET = 10


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class FinalResponseStatus(str, Enum):
    ANSWER_READY = "answer_ready"
    ABSTAIN_READY = "abstain_ready"
    EXECUTION_FAILURE = "execution_failure"


class ChannelMode(str, Enum):
    LITERATURE_ONLY = "literature_only"
    STRUCTURED_ONLY = "structured_only"
    HYBRID = "hybrid"
    NO_EVIDENCE = "no_evidence"


class FinalAnswerErrorCode(str, Enum):
    PROVIDER_FAILURE = "provider_failure"
    SCHEMA_FAILURE = "schema_failure"
    GROUNDING_VALIDATION_FAILURE = "grounding_validation_failure"
    GRAPH_EXECUTION_FAILURE = "graph_execution_failure"
    INVALID_GRAPH_STATE = "invalid_graph_state"


class AnswerTokenUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class StructuredEvidence(StrictModel):
    result_id: str = Field(min_length=1)
    tool: Literal["query_training_log", "compute_metrics"]
    phase: Literal["initial"] = "initial"
    requested_operation: str = Field(min_length=1)
    result: Any
    provenance: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class LiteratureEvidence(StrictModel):
    chunk_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    pmcid: str | None = None
    pmid: str | None = None
    doi: str | None = None
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    text: str = Field(min_length=1)
    rank: int = Field(ge=1, le=EVIDENCE_BUDGET)
    corpus_version: str = Field(min_length=1)
    retrieval_provenance: dict[str, Any] = Field(default_factory=dict)


class FinalAnswerInput(StrictModel):
    original_question: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    literature_subquestion: str | None = None
    channel_mode: ChannelMode
    structured_evidence: list[StructuredEvidence] = Field(default_factory=list)
    literature_evidence: list[LiteratureEvidence] = Field(
        default_factory=list,
        max_length=EVIDENCE_BUDGET,
    )

    @model_validator(mode="after")
    def validate_channel_mode(self) -> "FinalAnswerInput":
        has_structured = bool(self.structured_evidence)
        has_literature = bool(self.literature_evidence)
        expected = (
            ChannelMode.HYBRID
            if has_structured and has_literature
            else ChannelMode.STRUCTURED_ONLY
            if has_structured
            else ChannelMode.LITERATURE_ONLY
            if has_literature
            else ChannelMode.NO_EVIDENCE
        )
        if self.channel_mode != expected:
            raise ValueError("channel_mode does not match supplied evidence")
        return self


class FinalAnswerDraft(StrictModel):
    """Model-authored prose plus references; no model-authored final status."""

    record_summary: str = ""
    literature_summary: str = ""
    integrated_summary: str = ""
    limitations: list[str] = Field(default_factory=list, max_length=8)
    used_tool_result_ids: list[str] = Field(default_factory=list)
    used_literature_chunk_ids: list[str] = Field(default_factory=list)


class UsedToolResult(StrictModel):
    result_id: str
    tool: Literal["query_training_log", "compute_metrics"]
    phase: Literal["initial"] = "initial"
    requested_operation: str
    result: Any
    provenance: dict[str, Any] = Field(default_factory=dict)


class FinalResponseError(StrictModel):
    code: FinalAnswerErrorCode
    user_message: str = Field(min_length=1)


class FinalResponseProvenance(StrictModel):
    component: Literal["final_answer_layer_v1"] = "final_answer_layer_v1"
    graph_terminal_status: FinalResponseStatus
    channel_mode: ChannelMode
    available_tool_result_ids: list[str] = Field(default_factory=list)
    used_tool_result_ids: list[str] = Field(default_factory=list)
    available_literature_chunk_ids: list[str] = Field(default_factory=list)
    used_literature_chunk_ids: list[str] = Field(default_factory=list)
    used_literature_sources: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None
    prompt_sha256: str | None = None
    config_sha256: str | None = None
    response_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    token_usage: AnswerTokenUsage = Field(default_factory=AnswerTokenUsage)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    internal_graph_errors: list[dict[str, Any]] = Field(default_factory=list)
    internal_error_detail: str | None = None

    @model_validator(mode="after")
    def validate_used_sources(self) -> "FinalResponseProvenance":
        source_ids = [str(item.get("chunk_id") or "") for item in self.used_literature_sources]
        if source_ids != self.used_literature_chunk_ids:
            raise ValueError("used literature source metadata differs from used IDs")
        return self


class FinalResponse(StrictModel):
    final_status: FinalResponseStatus
    answer_text: str = Field(min_length=1)
    used_tool_results: list[UsedToolResult] = Field(default_factory=list)
    used_literature_chunk_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: FinalResponseProvenance
    error: FinalResponseError | None = None

    @model_validator(mode="after")
    def validate_status_contract(self) -> "FinalResponse":
        is_failure = self.final_status == FinalResponseStatus.EXECUTION_FAILURE
        if is_failure != (self.error is not None):
            raise ValueError("only execution_failure responses contain error")
        used_result_ids = [item.result_id for item in self.used_tool_results]
        if used_result_ids != self.provenance.used_tool_result_ids:
            raise ValueError("used Tool provenance differs from response payload")
        if self.used_literature_chunk_ids != self.provenance.used_literature_chunk_ids:
            raise ValueError("used literature provenance differs from response payload")
        return self
