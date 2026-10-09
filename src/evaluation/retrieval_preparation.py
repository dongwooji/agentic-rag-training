"""Offline H0 preparation. Ranking receives questions only, never Gold or labels."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np

from src.retrieval.hybrid import FrozenHybridRetriever
from src.retrieval.postgres import SearchHit, SearchResponse
from .retrieval_metrics import evaluate_retrieval


SCORE_ATOL = 1e-6
METRIC_ATOL = 1e-12


class MemoryCosineStore:
    """Exact cosine with pgvector-compatible chunk-ID tie order; no persistence."""

    def __init__(self, chunk_ids: list[str], vectors: np.ndarray) -> None:
        values = np.asarray(vectors, dtype=np.float64)
        if values.ndim != 2 or len(values) != len(chunk_ids):
            raise ValueError("Embedding matrix and chunk IDs must align")
        norms = np.linalg.norm(values, axis=1)
        if not chunk_ids or len(set(chunk_ids)) != len(chunk_ids) or not np.isfinite(values).all() or not np.isfinite(norms).all() or (norms <= 0).any():
            raise ValueError("Invalid document vectors or duplicate chunk IDs")
        self.chunk_ids = tuple(chunk_ids)
        self.vectors = values / norms[:, None]
        self.searches: list[list[dict[str, Any]]] = []

    def search_exact_cosine(self, query_embedding: Any, *, embedding_run_id: str, top_k: int) -> SearchResponse:
        started = perf_counter()
        query = np.asarray(query_embedding, dtype=np.float64)
        norm = np.linalg.norm(query)
        if query.shape != (self.vectors.shape[1],) or not np.isfinite(query).all() or not np.isfinite(norm) or norm <= 0:
            raise ValueError("Invalid query vector")
        if not 1 <= top_k <= len(self.chunk_ids):
            raise ValueError("Invalid search depth")
        scores = self.vectors @ (query / norm)
        order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), self.chunk_ids[i]))[:top_k]
        hits = [SearchHit(self.chunk_ids[i], float(scores[i])) for i in order]
        self.searches.append([asdict(hit) for hit in hits])
        return SearchResponse(
            hits=hits,
            database_search_ms=(perf_counter() - started) * 1000,
        )


def rank_questions(retriever: FrozenHybridRetriever, questions: list[tuple[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """Bookkeeping IDs never enter the encoder or retriever."""
    if len({case_id for case_id, _ in questions}) != len(questions):
        raise ValueError("Duplicate question IDs")
    return {
        case_id: [asdict(hit) for hit in retriever.search(question, top_k=10).hits]
        for case_id, question in questions
    }


def evaluate_rankings(cases: list[dict[str, Any]], ranked: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return evaluate_retrieval(cases, {k: [hit['chunk_id'] for hit in v] for k, v in ranked.items()}, ks=(10,))


def compare_baseline(ranked: dict[str, list[dict[str, Any]]], metrics: dict[str, Any], baseline_dir: Path) -> dict[str, Any]:
    baseline = [json.loads(line) for line in (baseline_dir / 'hybrid_results.jsonl').read_text(encoding='utf-8').splitlines() if line]
    expected = {row['case_id']: row['retrieved'] for row in baseline}
    comparisons = []
    for case_id, hits in ranked.items():
        old = expected[case_id]
        same = [h['chunk_id'] for h in hits] == [h['chunk_id'] for h in old]
        errors = []
        if same:
            for fresh, stored in zip(hits, old, strict=True):
                for key, old_key in [('rrf_score', 'score'), ('dense_rrf_contribution', 'dense_contribution'), ('bm25_rrf_contribution', 'bm25_contribution')]:
                    a, b = fresh.get(key), stored.get(old_key)
                    if a is not None and b is not None:
                        errors.append(abs(a-b))
                    elif a != b:
                        errors.append(float('inf'))
        source_ranks_match = same and all(fresh['dense_rank'] == stored['dense_rank'] and fresh['bm25_rank'] == stored['bm25_rank'] for fresh, stored in zip(hits, old, strict=True))
        comparisons.append({'case_id': case_id, 'top10_exact_rank_match': same, 'source_ranks_match': source_ranks_match, 'max_score_difference': max(errors, default=0.0), 'scores_within_tolerance': same and all(e <= SCORE_ATOL for e in errors)})
    old_metrics = json.loads((baseline_dir / 'comparison.json').read_text(encoding='utf-8'))['metrics']['hybrid']
    metric_match = all(
        math.isclose(value, old_metrics['macro'][name], rel_tol=0, abs_tol=METRIC_ATOL)
        for name, value in metrics['macro'].items()
    ) and all(
        math.isclose(value, old_metrics['per_case'][case_id][name], rel_tol=0, abs_tol=METRIC_ATOL)
        for case_id, values in metrics['per_case'].items() for name, value in values.items()
    )
    case_set_match = set(ranked) == set(expected)
    return {'score_atol': SCORE_ATOL, 'metric_atol': METRIC_ATOL, 'case_set_match': case_set_match, 'metrics_match': metric_match, 'cases': comparisons, 'passed': case_set_match and metric_match and all(c['scores_within_tolerance'] and c['source_ranks_match'] for c in comparisons)}


def compare_source(rankings: dict[str, list[dict[str, Any]]], path: Path) -> dict[str, Any]:
    expected = {r['case_id']: r['retrieved'] for r in [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]}
    cases = []
    for case_id, fresh in rankings.items():
        old = expected[case_id]
        same = [h['chunk_id'] for h in fresh] == [h['chunk_id'] for h in old]
        error = max((abs(a['score']-b['score']) for a,b in zip(fresh,old,strict=True)), default=0.0) if same else None
        cases.append({'case_id': case_id, 'top10_exact_rank_match': same, 'max_score_difference': error, 'passed': same and error <= SCORE_ATOL})
    return {'passed': set(rankings) == set(expected) and all(c['passed'] for c in cases), 'score_atol': SCORE_ATOL, 'cases': cases}


def rankings_equal_with_tolerance(before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]]) -> bool:
    """IDs, rank and contributions stay aligned; floats get declared tolerance."""
    if set(before) != set(after):
        return False
    score_fields = {'score', 'rrf_score', 'dense_score', 'bm25_score', 'dense_rrf_contribution', 'bm25_rrf_contribution'}
    for case_id, old_hits in before.items():
        hits = after[case_id]
        if len(old_hits) != len(hits):
            return False
        for old, new in zip(old_hits, hits, strict=True):
            if set(old) != set(new):
                return False
            for key, value in old.items():
                other = new[key]
                if key in score_fields and value is not None and other is not None:
                    if not math.isclose(value, other, rel_tol=0, abs_tol=SCORE_ATOL):
                        return False
                elif value != other:
                    return False
    return True
