"""Synthetic child construction, mapping, criteria and unchanged legacy settings."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import re
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from src.retrieval.child import build_children, sentence_spans, ParentChildStore, token_count
from src.retrieval.rrf import reciprocal_rank_fusion
from src.retrieval.postgres import SearchHit, SearchResponse
from src.retrieval.runtime_config import load_retrieval_config, RetrievalConfig
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.tools.literature import LiteratureTool, LiteratureInput
from src.evaluation.child_depth import adoption_decision, combined_metrics
from src.evaluation.retrieval_preparation import evaluate_rankings
from test_retrieval_config import synthetic_assets

ROOT=Path(__file__).resolve().parents[1]


class WordTokenizer:
    is_fast=True
    def num_special_tokens_to_add(self, *, pair):
        return 2
    def __call__(self,text,*,add_special_tokens=True,truncation=False,return_offsets_mapping=False):
        matches=list(re.finditer(r'\S+',text))
        result={'input_ids':list(range(len(matches)+(2 if add_special_tokens else 0)))}
        if return_offsets_mapping:
            result['offset_mapping']=[m.span() for m in matches]
        return result


def test_normal_children_keep_sentence_boundaries_and_exact_parent_spans():
    parent={'chunk_id':'parent','text':'One two. Three four. Five.'}
    children,stats=build_children([parent],WordTokenizer(),max_tokens=4)
    assert [c['text'] for c in children]==['One two.','Three four.','Five.']
    assert all(parent['text'][c['start_char']:c['end_char']]==c['text'] for c in children)
    assert all(c['parent_id']=='parent' and c['token_count']<=4 for c in children)
    assert stats['forced_split_sentence_count']==stats['forced_split_child_count']==0


def test_decimal_and_abbreviation_boundaries_and_quotes():
    text='Fig. 2 shows 3.5 units. "Next sentence!" Last sentence?'
    assert [text[a:b].strip() for a,b in sentence_spans(text)]==[
        'Fig. 2 shows 3.5 units.','"Next sentence!"','Last sentence?']


def test_only_oversized_sentence_forced_split_with_coverage_and_statistics():
    text=' '.join(f'token{i}' for i in range(250))+'. Next sentence.'
    parent={'chunk_id':'parent','text':text}
    rows,stats=build_children([parent],WordTokenizer())
    assert stats['forced_split_sentence_count']==1
    assert stats['forced_split_child_count']==3
    assert stats['child_count']==4 and stats['forced_split_child_ratio']==.75
    assert [r['forced_split'] for r in rows]==[True,True,True,False]
    assert all(token_count(WordTokenizer(),r['text'])<=110 for r in rows)
    assert re.sub(r'\s+','',text)==re.sub(r'\s+','',''.join(r['text'] for r in rows))
    assert len({r['child_id'] for r in rows})==len(rows)
    assert stats['tokens']['max']==110
    assert rows==build_children([parent],WordTokenizer())[0]


def test_empty_duplicate_or_non_offset_tokenizer_rejected():
    with pytest.raises(ValueError,match='Empty'):
        build_children([{'chunk_id':'p','text':' '}],WordTokenizer())
    with pytest.raises(ValueError,match='Duplicate'):
        build_children([{'chunk_id':'p','text':'one'}]*2,WordTokenizer())
    with pytest.raises(ValueError,match='fast tokenizer'):
        build_children([{'chunk_id':'p','text':'one'}],object())


def mapped_store():
    rows=[dict(child_id='a1',parent_id='a'),dict(child_id='a2',parent_id='a'),
          dict(child_id='b1',parent_id='b'),dict(child_id='c1',parent_id='c')]
    raw=Mock(search_exact_cosine=Mock(return_value=SearchResponse([
        SearchHit('a1',.9),SearchHit('a2',.85),SearchHit('b1',.8),SearchHit('c1',.7)],.1)))
    return raw,ParentChildStore(raw,rows,{'a','b','c'})


def test_collapse_uses_best_child_only_and_does_not_fill_short_pool():
    raw,store=mapped_store()
    hits=store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=20).hits
    assert [(h.chunk_id,h.score) for h in hits]==[('a',.9),('b',.8),('c',.7)]
    assert store.mappings[0]['selected'][1]['best_child_rank']==3
    assert store.mappings[0]['selected'][1]['parent_rank']==2
    assert store.mappings[0]['actual_parent_count']==3
    assert raw.search_exact_cosine.call_args.kwargs['top_k']==4
    fused=reciprocal_rank_fusion([h.chunk_id for h in hits],[],top_k=10)
    assert fused.hits[1].score==1/62
    assert all(h.bm25_contribution==0 for h in fused.hits)


def test_parent_cap_changes_only_rrf_input_prefix_not_child_pool():
    raw,store=mapped_store()
    a=store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=1)
    b=store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=3)
    assert a.hits==b.hits[:1]
    assert store.child_searches[0]==store.child_searches[1]
    assert all(call.kwargs['top_k']==4 for call in raw.search_exact_cosine.call_args_list)


def test_mapping_fails_on_invalid_membership_or_unknown_child():
    with pytest.raises(ValueError,match='membership'):
        ParentChildStore(object(),[dict(child_id='a',parent_id='outside')],{'p'})
    raw,store=mapped_store()
    raw.search_exact_cosine.return_value=SearchResponse([SearchHit('unknown',1.)],0.)
    with pytest.raises(ValueError,match='unknown child'):
        store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=10)


@pytest.mark.parametrize('kind', ['child','parent'])
def test_depth_configs_change_only_candidate_caps(kind):
    configs=[load_retrieval_config(ROOT/f'config/retrieval_step5_{kind}_d{d}_v1.json') for d in (10,20,50)]
    normalized=[]
    for depth,config in zip((10,20,50),configs):
        assert config.dense.source_depth==config.bm25.source_depth==depth
        assert config.rrf.top_k==10
        data=config.model_dump()
        data.update(config_version='normalized')
        data['dense']['source_depth']=data['bm25']['source_depth']=10
        normalized.append(data)
        if kind=='child':
            assert config.dense.child.search_depth==50 and config.dense.child.max_tokens==110
    assert normalized[0]==normalized[1]==normalized[2]
    old=load_retrieval_config(ROOT/'config/retrieval_step3b_translation_v1.json')
    assert 'child' not in old.model_dump()['dense'] and old.schema_version==1


def test_historical_depth_contract_and_new_child_store_contract(synthetic_assets):
    root,config=synthetic_assets
    assets=load_frozen_literature_assets(root,config=config)
    data=config.model_dump()
    data.update(schema_version=2,setting='H1',query_mode='translated_both')
    data['bm25']['score_policy']='positive_only'
    data['dense'].update(unit='child',child=dict(representation_version='retrieval_child_v1',max_tokens=110,search_depth=50))
    child_config=RetrievalConfig.model_validate(data)
    child_assets=load_frozen_literature_assets(root,config=child_config)
    with pytest.raises(RuntimeError,match='parent-mapping store'):
        FrozenHybridRetriever(assets=child_assets,encoder=object(),vector_store=object())
    # Synthetic child-backed literature integration, without DB or real encoder.
    children=[dict(child_id=f'child{i}',parent_id=c['chunk_id']) for i,c in enumerate(assets.chunks)]
    raw=Mock(search_exact_cosine=Mock(return_value=SearchResponse([
        SearchHit(c['child_id'],1.-i*.01) for i,c in enumerate(children)],0.)))
    store=ParentChildStore(raw,children,set(assets.chunks_by_id))
    encoder=SimpleNamespace(metadata=None,encode=Mock(return_value=[[1.]*384]))
    retriever=FrozenHybridRetriever(assets=child_assets,encoder=encoder,vector_store=store)
    rich_chunks={cid:dict(c,paper_id='synthetic-paper',title='Synthetic measurement',section='Methods',
                         text_sha256='a'*64,source_sha256='b'*64) for cid,c in child_assets.chunks_by_id.items()}
    tool=LiteratureTool(assets=replace(child_assets,chunks_by_id=rich_chunks),retriever=retriever)
    response=tool.execute(LiteratureInput(operation='search',query='없는질문'))
    assert response.success and len(response.result['hits'])==10
    assert all(hit['chunk_id'] in assets.chunks_by_id for hit in response.result['hits'])
    assert all(hit['text']=='alpha beta' for hit in response.result['hits'])


def test_child_postgres_fails_before_db_or_embedding_initialization(monkeypatch):
    forbidden=Mock(side_effect=AssertionError('No child DB initialized'))
    monkeypatch.setattr('src.tools.literature.PgVectorStore',forbidden)
    monkeypatch.setattr('src.tools.literature.MiniLMEncoder',forbidden)
    with pytest.raises(ValueError,match='Child pgvector'):
        LiteratureTool.from_postgres(password='synthetic',retrieval_config=load_retrieval_config(
            ROOT/'config/retrieval_step4_child_v1.json'))
    forbidden.assert_not_called()


def test_historical_config_serialization_matches_exact_original_fields():
    for version in ('retrieval_h0_v1','retrieval_h1_v1','retrieval_step3b_translation_v1'):
        path=ROOT/'config'/(version+'.json')
        assert load_retrieval_config(path).model_dump()==json.loads(path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('change', ['depth','query','budget','child_missing'])
def test_experiment_config_rejects_undeclared_variables(change):
    data=load_retrieval_config(ROOT/'config/retrieval_step4_child_v1.json').model_dump()
    if change=='depth': data['dense']['source_depth']=30
    elif change=='query': data['query_mode']='generated_ko'
    elif change=='budget': data['dense']['child']['max_tokens']=127
    else: data['dense'].pop('child')
    with pytest.raises(ValueError):
        RetrievalConfig.model_validate(data)


def test_evaluation_checks_mapping_approval_before_assets_or_encoder(monkeypatch):
    import sys
    spec=importlib.util.spec_from_file_location('child_runner_gate',ROOT/'scripts/run_retrieval_child_depth.py')
    runner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    forbidden=Mock(side_effect=AssertionError('No backend before approval'))
    monkeypatch.setattr(runner,'load_frozen_literature_assets',forbidden)
    monkeypatch.setattr(runner,'MiniLMEncoder',forbidden)
    monkeypatch.setattr(runner,'load_reviewed_conditions',Mock(side_effect=ValueError('mapping approval missing')))
    monkeypatch.setattr(sys,'argv',['runner','--output',str(ROOT/'reports/experiments/synthetic-child-gate-no-output')])
    with pytest.raises(ValueError,match='approval'):
        runner.main()
    forbidden.assert_not_called()


def metric_pair(dev_hit,ko_hit):
    cases=[dict(id='synthetic',gold={'literature_evidence_groups':[
        dict(required=True,match='any',chunk_ids=['evidence'])]})]
    return {dataset:evaluate_rankings(cases,{'synthetic':[dict(chunk_id='evidence' if hit else 'other')]})
            for dataset,hit in (('dev',dev_hit),('dev-ko',ko_hit))}


def test_criteria_count_language_pairs_separately_and_do_not_veto_one_language():
    before=metric_pair(True,False)
    after=metric_pair(False,True)
    assert combined_metrics(after)['case_count']==2
    assert not adoption_decision(before,after)['adopted']  # one improvement == one worsening
    # Add another paired case: only Korean improves, while dev remains worse.
    before['dev']['per_case']['second']=before['dev-ko']['per_case']['synthetic'].copy()
    before['dev-ko']['per_case']['second']=before['dev-ko']['per_case']['synthetic'].copy()
    after['dev']['per_case']['second']=after['dev']['per_case']['synthetic'].copy()
    after['dev-ko']['per_case']['second']=after['dev-ko']['per_case']['synthetic'].copy()
    result=adoption_decision(before,after)
    assert result['adopted']
    assert result['combined']['counts']['evidence_group_recall@10']==dict(improved=2,worsened=1,unchanged=1)
    assert result['datasets']['dev']['macro']['complete_evidence@10']['delta']<0
