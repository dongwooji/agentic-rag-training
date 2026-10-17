"""Synthetic-only validation of the frozen decision arithmetic and runner flow."""
from copy import deepcopy
import json
from pathlib import Path
import importlib.util

import pytest

from src.evaluation.fixed_requirements_v1 import (
    adoption,grader_disagreements,initial_state,nodes,first_assessment,finish_assessment,score_pair,
)
from src.grading.fixed_requirements_v1.grader import FixedRequirementsGrader
from test_fixed_requirements_grader_v1 import Provider,component,QUESTION


def synthetic_row(ce,old,new,on_ce=None,fail=False):
    def condition(value):return dict(execution_failure=fail,grade=dict(execution_status="completed",evidence_sufficient=value),final_ce10=ce)
    row=dict(initial_ce10=ce,old=condition(old),new_off=condition(new),new_on=condition(new))
    if on_ce is not None:row['new_on']['final_ce10']=on_ce
    return row


def test_adoption_requires_strict_reduction_and_neither_direction_can_increase():
    improved=[synthetic_row(1,False,True),synthetic_row(0,True,False)]
    assert adoption(improved)['grader_adopted']
    assert not adoption([synthetic_row(1,True,True)])['grader_adopted']
    trade=[synthetic_row(1,False,True),synthetic_row(1,False,True),synthetic_row(0,False,True)]
    assert not adoption(trade)['grader_adopted']


def test_more_execution_failures_block_adoption_and_are_not_removed():
    row=synthetic_row(1,False,True);row['new_off']['execution_failure']=True
    assert not adoption([row])['grader_adopted']
    assert grader_disagreements([row],'new_off')['inputs']==1


def test_recovery_guard_independent_from_grader_adoption():
    row=synthetic_row(1,False,True,on_ce=0)
    d=adoption([row]);assert d['grader_adopted'] and not d['recovery']['adopted']
    row=synthetic_row(1,False,True);row['new_on']['execution_failure']=True
    assert not adoption([row])['recovery']['adopted']


def test_execution_failures_not_disguised_as_insufficient():
    row=synthetic_row(1,True,False)
    row['new_off'].update(execution_failure=True,grade=dict(execution_status='failure',evidence_sufficient=False))
    stats=grader_disagreements([row],'new_off')
    assert stats==dict(A=0,B=0,total=0,execution_failures=1,inputs=1)


def test_existing_any_all_semantics_and_top_ten_budget():
    gold=dict(gold=dict(literature_evidence_groups=[
        dict(required=True,match='any',chunk_ids=['a','b']),
        dict(required=True,match='all',chunk_ids=['c','d'])]))
    metrics=score_pair(gold,['a','c'],['a','c','d'])
    assert metrics['initial']==dict(egr10=.5,ce10=0)
    assert metrics['final']==dict(egr10=1,ce10=1)
    assert score_pair(gold,['a','c'],['a','c',*['x'+str(i) for i in range(8)],'d'])['final']['ce10']==0


def test_shared_initial_assessment_and_bounded_recovery_use_real_nodes():
    from graph_test_support import SequenceLiteratureTool,literature_hit,SequenceRecoveryProvider
    from src.recovery.agent import EvidenceRecoveryAgent
    # All texts/hits are synthetic, with no frozen question/Gold input.
    p=Provider(dict(components=[component(status='missing',ids=[])]),
               dict(components=[dict(component_id='C1',status='supported',supporting_chunk_ids=['new'])]))
    recovery_provider=SequenceRecoveryProvider([dict(target_component_ids=['C1'],
        recovery_query='휴식 길이 비교 결과 연구',preserved_terms=['휴식'])])
    rec=EvidenceRecoveryAgent(recovery_provider)
    tool=SequenceLiteratureTool([[literature_hit('new',1)]])
    node=nodes(FixedRequirementsGrader(p),rec,tool)
    first,ms=first_assessment(node,initial_state(QUESTION,QUESTION,'rest interval evidence',[literature_hit('s1',1)]))
    unchanged=deepcopy(first)
    off=finish_assessment(node,first,recovery_enabled=False)
    on=finish_assessment(node,first,recovery_enabled=True)
    assert first==unchanged
    assert off['final_behavior']=='abstain_ready' and on['final_behavior']=='answer_ready'
    assert not on['execution_failure']
    assert len(p.calls)==2 and len(tool.calls)==1
    assert p.calls[1].requirements.sha256==on['state']['literature_requirements']['sha256']
    assert not any(k in first for k in ['gold','case_id','expected_behavior'])


def test_later_recovery_execution_error_does_not_overwrite_last_grade_behavior():
    from graph_test_support import SequenceLiteratureTool,literature_hit
    p=Provider(dict(components=[component(status='missing',ids=[])]))
    class Recovery:
        def generate(self,value):raise RuntimeError('synthetic error')
    node=nodes(FixedRequirementsGrader(p),Recovery(),SequenceLiteratureTool([]))
    first,_=first_assessment(node,initial_state(QUESTION,QUESTION,'synthetic query',[literature_hit('s1',1)]))
    end=finish_assessment(node,first,recovery_enabled=True)
    assert end['final_behavior']=='abstain_ready'
    assert end['execution_failure']


