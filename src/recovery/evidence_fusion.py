"""Deterministic Top-10 fusion of initial and recovery literature evidence."""

from __future__ import annotations

from enum import Enum
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.retrieval.rrf import DEFAULT_RRF_K


FUSION_POLICY = "retry_evidence_fusion_v1"
EVIDENCE_BUDGET = 10


class FusionStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class FusionSource(str, Enum):
    INITIAL = "initial"
    RECOVERY = "recovery"


class FusionReason(str, Enum):
    BOTH_RETRIEVALS = "present_in_both_retrievals"
    INITIAL_ONLY = "initial_retrieval_only"
    RECOVERY_ONLY = "recovery_retrieval_only"


class LiteratureRetrievalHit(FusionStrictModel):
    """One frozen Hybrid Literature Tool hit, before cross-query fusion."""

    chunk_id: str = Field(min_length=1)
    paper_id: str = Field(min_length=1)
    pmcid: str | None = None
    pmid: str | None = None
    doi: str | None = None
    title: str = Field(min_length=1)
    section: str = Field(min_length=1)
    text: str = Field(min_length=1)
    rank: int = Field(ge=1, le=EVIDENCE_BUDGET)
    rrf_score: float = Field(ge=0.0)
    dense_rank: int | None = Field(default=None, ge=1, le=EVIDENCE_BUDGET)
    dense_score: float | None = None
    bm25_rank: int | None = Field(default=None, ge=1, le=EVIDENCE_BUDGET)
    bm25_score: float | None = Field(default=None, ge=0.0)
    dense_rrf_contribution: float = Field(ge=0.0)
    bm25_rrf_contribution: float = Field(ge=0.0)
    text_sha256: str = Field(min_length=64, max_length=64)
    source_sha256: str = Field(min_length=64, max_length=64)
    corpus_version: str = Field(min_length=1)


class RecoveryEvidenceFusionInput(FusionStrictModel):
    initial_results: list[LiteratureRetrievalHit] = Field(
        default_factory=list,
        max_length=EVIDENCE_BUDGET,
    )
    recovery_results: list[LiteratureRetrievalHit] = Field(
        default_factory=list,
        max_length=EVIDENCE_BUDGET,
    )
    initial_query: str = Field(min_length=1, max_length=1500)
    recovery_query: str = Field(min_length=1, max_length=1500)
    query_history: list[str] = Field(default_factory=list, max_length=20)
    fusion_policy: Literal["retry_evidence_fusion_v1"] = FUSION_POLICY
    rrf_k: Literal[60] = DEFAULT_RRF_K
    evidence_budget: Literal[10] = EVIDENCE_BUDGET

    @model_validator(mode="after")
    def validate_rankings(self) -> "RecoveryEvidenceFusionInput":
        for name, ranking in (
            ("initial", self.initial_results),
            ("recovery", self.recovery_results),
        ):
            chunk_ids = [item.chunk_id for item in ranking]
            if len(chunk_ids) != len(set(chunk_ids)):
                raise ValueError(f"{name} ranking contains duplicate chunk IDs")
            ranks = [item.rank for item in ranking]
            if ranks != list(range(1, len(ranking) + 1)):
                raise ValueError(f"{name} ranking must use contiguous rank order")

        all_hits = [*self.initial_results, *self.recovery_results]
        corpus_versions = {item.corpus_version for item in all_hits}
        if len(corpus_versions) > 1:
            raise ValueError("all fusion candidates must use one corpus version")

        initial_by_id = {item.chunk_id: item for item in self.initial_results}
        for recovery_hit in self.recovery_results:
            initial_hit = initial_by_id.get(recovery_hit.chunk_id)
            if initial_hit is None:
                continue
            identity_fields = (
                "paper_id",
                "pmcid",
                "pmid",
                "doi",
                "title",
                "section",
                "text",
                "text_sha256",
                "source_sha256",
                "corpus_version",
            )
            mismatches = [
                field
                for field in identity_fields
                if getattr(initial_hit, field) != getattr(recovery_hit, field)
            ]
            if mismatches:
                raise ValueError(
                    "same chunk ID has inconsistent immutable metadata: "
                    + ", ".join(mismatches)
                )
        return self


class SourceRetrievalProvenance(FusionStrictModel):
    source: FusionSource
    query: str
    original_rank: int = Field(ge=1, le=EVIDENCE_BUDGET)
    original_rrf_score: float = Field(ge=0.0)
    dense_rank: int | None = Field(default=None, ge=1, le=EVIDENCE_BUDGET)
    dense_score: float | None = None
    bm25_rank: int | None = Field(default=None, ge=1, le=EVIDENCE_BUDGET)
    bm25_score: float | None = Field(default=None, ge=0.0)
    dense_rrf_contribution: float = Field(ge=0.0)
    bm25_rrf_contribution: float = Field(ge=0.0)


