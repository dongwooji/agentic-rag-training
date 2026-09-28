"""Checklist contracts for the preregistered Grader v2 candidate."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .contracts import StrictModel, Verdict


class QuestionPolarityV2(str, Enum):
    AFFIRMATIVE = "affirmative"
    NEGATIVE_OR_CHALLENGE = "negative_or_challenge"
    COMPARATIVE = "comparative"
    CONDITIONAL = "conditional"
    UNCLEAR = "unclear"


class PropositionResolutionV2(str, Enum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    MIXED_OR_QUALIFIED = "mixed_or_qualified"
    UNRESOLVED = "unresolved"


class LimitationStatusV2(str, Enum):
    SUPPORTED = "supported"
    NOT_REQUIRED = "not_required"
    MISSING = "missing"


class ScopeStatusV2(str, Enum):
    ADEQUATE = "adequate"
    MISMATCH = "mismatch"
    NOT_APPLICABLE = "not_applicable"


class MultiEvidenceStatusV2(str, Enum):
    COMPLETE = "complete"
    NOT_REQUIRED = "not_required"
    INCOMPLETE = "incomplete"


class InsufficiencyReasonV2(str, Enum):
    MISSING_MAIN_CLAIM = "missing_main_claim"
    MISSING_LIMITATION_OR_CONTEXT = "missing_limitation_or_context"
    POPULATION_MISMATCH = "population_mismatch"
    OUTCOME_MISMATCH = "outcome_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    NO_RELEVANT_EVIDENCE = "no_relevant_evidence"
    MULTI_EVIDENCE_INCOMPLETE = "multi_evidence_incomplete"
    NONE = "none"


class GradingProcedureV2(StrictModel):
    evidence_channel: Literal["literature_only"] = "literature_only"
    structured_evidence_handling: Literal[
        "excluded_assumed_evaluated_separately"
    ] = "excluded_assumed_evaluated_separately"
    require_literature_subquestion: Literal[True] = True
    require_question_polarity: Literal[True] = True
    negative_evidence_can_answer_challenge: Literal[True] = True
    require_main_claim_check: Literal[True] = True
    require_limitation_check: Literal[True] = True
    require_population_check: Literal[True] = True
    require_outcome_check: Literal[True] = True
    require_terminology_scope_check: Literal[True] = True
    require_multi_evidence_check: Literal[True] = True
    use_only_supplied_evidence: Literal[True] = True


class EvidenceChunkInputV2(StrictModel):
    evidence_rank: int = Field(ge=1, le=10)
    evidence_source: Literal["controlled_heldout_bundle", "hybrid_top10"]
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


class GraderV2Input(StrictModel):
    question: str = Field(min_length=1)
    grading_procedure: GradingProcedureV2
    retrieved_evidence: list[EvidenceChunkInputV2] = Field(
        min_length=1, max_length=10
    )

    @model_validator(mode="after")
    def validate_rank_order(self) -> "GraderV2Input":
        ranks = [item.evidence_rank for item in self.retrieved_evidence]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("retrieved_evidence must be ordered at contiguous ranks")
        return self


class EvidenceAssessmentDraftV2(StrictModel):
    literature_scope_only: Literal[True]
    structured_evidence_handling: Literal[
        "excluded_assumed_evaluated_separately"
    ]
    literature_subquestion: str = Field(min_length=1, max_length=500)
    question_polarity: QuestionPolarityV2
    surface_proposition_resolution: PropositionResolutionV2
    relevant_evidence_present: bool
    answer_to_literature_question_supported: bool
    limitation_or_context_supported: LimitationStatusV2
    population_scope: ScopeStatusV2
    outcome_scope: ScopeStatusV2
    terminology_scope: ScopeStatusV2
    multi_evidence_status: MultiEvidenceStatusV2
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_component_logic(self) -> "EvidenceAssessmentDraftV2":
        if not self.relevant_evidence_present:
            if self.answer_to_literature_question_supported:
                raise ValueError("irrelevant evidence cannot support the answer")
            if self.surface_proposition_resolution != PropositionResolutionV2.UNRESOLVED:
                raise ValueError("no relevant evidence requires unresolved proposition")
        if (
            self.answer_to_literature_question_supported
            and self.surface_proposition_resolution == PropositionResolutionV2.UNRESOLVED
        ):
            raise ValueError("a supported answer requires a resolved proposition")
        return self


def derive_insufficiency_reason(
    assessment: EvidenceAssessmentDraftV2,
) -> InsufficiencyReasonV2:
    """Apply the fixed answerability-first priority.

    Scope mismatches remain diagnostic facts.  They become insufficiency reasons
    only when the supplied evidence cannot ground an answer to the literature
    question.  This permits a mismatch to support a bounded negative answer to a
    challenge question while still rejecting unsupported target-population,
    outcome, or terminology claims.
    """

    if not assessment.relevant_evidence_present:
        return InsufficiencyReasonV2.NO_RELEVANT_EVIDENCE
    if not assessment.answer_to_literature_question_supported:
        if assessment.population_scope == ScopeStatusV2.MISMATCH:
            return InsufficiencyReasonV2.POPULATION_MISMATCH
        if assessment.outcome_scope == ScopeStatusV2.MISMATCH:
            return InsufficiencyReasonV2.OUTCOME_MISMATCH
        if assessment.terminology_scope == ScopeStatusV2.MISMATCH:
            return InsufficiencyReasonV2.SCOPE_MISMATCH
        if assessment.multi_evidence_status == MultiEvidenceStatusV2.INCOMPLETE:
            return InsufficiencyReasonV2.MULTI_EVIDENCE_INCOMPLETE
        return InsufficiencyReasonV2.MISSING_MAIN_CLAIM
    if assessment.limitation_or_context_supported == LimitationStatusV2.MISSING:
        return InsufficiencyReasonV2.MISSING_LIMITATION_OR_CONTEXT
    if assessment.multi_evidence_status == MultiEvidenceStatusV2.INCOMPLETE:
        return InsufficiencyReasonV2.MULTI_EVIDENCE_INCOMPLETE
    return InsufficiencyReasonV2.NONE


class EvidenceGradeV2(EvidenceAssessmentDraftV2):
    verdict: Verdict
    insufficiency_reason: InsufficiencyReasonV2

    @model_validator(mode="after")
    def validate_final_verdict(self) -> "EvidenceGradeV2":
        assessment = EvidenceAssessmentDraftV2.model_validate(
            self.model_dump(exclude={"verdict", "insufficiency_reason"})
        )
        expected_reason = derive_insufficiency_reason(assessment)
        expected_verdict = (
            Verdict.SUFFICIENT
            if expected_reason == InsufficiencyReasonV2.NONE
            else Verdict.INSUFFICIENT
        )
        if self.insufficiency_reason != expected_reason:
            raise ValueError("insufficiency_reason differs from component decision")
        if self.verdict != expected_verdict:
            raise ValueError("verdict differs from component decision")
        return self


def finalize_v2_assessment(
    assessment: EvidenceAssessmentDraftV2,
) -> EvidenceGradeV2:
    reason = derive_insufficiency_reason(assessment)
    verdict = (
        Verdict.SUFFICIENT
        if reason == InsufficiencyReasonV2.NONE
        else Verdict.INSUFFICIENT
    )
    return EvidenceGradeV2.model_validate(
        {
            **assessment.model_dump(mode="json"),
            "verdict": verdict.value,
            "insufficiency_reason": reason.value,
        }
    )
