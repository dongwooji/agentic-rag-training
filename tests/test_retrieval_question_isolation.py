"""Query selection and freeze checks only; no encoder, search or retrieval metrics."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from graph_test_support import build_workflow, literature_hit, HYBRID_QUESTION, hybrid_tool_inputs
from test_question_interpretation import FakeInterpreterProvider, draft, interpreter, PARAPHRASE
from src.graph.first_literature_query import select_phase_a_query, select_deterministic_query
from src.graph.interpreted_workflow import InterpretedWorkflow
from src.graph.workflow import AgenticRAGWorkflow
from src.evaluation.query_preparation import (
    freeze_query_preparation, isolation_summary, load_reviewed_queries,
    prepare_query_record, sha256, write_new_json,
)
from src.retrieval.runtime_config import DEFAULT_RETRIEVAL_CONFIG, load_retrieval_config
from src.routing.runtime import RuntimeRouter


ROOT = Path(__file__).resolve().parents[1]
STEP2 = load_retrieval_config(ROOT / 'config/retrieval_step2_v1.json')


def test_step2_changes_only_first_query_mode_from_h1():
    h1 = load_retrieval_config(ROOT / 'config/retrieval_h1_v1.json').model_dump()
    new = STEP2.model_dump()
    assert {key for key in new if new[key] != h1[key]} == {'config_version', 'query_mode'}
    assert new['query_mode'] == 'literature_subquestion'
    assert DEFAULT_RETRIEVAL_CONFIG.setting == 'H0'
    assert DEFAULT_RETRIEVAL_CONFIG.query_mode == 'original_question'


@pytest.mark.parametrize('status', ['failure', 'clarification_required'])
def test_failed_phase_a_retains_original_even_if_raw_subquestion_exists(status):
    result = select_phase_a_query('original', {'task_type':'hybrid'},
                                  validation_status=status, literature_subquestion='raw text')
    assert result.query == 'original'
    assert result.query_source == 'original'
    assert result.isolation_status == 'validation_failed'
    assert result.fallback_reason == 'phase_a_' + status


def test_literature_only_retains_original_in_both_paths():
    route = {'task_type':'literature_only'}
    phase_a = select_phase_a_query('original', route, validation_status='ready', literature_subquestion='rewrite')
    legacy = select_deterministic_query('original', route)
    assert phase_a == legacy
    assert phase_a.isolation_status == 'original_already_suitable'
    assert not phase_a.fallback


def test_same_phase_a_subquestion_is_not_counted_as_isolated():
    result = select_phase_a_query('original', {'task_type':'hybrid'}, validation_status='ready', literature_subquestion='original')
    assert result.isolation_status == 'safe_fallback'
    assert result.query_source == 'original'


@pytest.mark.parametrize('prefix', ['내 기록', '2019-01-01', 'median e1RM', '5-session', 'training gap'])
def test_generic_legacy_guard_rejects_personal_context_after_anchor(prefix):
    question = '기록 조회; periodization 근거와 ' + prefix + ' 비교해줘.'
    route = {'task_type':'hybrid', 'rule_match':{'matched_patterns':{'literature_concepts':['periodization']}}}
    result = select_deterministic_query(question, route)
    assert result.query == question
    assert result.fallback_reason == 'deterministic_personal_context_remains'


def test_legacy_safe_clause_and_missing_anchor():
    question = '개인 기록을 확인; periodization 효과를 설명해줘.'
    route = {'task_type':'hybrid', 'rule_match':{'matched_patterns':{'literature_concepts':['periodization']}}}
    result = select_deterministic_query(question, route)
    assert result.query == 'periodization 효과를 설명해줘.'
    assert result.query_source == 'deterministic'
    assert select_deterministic_query(question, {'task_type':'hybrid'}).fallback_reason == 'deterministic_no_separable_clause'


@pytest.mark.parametrize('config_name', ['retrieval_h0_v1', 'retrieval_h1_v1', 'retrieval_step2_v1'])
def test_phase_a_original_query_and_historical_mode_is_audit_only(config_name):
    question = PARAPHRASE + ' 점진적 과부하 논문도 함께 설명해줘.'
    subquestion = '점진적 과부하에 관한 문헌 근거'
    provider = FakeInterpreterProvider(draft(literature_requested=True, literature_subquestion=subquestion))
    component = interpreter(provider)
    legacy, _, _, literature, _, _ = build_workflow(literature_responses=[[literature_hit('mock',1)]], grader_results=[True], recovery_payloads=[])
    config = load_retrieval_config(ROOT / ('config/'+config_name+'.json'))
    workflow = InterpretedWorkflow(interpreter=component, tool_executor=legacy.nodes.tool_executor,
                                  literature_tool=literature, runtime_grader=legacy.nodes.runtime_grader,
                                  recovery_agent=legacy.nodes.recovery_agent, retrieval_config=config)
    state = workflow.invoke(question)
    if config_name == 'retrieval_step2_v1':
        assert state['final_status'] == 'execution_failure'
        assert literature.calls == []
        assert provider.calls == [question]
        return
    assert state['final_status'] == 'answer_ready'
    assert provider.calls == [question]
    assert literature.calls[0].query == question
    assert 'first_query_selection' not in state
    assert state['literature_subquestion'] == subquestion


def test_legacy_historical_mode_is_audit_only_without_affecting_h1():
    for config in (STEP2, load_retrieval_config(ROOT / 'config/retrieval_h1_v1.json')):
        base, _, _, literature, _, _ = build_workflow(literature_responses=[[literature_hit('mock',1)]], grader_results=[True], recovery_payloads=[])
        if config == STEP2:
            with pytest.raises(ValueError, match='audit-only'):
                AgenticRAGWorkflow(tool_executor=base.nodes.tool_executor, literature_tool=literature,
                                   runtime_grader=base.nodes.runtime_grader, recovery_agent=base.nodes.recovery_agent,
                                   retrieval_config=config)
            assert literature.calls == []
            continue
        workflow = AgenticRAGWorkflow(tool_executor=base.nodes.tool_executor, literature_tool=literature,
                                     runtime_grader=base.nodes.runtime_grader, recovery_agent=base.nodes.recovery_agent,
                                     retrieval_config=config)
        state = workflow.invoke(HYBRID_QUESTION, tool_inputs=hybrid_tool_inputs())
        assert state['final_status'] == 'answer_ready'
        assert literature.calls[0].query == HYBRID_QUESTION
        assert 'first_query_selection' not in state


def test_phase_a_failure_eval_keeps_case_and_runtime_calls_no_tools():
    component = interpreter(FakeInterpreterProvider(error='fixture failure'))
    base, training, metric, literature, grader, recovery = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    workflow = InterpretedWorkflow(interpreter=component, tool_executor=base.nodes.tool_executor, literature_tool=literature,
                                  runtime_grader=grader, recovery_agent=base.nodes.recovery_agent,
                                  retrieval_config=load_retrieval_config(ROOT / 'config/retrieval_h1_v1.json'))
    state = workflow.invoke(PARAPHRASE)
    assert state['final_status'] == 'execution_failure'
    assert training.calls == metric.calls == literature.calls == grader.calls == recovery.calls == []
    record = prepare_query_record(PARAPHRASE, component.interpret(PARAPHRASE), RuntimeRouter())
    assert record['phase_a']['query'] == PARAPHRASE
    assert isolation_summary([record])['phase_a_validation_failure'] == 1


def test_phase_a_runtime_literature_only_keeps_original_despite_rewrite():
    question = '근비대 논문에서 실패지점 훈련의 한계를 설명해줘.'
    output = draft(personal_record_requested=False, literature_requested=True,
                   exercise_mention=None, canonical_exercise_name=None, record_operation=None,
                   requested_analyses=[], literature_subquestion='실패지점 훈련에 관한 근비대 문헌 근거')
    component = interpreter(FakeInterpreterProvider(output))
    base, training, metric, literature, grader, _ = build_workflow(literature_responses=[[literature_hit('mock',1)]], grader_results=[True], recovery_payloads=[])
    workflow = InterpretedWorkflow(interpreter=component, tool_executor=base.nodes.tool_executor, literature_tool=literature,
                                  runtime_grader=grader, recovery_agent=base.nodes.recovery_agent,
                                  retrieval_config=load_retrieval_config(ROOT / 'config/retrieval_h1_v1.json'))
    state = workflow.invoke(question)
    assert state['final_status'] == 'answer_ready'
    assert literature.calls[0].query == question
    assert training.calls == metric.calls == []
    record = prepare_query_record(question, component.interpret(question), RuntimeRouter())
    assert record['runtime_task_type'] == 'literature_only'
    assert 'first_query_selection' not in state
    assert record['phase_a']['query'] == literature.calls[0].query
    assert record['phase_a']['isolation_status'] == 'original_already_suitable'


@pytest.fixture
def frozen_queries(tmp_path):
    (tmp_path / 'config').mkdir()
    config = json.loads((ROOT / 'config/question_interpreter.json').read_text(encoding='utf-8'))
    write_new_json(tmp_path / 'config/question_interpreter.json', config)
    (tmp_path / config['prompt_path']).write_text('fixture prompt', encoding='utf-8')
    rules = tmp_path / 'rules'
    rules.mkdir()
    write_new_json(rules / 'rules.json', {'fixture':True})
    write_new_json(rules / 'manifest.json', {'status':'frozen', 'sources':[], 'rules_sha256':sha256(rules / 'rules.json')})
    component = interpreter(FakeInterpreterProvider(error='fixture failure'))
    wrapped = Mock(wraps=component)
    datasets = {name:[{'case_id':'synthetic', 'question':PARAPHRASE, 'display_task_type':label}] for name,label in [('dev','hybrid'),('dev-ko','literature_only')]}
    output = tmp_path / 'queries'
    manifest = freeze_query_preparation(root=tmp_path, output=output, rules_folder=rules, datasets=datasets,
                                      interpreter=wrapped, legacy_router=RuntimeRouter(), model_config=config, input_hashes={})
    return output, manifest, wrapped, rules, datasets, config


def test_freeze_once_preserves_failures_provenance_and_ignores_display_label(frozen_queries):
    output, manifest, component, _, _, _ = frozen_queries
    assert component.interpret.call_count == 2
    rows = [json.loads((output / (name+'.jsonl')).read_text(encoding='utf-8')) for name in ('dev','dev-ko')]
    assert rows[0]['phase_a'] == rows[1]['phase_a']
    assert rows[0]['phase_a_validation_status'] == 'validation_failed'
    assert rows[0]['interpretation_response']['provider']['response_id'] == 'fixture-1'
    assert rows[0]['prompt_version']
    assert len(rows[0]['prompt_sha256']) == 64
    assert manifest['retrieval_executed'] is False
    assert manifest['primary_condition'] == 'phase_a'
    for artifact in manifest['artifacts']:
        assert sha256(output / artifact['path']) == artifact['sha256']


def test_freeze_refuses_overwrite_before_any_interpretation(frozen_queries, tmp_path):
    output, _, component, rules, datasets, config = frozen_queries
    calls = component.interpret.call_count
    with pytest.raises(FileExistsError):
        freeze_query_preparation(root=tmp_path, output=output, rules_folder=rules, datasets=datasets,
                                 interpreter=component, legacy_router=RuntimeRouter(), model_config=config, input_hashes={})
    assert component.interpret.call_count == calls


def test_review_gate_requires_exact_user_approval_and_checks_tampering(frozen_queries, tmp_path):
    output, _, component, _, _, _ = frozen_queries
    approval = tmp_path / 'approval.json'
    write_new_json(approval, {'actor':'user', 'decision':'approved', 'evidence_ref':'synthetic test only', 'manifest_sha256':'0'*64})
    with pytest.raises(ValueError, match='approval'):
        load_reviewed_queries(output, approval, dataset='dev')
    value = json.loads(approval.read_text())
    value['manifest_sha256'] = sha256(output / 'manifest.json')
    approval.write_text(json.dumps(value))
    assert len(load_reviewed_queries(output, approval, dataset='dev')) == 1
    assert component.interpret.call_count == 2
    with pytest.raises(ValueError, match='Only dev'):
        load_reviewed_queries(output, approval, dataset='heldout')
    (output / 'dev.jsonl').write_text('{}\n')
    with pytest.raises(ValueError, match='changed'):
        load_reviewed_queries(output, approval, dataset='dev')


def test_query_selection_interfaces_accept_no_case_label_or_gold():
    import inspect
    assert set(inspect.signature(prepare_query_record).parameters) == {'question', 'result', 'legacy_router'}


def test_existing_evaluation_runner_blocks_step2_before_loading_assets(monkeypatch, tmp_path):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location('query_gate_fixture', ROOT / 'scripts/run_retrieval_v2_preparation.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    forbidden = Mock(side_effect=AssertionError('Must never reach actual retrieval preparation'))
    monkeypatch.setattr(runner, 'load_frozen_literature_assets', forbidden)
    monkeypatch.setattr(runner, 'MiniLMEncoder', forbidden)
    monkeypatch.setattr(sys, 'argv', ['runner', '--config', str(ROOT / 'config/retrieval_step2_v1.json'),
                                    '--output', str(ROOT / 'reports/experiments/synthetic-query-gate-no-output')])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code == 2
    forbidden.assert_not_called()
    assert not (ROOT / 'reports/experiments/synthetic-query-gate-no-output').exists()


@pytest.mark.parametrize('mode', ['legacy', 'phase_a'])
@pytest.mark.parametrize('setting', ['default', 'step2'])
def test_service_factory_passes_same_config_to_tool_and_workflow_without_external_calls(monkeypatch, mode, setting):
    from src.api import dependencies as dep
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-a-secret')
    monkeypatch.setenv('PGPASSWORD', 'synthetic-not-a-secret')
    monkeypatch.setenv('AGENTIC_RAG_QUESTION_INTERPRETATION', mode)
    if setting == 'step2':
        monkeypatch.setenv('AGENTIC_RAG_RETRIEVAL_CONFIG', str(ROOT / 'config/retrieval_step2_generation_v1.json'))
    else:
        monkeypatch.delenv('AGENTIC_RAG_RETRIEVAL_CONFIG', raising=False)
    for name in ('PsycopgTrainingRepository', 'TrainingLogTool', 'MetricTool', 'RuntimeEvidenceGrader',
                 'OpenAIRuntimeEvidenceProvider', 'EvidenceRecoveryAgent', 'OpenAIEvidenceRecoveryProvider',
                 'FinalResponseLayer', 'OpenAIFinalAnswerProvider',
                 'RuntimeToolInputResolver', 'ToolInputResolver', 'OpenAIToolInputProvider',
                 'QuestionInterpreter', 'OpenAIQuestionInterpreterProvider', 'OpenAILiteratureQueryProvider'):
        monkeypatch.setattr(dep, name, Mock())
    tool = Mock()
    monkeypatch.setattr(dep.LiteratureTool, 'from_postgres', tool)
    workflow = Mock()
    monkeypatch.setattr(dep, 'InterpretedWorkflow' if mode == 'phase_a' else 'AgenticRAGWorkflow', workflow)
    dep._build_runtime_workflow()
    expected = load_retrieval_config(ROOT / 'config/retrieval_step2_generation_v1.json') if setting == 'step2' else DEFAULT_RETRIEVAL_CONFIG
    assert tool.call_args.kwargs['retrieval_config'] == expected
    assert workflow.call_args.kwargs['retrieval_config'] == expected
    assert ('query_generator' in workflow.call_args.kwargs) == (setting == 'step2')
    if setting == 'default':
        dep.OpenAILiteratureQueryProvider.assert_not_called()