class FusedEvidenceHit(FusionStrictModel):
    chunk_id: str
    paper_id: str
    pmcid: str | None = None
    pmid: str | None = None
    doi: str | None = None
    title: str
    section: str
    text: str
    text_sha256: str
    source_sha256: str
    corpus_version: str
    final_rank: int = Field(ge=1, le=EVIDENCE_BUDGET)
    fusion_score: float = Field(gt=0.0)
    fusion_reason: FusionReason
    sources: list[FusionSource] = Field(min_length=1, max_length=2)
    initial_rank: int | None = Field(default=None, ge=1, le=EVIDENCE_BUDGET)
    recovery_rank: int | None = Field(default=None, ge=1, le=EVIDENCE_BUDGET)
    initial_fusion_contribution: float = Field(ge=0.0)
    recovery_fusion_contribution: float = Field(ge=0.0)
    initial_retrieval: SourceRetrievalProvenance | None = None
    recovery_retrieval: SourceRetrievalProvenance | None = None


class RecoveryEvidenceFusionProvenance(FusionStrictModel):
    fusion_policy: Literal["retry_evidence_fusion_v1"] = FUSION_POLICY
    formula: Literal[
        "score(d)=sum(1/(60+rank_source(d)))"
    ] = "score(d)=sum(1/(60+rank_source(d)))"
    rrf_k: Literal[60] = DEFAULT_RRF_K
    evidence_budget: Literal[10] = EVIDENCE_BUDGET
    source_weights: dict[str, float] = Field(
        default_factory=lambda: {"initial": 1.0, "recovery": 1.0}
    )
    tie_break: list[str] = Field(
        default_factory=lambda: [
            "fusion_score_desc",
            "best_source_rank_asc",
            "source_rank_sum_asc",
            "chunk_id_lexicographic_asc",
        ]
    )
    initial_query: str
    recovery_query: str
    query_history: list[str]
    initial_ranking: list[str]
    recovery_ranking: list[str]
    initial_count: int = Field(ge=0, le=EVIDENCE_BUDGET)
    recovery_count: int = Field(ge=0, le=EVIDENCE_BUDGET)
    shared_count: int = Field(ge=0, le=EVIDENCE_BUDGET)
    candidate_count: int = Field(ge=0, le=2 * EVIDENCE_BUDGET)
    selected_count: int = Field(ge=0, le=EVIDENCE_BUDGET)
    corpus_versions: list[str]


class RecoveryEvidenceFusionResult(FusionStrictModel):
    evidence: list[FusedEvidenceHit] = Field(max_length=EVIDENCE_BUDGET)
    provenance: RecoveryEvidenceFusionProvenance

    @model_validator(mode="after")
    def validate_final_ranks(self) -> "RecoveryEvidenceFusionResult":
        ranks = [item.final_rank for item in self.evidence]
        if ranks != list(range(1, len(self.evidence) + 1)):
            raise ValueError("fused evidence must use contiguous final ranks")
        chunk_ids = [item.chunk_id for item in self.evidence]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("fused evidence cannot contain duplicate chunks")
        return self


def _source_provenance(
    hit: LiteratureRetrievalHit | None,
    *,
    source: FusionSource,
    query: str,
) -> SourceRetrievalProvenance | None:
    if hit is None:
        return None
    return SourceRetrievalProvenance(
        source=source,
        query=query,
        original_rank=hit.rank,
        original_rrf_score=hit.rrf_score,
        dense_rank=hit.dense_rank,
        dense_score=hit.dense_score,
        bm25_rank=hit.bm25_rank,
        bm25_score=hit.bm25_score,
        dense_rrf_contribution=hit.dense_rrf_contribution,
        bm25_rrf_contribution=hit.bm25_rrf_contribution,
    )


