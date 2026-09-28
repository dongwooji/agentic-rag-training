"""Strict contracts for the preregistered Phase 11A Evidence Grader."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Verdict(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"


class ReasonCode(str, Enum):
    SUFFICIENT = "sufficient"
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"
    POPULATION_MISMATCH = "population_mismatch"
    OUTCOME_MISMATCH = "outcome_mismatch"
    TERMINOLOGY_OR_SCOPE_MISMATCH = "terminology_or_scope_mismatch"
    MISSING_LIMITATION_OR_CONTEXT = "missing_limitation_or_context"
    NO_RELEVANT_EVIDENCE = "no_relevant_evidence"
    MULTI_EVIDENCE_INCOMPLETE = "multi_evidence_incomplete"


class MissingEvidenceType(str, Enum):
    NONE = "none"
    REQUIRED_CLAIM = "required_claim"
    POPULATION = "population"
    OUTCOME = "outcome"
    TERMINOLOGY_OR_SCOPE = "terminology_or_scope"
    LIMITATION_OR_CONTEXT = "limitation_or_context"
    RELEVANT_EVIDENCE = "relevant_evidence"
    MULTI_PART_COMPLETENESS = "multi_part_completeness"


REASON_TO_MISSING_TYPE = {
    ReasonCode.SUFFICIENT: MissingEvidenceType.NONE,
    ReasonCode.MISSING_REQUIRED_EVIDENCE: MissingEvidenceType.REQUIRED_CLAIM,
    ReasonCode.POPULATION_MISMATCH: MissingEvidenceType.POPULATION,
    ReasonCode.OUTCOME_MISMATCH: MissingEvidenceType.OUTCOME,
    ReasonCode.TERMINOLOGY_OR_SCOPE_MISMATCH: (
        MissingEvidenceType.TERMINOLOGY_OR_SCOPE
    ),
    ReasonCode.MISSING_LIMITATION_OR_CONTEXT: (
        MissingEvidenceType.LIMITATION_OR_CONTEXT
    ),
    ReasonCode.NO_RELEVANT_EVIDENCE: MissingEvidenceType.RELEVANT_EVIDENCE,
    ReasonCode.MULTI_EVIDENCE_INCOMPLETE: (
        MissingEvidenceType.MULTI_PART_COMPLETENESS
    ),
}


class RetrievalProvenance(StrictModel):
    hybrid_rank: int = Field(ge=1, le=10)
    rrf_score: float = Field(ge=0.0)
    dense_rank: int | None = Field(default=None, ge=1, le=10)
    bm25_rank: int | None = Field(default=None, ge=1, le=10)


class EvidenceChunkInput(StrictModel):
    chunk_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    pmcid: str | None = None
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    year: int | None = None
    population: str | None = None
    study_type: str | None = None
    topics: list[str]
    text: str = Field(min_length=1)
    retrieval: RetrievalProvenance


class GradingContext(StrictModel):
    scope: Literal["literature_evidence_only"] = "literature_evidence_only"
    hybrid_question_policy: Literal[
        "assume_structured_log_evidence_is_evaluated_separately"
    ] = "assume_structured_log_evidence_is_evaluated_separately"
    evidence_policy: Literal[
        "use_only_supplied_top10_and_require_all_material_literature_parts"
    ] = "use_only_supplied_top10_and_require_all_material_literature_parts"
    retrieval_order: Literal["best_to_worst"] = "best_to_worst"


class GraderInput(StrictModel):
    question: str = Field(min_length=1)
    grading_context: GradingContext
    retrieved_evidence: list[EvidenceChunkInput] = Field(
        min_length=10, max_length=10
    )


class EvidenceGrade(StrictModel):
    verdict: Verdict
    reason_code: ReasonCode
    missing_evidence_type: MissingEvidenceType
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_semantics(self) -> "EvidenceGrade":
        expected_missing = REASON_TO_MISSING_TYPE[self.reason_code]
        if self.missing_evidence_type != expected_missing:
            raise ValueError(
                "missing_evidence_type must match the bounded reason_code"
            )
        if self.verdict == Verdict.SUFFICIENT:
            if self.reason_code != ReasonCode.SUFFICIENT:
                raise ValueError("sufficient verdict requires sufficient reason_code")
        elif self.reason_code == ReasonCode.SUFFICIENT:
            raise ValueError(
                "insufficient verdict cannot use sufficient reason_code"
            )
        return self


class GraderUsage(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class GraderCallResult(StrictModel):
    raw_grade: dict[str, Any] | None = None
    model: str
    response_id: str | None = None
    latency_ms: float = Field(ge=0.0)
    usage: GraderUsage
    error: str | None = None

