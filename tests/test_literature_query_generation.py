"""Synthetic/mock query preparation only. No LLM, DB, encoder or search."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from graph_test_support import (build_workflow, literature_hit, recovery_payload,
                                LITERATURE_QUESTION, HYBRID_QUESTION, hybrid_tool_inputs)
from test_question_interpretation import FakeInterpreterProvider, draft, interpreter, PARAPHRASE
from src.graph.interpreted_workflow import InterpretedWorkflow
from src.graph.workflow import AgenticRAGWorkflow
from src.retrieval.literature_query import (
    LiteratureQueryGenerator, QueryProviderResult, generate_korean, translate_english,
    validate_generation, validate_translation,
)
from src.retrieval.query_provider import OpenAILiteratureQueryProvider
from src.retrieval.runtime_config import load_retrieval_config
from src.evaluation.literature_query_freeze import (
    freeze_stage, read_frozen, load_reviewed_conditions, sha256, write_new_json,
    source_hashes, verify_prompt_approval,
)
from src.tools.literature import LiteratureTool, LiteratureInput

ROOT = Path(__file__).resolve().parents[1]


def provider(payload=None, error=None):
    return Mock(invoke=Mock(return_value=QueryProviderResult(payload, {'api_requests':1}, error)))


def generation(query='고령자의 근감소 예방에 효과적인 저항운동 방법은 무엇인가?', personal=True):
    return provider({'has_personal_context':personal, 'literature_query':query})


@pytest.mark.parametrize('query', [
    # Synthetic validation examples, never evaluation questions or expected retrieval results.
    '고령자의 저항운동 전 10분 준비운동의 효과는?', '고령자의 근감소 예방에 주 2회 훈련의 효과는?',
    '고령자의 저항운동에서 8~12회 반복의 효과는?', '고령자의 근감소 예방 연구에서 24주 중재의 효과는?',
    '센서 교정 실험에서 6% 오차의 영향은?',
])
def test_scientific_numbers_allowed(query):
    result = generate_korean(generation(query), '내 기록을 보고 '+query)
    assert result['actual_query'] == query
    assert result['validation_status'] == 'passed'
    assert not result['fallback']


@pytest.mark.parametrize('query,reason', [
    ('2021-02-01부터 2021-03-01의 근력 변화', 'personal_date'),
    ('2022년 3월의 근력 변화', 'personal_date'),
    ('3월 2일 이후의 변화', 'personal_date'),
    ('5-session의 훈련 변수', 'personal_session_condition'),
    ('최근 5세션의 근력 변화', 'personal_session_condition'),
    ('5회 세션의 변수', 'personal_session_condition'),
    ('Bench Press (Barbell)의 효과', 'canonical_catalog_form'),
    ('plateau candidate 구간의 원인', 'record_judgement'),
    ('정체 후보 구간의 해석', 'record_judgement'),
    ('100kg의 근력 변화', 'personal_record_value'),
    ('e1RM 105의 변화', 'personal_record_value'),
])
def test_personal_patterns_fail_and_preserve_original(query, reason):
    original = '내 기록의 중량은 100kg이고 e1RM 105이다. 근력 변화에 관한 문헌을 찾아줘.'
    result = generate_korean(generation(query), original)
    assert result['actual_query'] == original
    assert result['fallback']
    assert reason in result['fallback_reasons']
    assert result['isolation_status'] == 'validation_failed'


def test_absolute_research_weight_is_not_personal_just_because_it_is_numeric():
    query = '계측기 교정에서 37kg 표준 추를 사용하는 방법은?'
    assert validate_generation('내 기록을 보고 싶다. '+query, query) == []


@pytest.mark.parametrize('query', ['', '완전히 다른 생성문', '2020-01-01 5-session'])
def test_false_ignores_generated_output_and_keeps_original(query):
    result = generate_korean(generation(query, False), '고령자의 근감소 예방에 주 2회 훈련의 효과는?')
    assert result['actual_query'] == '고령자의 근감소 예방에 주 2회 훈련의 효과는?'
    assert result['isolation_status'] == 'original_already_suitable'
    assert not result['fallback']


@pytest.mark.parametrize('payload,error,reason', [
    ({'has_personal_context':'true','literature_query':'q'},None,'schema_validation'),
    ({'has_personal_context':True,'literature_query':'q','extra':True},None,'schema_validation'),
    ({'has_personal_context':True,'literature_query':' '},None,'empty_query'),
    (None,'arbitrary secret-bearing exception','provider_failure'),
])
def test_generation_failure_reasons_do_not_leak_provider_details(payload, error, reason):
    result = generate_korean(provider(payload,error), 'original')
    assert result['actual_query'] == 'original'
    assert result['fallback_reasons'] == [reason]
    assert 'secret-bearing' not in json.dumps(result)


@pytest.mark.parametrize('source,translated', [
    ('교정 장비 대기 40초 vs 2분', 'Calibration equipment wait of 40 seconds vs 2 minutes'),
    ('기호 보존용 합성 문자열: RIR 4, RPE 6, 1RM, e1RM',
     'Synthetic string for symbol preservation: RIR 4, RPE 6, 1RM, e1RM'),
    ('고령자 운동 주 2회', 'Older adults exercise 2 times per week'),
    ('2020-01-01 5-session 100kg', '2020-01-01 5-session 100kg'),
    ('20m/s 속도', '20 meters per second velocity'),
    ('20와트', '20 watts'),
    ('5회 세션', '5 sessions'),
])
def test_translation_preserves_numbers_units_and_acronyms(source, translated):
    assert validate_translation(source,translated) == []
    result = translate_english(provider({'literature_query':translated}),source)
    assert result['actual_query'] == translated
    assert not result['fallback']


@pytest.mark.parametrize('source,translated,reason', [
    ('교정 장비 대기 40초 vs 2분', 'Calibration equipment wait of 40 minutes vs 2 seconds', 'unit_preservation'),
    ('교정 장비 대기 40초', 'Calibration equipment wait of 0.667 minutes', 'number_preservation'),
    ('기호 보존용 합성 문자열: 1RM', 'Synthetic string for symbol preservation: RM', 'acronym_preservation'),
    ('기호 보존용 합성 문자열: RIR 4', 'Synthetic string for symbol preservation: rir 4', 'acronym_preservation'),
    ('센서 교정 6% 오차', 'Sensor calibration error', 'number_preservation'),
    ('RPE', 'RPE RPE', 'acronym_preservation'),
    ('20m/s', '20m/h', 'unit_preservation'),
    ('20volts', '20amps', 'unit_preservation'),
])
def test_translation_failure_uses_step2_not_original(source, translated, reason):
    result = translate_english(provider({'literature_query':translated}),source)
    assert result['actual_query'] == source
    assert reason in result['fallback_reasons']


def test_translation_schema_cannot_reclassify_personal_context():
    result = translate_english(provider({'literature_query':'english', 'has_personal_context':False}), '확정 질문')
    assert result['fallback_reasons'] == ['schema_validation']
    assert result['actual_query'] == '확정 질문'


@pytest.mark.parametrize('source,translated', [
    ('2033-04-17 calibration protocol', 'Calibration protocol for 2033-04-17'),
    ('2033/04/17 measurement completed', 'Measurement on 2033/04/17 was completed'),
    ('7 participants attended', 'There were 7 participants'),
    ('교정에서 22 볼트와 4 암페어', 'Calibration at 22 volts and 4 amps'),
])
def test_translation_dates_and_prose_are_not_units(source, translated):
    assert validate_translation(source, translated) == []


@pytest.mark.parametrize('source,translated,reason', [
    ('2033-04-17 calibration protocol', 'Calibration protocol for 2033-04-18', 'number_preservation'),
    ('2033-04-17 calibration 22 volts', 'Calibration on 2033-04-17 at 22 amps', 'unit_preservation'),
    ('7furlongs', '7yards', 'unit_preservation'),
    ('RIR 4 on 2033-04-17', 'RPE 4 on 2033-04-17', 'acronym_preservation'),
])
def test_repaired_unit_rule_still_rejects_real_changes(source, translated, reason):
    assert reason in validate_translation(source, translated)


@pytest.mark.parametrize('mode,version,dense,bm25,calls', [
    ('generated_ko','retrieval_step2_generation_v1','한국어 연구 질문','한국어 연구 질문',0),
    ('translated_bm25','retrieval_step3a_translation_v1','한국어 연구 질문','English research question',1),
    ('translated_both','retrieval_step3b_translation_v1','English research question','English research question',1),
])
@pytest.mark.parametrize('question', [LITERATURE_QUESTION, HYBRID_QUESTION])
def test_router_independent_initial_boundary_and_two_calls_only(mode,version,dense,bm25,calls,question):
    gen, trans = generation('한국어 연구 질문'), provider({'literature_query':'English research question'})
    base, training, metric, lit, grader, recovery = build_workflow(
        literature_responses=[[literature_hit('mock',1)]], grader_results=[True], recovery_payloads=[])
    gen.invoke.side_effect = lambda original: (
        QueryProviderResult({'has_personal_context':True,'literature_query':'한국어 연구 질문'})
        if (question == LITERATURE_QUESTION or (len(training.calls)==1 and len(metric.calls)==1))
        else pytest.fail('Generation must happen after structured tools, immediately before literature'))
    workflow = AgenticRAGWorkflow(tool_executor=base.nodes.tool_executor,literature_tool=lit,
        runtime_grader=grader,recovery_agent=base.nodes.recovery_agent,
        retrieval_config=load_retrieval_config(ROOT/'config'/f'{version}.json'),
        query_generator=LiteratureQueryGenerator(gen,trans))
    state = workflow.invoke(question,tool_inputs=hybrid_tool_inputs() if question==HYBRID_QUESTION else None)
    assert state['final_status'] == 'answer_ready'
    gen.invoke.assert_called_once_with(question)
    assert trans.invoke.call_count == calls
    if calls:
        trans.invoke.assert_called_once_with('한국어 연구 질문')
    assert lit.calls[0].query == dense
    assert lit.calls[0].bm25_query == bm25
    assert state['first_query_selection']['mode'] == mode
    assert state['initial_query'] == dense
    assert state['initial_bm25_query'] == bm25
    assert state['query_history'] == [dense]


def test_recovery_does_not_regenerate_or_translate_and_fusion_policy_is_unchanged():
    gen, trans = generation('근력 연구 질문'), provider({'literature_query':'Strength research question'})
    base, _, _, lit, grader, recovery = build_workflow(literature_responses=[
        [literature_hit('first',1)],[literature_hit('recovered',1)]],
        grader_results=[False,True], recovery_payloads=[recovery_payload('VBT recover missing literature')])
    workflow = AgenticRAGWorkflow(tool_executor=base.nodes.tool_executor,literature_tool=lit,
        runtime_grader=grader,recovery_agent=base.nodes.recovery_agent,
        retrieval_config=load_retrieval_config(ROOT/'config/retrieval_step3a_translation_v1.json'),
        query_generator=LiteratureQueryGenerator(gen,trans))
    state = workflow.invoke(LITERATURE_QUESTION)
    assert state['final_status'] == 'answer_ready', state.get('errors')
    assert gen.invoke.call_count == trans.invoke.call_count == 1
    assert lit.calls[1].query == 'VBT recover missing literature'
    assert lit.calls[1].bm25_query is None
    assert {hit['chunk_id'] for hit in state['fused_evidence']} == {'first','recovered'}
    assert state['query_history'] == ['근력 연구 질문','VBT recover missing literature']


def test_phase_a_subquestion_is_not_used_for_first_query_but_stays_for_grader():
    question = PARAPHRASE + ' 점진적 과부하 논문도 함께 설명해줘.'
    component = interpreter(FakeInterpreterProvider(draft(literature_requested=True,
                                                        literature_subquestion='Phase A 문헌 질문')))
    base, _, _, lit, grader, _ = build_workflow(literature_responses=[[literature_hit('mock',1)]],
                                              grader_results=[True],recovery_payloads=[])
    gen = generation('전용 생성기의 문헌 질문')
    workflow = InterpretedWorkflow(interpreter=component,tool_executor=base.nodes.tool_executor,literature_tool=lit,
                                  runtime_grader=grader,recovery_agent=base.nodes.recovery_agent,
                                  retrieval_config=load_retrieval_config(ROOT/'config/retrieval_step2_generation_v1.json'),
                                  query_generator=LiteratureQueryGenerator(gen))
    state = workflow.invoke(question)
    assert state['final_status'] == 'answer_ready'
    gen.invoke.assert_called_once_with(question)
    assert lit.calls[0].query == '전용 생성기의 문헌 질문'
    assert state['literature_subquestion'] == 'Phase A 문헌 질문'


def test_h0_h1_and_new_configs_keep_non_query_parameters_identical():
    h1 = load_retrieval_config(ROOT/'config/retrieval_h1_v1.json').model_dump()
    for version in ('retrieval_step2_generation_v1','retrieval_step3a_translation_v1','retrieval_step3b_translation_v1'):
        new = load_retrieval_config(ROOT/'config'/f'{version}.json').model_dump()
        assert {key for key in new if new[key]!=h1[key]} == {'config_version','query_mode'}
    from src.retrieval.runtime_config import DEFAULT_RETRIEVAL_CONFIG
    assert DEFAULT_RETRIEVAL_CONFIG.setting == 'H0'
    assert DEFAULT_RETRIEVAL_CONFIG.query_mode == 'original_question'


def test_literature_tool_forwards_two_queries_to_mock_retriever_without_search():
    retriever=Mock()
    retriever.search.return_value=SimpleNamespace(hits=[],candidate_count=0,latency_ms={})
    assets=SimpleNamespace(chunks_by_id={})
    tool=LiteratureTool(assets=assets,retriever=retriever)
    # Avoid frozen assets; provenance is synthetic, no real retriever construction.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(LiteratureTool,'provenance',property(lambda self:{'mock':True}))
        response=tool.execute(LiteratureInput('search','한국어',bm25_query='English'))
    assert response.success
    retriever.search.assert_called_once_with('한국어',top_k=10,bm25_query='English')
    assert response.result['dense_query']=='한국어'
    assert response.result['bm25_query']=='English'


def test_typed_provider_mock_reports_usage_latency_and_secret_free_failure():
    client=Mock()
    client.responses.parse.return_value=SimpleNamespace(output_parsed={'has_personal_context':False,'literature_query':'q'},
        model='synthetic-model',id='mock-response',status='completed',usage={'input_tokens':100,'output_tokens':20,
        'total_tokens':120,'input_tokens_details':{'cached_tokens':40}})
    gen=OpenAILiteratureQueryProvider(stage='generation',client=client)
    result=gen.invoke('original')
    assert json.loads(client.responses.parse.call_args.kwargs['input']) == {'original_question':'original'}
    assert result.provenance['api_requests']==1
    assert result.provenance['total_tokens']==120
    assert result.provenance['estimated_cost_usd']>0
    assert result.provenance['latency_ms']>=0
    client.responses.parse.side_effect=RuntimeError('synthetic-secret must never leave exception')
    result=gen.invoke('original')
    assert result.error=='provider_failure'
    assert result.provenance['failed_calls']==1
    assert not result.provenance['usage_available']
    assert 'synthetic-secret' not in str(result)


@pytest.fixture
def frozen_pair(tmp_path):
    datasets={name:[dict(case_id='synthetic',display_task_type='display-only',original_question='내 기록에 관한 문헌 질문')]
              for name in ('dev','dev-ko')}
    ko, en=tmp_path/'ko',tmp_path/'en'
    freeze_stage(output=ko,stage='generation',datasets=datasets,provider=generation('한국어 문헌 질문'),
                 input_manifest_sha256='a'*64,sources={})
    trans=provider({'literature_query':'English literature question'})
    freeze_stage(output=en,stage='translation',datasets=read_frozen(ko,stage='generation'),provider=trans,
                 input_manifest_sha256=sha256(ko/'manifest.json'),sources={})
    assert [call.args[0] for call in trans.invoke.call_args_list]==['한국어 문헌 질문']*2
    return ko,en


def test_freeze_chain_and_review_approval_gates(frozen_pair,tmp_path):
    ko,en=frozen_pair
    approval=tmp_path/'approval.json'
    write_new_json(approval,dict(actor='user',decision='approved',evidence_ref='synthetic-user-message',
        manifest_sha256={'generation':sha256(ko/'manifest.json'),'translation':sha256(en/'manifest.json')}))
    rows=load_reviewed_conditions(ko,en,approval,dataset='dev')
    assert rows[0]['step2']==('한국어 문헌 질문','한국어 문헌 질문')
    assert rows[0]['step3a']==('한국어 문헌 질문','English literature question')
    assert rows[0]['step3b']==('English literature question','English literature question')
    bad=tmp_path/'bad.json'
    write_new_json(bad,dict(actor='user',decision='approved',evidence_ref='synthetic-user-message',manifest_sha256={}))
    with pytest.raises(ValueError,match='approval'):
        load_reviewed_conditions(ko,en,bad,dataset='dev')
    with (en/'dev.jsonl').open('a',encoding='utf-8') as f: f.write('\n')
    with pytest.raises(ValueError,match='artifact'):
        load_reviewed_conditions(ko,en,approval,dataset='dev')


def test_freeze_refuses_existing_output_before_calls(frozen_pair):
    ko,_=frozen_pair
    gen=generation()
    with pytest.raises(FileExistsError):
        freeze_stage(output=ko,stage='generation',datasets=read_frozen(ko),provider=gen,
                     input_manifest_sha256='a'*64,sources={})
    gen.invoke.assert_not_called()


def test_translation_stage_uses_original_when_step2_falls_back(tmp_path):
    original='내 기록의 e1RM 105를 확인하고 연구를 찾아줘.'
    datasets={name:[dict(case_id='synthetic',display_task_type='display-only',original_question=original)]
              for name in ('dev','dev-ko')}
    ko=tmp_path/'ko'
    freeze_stage(output=ko,stage='generation',datasets=datasets,provider=generation('e1RM 105 연구'),
                 input_manifest_sha256='a'*64,sources={})
    trans=provider({'literature_query':'English query with e1RM 105'})
    freeze_stage(output=tmp_path/'en',stage='translation',datasets=read_frozen(ko),provider=trans,
                 input_manifest_sha256=sha256(ko/'manifest.json'),sources={})
    assert [call.args[0] for call in trans.invoke.call_args_list]==[original]*2


def test_prompt_approval_requires_current_source_hashes(tmp_path):
    approval=tmp_path/'approval.json'
    write_new_json(approval,dict(actor='user',decision='approved',evidence_ref='synthetic-message',sources=source_hashes(ROOT)))
    verify_prompt_approval(ROOT,approval)
    payload=json.loads(approval.read_text(encoding='utf-8'))
    payload['sources']['config/literature_query_generation_v1.md']='0'*64
    approval.write_text(json.dumps(payload),encoding='utf-8')
    with pytest.raises(ValueError,match='approval'):
        verify_prompt_approval(ROOT,approval)


def test_phase_a_failure_never_reaches_dedicated_generator():
    component=interpreter(FakeInterpreterProvider(error='synthetic failure'))
    base,training,metric,lit,grader,recovery=build_workflow(literature_responses=[],grader_results=[],recovery_payloads=[])
    gen,trans=generation(),provider({'literature_query':'english'})
    workflow=InterpretedWorkflow(interpreter=component,tool_executor=base.nodes.tool_executor,literature_tool=lit,
        runtime_grader=grader,recovery_agent=base.nodes.recovery_agent,
        retrieval_config=load_retrieval_config(ROOT/'config/retrieval_step3a_translation_v1.json'),
        query_generator=LiteratureQueryGenerator(gen,trans))
    state=workflow.invoke(PARAPHRASE)
    assert state['final_status']=='execution_failure'
    assert training.calls==metric.calls==lit.calls==grader.calls==recovery.calls==[]
    gen.invoke.assert_not_called()
    trans.invoke.assert_not_called()


@pytest.mark.parametrize('version', ['retrieval_step2_generation_v1','retrieval_step3a_translation_v1','retrieval_step3b_translation_v1'])
def test_existing_runner_blocks_all_generated_modes_before_assets(monkeypatch,version):
    import importlib.util
    import sys
    spec=importlib.util.spec_from_file_location('query_generated_gate',ROOT/'scripts/run_retrieval_v2_preparation.py')
    runner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    forbidden=Mock(side_effect=AssertionError('No real retrieval permitted'))
    monkeypatch.setattr(runner,'load_frozen_literature_assets',forbidden)
    monkeypatch.setattr(runner,'MiniLMEncoder',forbidden)
    monkeypatch.setattr(sys,'argv',['runner','--config',str(ROOT/'config'/f'{version}.json'),
                                  '--output',str(ROOT/'reports/experiments/synthetic-generated-gate-no-output')])
    with pytest.raises(SystemExit) as result:
        runner.main()
    assert result.value.code==2
    forbidden.assert_not_called()


def test_preparation_cli_checks_prompt_approval_before_secrets_or_provider(monkeypatch,tmp_path):
    import importlib.util
    import sys
    spec=importlib.util.spec_from_file_location('query_preparation_gate',ROOT/'scripts/prepare_literature_queries.py')
    runner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    approval=tmp_path/'approval.json'
    write_new_json(approval,dict(actor='user',decision='not-approved',sources={}))
    forbidden=Mock(side_effect=AssertionError('No secret, question or provider read before approval'))
    monkeypatch.setattr(runner,'read_frozen',forbidden)
    monkeypatch.setattr(runner,'OpenAILiteratureQueryProvider',forbidden)
    monkeypatch.setattr(sys,'argv',['runner','--stage','generation',
        '--input',str(ROOT/'data/evaluation/retrieval_step2_queries_v1'),
        '--output',str(ROOT/'data/evaluation/synthetic-generation-gate-no-output'),
        '--prompt-approval',str(approval),'--secrets-json',str(tmp_path/'not-created-secret.json')])
    with pytest.raises(ValueError,match='approval'):
        runner.main()
    forbidden.assert_not_called()
    assert not (ROOT/'data/evaluation/synthetic-generation-gate-no-output').exists()
