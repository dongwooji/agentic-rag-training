"""Public synthetic final protocol tests: no private data, DB, model or API."""
from copy import deepcopy
from dataclasses import make_dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from test_heldout_v2 import example
from src.evaluation import retrieval_final as final
from src.evaluation.literature_query_freeze import sha256, write_new_json
from src.retrieval.hybrid import load_frozen_literature_assets
from src.retrieval.literature_query import QueryProviderResult
from src.retrieval.runtime_config import DEFAULT_RETRIEVAL_CONFIG, RetrievalConfig

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_new_json(path, value)


@pytest.fixture
def setup_final(tmp_path, example, monkeypatch):
    root = tmp_path
    for p in final.EXECUTION_SOURCES:
        target = root/p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/p).read_bytes())
    for base, _ in final.CONDITIONS.values():
        write(root/'config'/(base+'.json'), json.loads((ROOT/'config'/(base+'.json')).read_text(encoding='utf-8')))
    version = 'synthetic_corpus'
    chunks = []
    for i in range(12):
        c = deepcopy(example['chunks'][0])
        c.update(chunk_id=f'FAKE-CHUNK-{i:02}', text=example['paragraphs'][0]['text'] if i == 0 else 'other synthetic words',
                 corpus_version=version)
        if i != 0:
            c['section_index'] = 2
        chunks.append(c)
    case_rows, expected, groups = [], [], []
    for i in range(24):
        case = deepcopy(example['cases']['cases'][0])
        case.update(id=f'FAKE-CASE-{i:02}', question='Synthetic question about a narrow comparison?')
        gold_case = deepcopy(example['gold']['cases'][0])
        gold_case['case_id'] = case['id']
        if i >= 18:
            case.update(task_type='boundary', category='boundary', scope_class='OUT_OF_SCOPE',
                        question_family_id='FAKE-BOUNDARY', requires_literature=False,
                        retrieval_metric_eligible=False, expected_behavior='abstain')
            gold_case['expected_behavior'] = 'abstain'
        else:
            group = deepcopy(example['gold']['evidence_groups'][0])
            group.update(evidence_group_id=f'FAKE-E{i}', case_id=case['id'],
                         parent_mapping=dict(match='any', chunk_ids=['FAKE-CHUNK-00']))
            groups.append(group)
        case_rows.append(case)
        expected.append(gold_case)
    cases = dict(dataset_version='synthetic_eval', cases=case_rows)
    gold = dict(dataset_version='synthetic_eval', cases=expected, evidence_groups=groups)
    scope = deepcopy(example['scope'])
    scope['questions'].append(dict(question_id='FAKE-BOUNDARY', scope='OUT_OF_SCOPE', human_approved_scope=True,
                                   NOT_DECIDED=False, approved_scope_boundary=case_rows[0]['scope_boundary_reference']))
    build = root/'data/literature/v2/build_freeze_v1'
    output_files = {}
    for name, rows in dict(chunks=chunks, paragraphs=example['paragraphs'], papers=example['papers']).items():
        path = build/(name+'.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')
        output_files[name] = final.reference(root, path)
    write(build/'build_manifest.json', dict(status='FROZEN_BUILD', corpus_version=version, chunk_count=12, output_files=output_files))
    write(build/'freeze_integrity.json', {})
    write(root/'data/literature/v2/manifests/selection_freeze_v1/supported_scope.json', scope)
    folder = root/'data/evaluation'/final.GOLD_VERSION
    write(folder/'evaluation_cases.json', cases)
    write(folder/'gold_evidence.json', gold)
    monkeypatch.setattr(final, 'GOLD_SHA256', sha256(folder/'gold_evidence.json'))
    return root, root/'reports/experiments/config_v1'


class Provider:
    def __init__(self, payload):
        self.inputs = []
        self.payload = payload

    def invoke(self, text):
        self.inputs.append(text)
        return QueryProviderResult(self.payload, dict(api_requests=1, usage_available=True,
                                                    input_tokens=7, output_tokens=3, latency_ms=1))


