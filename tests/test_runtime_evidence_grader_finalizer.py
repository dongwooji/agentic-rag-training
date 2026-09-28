from src.grading.runtime_contracts import (
    RuntimeComponentStatus,
    RuntimeEvidenceAssessmentDraft,
    RuntimeEvidenceGraderInput,
)
from src.grading.runtime_finalizer import finalize_runtime_assessment


def _chunk(chunk_id: str, rank: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_id": f"paper-{rank}",
        "pmcid": f"PMC{rank}",
        "title": f"Paper {rank}",
        "section": "Results",
        "text": f"Deterministically resolved source text {rank}.",
        "retrieval": {"rank": rank, "rrf_score": 0.02},
        "corpus_version": "literature_corpus_v1",
    }


def _input() -> RuntimeEvidenceGraderInput:
    return RuntimeEvidenceGraderInput.model_validate(
        {
            "question": "사용자 질문",
            "literature_subquestion": "문헌 질문",
            "supplied_chunks": [_chunk("chunk-1", 1), _chunk("chunk-2", 2)],
        }
    )


def _draft(*components: tuple[str, str, list[str]]) -> RuntimeEvidenceAssessmentDraft:
    return RuntimeEvidenceAssessmentDraft.model_validate(
        {
            "components": [
                {
                    "component_id": component_id,
                    "requirement": f"Requirement {component_id}",
                    "status": status,
                    "supporting_chunk_ids": chunk_ids,
                }
                for component_id, status, chunk_ids in components
            ]
        }
    )


def _finalize(draft: RuntimeEvidenceAssessmentDraft):
    return finalize_runtime_assessment(
        _input(),
        draft,
        model="test-model",
        prompt_sha256="a" * 64,
        config_sha256="b" * 64,
    )


def test_all_supported_is_sufficient_and_any_missing_is_not() -> None:
    sufficient = _finalize(
        _draft(
            ("C1", "supported", ["chunk-1"]),
            ("C2", "supported", ["chunk-2"]),
        )
    )
    insufficient = _finalize(
        _draft(
            ("C1", "supported", ["chunk-1"]),
            ("C2", "missing", []),
        )
    )

    assert sufficient.evidence_sufficient is True
    assert sufficient.missing_component_ids == []
    assert insufficient.evidence_sufficient is False
    assert insufficient.missing_component_ids == ["C2"]


def test_duplicate_chunk_ids_are_removed_in_first_seen_order() -> None:
    grade = _finalize(
        _draft(("C1", "supported", ["chunk-2", "chunk-1", "chunk-2", "chunk-1"]))
    )
    component = grade.components[0]
    assert component.supporting_chunk_ids == ["chunk-2", "chunk-1"]
    assert component.duplicate_anchor_count == 2
    assert grade.provenance.accepted_chunk_ids == ["chunk-2", "chunk-1"]


def test_unsupplied_chunk_is_rejected_but_valid_anchor_survives() -> None:
    grade = _finalize(
        _draft(("C1", "supported", ["not-supplied", "chunk-1"]))
    )
    component = grade.components[0]
    assert component.status == RuntimeComponentStatus.SUPPORTED
    assert component.supporting_chunk_ids == ["chunk-1"]
    assert component.rejected_chunk_ids == ["not-supplied"]
    assert grade.provenance.rejected_chunk_ids == ["not-supplied"]


def test_supported_with_only_rejected_anchors_is_downgraded_to_missing() -> None:
    grade = _finalize(_draft(("C1", "supported", ["not-supplied"])))
    component = grade.components[0]
    assert component.model_status == RuntimeComponentStatus.SUPPORTED
    assert component.status == RuntimeComponentStatus.MISSING
    assert component.supporting_chunk_ids == []
    assert grade.evidence_sufficient is False
    assert grade.missing_component_ids == ["C1"]


def test_model_missing_cannot_be_promoted_by_an_anchor() -> None:
    grade = _finalize(_draft(("C1", "missing", ["chunk-1"])))
    assert grade.components[0].status == RuntimeComponentStatus.MISSING
    assert grade.components[0].supporting_chunk_ids == []
    assert grade.evidence_sufficient is False


def test_provenance_resolves_original_text_from_supplied_chunk_id() -> None:
    grade = _finalize(_draft(("C1", "supported", ["chunk-1"])))
    component = grade.components[0]
    assert component.supporting_evidence[0].text == (
        "Deterministically resolved source text 1."
    )
    assert component.supporting_evidence[0].retrieval.rank == 1
    assert grade.provenance.component_support == {"C1": ["chunk-1"]}

