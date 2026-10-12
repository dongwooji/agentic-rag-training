"""Public synthetic tests: scoring contract, coverage bounds, selection and freeze."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.retrieval.reranker import load_reranker_config, rerank_parents, MiniLMReranker
from src.evaluation.reranker import (fused_candidate_ids, diagnose_missing_groups, pool_evidence_upper_bound,
                                    select_reranker, latency_summary, summarize_token_audit)
from src.evaluation.retrieval_preparation import evaluate_rankings
from src.evaluation.dense_length import verify_frozen_folder

ROOT = Path(__file__).resolve().parents[1]


class SyntheticTokenizer:
    def encode(self, text, **kwargs):
        assert kwargs == {'add_special_tokens':False}
        return text.split()
    def num_special_tokens_to_add(self, *, pair):
        assert pair
        return 3


def parent(cid, text='synthetic full parent text'):
    return dict(chunk_id=cid,text=text)


def test_reranker_passes_full_text_and_only_query_text_not_ids_to_scorer():
    text = ' '.join(['synthetic']*510)
    seen = []
    def score(pairs):
        seen.extend(pairs)
        return [-5,3,3]
    result = rerank_parents('two words',[parent('a',text),parent('b'),parent('c')],
        tokenizer=SyntheticTokenizer(),score_pairs=score)
    assert seen == [('two words',text),('two words','synthetic full parent text'),('two words','synthetic full parent text')]
    assert [h['chunk_id'] for h in result['hits']] == ['b','c','a']
    assert result['hits'][0]['score'] == 3 and result['hits'][-1]['score'] == -5
    assert result['token_audit'][0] == dict(chunk_id='a',query_tokens=2,parent_tokens=510,
        special_tokens=3,pair_tokens=515,retained_parent_tokens=507,truncated=True)
    assert result['candidate_count'] == 3 and result['latency_ms'] >= 0
    assert summarize_token_audit([result])['truncated_pairs'] == 1


def test_exact_input_boundary_and_top10_membership_are_preserved():
    result = rerank_parents('query',[parent(str(i),' '.join(['x']*508)) for i in range(12)],
        tokenizer=SyntheticTokenizer(),score_pairs=lambda pairs:list(range(len(pairs))))
    assert len(result['hits']) == 10 and len(result['all_scores']) == 12
    assert [h['chunk_id'] for h in result['hits']] == list(map(str,range(11,1,-1)))
    assert all(not a['truncated'] and a['pair_tokens']==512 for a in result['token_audit'])


@pytest.mark.parametrize('query,parents',[
    ('',[parent('a')]),('q',[]),('q',[parent('a'),parent('a')]),('q',[parent('a','')]),
    (' '.join(['q']*509),[parent('a')]),
])
def test_invalid_inputs_fail_before_scoring(query,parents):
    def forbidden(pairs):
        pytest.fail('Invalid inputs must not reach model scoring')
    with pytest.raises(ValueError):
        rerank_parents(query,parents,tokenizer=SyntheticTokenizer(),score_pairs=forbidden)


@pytest.mark.parametrize('scores', [[],[float('nan')],[float('inf')]])
def test_invalid_model_outputs_are_execution_failures(scores):
    with pytest.raises(ValueError):
        rerank_parents('q',[parent('a')],tokenizer=SyntheticTokenizer(),score_pairs=lambda _:scores)


def test_pinned_config_rejects_new_model_or_unregistered_settings(tmp_path):
    source = json.loads((ROOT/'config/reranker_minilm_v1.json').read_text(encoding='utf-8'))
    assert load_reranker_config(ROOT/'config/reranker_minilm_v1.json').model_revision == '233902d25c440f23af6f7d6e94d2946bac0bee0a'
    for key,value in [('model_revision','main'),('model_id','other/model'),('cpu_threads',8),('max_length',1024),('truncation','longest_first')]:
        data = dict(source,**{key:value})
        path = tmp_path/(key+'.json')
        path.write_text(json.dumps(data),encoding='utf-8')
        with pytest.raises(ValidationError):
            load_reranker_config(path)


def test_cpu_model_batching_uses_parent_only_truncation_and_raw_logits():
    import torch
    config = load_reranker_config(ROOT/'config/reranker_minilm_v1.json')
    instance = object.__new__(MiniLMReranker)
    instance.config,instance._torch = config,torch
    calls = []
    def tokenize(queries,texts,**kw):
        calls.append((queries,texts,kw))
        return {'sample_count':len(queries)}
    instance.tokenizer = tokenize
    instance._model = lambda **features:SimpleNamespace(logits=torch.tensor([[-2.]]*features['sample_count']))
    pairs = [('full query','full parent')]*17
    assert instance._score_pairs(pairs) == [-2.]*17
    assert [len(c[0]) for c in calls] == [16,1]
    assert all(c[2] == dict(padding=True,truncation='only_second',max_length=512,return_tensors='pt') for c in calls)


def test_model_loading_pins_revision_and_forces_cpu_float32(tmp_path,monkeypatch):
    import huggingface_hub
    import transformers
    import torch
    calls = []
    (tmp_path/'config.json').write_text('{}',encoding='utf-8')
    def snapshot(**kwargs):
        calls.append(kwargs)
        return str(tmp_path)
    model = SimpleNamespace(config=SimpleNamespace(num_labels=1,max_position_embeddings=512),
        to=lambda **kwargs:calls.append(kwargs),eval=lambda:calls.append('eval'))
    monkeypatch.setattr(huggingface_hub,'snapshot_download',snapshot)
    monkeypatch.setattr(transformers.AutoTokenizer,'from_pretrained',lambda *a,**k:SyntheticTokenizer())
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification,'from_pretrained',lambda *a,**k:model)
    monkeypatch.setattr(torch,'set_num_threads',lambda n:calls.append(('threads',n)))
    monkeypatch.setattr(torch,'get_num_threads',lambda:4)
    config = load_reranker_config(ROOT/'config/reranker_minilm_v1.json')
    instance = MiniLMReranker(config,cache_dir=tmp_path)
    download = next(c for c in calls if isinstance(c,dict) and 'repo_id' in c)
    assert download['repo_id'] == config.model_id and download['revision'] == config.model_revision
    assert ('threads',4) in calls and dict(device='cpu',dtype=torch.float32) in calls and 'eval' in calls
    assert instance.metadata['cpu_threads'] == 4 and set(instance.metadata['files']) == {'config.json'}
    model.config.num_labels = 2
    with pytest.raises(ValueError,match='contract'):
        MiniLMReranker(config,cache_dir=tmp_path)


def group(ids, match='any'):
    return dict(required=True,match=match,chunk_ids=ids)


def case(cid, groups):
    return dict(id=cid,category='literature_only',gold={'literature_evidence_groups':groups})


def test_empty_bm25_union_and_rrf_tie_order_are_supported():
    dense = [dict(chunk_id=cid,score=1.) for cid in ('a','b')]
    assert fused_candidate_ids(dense,[]) == ['a','b']
    assert set(fused_candidate_ids(dense,[dict(chunk_id='c',score=1.)])) == {'a','b','c'}


def test_missing_group_diagnosis_is_exclusive_and_respects_any_all():
    cases = [case('one',[group(['t']),group(['a','alt']),group(['b1','b2'],'all'),group(['c1','c2'],'all')]),
             case('two',[group(['none'])])]
    top = dict(one=['t','b1'],two=['t'])
    a = dict(one=['t','b1','a'],two=['t'])
    b = dict(one=['t','b1','a','b2','c1'],two=['t'])
    result = diagnose_missing_groups(cases,top,a,b)
    assert result['missing_group_count'] == 4 and result['missing_case_count'] == 2
    assert result['categories'] == dict(a=dict(group_count=1,case_count=1),b=dict(group_count=1,case_count=1),c=dict(group_count=2,case_count=2))
    assert result['b_inclusive_group_count'] == 2
    assert [r['category'] for r in result['per_case']['one']] == ['a','b','c']
    bound = pool_evidence_upper_bound(cases,b)
    assert bound['per_case']['one']['evidence_group_recall'] == .75
    assert bound['macro']['complete_evidence'] == 0
    with pytest.raises(ValueError,match='Nested'):
        diagnose_missing_groups(cases,top,a,dict(one=['t'],two=['t']))


def metrics(values):
    return {d:dict(per_case={f'c{i}':{'complete_evidence@10':ce,'evidence_group_recall@10':egr,'reciprocal_rank':egr}
                 for i,(ce,egr) in enumerate(values)},macro={'complete_evidence@10':sum(v[0] for v in values)/len(values),
                     'evidence_group_recall@10':sum(v[1] for v in values)/len(values),'mrr':sum(v[1] for v in values)/len(values)})
            for d in ('dev','dev-ko')}


def timings(size,ms):
    return {d+':c'+str(i):ms for d in ('dev','dev-ko') for i in range(size)}


def test_selection_applies_performance_and_latency_gates_and_exact_3s_boundary():
    base = metrics([(0,.2),(1,1.)])
    better = metrics([(1,.8),(1,1.)])
    conditions = dict(A=better,B=better)
    result = select_reranker(base,conditions,dict(A=timings(2,3000),B=timings(2,3000.01)))
    assert result['selected_condition']=='A' and result['eligible']==['A']
    assert not result['decisions']['B']['checks']['mean_cpu_latency_within_3s']
    assert select_reranker(base,conditions,dict(A=timings(2,3001),B=timings(2,3001)))['selected_condition']=='step3b'


def test_selection_ce_then_egr_then_a_and_no_improvement_rejection():
    base = metrics([(0,.2),(0,.2)])
    latencies = dict(A=timings(2,1),B=timings(2,1))
    a,b = metrics([(1,.8),(0,.8)]),metrics([(1,.6),(1,.6)])
    assert select_reranker(base,dict(A=a,B=b),latencies)['selected_condition']=='B'
    a,b = metrics([(1,.8),(1,.8)]),metrics([(1,.9),(1,.9)])
    assert select_reranker(base,dict(A=a,B=b),latencies)['selected_condition']=='B'
    assert select_reranker(base,dict(A=b,B=b),latencies)['selected_condition']=='A'
    assert select_reranker(base,dict(A=base,B=base),latencies)['selected_condition']=='step3b'
    with pytest.raises(ValueError,match='every evaluated'):
        select_reranker(base,dict(A=b,B=b),dict(A={'dev:c0':1},B=timings(2,1)))


def test_ce_regression_and_balanced_egr_changes_cannot_pass():
    base = metrics([(1,.5),(0,.5)])
    regression = metrics([(0,.8),(0,.8)])
    balanced = metrics([(1,.8),(0,.2)])
    result = select_reranker(base,dict(A=regression,B=balanced),dict(A=timings(2,1),B=timings(2,1)))
    assert result['selected_condition']=='step3b'
    assert not result['decisions']['A']['checks']['combined_complete_non_decreasing']
    assert not result['decisions']['B']['checks']['combined_more_improved_than_worsened']


def test_latency_summary_nearest_rank_p95_and_invalid_measurements():
    assert latency_summary([1,2,3,4]) == dict(case_count=4,mean_ms=2.5,median_ms=2.5,p95_ms=4,max_ms=4)
    for values in ([],[-1],[float('inf')]):
        with pytest.raises(ValueError):
            latency_summary(values)


def load_runner():
    spec = importlib.util.spec_from_file_location('reranker_runner_test',ROOT/'scripts/run_retrieval_reranker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_synthetic_experiment_freezes_both_rankings_before_gold_and_refuses_overwrite(tmp_path,monkeypatch):
    runner = load_runner()
    (tmp_path/'config').mkdir()
    (tmp_path/'config/reranker_minilm_v1.json').write_bytes((ROOT/'config/reranker_minilm_v1.json').read_bytes())
    source_paths = ('config/reranker_minilm_v1.json',)
    monkeypatch.setattr(runner,'SOURCE_FILES',source_paths)
    rows = [dict(case_id='synthetic',step3b=('unchanged query','unchanged query'))]
    sources = {n:{d:dict(dense={'synthetic':[dict(chunk_id='a',score=1.)]},bm25={'synthetic':[]})
                  for d in ('dev','dev-ko')} for n in ('A','B')}
    rankings = {n:{d:{'synthetic':[dict(chunk_id='a',score=1.)]} for d in ('dev','dev-ko')} for n in ('rrf_A','rrf_B')}
    cases = [case('synthetic',[group(['a'])])]
    expected = {n:{d:evaluate_rankings(cases,hits) for d,hits in ds.items()} for n,ds in rankings.items()}
    prepared = dict(queries={d:rows for d in ('dev','dev-ko')},sources=sources,rankings=rankings,
        assets=SimpleNamespace(chunks_by_id={'a':parent('a')}),dev=tmp_path/'synthetic_gold.json',
        reference_metrics=expected,input_hashes={'synthetic':'hash'})
    monkeypatch.setattr(runner,'load_inputs',lambda root:prepared)
    output = tmp_path/'reports/experiments/synthetic_v1'
    def gold_read(path):
        assert path == prepared['dev']
        assert all((output/n/'rankings.json').exists() and (output/n/'scores_latency_tokens.json').exists() for n in ('A','B'))
        assert json.loads((output/'protocol.json').read_text())['all_rankings_before_gold']
        return cases
    monkeypatch.setattr(runner,'read_gold_cases',gold_read)
    class FakeReranker:
        def __init__(self,config,**kw):
            self.metadata = {'synthetic':True}
        def warm_up(self):
            pass
        def rerank(self,query,parents):
            assert query=='unchanged query' and parents==[parent('a')]
            return rerank_parents(query,parents,tokenizer=SyntheticTokenizer(),score_pairs=lambda _: [1.])
    result = runner.run_experiment(tmp_path,output,reranker_factory=FakeReranker)
    assert result['selected_condition']=='step3b' and result['held_out_used'] is False
    assert verify_frozen_folder(output)['status']=='frozen'
    with pytest.raises(ValueError,match='new versioned'):
        runner.run_experiment(tmp_path,output,reranker_factory=FakeReranker)


def test_same_pool_rrf_reproduction_rejects_wrong_order():
    runner = load_runner()
    dense = [dict(chunk_id='a',score=1.),dict(chunk_id='b',score=.5)]
    stored = [dict(chunk_id='a',dense_rank=1,bm25_rank=None,rrf_score=1/61),
              dict(chunk_id='b',dense_rank=2,bm25_rank=None,rrf_score=1/62)]
    runner.verify_rrf(dense,[],stored)
    with pytest.raises(ValueError,match='reproduction'):
        runner.verify_rrf(dense,[],stored[::-1])