def fuse_recovery_evidence(
    fusion_input: RecoveryEvidenceFusionInput,
) -> RecoveryEvidenceFusionResult:
    """Fuse initial and recovery rankings with fixed equal-weight RRF."""

    initial_by_id = {
        item.chunk_id: item for item in fusion_input.initial_results
    }
    recovery_by_id = {
        item.chunk_id: item for item in fusion_input.recovery_results
    }
    candidate_ids = set(initial_by_id) | set(recovery_by_id)
    missing_rank = max(
        len(fusion_input.initial_results),
        len(fusion_input.recovery_results),
    ) + 1
    scored: list[tuple[str, float, float, float]] = []

    for chunk_id in candidate_ids:
        initial_hit = initial_by_id.get(chunk_id)
        recovery_hit = recovery_by_id.get(chunk_id)
        initial_contribution = (
            0.0
            if initial_hit is None
            else 1.0 / (fusion_input.rrf_k + initial_hit.rank)
        )
        recovery_contribution = (
            0.0
            if recovery_hit is None
            else 1.0 / (fusion_input.rrf_k + recovery_hit.rank)
        )
        score = initial_contribution + recovery_contribution
        if not math.isfinite(score):
            raise RuntimeError("Recovery evidence fusion produced a non-finite score")
        scored.append(
            (chunk_id, score, initial_contribution, recovery_contribution)
        )

    def sort_key(item: tuple[str, float, float, float]) -> tuple:
        chunk_id, score, _, _ = item
        initial_rank = (
            initial_by_id[chunk_id].rank
            if chunk_id in initial_by_id
            else missing_rank
        )
        recovery_rank = (
            recovery_by_id[chunk_id].rank
            if chunk_id in recovery_by_id
            else missing_rank
        )
        return (
            -score,
            min(initial_rank, recovery_rank),
            initial_rank + recovery_rank,
            chunk_id,
        )

    scored.sort(key=sort_key)
    selected = scored[: fusion_input.evidence_budget]
    evidence: list[FusedEvidenceHit] = []

    for final_rank, (chunk_id, score, initial_part, recovery_part) in enumerate(
        selected,
        1,
    ):
        initial_hit = initial_by_id.get(chunk_id)
        recovery_hit = recovery_by_id.get(chunk_id)
        canonical = initial_hit or recovery_hit
        if canonical is None:  # defensive; candidate IDs come from the two maps
            raise RuntimeError("Fusion candidate has no source retrieval hit")
        sources = [
            source
            for source, hit in (
                (FusionSource.INITIAL, initial_hit),
                (FusionSource.RECOVERY, recovery_hit),
            )
            if hit is not None
        ]
        reason = (
            FusionReason.BOTH_RETRIEVALS
            if initial_hit is not None and recovery_hit is not None
            else (
                FusionReason.INITIAL_ONLY
                if initial_hit is not None
                else FusionReason.RECOVERY_ONLY
            )
        )
        evidence.append(
            FusedEvidenceHit(
                chunk_id=canonical.chunk_id,
                paper_id=canonical.paper_id,
                pmcid=canonical.pmcid,
                pmid=canonical.pmid,
                doi=canonical.doi,
                title=canonical.title,
                section=canonical.section,
                text=canonical.text,
                text_sha256=canonical.text_sha256,
                source_sha256=canonical.source_sha256,
                corpus_version=canonical.corpus_version,
                final_rank=final_rank,
                fusion_score=score,
                fusion_reason=reason,
                sources=sources,
                initial_rank=initial_hit.rank if initial_hit else None,
                recovery_rank=recovery_hit.rank if recovery_hit else None,
                initial_fusion_contribution=initial_part,
                recovery_fusion_contribution=recovery_part,
                initial_retrieval=_source_provenance(
                    initial_hit,
                    source=FusionSource.INITIAL,
                    query=fusion_input.initial_query,
                ),
                recovery_retrieval=_source_provenance(
                    recovery_hit,
                    source=FusionSource.RECOVERY,
                    query=fusion_input.recovery_query,
                ),
            )
        )

    shared_ids = set(initial_by_id) & set(recovery_by_id)
    corpus_versions = sorted(
        {
            item.corpus_version
            for item in [
                *fusion_input.initial_results,
                *fusion_input.recovery_results,
            ]
        }
    )
    return RecoveryEvidenceFusionResult(
        evidence=evidence,
        provenance=RecoveryEvidenceFusionProvenance(
            initial_query=fusion_input.initial_query,
            recovery_query=fusion_input.recovery_query,
            query_history=list(fusion_input.query_history),
            initial_ranking=[
                item.chunk_id for item in fusion_input.initial_results
            ],
            recovery_ranking=[
                item.chunk_id for item in fusion_input.recovery_results
            ],
            initial_count=len(fusion_input.initial_results),
            recovery_count=len(fusion_input.recovery_results),
            shared_count=len(shared_ids),
            candidate_count=len(candidate_ids),
            selected_count=len(evidence),
            corpus_versions=corpus_versions,
        ),
    )
