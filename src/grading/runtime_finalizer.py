"""Pure normalization and finalization for the Runtime Evidence Grader."""

from __future__ import annotations

from collections.abc import Iterable

from .runtime_contracts import (
    RuntimeComponentStatus,
    RuntimeErrorCode,
    RuntimeEvidenceAssessmentDraft,
    RuntimeEvidenceGrade,
    RuntimeEvidenceGraderInput,
    RuntimeExecutionStatus,
    RuntimeFinalizedComponent,
    RuntimeGraderProvenance,
    RuntimeTokenUsage,
)


def _deduplicate(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_runtime_provenance(
    grader_input: RuntimeEvidenceGraderInput,
    *,
    model: str,
    prompt_sha256: str,
    config_sha256: str,
    response_id: str | None = None,
    latency_ms: float = 0.0,
    token_usage: RuntimeTokenUsage | None = None,
    accepted_chunk_ids: Iterable[str] = (),
    rejected_chunk_ids: Iterable[str] = (),
    component_support: dict[str, list[str]] | None = None,
) -> RuntimeGraderProvenance:
    return RuntimeGraderProvenance(
        model=model,
        prompt_sha256=prompt_sha256,
        config_sha256=config_sha256,
        corpus_version=grader_input.supplied_chunks[0].corpus_version,
        supplied_chunk_ids=[item.chunk_id for item in grader_input.supplied_chunks],
        accepted_chunk_ids=_deduplicate(accepted_chunk_ids),
        rejected_chunk_ids=_deduplicate(rejected_chunk_ids),
        component_support=component_support or {},
        response_id=response_id,
        latency_ms=latency_ms,
        token_usage=token_usage or RuntimeTokenUsage(),
    )


def finalize_runtime_assessment(
    grader_input: RuntimeEvidenceGraderInput,
    draft: RuntimeEvidenceAssessmentDraft,
    *,
    model: str,
    prompt_sha256: str,
    config_sha256: str,
    response_id: str | None = None,
    latency_ms: float = 0.0,
    token_usage: RuntimeTokenUsage | None = None,
) -> RuntimeEvidenceGrade:
    """Normalize anchors and derive sufficiency without model verdict fields."""

    supplied_by_id = {
        item.chunk_id: item for item in grader_input.supplied_chunks
    }
    components: list[RuntimeFinalizedComponent] = []
    all_accepted: list[str] = []
    all_rejected: list[str] = []
    component_support: dict[str, list[str]] = {}

    for item in draft.components:
        unique_proposed: list[str] = []
        seen: set[str] = set()
        duplicate_count = 0
        for chunk_id in item.supporting_chunk_ids:
            if chunk_id in seen:
                duplicate_count += 1
                continue
            seen.add(chunk_id)
            unique_proposed.append(chunk_id)

        valid_ids = [
            chunk_id
            for chunk_id in unique_proposed
            if chunk_id in supplied_by_id
        ]
        rejected_ids = [
            chunk_id
            for chunk_id in unique_proposed
            if chunk_id not in supplied_by_id
        ]
        final_ids = (
            valid_ids
            if item.status == RuntimeComponentStatus.SUPPORTED
            else []
        )
        final_status = (
            RuntimeComponentStatus.SUPPORTED
            if item.status == RuntimeComponentStatus.SUPPORTED and final_ids
            else RuntimeComponentStatus.MISSING
        )
        all_accepted.extend(final_ids)
        all_rejected.extend(rejected_ids)
        component_support[item.component_id] = list(final_ids)
        components.append(
            RuntimeFinalizedComponent(
                component_id=item.component_id,
                requirement=item.requirement,
                model_status=item.status,
                status=final_status,
                proposed_chunk_ids=list(item.supporting_chunk_ids),
                supporting_chunk_ids=final_ids,
                rejected_chunk_ids=rejected_ids,
                duplicate_anchor_count=duplicate_count,
                supporting_evidence=[
                    supplied_by_id[chunk_id] for chunk_id in final_ids
                ],
            )
        )

    missing_component_ids = [
        item.component_id
        for item in components
        if item.status == RuntimeComponentStatus.MISSING
    ]
    provenance = build_runtime_provenance(
        grader_input,
        model=model,
        prompt_sha256=prompt_sha256,
        config_sha256=config_sha256,
        response_id=response_id,
        latency_ms=latency_ms,
        token_usage=token_usage,
        accepted_chunk_ids=all_accepted,
        rejected_chunk_ids=all_rejected,
        component_support=component_support,
    )
    return RuntimeEvidenceGrade(
        execution_status=RuntimeExecutionStatus.COMPLETED,
        evidence_sufficient=not missing_component_ids,
        components=components,
        missing_component_ids=missing_component_ids,
        provenance=provenance,
    )


def build_fail_closed_grade(
    grader_input: RuntimeEvidenceGraderInput,
    *,
    error_code: RuntimeErrorCode,
    error_detail: str,
    model: str,
    prompt_sha256: str,
    config_sha256: str,
    response_id: str | None = None,
    latency_ms: float = 0.0,
    token_usage: RuntimeTokenUsage | None = None,
) -> RuntimeEvidenceGrade:
    """Return a structured failure without asserting semantic missingness."""

    return RuntimeEvidenceGrade(
        execution_status=RuntimeExecutionStatus.FAILURE,
        evidence_sufficient=False,
        components=[],
        missing_component_ids=[],
        error_code=error_code,
        error_detail=error_detail,
        provenance=build_runtime_provenance(
            grader_input,
            model=model,
            prompt_sha256=prompt_sha256,
            config_sha256=config_sha256,
            response_id=response_id,
            latency_ms=latency_ms,
            token_usage=token_usage,
        ),
    )