def prepare(root, folder):
    final.freeze_configuration(root, folder)
    gen, en = folder.parent/'generation_v1', folder.parent/'translation_v1'
    gp = Provider(dict(has_personal_context=False, literature_query='ignored output'))
    tp = Provider(dict(literature_query='Synthetic question about a narrow comparison?'))
    final.freeze_queries(root, folder, gen, 'generation', gp)
    final.freeze_queries(root, folder, en, 'translation', tp, generation=gen)
    return gen, en, gp, tp


def test_final_config_is_write_once_and_historical_default_unchanged(setup_final):
    root, folder = setup_final
    before = DEFAULT_RETRIEVAL_CONFIG.model_dump(mode='json')
    final.freeze_configuration(root, folder)
    protocol, configs, _ = final.verified_configuration(root, folder)
    assert protocol['gold_opened_only_after_all_rankings'] is True
    assert configs['final_v2'].query_mode == 'translated_both'
    assert configs['H0'].bm25.score_policy == 'retain_zero'
    assert all(cfg.dense.unit == 'parent' and cfg.dense.max_sequence_length == 128 for cfg in configs.values())
    assert DEFAULT_RETRIEVAL_CONFIG.model_dump(mode='json') == before
    assert DEFAULT_RETRIEVAL_CONFIG.corpus_paths is None
    with pytest.raises(FileExistsError):
        final.freeze_configuration(root, folder)


@pytest.mark.parametrize('path', ['../secret', 'data/literature/../../secret', 'C:/data/literature/file',
                                'data/literature/../file', 'data/literature\\file', 'data/evaluation/input'])
def test_corpus_paths_reject_escape_and_non_corpus(setup_final, path):
    root, folder = setup_final
    data = final.freeze_configuration(root, folder)['definitions']['final_v2']
    data['corpus_paths']['chunks'] = path
    with pytest.raises(ValidationError):
        RetrievalConfig.model_validate(data)


@pytest.mark.parametrize('target', ['chunks.jsonl','build_manifest.json'])
def test_configured_corpus_validates_hashes(setup_final, target):
    root, folder = setup_final
    final.freeze_configuration(root, folder)
    _, configs, _ = final.verified_configuration(root, folder)
    assets = load_frozen_literature_assets(root, config=configs['final_v2'])
    assert len(assets.chunks) == 12
    assert assets.bm25_metadata['document_count'] == 12
    from src.tools.literature import LiteratureTool
    provenance = LiteratureTool(assets=assets, retriever=object()).provenance
    assert provenance['retrieval_version'] == configs['final_v2'].retrieval_version
    assert provenance['bm25']['tokenizer_version'] == configs['final_v2'].bm25.tokenizer_version
    with (root/'data/literature/v2/build_freeze_v1'/target).open('a') as f:
        f.write(' ')
    with pytest.raises(RuntimeError, match='hash changed'):
        load_frozen_literature_assets(root, config=configs['final_v2'])


def test_queries_text_only_one_call_no_human_review_and_fixed_fallback(setup_final):
    root, folder = setup_final
    gen, en, gp, tp = prepare(root, folder)
    questions, _, _ = final.frozen_query_conditions(root, folder, gen, en)
    assert len(gp.inputs) == len(tp.inputs) == 24
    assert all(isinstance(q,str) and 'FAKE-CASE' not in q for q in gp.inputs + tp.inputs)
    assert all(q['H0'] == q['H1'] == q['step2'] == q['final_v2'] for q in questions)
    assert final.verify_bundle(gen)['query_review'] == 'no_human_review'
    with pytest.raises(FileExistsError):
        final.freeze_queries(root, folder, gen, 'generation', gp)
    assert len(gp.inputs) == 24


def test_translation_uses_actual_generation_fallback(setup_final):
    root, folder = setup_final
    final.freeze_configuration(root, folder)
    gp = Provider(dict(has_personal_context=True, literature_query='2020-03-01 synthetic condition'))
    tp = Provider(dict(literature_query=''))
    gen, en = folder.parent/'generation', folder.parent/'translation'
    g = final.freeze_queries(root,folder,gen,'generation',gp)
    t = final.freeze_queries(root,folder,en,'translation',tp,generation=gen)
    assert all(row['result']['fallback'] for row in g + t)
    assert tp.inputs == [r['result']['actual_query'] for r in g]
    assert all(r['result']['actual_query'] == r['step2']['actual_query'] for r in t)