def test_failed_question_validation_is_preserved_without_provider_or_recovery_call():
    from graph_test_support import SequenceLiteratureTool,literature_hit
    provider=Provider()
    node=nodes(FixedRequirementsGrader(provider),None,SequenceLiteratureTool([]))
    first,_=first_assessment(node,initial_state(QUESTION,QUESTION,'synthetic initial',
        [literature_hit('s1',1)],validation_status='failed'))
    result=finish_assessment(node,first,recovery_enabled=True)
    assert not provider.calls
    assert result['execution_failure'] and result['final_behavior']=='execution_failure'
    assert result['state']['grader_result']['error_code']=='input_failure'


def test_two_recovery_limit_and_fixed_requirements_preserved_across_both_rounds():
    from graph_test_support import SequenceLiteratureTool,literature_hit,SequenceRecoveryProvider
    from src.recovery.agent import EvidenceRecoveryAgent
    p=Provider(dict(components=[component(status='missing',ids=[])]),
               *[dict(components=[dict(component_id='C1',status='missing',supporting_chunk_ids=[])]) for _ in range(2)])
    recp=SequenceRecoveryProvider([dict(target_component_ids=['C1'],recovery_query=q,preserved_terms=['휴식'])
        for q in ['휴식 길이 실험 비교 결과','휴식 길이 체계적 검토 결과']])
    tool=SequenceLiteratureTool([[literature_hit('s2',1)],[literature_hit('s3',1)]])
    node=nodes(FixedRequirementsGrader(p),EvidenceRecoveryAgent(recp),tool)
    first,_=first_assessment(node,initial_state(QUESTION,QUESTION,'synthetic initial',[literature_hit('s1',1)]))
    result=finish_assessment(node,first,recovery_enabled=True)
    assert result['final_behavior']=='abstain_ready' and not result['execution_failure']
    assert len(tool.calls)==len(recp.calls)==2 and len(p.calls)==3
    assert p.calls[1].requirements.sha256==p.calls[2].requirements.sha256


def test_new_config_preserves_model_and_decoding_and_old_config_is_not_replaced():
    root=Path(__file__).resolve().parents[1]
    old=json.loads((root/'config/runtime_evidence_grader.json').read_text())
    new=json.loads((root/'config/runtime_evidence_grader_fixed_requirements_v1.json').read_text())
    for key in ['model','temperature','reasoning_effort','verbosity','max_retries','timeout_seconds','service_tier','pricing_usd_per_million_tokens']:
        assert old[key]==new[key]
    assert old['prompt_path']!=new['prompt_path']


def load_runner():
    path=Path(__file__).resolve().parents[1]/'scripts/run_fixed_requirements_v1.py'
    spec=importlib.util.spec_from_file_location('synthetic_fixed_runner',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_write_once_outputs_and_protocol_cannot_be_replaced(tmp_path):
    runner=load_runner();p=tmp_path/'result.json'
    runner.save(p,dict(value=1))
    with pytest.raises(FileExistsError):runner.save(p,dict(value=2))
    assert json.loads(p.read_text())==dict(value=1)


def test_heldout_requires_decision_before_loading_any_heldout_input(tmp_path,monkeypatch):
    runner=load_runner();monkeypatch.setattr(runner,'verify_seal',lambda output:None)
    accessed=[];monkeypatch.setattr(runner,'retrieval',lambda *a:accessed.append(a))
    with pytest.raises(FileNotFoundError):runner.run_heldout(tmp_path,'synthetic-key')
    assert not accessed


def test_report_serializes_synthetic_results_without_paper_text(tmp_path,monkeypatch):
    runner=load_runner();monkeypatch.setattr(runner,'ROOT',tmp_path)
    output=tmp_path/'local';output.mkdir()
    protocol=tmp_path/'protocol.json';runner.save(protocol,dict(source_sha256={}))
    monkeypatch.setattr(runner,'PROTOCOL',protocol);monkeypatch.setattr(runner,'verify_protocol',lambda:None)
    decision=dict(grader_adopted=False,recovery=dict(adopted=True),implementation_sha='c'*40)
    runner.save(output/'dev_decision.json',decision)
    runner.save(output/'dev_decision_seal.json',dict(sha256=runner.sha(output/'dev_decision.json')))
    grade=dict(execution_status='completed',evidence_sufficient=True,components=[dict(component_id='C1',requirement='합성 요구',status='supported',supporting_chunk_ids=['s1'],supporting_evidence=[dict(text='do not publish raw text')])])
    condition=dict(execution_failure=False,grade=grade,final_ce10=1.,final_egr10=1.,final_behavior='answer_ready',llm_calls=1,initial_latency_ms=1.,continuation_latency_ms=1.)
    rows=[dict(split=split,case_id='synthetic',initial_ce10=1.,old=condition,new_off=condition,new_on=condition) for split in ['dev','dev-ko']]
    runner.save(output/'dev_scored.json',rows)
    runner.save(output/'heldout_scored.json',dict(selected_grader='legacy',recovery_enabled=True,cases=[dict(case_id='synthetic-held',initial=dict(egr10=1.,ce10=1.),final=dict(egr10=1.,ce10=1.),**condition)]))
    for p in ['dev_events.json','heldout_events.json']:runner.save(output/p,[])
    runner.save(output/'protected_before.json',{})
    runner.write_report(output)
    folder=tmp_path/'docs/evaluations/grader_fixed_requirements_v1'
    assert (folder/'REPORT.md').exists() and (folder/'manifest.json').exists()
    assert 'do not publish raw text' not in (folder/'dev_results.json').read_text(encoding='utf-8')
