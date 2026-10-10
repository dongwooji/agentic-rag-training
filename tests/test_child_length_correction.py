"""Public synthetic tests for quota correction, length controls and selection."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from statistics import mean
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from src.retrieval.child_quota import ParentQuotaChildStore
from src.retrieval.child import ParentChildStore
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.retrieval.postgres import SearchHit, SearchResponse
from src.retrieval.runtime_config import load_retrieval_config, RetrievalConfig
from src.evaluation.dense_length import select_condition, truncation_statistics, verify_frozen_folder
from src.evaluation.literature_query_freeze import sha256
from test_retrieval_config import synthetic_assets
from test_child_depth import WordTokenizer

ROOT = Path(__file__).resolve().parents[1]


def quota_store(parents):
    rows = [dict(child_id=f'child{i:04d}',parent_id=p) for i,p in enumerate(parents)]
    hits = [SearchHit(row['child_id'],1-i*.001) for i,row in enumerate(rows)]
    raw = Mock(search_exact_cosine=Mock(side_effect=lambda q,**kw: SearchResponse(hits[:kw['top_k']],.1)))
    return raw, ParentQuotaChildStore(raw,rows,set(parents))


def test_quota_stops_at_tenth_unique_parent_and_discards_extra_parent_candidates():
    raw,store = quota_store(['a']*5 + [f'p{i}' for i in range(30)])
    result = store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=10)
    assert [h.chunk_id for h in result.hits] == ['a']+[f'p{i}' for i in range(9)]
    mapping = store.mappings[0]
    assert mapping['actual_parent_count'] == 10
    assert mapping['consumed_child_count'] == 14 and mapping['retrieved_child_count'] == 35
    assert mapping['selected'][-1]['best_child_rank'] == 14
    assert mapping['selected'][-1]['parent_rank'] == 10
    assert mapping['quota_filled']
    assert raw.search_exact_cosine.call_count == 1


def test_quota_expands_beyond_fifty_when_duplicates_hide_new_parents():
    raw,store = quota_store(['a']*55+['b','c','d','e','f'])
    result = store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=3)
    assert [h.chunk_id for h in result.hits] == ['a','b','c']
    assert [c.kwargs['top_k'] for c in raw.search_exact_cosine.call_args_list] == [50,60]
    assert store.mappings[0]['consumed_child_count'] == 57
    assert result.database_search_ms == pytest.approx(.2)


def test_quota_underfill_only_after_all_children_exhausted():
    raw,store = quota_store(['a']*110+['b']*10)
    result = store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=10)
    assert [h.chunk_id for h in result.hits] == ['a','b']
    assert store.mappings[0]['search_depths'] == [50,100,120]
    assert store.mappings[0]['exhausted'] and not store.mappings[0]['quota_filled']


@pytest.mark.parametrize('failure', ['unknown','duplicate','prefix'])
def test_quota_rejects_corrupt_child_search_results(failure):
    raw,store = quota_store(['a']*55+['b','c'])
    if failure == 'unknown':
        raw.search_exact_cosine.side_effect = lambda *a,**k: SearchResponse([SearchHit('missing',1.)],0.)
    elif failure == 'duplicate':
        raw.search_exact_cosine.side_effect = lambda *a,**k: SearchResponse([SearchHit('child0000',1.)]*2,0.)
    else:
        original = raw.search_exact_cosine.side_effect
        raw.search_exact_cosine.side_effect = lambda *a,**k: (
            original(*a,**k) if k['top_k']==50 else SearchResponse([SearchHit('child0055',1.)],0.))
    with pytest.raises(ValueError):
        store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=10)


def test_quota_batch_and_target_must_be_positive():
    raw,store = quota_store(['a'])
    with pytest.raises(ValueError,match='quota'):
        store.search_exact_cosine([1.],embedding_run_id='synthetic',top_k=0)
    with pytest.raises(ValueError,match='batch'):
        ParentQuotaChildStore(raw,[dict(child_id='a',parent_id='a')],{'a'},child_depth=0)


@pytest.fixture
def fake_encoder_model(monkeypatch):
    class FakeModel:
        max_seq_length = 128
        def __getitem__(self, index):
            return SimpleNamespace(auto_model=SimpleNamespace(config=SimpleNamespace(max_position_embeddings=512)))
        def get_embedding_dimension(self):
            return 384
        def encode(self, texts, **kwargs):
            out = np.zeros((len(texts),384),dtype=np.float32)
            out[:,0] = 1
            return out
    model = FakeModel()
    download = Mock(return_value='/synthetic/model')
    monkeypatch.setitem(sys.modules,'huggingface_hub',SimpleNamespace(snapshot_download=download))
    monkeypatch.setitem(sys.modules,'sentence_transformers',SimpleNamespace(SentenceTransformer=lambda *a,**k:model))
    return model,download


@pytest.mark.parametrize('limit', [None,128,256,512])
def test_encoder_honors_declared_limit_and_default_is_unchanged(fake_encoder_model,limit):
    model,download = fake_encoder_model
    encoder = MiniLMEncoder(max_seq_length=limit)
    assert model.max_seq_length == encoder.metadata.max_sequence_length == (limit or 128)
    assert encoder.encode(['synthetic input']).shape == (1,384)
    assert download.call_args.kwargs['revision'] == encoder.metadata.model_revision


@pytest.mark.parametrize('limit', [255,1024,True])
def test_encoder_rejects_undeclared_length(fake_encoder_model,limit):
    with pytest.raises(ValueError,match='declared'):
        MiniLMEncoder(max_seq_length=limit)


def test_encoder_never_exceeds_model_position_capacity(fake_encoder_model):
    model,_ = fake_encoder_model
    model.__class__.__getitem__ = lambda self,i: SimpleNamespace(auto_model=SimpleNamespace(
        config=SimpleNamespace(max_position_embeddings=128)))
    with pytest.raises(ValueError,match='capacity'):
        MiniLMEncoder(max_seq_length=256)


def test_truncation_audit_includes_exact_boundary_and_weighted_retention():
    stats = truncation_statistics([128,256,512,600],256)
    assert stats['truncated_parent_count'] == 2 and stats['truncated_parent_ratio'] == .5
    assert stats['token_retention_ratio'] == pytest.approx((128+256*3)/(128+256+512+600))
    assert truncation_statistics([128,256,512,600],512)['truncated_parent_count'] == 1
    with pytest.raises(ValueError):
        truncation_statistics([],256)


def make_metrics(values):
    result = {}
    for dataset in ('dev','dev-ko'):
        per_case = {f'synthetic{i}':dict(reciprocal_rank=egr,evidence_group_recall=egr,
            **{'evidence_group_recall@10':egr,'complete_evidence@10':ce,'chunk_recall@10':egr})
            for i,(ce,egr) in enumerate(values)}
        for v in per_case.values():
            v.pop('evidence_group_recall')
        macro = {('mrr' if k=='reciprocal_rank' else k):mean(v[k] for v in per_case.values())
                 for k in next(iter(per_case.values()))}
        result[dataset] = dict(per_case=per_case,macro=macro)
    return result


def test_three_conditions_independent_and_no_pass_retains_step3b():
    base = make_metrics([(1,1),(1,1)])
    candidate = make_metrics([(1,1),(0,.5)])
    result = select_condition(base,{name:candidate for name in ('child_corrected','parent256','parent512')})
    assert result['selected_condition'] == 'step3b' and result['eligible'] == []
    assert all(d['reference']=='step3b' for d in result['decisions'].values())


def test_selection_prefers_complete_then_egr_then_user_simplicity():
    base = make_metrics([(0,0),(0,0)])
    lower_complete = make_metrics([(1,1),(0,.75)])
    higher_complete = make_metrics([(1,1),(1,1)])
    result = select_condition(base,dict(child_corrected=higher_complete,parent256=lower_complete,parent512=lower_complete))
    assert result['selected_condition'] == 'child_corrected'
    result = select_condition(base,dict(child_corrected=lower_complete,parent256=make_metrics([(1,1),(0,.5)]),parent512=make_metrics([(1,1),(0,.25)])))
    assert result['selected_condition'] == 'child_corrected'
    result = select_condition(base,{name:higher_complete for name in ('child_corrected','parent256','parent512')})
    assert result['selected_condition'] == 'parent512'
    assert result['eligible'] == ['child_corrected','parent256','parent512']


def test_historical_serialization_and_experimental_config_differences():
    old_paths = [ROOT/'config'/name for name in ('retrieval_h0_v1.json','retrieval_h1_v1.json',
        'retrieval_step3b_translation_v1.json','retrieval_step4_child_v1.json')]
    for path in old_paths:
        assert load_retrieval_config(path).model_dump() == json.loads(path.read_text(encoding='utf-8'))
    base = load_retrieval_config(old_paths[2]).model_dump()
    for limit in (256,512):
        cfg = load_retrieval_config(ROOT/f'config/retrieval_parent_length{limit}_v1.json').model_dump()
        assert cfg['dense']['max_sequence_length'] == limit
        cfg.update(schema_version=base['schema_version'],config_version=base['config_version'],embedding_run_id=base['embedding_run_id'])
        cfg['dense']['max_sequence_length'] = 128
        assert cfg == base


@pytest.mark.parametrize('field', ['length','depth','query','policy'])
def test_new_config_blocks_undeclared_changes(field):
    cfg = load_retrieval_config(ROOT/'config/retrieval_parent_length256_v1.json').model_dump()
    if field=='length': cfg['dense']['max_sequence_length']=384
    elif field=='depth': cfg['dense']['source_depth']=20
    elif field=='query': cfg['query_mode']='generated_ko'
    else: cfg['bm25']['score_policy']='retain_zero'
    with pytest.raises(ValueError):
        RetrievalConfig.model_validate(cfg)


def test_quota_adapter_required_and_rrf_handles_empty_bm25(synthetic_assets):
    root,cfg = synthetic_assets
    data = cfg.model_dump()
    data.update(schema_version=3,setting='H1',query_mode='translated_both')
    data['bm25']['score_policy'] = 'positive_only'
    data['dense'].update(unit='child',child=dict(representation_version='retrieval_child_v1',max_tokens=110,
        search_depth=50,parent_selection='unique_parent_quota'))
    config = RetrievalConfig.model_validate(data)
    assets = load_frozen_literature_assets(root,config=config)
    parent_ids = [c['chunk_id'] for c in assets.chunks]
    rows = [dict(child_id=f'child{i}',parent_id=p) for i,p in enumerate(parent_ids)]
    hits = [SearchHit(c['child_id'],1-i*.01) for i,c in enumerate(rows)]
    raw = Mock(search_exact_cosine=Mock(return_value=SearchResponse(hits,0.)))
    encoder = SimpleNamespace(metadata=None,encode=Mock(return_value=[[1.]*384]))
    with pytest.raises(RuntimeError,match='parent-quota'):
        FrozenHybridRetriever(assets=assets,encoder=encoder,vector_store=ParentChildStore(raw,rows,set(parent_ids)))
    store = ParentQuotaChildStore(raw,rows,set(parent_ids))
    retriever = FrozenHybridRetriever(assets=assets,encoder=encoder,vector_store=store)
    result = retriever.search('존재하지않는단어')
    assert len(result.hits) == len(store.searches[0]) == 10
    assert all(h.bm25_rank is None and h.bm25_rrf_contribution==0 for h in result.hits)
    assert result.hits[0].rrf_score == pytest.approx(1/61)


def test_new_runner_approval_gate_precedes_backend(monkeypatch):
    spec = importlib.util.spec_from_file_location('child_length_gate',ROOT/'scripts/run_retrieval_child_length.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    forbidden = Mock(side_effect=AssertionError('Backend before approval'))
    monkeypatch.setattr(runner,'load_frozen_literature_assets',forbidden)
    monkeypatch.setattr(runner,'MiniLMEncoder',forbidden)
    monkeypatch.setattr(runner,'load_reviewed_conditions',Mock(side_effect=ValueError('mapping approval missing')))
    monkeypatch.setattr(sys,'argv',['runner','--output',str(ROOT/'reports/experiments/synthetic-child-length-gate')])
    with pytest.raises(ValueError,match='approval'):
        runner.main()
    forbidden.assert_not_called()


def test_frozen_embedding_manifest_detects_tampering(tmp_path):
    path = tmp_path/'embeddings.npy'
    path.write_bytes(b'synthetic')
    (tmp_path/'manifest.json').write_text(json.dumps(dict(status='frozen',artifacts=[dict(path=path.name,sha256=sha256(path))])),encoding='utf-8')
    verify_frozen_folder(tmp_path)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):
        verify_frozen_folder(tmp_path)


def test_parent_embedding_preparation_freezes_complete_output_without_questions(tmp_path,monkeypatch,fake_encoder_model,capsys):
    spec = importlib.util.spec_from_file_location('synthetic_length_builder',ROOT/'scripts/build_retrieval_parent_lengths.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    config = load_retrieval_config(ROOT/'config/retrieval_parent_length256_v1.json')
    model,_ = fake_encoder_model
    model.tokenizer = WordTokenizer()
    encoder = MiniLMEncoder(max_seq_length=256)
    files = ('config/retrieval_parent_length256_v1.json','src/retrieval/dense.py','src/retrieval/runtime_config.py',
        'src/retrieval/hybrid.py','src/retrieval/child.py','src/evaluation/dense_length.py',
        'scripts/build_retrieval_parent_lengths.py','docs/decisions/RETRIEVAL_V2_CHILD_CORRECTION_LENGTH.md')
    for name in files:
        path = tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('synthetic source',encoding='utf-8')
    monkeypatch.setattr(builder,'ROOT',tmp_path)
    monkeypatch.setattr(builder,'load_retrieval_config',Mock(return_value=config))
    monkeypatch.setattr(builder,'load_frozen_literature_assets',Mock(return_value=SimpleNamespace(
        chunks=[dict(chunk_id='synthetic-parent',text='One complete sentence.')],corpus_chunks_sha256='a'*64)))
    monkeypatch.setattr(builder,'MiniLMEncoder',Mock(return_value=encoder))
    folder = tmp_path/'data/literature/representations/synthetic-v1'
    monkeypatch.setattr(sys,'argv',['builder','--length','256','--output',str(folder)])
    builder.main()
    manifest = verify_frozen_folder(folder)
    assert manifest['gold_used'] is False and manifest['retrieval_executed'] is False
    assert np.load(folder/'embeddings.npy',allow_pickle=False).shape == (1,384)
    completion = json.loads(capsys.readouterr().out)
    assert completion['manifest_sha256'] == sha256(folder/'manifest.json')
    assert completion['stats']['truncated_parent_count'] == 0
