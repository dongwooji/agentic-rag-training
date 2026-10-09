"""Public synthetic H1 checks, including actual retrieval through Recovery fusion."""

from copy import deepcopy
from dataclasses import asdict
import json

import numpy as np
import pytest
from pydantic import ValidationError

from test_retrieval_config import synthetic_assets
from graph_test_support import (
    FakeTrainingLogTool, FakeMetricTool, SequenceRuntimeGrader, SequenceRecoveryProvider,
    LITERATURE_QUESTION, LITERATURE_SUBQUESTION, INITIAL_QUERY, recovery_payload,
)
from src.agent.executor import DeterministicToolExecutor
from src.graph.workflow import AgenticRAGWorkflow
from src.recovery.agent import EvidenceRecoveryAgent
from src.recovery.evidence_fusion import FUSION_POLICY
from src.retrieval.bm25 import BM25Index
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.retrieval.postgres import SearchHit, SearchResponse
from src.retrieval.rrf import reciprocal_rank_fusion
from src.retrieval.runtime_config import DEFAULT_RETRIEVAL_CONFIG, RetrievalConfig, load_retrieval_config
from src.tools.literature import LiteratureTool
from src.evaluation.retrieval_preparation import compare_h1_to_h0, metric_changes


def test_h1_config_changes_only_policy_and_identifiers():
    h0 = DEFAULT_RETRIEVAL_CONFIG.model_dump()
    h1 = load_retrieval_config('config/retrieval_h1_v1.json').model_dump()
    assert h1['setting'] == 'H1'
    assert h1['config_version'] == 'retrieval_h1_v1'
    assert h1['bm25']['score_policy'] == 'positive_only'
    h1['setting'], h1['config_version'] = h0['setting'], h0['config_version']
    h1['bm25']['score_policy'] = h0['bm25']['score_policy']
    assert h1 == h0


@pytest.mark.parametrize('setting,policy', [('H0', 'positive_only'), ('H1', 'retain_zero')])
def test_setting_policy_mismatch_is_rejected(setting, policy):
    data = DEFAULT_RETRIEVAL_CONFIG.model_dump()
    data['setting'], data['bm25']['score_policy'] = setting, policy
    with pytest.raises(ValidationError, match='policy differs'):
        RetrievalConfig.model_validate(data)


@pytest.mark.parametrize('query', ['무일치질문', '', '---'])
def test_h1_no_match_is_empty_and_h0_preserves_zeros(query):
    index = BM25Index([{'chunk_id': 'b', 'text': 'beta'}, {'chunk_id': 'a', 'text': 'alpha'}])
    old = index.search(query, top_k=2)
    new = index.search(query, top_k=2, score_policy='positive_only')
    assert [h.chunk_id for h in old.hits] == ['a', 'b']
    assert all(h.score == 0 for h in old.hits)
    assert new.hits == [] and new.matched_query_terms == []


def test_positive_scores_and_ties_are_preserved_without_padding():
    index = BM25Index([{'chunk_id': 'z', 'text': 'alpha'}, {'chunk_id': 'a', 'text': 'alpha'}, {'chunk_id': 'b', 'text': 'beta'}])
    before = index.search('alpha', top_k=3)
    after = index.search('alpha', top_k=3, score_policy='positive_only')
    assert [asdict(h) for h in after.hits] == [asdict(h) for h in before.hits[:2]]
    assert [h.chunk_id for h in after.hits] == ['a', 'z']
    assert len(index.search('alpha', top_k=1, score_policy='positive_only').hits) == 1
    with pytest.raises(ValueError, match='score policy'):
        index.search('alpha', top_k=1, score_policy='unknown')


def test_nonpositive_scores_are_excluded_even_if_a_term_matches():
    index = BM25Index([{'chunk_id': 'a', 'text': 'alpha'}])
    # The current Okapi IDF is positive; inject a negative contribution to check the boundary.
    index._idf['alpha'] = -1.0
    assert index.search('alpha', top_k=1).hits[0].score < 0
    assert index.search('alpha', top_k=1, score_policy='positive_only').hits == []


def test_empty_bm25_rrf_keeps_dense_order_and_contributions():
    result = reciprocal_rank_fusion(['z', 'a', 'm'], [], top_k=10)
    assert [h.chunk_id for h in result.hits] == ['z', 'a', 'm']
    assert result.candidate_count == 3
    for rank, hit in enumerate(result.hits, 1):
        assert hit.dense_rank == rank and hit.bm25_rank is None
        assert hit.score == hit.dense_contribution == 1 / (60 + rank)
        assert hit.bm25_contribution == 0
    for dense, bm25 in (([], []), ([], ['a']), (['a', 'a'], []), (['a'], ['b', 'b'])):
        with pytest.raises(ValueError):
            reciprocal_rank_fusion(dense, bm25)


