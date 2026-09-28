"""Question-conditioned component contracts for the Grader v2.1 design.

This module is intentionally offline.  It defines a candidate model-output
schema and deterministic finalizer, but it does not contain a provider call,
retry loop, retrieval action, or Recovery Agent.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .contracts import StrictModel, Verdict


class ComponentKindV21(str, Enum):
    MAIN_CLAIM = "main_claim"
    COMPARISON = "comparison"
    OUTCOME = "outcome"
    QUANTITATIVE_DETAIL = "quantitative_detail"
    LIMITATION_OR_CONTEXT = "limitation_or_context"
    POPULATION = "population"
    TERMINOLOGY_OR_SCOPE = "terminology_or_scope"
    GENERALIZATION = "generalization"


class ComponentStatusV21(str, Enum):
    SUPPORTED = "supported"
    MISSING = "missing"
    MISMATCH = "mismatch"


class InsufficiencyReasonV21(str, Enum):
    NONE = "none"
    MISSING_REQUIRED_COMPONENT = "missing_required_component"
    COMPONENT_MISMATCH = "component_mismatch"
    MISSING_AND_MISMATCH = "missing_and_mismatch"


class GradingProcedureV21(StrictModel):
    evidence_channel: Literal["literature_only"] = "literature_only"
    structured_evidence_handling: Literal[
        "excluded_assumed_evaluated_separately"
    ] = "excluded_assumed_evaluated_separately"
    component_source: Literal[
        "extract_every_material_literature_component_from_question"
    ] = "extract_every_material_literature_component_from_question"
    component_statuses: Literal[
        "supported_missing_or_mismatch"
    ] = "supported_missing_or_mismatch"
    require_question_span: Literal[True] = True
    require_evidence_anchor_for_supported_or_mismatch: Literal[True] = True
    missing_component_has_no_anchor: Literal[True] = True
    use_only_supplied_evidence: Literal[True] = True
    overall_completeness_not_model_output: Literal[True] = True
    deterministic_all_required_policy: Literal[True] = True


class EvidenceChunkInputV21(StrictModel):
    evidence_rank: int = Field(ge=1, le=10)
    evidence_source: Literal[
        "controlled_heldout_bundle", "synthetic_dev_fixture"
    ]
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


class GraderV21Input(StrictModel):
    question: str = Field(min_length=1, max_length=1000)
    grading_procedure: GradingProcedureV21
    retrieved_evidence: list[EvidenceChunkInputV21] = Field(
        min_length=1, max_length=10
    )

    @model_validator(mode="after")
    def validate_rank_order(self) -> "GraderV21Input":
        ranks = [item.evidence_rank for item in self.retrieved_evidence]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("retrieved_evidence must use contiguous rank order")
        chunk_ids = [item.chunk_id for item in self.retrieved_evidence]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("retrieved_evidence chunk IDs must be unique")
        return self


class ComponentEvidenceAnchorV21(StrictModel):
    chunk_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=400)


class RequiredComponentAssessmentV21(StrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    required: Literal[True] = True
    kind: ComponentKindV21
    question_span: str = Field(min_length=1, max_length=200)
    requirement: str = Field(min_length=1, max_length=500)
    status: ComponentStatusV21
    evidence: list[ComponentEvidenceAnchorV21] = Field(max_length=10)
    assessment_note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_local_status_contract(self) -> "RequiredComponentAssessmentV21":
        anchor_ids = [item.chunk_id for item in self.evidence]
        if len(anchor_ids) != len(set(anchor_ids)):
            raise ValueError("component evidence chunk IDs must be unique")
        if self.status == ComponentStatusV21.MISSING and self.evidence:
            raise ValueError("a missing component cannot cite supporting evidence")
        if self.status != ComponentStatusV21.MISSING and not self.evidence:
            raise ValueError("supported or mismatch components require evidence")
        return self


class EvidenceAssessmentDraftV21(StrictModel):
    literature_scope_only: Literal[True]
    structured_evidence_handling: Literal[
        "excluded_assumed_evaluated_separately"
    ]
    literature_subquestion: str = Field(min_length=1, max_length=700)
    required_components: list[RequiredComponentAssessmentV21] = Field(
        min_length=1, max_length=8
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_component_order(self) -> "EvidenceAssessmentDraftV21":
        component_ids = [item.component_id for item in self.required_components]
        expected = [f"C{index}" for index in range(1, len(component_ids) + 1)]
        if component_ids != expected:
            raise ValueError("required components must be contiguous C1..Cn")
        return self


class EvidenceGradeV21(EvidenceAssessmentDraftV21):
    verdict: Verdict
    insufficiency_reason: InsufficiencyReasonV21
    failed_component_ids: list[str]

    @model_validator(mode="after")
    def validate_deterministic_decision(self) -> "EvidenceGradeV21":
        expected_verdict, expected_reason, expected_failed = derive_v21_decision(
            self.required_components
        )
        if self.verdict != expected_verdict:
            raise ValueError("verdict differs from required-component decision")
        if self.insufficiency_reason != expected_reason:
            raise ValueError("reason differs from required-component decision")
        if self.failed_component_ids != expected_failed:
            raise ValueError("failed IDs differ from required-component decision")
        return self


def derive_v21_decision(
    components: list[RequiredComponentAssessmentV21],
) -> tuple[Verdict, InsufficiencyReasonV21, list[str]]:
    """Use statuses only; free-text notes never influence the decision."""

    failed = [
        item.component_id
        for item in components
        if item.status != ComponentStatusV21.SUPPORTED
    ]
    if not failed:
        return Verdict.SUFFICIENT, InsufficiencyReasonV21.NONE, []
    has_missing = any(
        item.status == ComponentStatusV21.MISSING for item in components
    )
    has_mismatch = any(
        item.status == ComponentStatusV21.MISMATCH for item in components
    )
    if has_missing and has_mismatch:
        reason = InsufficiencyReasonV21.MISSING_AND_MISMATCH
    elif has_mismatch:
        reason = InsufficiencyReasonV21.COMPONENT_MISMATCH
    else:
        reason = InsufficiencyReasonV21.MISSING_REQUIRED_COMPONENT
    return Verdict.INSUFFICIENT, reason, failed


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def finalize_v21_assessment(
    grader_input: GraderV21Input,
    assessment: EvidenceAssessmentDraftV21,
) -> EvidenceGradeV21:
    """Validate grounding, then derive verdict from component states only."""

    question = _normalized(grader_input.question)
    chunks = {item.chunk_id: item for item in grader_input.retrieved_evidence}
    for component in assessment.required_components:
        if _normalized(component.question_span) not in question:
            raise ValueError(
                f"{component.component_id} question_span is not in the question"
            )
        for anchor in component.evidence:
            chunk = chunks.get(anchor.chunk_id)
            if chunk is None:
                raise ValueError(
                    f"{component.component_id} cites an unsupplied chunk"
                )
            if _normalized(anchor.quote) not in _normalized(chunk.text):
                raise ValueError(
                    f"{component.component_id} quote is not in the cited chunk"
                )

    verdict, reason, failed = derive_v21_decision(
        assessment.required_components
    )
    return EvidenceGradeV21.model_validate(
        {
            **assessment.model_dump(mode="json"),
            "verdict": verdict.value,
            "insufficiency_reason": reason.value,
            "failed_component_ids": failed,
        }
    )
