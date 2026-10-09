from dataclasses import asdict
import json

import numpy as np
import pytest

from src.evaluation.retrieval_preparation import MemoryCosineStore, compare_baseline, compare_source, rank_questions, rankings_equal_with_tolerance
from src.retrieval.hybrid import HybridRuntimeHit, HybridRuntimeResponse


def test_exact_cosine_normalizes_vectors_and_breaks_ties_by_id():
    store = MemoryCosineStore(['z', 'a', 'b'], np.array([[2, 0], [1, 0], [0, 1]]))
    result = store.search_exact_cosine([3, 0], embedding_run_id='synthetic', top_k=3)
    assert [h.chunk_id for h in result.hits] == ['a', 'z', 'b']
    assert [h.score for h in result.hits] == [1.0, 1.0, 0.0]


@pytest.mark.parametrize('vectors,ids', [(np.array([[0, 0]]), ['a']), (np.array([[np.nan, 1]]), ['a']), (np.eye(2), ['a', 'a'])])
def test_memory_store_rejects_invalid_documents(vectors, ids):
    with pytest.raises(ValueError):
        MemoryCosineStore(ids, vectors)


def test_ranker_receives_question_only():
    seen = []
    class Retriever:
        def search(self, query, *, top_k):
            seen.append((query, top_k))
            return HybridRuntimeResponse([], {}, 0)
    assert rank_questions(Retriever(), [('case-label', 'question')]) == {'case-label': []}
    assert seen == [('question', 10)]
    with pytest.raises(ValueError, match='Duplicate'):
        rank_questions(Retriever(), [('a', 'q'), ('a', 'q')])


def test_source_comparison_detects_order_and_score_changes(tmp_path):
    path = tmp_path / 'source.jsonl'
    path.write_text(json.dumps({'case_id': 'x', 'retrieved': [{'chunk_id': 'a', 'score': 0.5}]}), encoding='utf-8')
    assert compare_source({'x': [{'chunk_id': 'a', 'score': 0.5000001}]}, path)['passed']
    assert not compare_source({'x': [{'chunk_id': 'a', 'score': 0.51}]}, path)['passed']
    assert not compare_source({'x': [{'chunk_id': 'b', 'score': 0.5}]}, path)['passed']


def test_hybrid_comparison_checks_rrf_score_source_ranks_and_metrics(tmp_path):
    old = {'chunk_id': 'a', 'score': 2/61, 'dense_rank': 1, 'bm25_rank': 1, 'dense_contribution': 1/61, 'bm25_contribution': 1/61}
    (tmp_path / 'hybrid_results.jsonl').write_text(json.dumps({'case_id': 'x', 'retrieved': [old]}), encoding='utf-8')
    metrics = {'macro': {'mrr': 1.0}, 'per_case': {'x': {'reciprocal_rank': 1.0}}}
    (tmp_path / 'comparison.json').write_text(json.dumps({'metrics': {'hybrid': metrics}}), encoding='utf-8')
    hit = asdict(HybridRuntimeHit('a', 1, 2/61, 1, .5, 1, 2., 1/61, 1/61))
    assert compare_baseline({'x': [hit]}, metrics, tmp_path)['passed']
    hit['rrf_score'] = .9
    assert not compare_baseline({'x': [hit]}, metrics, tmp_path)['passed']
    hit['rrf_score'] = 2/61
    hit['bm25_rank'] = 2
    assert not compare_baseline({'x': [hit]}, metrics, tmp_path)['passed']


def test_refactor_comparison_tolerates_floats_but_rejects_rank_changes():
    before = {'x': [{'chunk_id': 'a', 'rank': 1, 'score': .5}]}
    assert rankings_equal_with_tolerance(before, {'x': [{'chunk_id': 'a', 'rank': 1, 'score': .5000001}]})
    assert not rankings_equal_with_tolerance(before, {'x': [{'chunk_id': 'a', 'rank': 2, 'score': .5}]})
    assert not rankings_equal_with_tolerance(before, {'x': [{'chunk_id': 'b', 'rank': 1, 'score': .5}]})
