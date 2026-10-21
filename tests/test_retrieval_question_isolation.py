"""Service query isolation and factory guards; no encoder, search or external calls."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from graph_test_support import build_workflow, literature_hit, HYBRID_QUESTION, hybrid_tool_inputs
from test_question_interpretation import FakeInterpreterProvider, draft, interpreter, PARAPHRASE
from src.graph.interpreted_workflow import InterpretedWorkflow
from src.graph.workflow import AgenticRAGWorkflow
from src.retrieval.runtime_config import DEFAULT_RETRIEVAL_CONFIG, load_retrieval_config


ROOT = Path(__file__).resolve().parents[1]
STEP2 = load_retrieval_config(ROOT / 'config/retrieval_step2_v1.json')


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
    assert 'first_query_selection' not in state


@pytest.mark.parametrize('mode', ['legacy', 'phase_a'])
@pytest.mark.parametrize('setting', ['default', 'h0', 'step2'])
def test_service_factory_passes_same_config_to_tool_and_workflow_without_external_calls(monkeypatch, mode, setting):
    from src.api import dependencies as dep
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-not-a-secret')
    monkeypatch.setenv('PGPASSWORD', 'synthetic-not-a-secret')
    monkeypatch.setenv('AGENTIC_RAG_QUESTION_INTERPRETATION', mode)
    if setting == 'step2':
        monkeypatch.setenv('AGENTIC_RAG_RETRIEVAL_CONFIG', str(ROOT / 'config/retrieval_step2_generation_v1.json'))
    elif setting == 'h0':
        monkeypatch.setenv('AGENTIC_RAG_RETRIEVAL_CONFIG', str(ROOT / 'config/retrieval_h0_v1.json'))
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
    expected = (load_retrieval_config(ROOT / 'config/retrieval_step2_generation_v1.json') if setting == 'step2'
                else load_retrieval_config(ROOT / 'config/retrieval_step3b_translation_v1.json') if setting == 'default'
                else DEFAULT_RETRIEVAL_CONFIG)
    assert tool.call_args.kwargs['retrieval_config'] == expected
    assert workflow.call_args.kwargs['retrieval_config'] == expected
    assert ('query_generator' in workflow.call_args.kwargs) == (setting != 'h0')
    if setting == 'h0':
        dep.OpenAILiteratureQueryProvider.assert_not_called()
    elif setting == 'default':
        assert [call.kwargs['stage'] for call in dep.OpenAILiteratureQueryProvider.call_args_list] == ['translation', 'generation']
    else:
        assert [call.kwargs['stage'] for call in dep.OpenAILiteratureQueryProvider.call_args_list] == ['generation']
