"""Invented fixture tests; private questions, corpus and models are never read."""
import ast
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.heldout_diagnosis import (
    build_diagnosis, final_case_diagnosis, gold_comparison, language_statistics, section_type,
)
from src.evaluation.heldout_v2 import retrieval_metric_cases
from src.evaluation.retrieval_metrics import evaluate_retrieval


def group(key,cid,ids,match='any',required=True,section='Discussion > Limitations'):
    return dict(evidence_group_id=key,case_id=cid,required=required,
                source=dict(section=section,paragraph_id='invented-paragraph'),parent_mapping=dict(chunk_ids=ids,match=match))


@pytest.fixture
def data():
    cases=dict(cases=[dict(id='invented-a',question='원형 도형은 무엇인가?',task_type='literature_only',retrieval_metric_eligible=True),
                      dict(id='invented-b',question='What is a polygon?',task_type='literature_only',retrieval_metric_eligible=True)])
    old=dict(evidence_groups=[group('g1','invented-a',['c1']),group('g2','invented-b',['c2','c3'],'all')])
    new=deepcopy(old)
    new['evidence_groups'] += [group('g3','invented-a',['c4']),group('g4','invented-b',['c5'])]
    prior=deepcopy(old)
    prior['evidence_groups'].append(deepcopy(new['evidence_groups'][2]))
    hits={'invented-a':[dict(chunk_id='c1',bm25_rank=1)],'invented-b':[dict(chunk_id='c2',bm25_rank=None)]}
    ranks={name:deepcopy(hits) for name in ('H0','H1','step2','final_v2')}
    adapted,_=retrieval_metric_cases(cases,new)
    expected={name:evaluate_retrieval(adapted,{cid:[h['chunk_id'] for h in hs] for cid,hs in hits.items()},ks=(10,)) for name in ranks}
    corpus=dict(papers=[{}],chunks=[dict(chunk_id=f'c{i}',paper_id='invented-paper') for i in range(1,6)])
    return dict(old_cases=cases,final_cases=deepcopy(cases),old_gold=old,final_gold=new,prefinal_gold=prior,
                rankings=ranks,dense10={'invented-a':[dict(chunk_id='c1'),dict(chunk_id='c4')],
                                      'invented-b':[dict(chunk_id='c2')]},
                index_ids=[f'c{i}' for i in range(1,6)],corpus_v1=deepcopy(corpus),
                corpus_v2={**deepcopy(corpus),'embedding_row_count':5},published_metrics=expected)


def test_saved_rank_only_diagnosis_and_required_membership(data):
    result=build_diagnosis(**data)
    assert result['search_calls']==result['llm_calls']==0
    assert result['integrity']['all_required_ids_in_index']
    assert result['integrity']['required_chunk_links']==5
    assert result['gold_change']['prefinal_added_ids']==['g3']
    assert result['gold_change']['final_only_added_ids']==['g4']
    # match=all with just one required member is a miss, never partial credit.
    assert result['final_v2']['found_group_count']==1
    assert result['final_v2']['missed_group_count']==3
    assert result['final_v2']['candidate50']['known_inside_by_saved_prefix']==1
    assert result['final_v2']['candidate50']['unknown']==2
    assert result['final_v2']['candidate50']['confirmed_outside'] is None
    assert result['metrics']['v1']['final_v2']['macro']['complete_evidence@10']==.5
    assert result['metrics']['v2_1_final']['final_v2']['macro']['complete_evidence@10']==0


@pytest.mark.parametrize('mutation', ['index','embedding_rows','original_question','ranking_membership','duplicate_hit','published'])
def test_inconsistent_saved_inputs_fail_closed(data,mutation):
    if mutation=='index': data['index_ids'].pop()
    elif mutation=='embedding_rows': data['corpus_v2']['embedding_row_count']=4
    elif mutation=='original_question': data['old_cases']['cases'][0]['question']='different'
    elif mutation=='ranking_membership': data['rankings']['H0'].pop('invented-b')
    elif mutation=='duplicate_hit': data['rankings']['H0']['invented-a'] *= 2
    else: data['published_metrics']={}
    with pytest.raises(ValueError):
        build_diagnosis(**data)


def test_missing_gold_chunk_is_reported_without_calling_search(data):
    data['final_gold']['evidence_groups'][2]['parent_mapping']['chunk_ids']=['absent']
    result=build_diagnosis(**data)
    assert result['integrity']['missing_required_chunk_ids']==['absent']
    assert not result['integrity']['all_required_ids_in_index']


@pytest.mark.parametrize('heading,kind',[('Materials and Methods','methods'),('RESULTS > measurements','results'),
                                       ('Discussion > Limitations','limitations'),('Discussion > interpretations','discussion'),
                                       ('Introduction','introduction'),('Conclusion','conclusions'),('unknown title','other')])
def test_section_heading_rules(heading,kind):
    assert section_type(heading)==kind


def test_posthoc_notation_counts_include_acronyms_and_declare_alternative():
    records=[dict(id='a',question='원형 도형은 무엇인가?'),dict(id='b',question='Polygon의 넓이는?'),
             dict(id='c',question='RIR 2와 e1RM은?')]
    result=language_statistics(records)
    assert result['english_notation_including_abbreviations']==2
    assert result['pure_korean_no_latin']==1
    assert result['terms_beyond_allowed_abbreviations_units']==1
    assert result['pure_korean_allowing_declared_abbreviations_units']==2


def test_diagnosis_module_has_no_retrieval_or_provider_imports():
    root=Path(__file__).resolve().parents[1]
    for name in ('src/evaluation/heldout_diagnosis.py','scripts/diagnose_retrieval_heldout.py'):
        source=(root/name).read_text(encoding='utf-8')
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):
                assert not (node.module or '').startswith(('src.retrieval','openai','sentence_transformers'))
        assert '--secrets-json' not in source