@pytest.mark.parametrize('recovery_positive', [False, True])
@pytest.mark.parametrize('sufficient', [False, True])
def test_h1_actual_retrieval_recovery_and_fusion(synthetic_assets, recovery_positive, sufficient):
    root, h0 = synthetic_assets
    data = h0.model_dump()
    data['setting'], data['config_version'], data['bm25']['score_policy'] = 'H1', 'synthetic_h1', 'positive_only'
    config = RetrievalConfig.model_validate(data)
    assets = load_frozen_literature_assets(root, config=config)
    for chunk in assets.chunks:
        chunk.update(paper_id='synthetic-paper', title='Synthetic evidence', section='Results', text_sha256='a'*64, source_sha256='b'*64)
    class Encoder:
        def encode(self, texts, *, show_progress=False):
            return np.ones((len(texts), 384))
    class Store:
        calls = 0
        def search_exact_cosine(self, query, *, embedding_run_id, top_k):
            start = self.calls
            self.calls += 1
            return SearchResponse([SearchHit(f'c{(start+i)%12:02}', .9-i*.01) for i in range(top_k)], .1)
    store = Store()
    retriever = FrozenHybridRetriever(assets=assets, encoder=Encoder(), vector_store=store)
    tool = LiteratureTool(assets=assets, retriever=retriever)
    training, metric = FakeTrainingLogTool(), FakeMetricTool()
    grader = SequenceRuntimeGrader([False, True] if sufficient else [False, False, False])
    term = 'alpha' if recovery_positive else 'unknownterm'
    queries = [f'VBT {term} direct evidence', f'VBT {term} additional evidence']
    recovery = SequenceRecoveryProvider([recovery_payload(q) for q in queries])
    workflow = AgenticRAGWorkflow(
        tool_executor=DeterministicToolExecutor(training_log_tool=training, metric_tool=metric, literature_tool=tool),
        literature_tool=tool, runtime_grader=grader, recovery_agent=EvidenceRecoveryAgent(recovery),
    )
    state = workflow.invoke(LITERATURE_QUESTION, literature_subquestion=LITERATURE_SUBQUESTION, initial_query=INITIAL_QUERY)
    attempts = 1 if sufficient else 2
    assert state['final_status'] == ('answer_ready' if sufficient else 'abstain_ready')
    assert not state.get('errors')
    assert state['retry_count'] == attempts and store.calls == attempts + 1
    assert state['query_history'] == [INITIAL_QUERY, *queries[:attempts]]
    assert len(state['fusion_history']) == attempts
    assert all(h['bm25_rank'] is None and h['bm25_score'] is None and h['bm25_rrf_contribution'] == 0 for h in state['literature_evidence'])
    assert all((h['bm25_rank'] is not None) == recovery_positive for h in state['recovery_evidence'])
    assert len(state['fused_evidence']) == 10
    for fusion in state['fusion_history']:
        assert fusion['provenance']['fusion_policy'] == FUSION_POLICY == 'retry_evidence_fusion_v1'
        assert fusion['provenance']['source_weights'] == {'initial': 1.0, 'recovery': 1.0}
        for hit in fusion['evidence']:
            assert hit['fusion_score'] == hit['initial_fusion_contribution'] + hit['recovery_fusion_contribution']
            if hit['initial_retrieval']:
                assert hit['initial_retrieval']['bm25_rank'] is None
                assert hit['initial_retrieval']['bm25_rrf_contribution'] == 0


def test_metric_change_counts_are_separate_and_include_empty_results():
    before = {'macro': {'mrr': .5}, 'per_case': {'a': {'reciprocal_rank': .5}, 'b': {'reciprocal_rank': 0.}, 'c': {'reciprocal_rank': 1.}}}
    after = {'macro': {'mrr': .5}, 'per_case': {'a': {'reciprocal_rank': 1.}, 'b': {'reciprocal_rank': 0.}, 'c': {'reciprocal_rank': .5}}}
    changes = metric_changes(before, after)
    assert changes['counts']['reciprocal_rank'] == {'improved': 1, 'worsened': 1, 'unchanged': 1}
    assert changes['macro']['mrr']['direction'] == 'unchanged'
    with pytest.raises(ValueError, match='identical cases'):
        metric_changes(before, {'macro': {'mrr': .5}, 'per_case': {}})


def test_h1_comparison_distinguishes_execution_from_dense_floor(tmp_path):
    h0 = DEFAULT_RETRIEVAL_CONFIG.model_dump()
    h1 = deepcopy(h0)
    h1.update(setting='H1', config_version='retrieval_h1_v1')
    h1['bm25']['score_policy'] = 'positive_only'
    metrics = lambda egr: {'macro': {'evidence_group_recall@10': egr}, 'per_case': {'x': {'evidence_group_recall@10': egr}}}
    dense = {'x': [{'chunk_id': 'a', 'score': .5}]}
    baseline = {'passed': True, 'config': h0, 'dataset': 'dev-ko', 'metrics': metrics(.1), 'source_metrics': {'dense': metrics(.3), 'bm25': metrics(0.)}}
    for name, payload in [('result.json', baseline), ('source_rankings.json', {'dense': dense, 'bm25': {'x': [{'chunk_id': 'b', 'score': 0.}]}}), ('rankings.json', {'x': []})]:
        (tmp_path/name).write_text(json.dumps(payload), encoding='utf-8')
    result = {**baseline, 'config': h1, 'metrics': metrics(.2)}
    rankings = {'x': [{'chunk_id': 'a', 'bm25_rank': None, 'bm25_score': None, 'bm25_rrf_contribution': 0}]}
    sources = {'dense': dense, 'bm25': {'x': []}}
    comparison = compare_h1_to_h0(result, rankings, sources, tmp_path)
    assert comparison['passed']
    assert not comparison['hybrid_vs_h0_dense_egr']['at_least_dense']
    assert comparison['performance']['counts']['evidence_group_recall@10']['improved'] == 1
    sources['dense']['x'][0]['chunk_id'] = 'changed'
    assert not compare_h1_to_h0(result, rankings, sources, tmp_path)['passed']
