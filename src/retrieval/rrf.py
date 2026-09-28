"""Reciprocal Rank Fusion for the pre-registered Phase 7 comparison."""

from __future__ import annotations

from dataclasses import dataclass
import math
from time import perf_counter
from typing import Sequence


DEFAULT_RRF_K = 60


@dataclass(frozen=True)
class RRFHit:
    chunk_id: str
    score: float
    dense_rank: int | None
    bm25_rank: int | None
    dense_contribution: float
    bm25_contribution: float


@dataclass(frozen=True)
class RRFResponse:
    hits: list[RRFHit]
    latency_ms: float
    candidate_count: int


def _validate_ranking(name: str, ranking: Sequence[str]) -> None:
    if not ranking:
        raise ValueError(f"{name} ranking cannot be empty")
    if len(ranking) != len(set(ranking)):
        raise ValueError(f"{name} ranking contains duplicate chunk IDs")


def reciprocal_rank_fusion(
    dense_ranking: Sequence[str],
    bm25_ranking: Sequence[str],
    *,
    rrf_k: int = DEFAULT_RRF_K,
    top_k: int = 10,
) -> RRFResponse:
    """Fuse equal-weight rankings with ``sum(1 / (rrf_k + rank))``."""

    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    _validate_ranking("Dense", dense_ranking)
    _validate_ranking("BM25", bm25_ranking)
    started = perf_counter()
    dense_ranks = {chunk_id: rank for rank, chunk_id in enumerate(dense_ranking, 1)}
    bm25_ranks = {chunk_id: rank for rank, chunk_id in enumerate(bm25_ranking, 1)}
    candidates = set(dense_ranks) | set(bm25_ranks)
    hits: list[RRFHit] = []
    for chunk_id in candidates:
        dense_rank = dense_ranks.get(chunk_id)
        bm25_rank = bm25_ranks.get(chunk_id)
        dense_contribution = (
            0.0 if dense_rank is None else 1.0 / (rrf_k + dense_rank)
        )
        bm25_contribution = (
            0.0 if bm25_rank is None else 1.0 / (rrf_k + bm25_rank)
        )
        hits.append(
            RRFHit(
                chunk_id=chunk_id,
                score=dense_contribution + bm25_contribution,
                dense_rank=dense_rank,
                bm25_rank=bm25_rank,
                dense_contribution=dense_contribution,
                bm25_contribution=bm25_contribution,
            )
        )

    missing_rank = max(len(dense_ranking), len(bm25_ranking)) + 1
    hits.sort(
        key=lambda hit: (
            -hit.score,
            min(
                hit.dense_rank if hit.dense_rank is not None else missing_rank,
                hit.bm25_rank if hit.bm25_rank is not None else missing_rank,
            ),
            (hit.dense_rank if hit.dense_rank is not None else missing_rank)
            + (hit.bm25_rank if hit.bm25_rank is not None else missing_rank),
            hit.chunk_id,
        )
    )
    selected = hits[:top_k]
    if any(not math.isfinite(hit.score) for hit in selected):
        raise RuntimeError("RRF produced a non-finite score")
    return RRFResponse(
        hits=selected,
        latency_ms=(perf_counter() - started) * 1000.0,
        candidate_count=len(candidates),
    )

