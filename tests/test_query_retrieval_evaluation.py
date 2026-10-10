"""Synthetic adoption decisions and offline revalidation, no real providers."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.evaluation.query_retrieval import adoption_decision, cumulative_decisions
from src.evaluation.literature_query_freeze import (
    write_new_json, sha256, read_frozen, revalidate_translation_freeze,
)


def metrics(value):
    fields = dict(evidence_group_recall__10=value, complete_evidence__10=value, reciprocal_rank=value)
    fields = {key.replace('__', '@'): v for key, v in fields.items()}
    macro = {('mrr' if k == 'reciprocal_rank' else k):v for k,v in fields.items()}
    return {dataset: dict(macro=macro.copy(), per_case={'synthetic':fields.copy()})
            for dataset in ('dev', 'dev-ko')}


def test_rejection_keeps_last_adopted_reference():
    result = cumulative_decisions(dict(H1=metrics(.5), step2=metrics(.2),
                                       step3a=metrics(.6), step3b=metrics(.4)))
    assert result['decisions']['step2']['reference'] == 'H1'
    assert not result['decisions']['step2']['adopted']
    assert result['decisions']['step3a']['reference'] == 'H1'
    assert result['decisions']['step3a']['adopted']
    assert result['decisions']['step3b']['reference'] == 'step3a'
    assert result['selected_condition'] == 'step3a'


def test_unchanged_is_rejected_and_korean_regression_is_rejected():
    reference = metrics(.5)
    assert not adoption_decision(reference, metrics(.5))['adopted']
    candidate = metrics(.6)
    candidate['dev-ko'] = metrics(.3)['dev-ko']
    assert not adoption_decision(reference, candidate)['checks']['dev_ko_non_decreasing']


def test_complete_regression_blocks_even_when_egr_improves():
    candidate = metrics(.6)
    candidate['dev']['macro']['complete_evidence@10'] = .4
    assert not adoption_decision(metrics(.5), candidate)['adopted']


def test_adoption_uses_real_evaluator_macro_contract():
    from src.evaluation.retrieval_preparation import evaluate_rankings
    cases = [dict(id='synthetic', gold={'literature_evidence_groups':[
        dict(required=True, match='any', chunk_ids=['evidence'])]})]
    old = evaluate_rankings(cases, {'synthetic': [{'chunk_id':'unrelated'}]})
    new = evaluate_rankings(cases, {'synthetic': [{'chunk_id':'evidence'}]})
    assert 'mrr' in new['macro'] and 'reciprocal_rank' in new['per_case']['synthetic']
    assert adoption_decision({d:old for d in ('dev','dev-ko')},
                             {d:new for d in ('dev','dev-ko')})['adopted']


def test_offline_revalidation_preserves_outputs_and_provenance(tmp_path):
    original = tmp_path / 'original'
    original.mkdir()
    row = dict(case_id='synthetic', display_task_type='display-only', original_question='source',
        step2={'actual_query':'2033-04-17 calibration protocol', 'has_personal_context':True,
               'generated_query':'source', 'fallback':False, 'fallback_reasons':[]},
        result=dict(input_query='2033-04-17 calibration protocol',
                    generated_query='Calibration protocol for 2033-04-17',
                    actual_query='2033-04-17 calibration protocol', query_source='step2',
                    validation_status='failed', fallback=True, fallback_reasons=['unit_preservation'],
                    provenance={'response_id':'synthetic-response', 'api_requests':1}))
    for dataset in ('dev', 'dev-ko'):
        (original / (dataset + '.jsonl')).write_text(json.dumps(row)+'\n', encoding='utf-8')
    write_new_json(original / 'manifest.json', dict(stage='translation', status='frozen',
        input_manifest_sha256='a'*64, artifacts=[dict(path=p.name, sha256=sha256(p))
                                               for p in sorted(original.iterdir())]))
    prior_hash = sha256(original / 'manifest.json')
    output = tmp_path / 'v2'
    validator = Path(__file__).resolve().parents[1] / 'src/retrieval/literature_query.py'
    manifest = revalidate_translation_freeze(previous=original, output=output, validator_source=validator)
    new = read_frozen(output, stage='translation')['dev'][0]
    assert new['result']['actual_query'] == row['result']['generated_query']
    assert new['result']['generated_query'] == row['result']['generated_query']
    assert new['result']['provenance'] == row['result']['provenance']
    assert new['result']['fallback_reasons'] == []
    assert new['revalidation']['additional_llm_calls'] == manifest['llm_calls_per_case'] == 0
    assert sha256(original / 'manifest.json') == prior_hash
    assert json.loads((original/'dev.jsonl').read_text(encoding='utf-8')) == row
    with pytest.raises(FileExistsError):
        revalidate_translation_freeze(previous=original, output=output, validator_source=validator)