def test_protocol_and_query_tampering_blocks_downstream(setup_final):
    root, folder = setup_final
    gen, en, _, _ = prepare(root, folder)
    with (en/'queries.jsonl').open('a') as handle:
        handle.write(' ')
    with pytest.raises(ValueError, match='reference changed'):
        final.frozen_query_conditions(root, folder, gen, en)


def test_complete_offline_flow_saves_all_ranks_before_gold_and_reports_boundaries(setup_final, monkeypatch):
    root, folder = setup_final
    gen, en, _, _ = prepare(root, folder)
    ranks, output = folder.parent/'rankings', folder.parent/'evaluation'
    actual_read = final.read_json
    def guarded_read(path):
        if path.name == 'gold_evidence.json':
            assert (ranks/'manifest.json').is_file()
            assert all((ranks/(c+'_rankings.json')).is_file() for c in final.CONDITIONS)
        return actual_read(path)
    monkeypatch.setattr(final,'read_json',guarded_read)
    Metadata = make_dataclass('Metadata', ['model_id','model_revision','embedding_dimension','max_sequence_length',
                                         'normalize_embeddings','precision','batch_size'])
    cfg = final.verified_configuration(root, folder)[1]['final_v2'].dense
    meta = Metadata(**{k:getattr(cfg,k) for k in Metadata.__dataclass_fields__})
    encoder = SimpleNamespace(metadata=meta, encode=lambda texts,**kwargs:np.tile(np.eye(384)[0], (len(texts),1)))
    final.rank_final(root,folder,gen,en,ranks,encoder)
    result = final.evaluate_final(root,folder,ranks,output)
    assert result['retrieval_metric_case_count'] == 18
    assert result['boundary']['case_count'] == 6
    assert result['abstention']['measured'] is False
    assert set(result['metrics']) == set(final.CONDITIONS)
    assert all(m['macro']['complete_evidence@10'] == 1 for m in result['metrics'].values())
    assert result['grouped_metrics']['final_v2']['scope_class']['OUT_OF_SCOPE']['metrics']['case_count'] == 0
    with pytest.raises(FileExistsError):
        final.rank_final(root,folder,gen,en,ranks,encoder)
    with pytest.raises(FileExistsError):
        final.evaluate_final(root,folder,ranks,output)


def test_gold_evaluation_requires_all_saved_conditions(setup_final):
    root,folder = setup_final
    prepare(root,folder)
    ranks = folder.parent/'incomplete_rankings'
    ranks.mkdir()
    final.freeze_bundle(ranks, stage='rankings', configuration_sha256=sha256(folder/'manifest.json'))
    with pytest.raises(FileNotFoundError):
        final.evaluate_final(root,folder,ranks,folder.parent/'evaluation')


def test_language_grouping_uses_only_existing_classification_fields():
    cases = [dict(id='invented-a', language_condition='invented-language'),
             dict(id='invented-b', language_condition='invented-language')]
    result = final.grouped_metrics(cases, [], {}, ['task_type'])
    assert result['language_condition']['invented-language']['total_case_count'] == 2
    assert result['language_condition']['invented-language']['retrieval_eligible_count'] == 0
    assert 'task_type' not in result


@pytest.mark.parametrize('status', ['FROZEN_BUILD', 'BUILD_FROZEN', 'draft'])
def test_existing_corpus_freeze_spellings_and_unfrozen_rejection(setup_final, status):
    root,folder = setup_final
    path = root/'data/literature/v2/build_freeze_v1/build_manifest.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['status'] = status
    path.write_text(json.dumps(data),encoding='utf-8')
    if status == 'draft':
        with pytest.raises(ValueError, match='not frozen'):
            final.freeze_configuration(root,folder)
        assert not folder.exists()
    else:
        final.freeze_configuration(root,folder)
        configs = final.verified_configuration(root,folder)[1]
        assert len(load_frozen_literature_assets(root,config=configs['final_v2']).chunks) == 12
