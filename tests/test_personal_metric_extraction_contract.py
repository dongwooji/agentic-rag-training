"""Versioned extraction wiring and new synthetic typed/mock confirmations."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.interpretation.contracts import SemanticInterpretationDraft
from src.interpretation.provider import OpenAIQuestionInterpreterProvider
from src.routing.interpretation_router import InterpretationRouter
from test_operation_argument_contract import LIFT, OTHER, Repository, draft, run

ROOT = Path(__file__).resolve().parents[1]


def test_original_prompt_and_config_are_preserved_and_only_new_prompt_is_selected():
    old = json.loads((ROOT/'config/question_interpreter_semantics_v1.json').read_text(encoding='utf-8'))
    new = json.loads((ROOT/'config/question_interpreter_semantics_v1_1.json').read_text(encoding='utf-8'))
    assert {k:v for k,v in old.items() if k != 'prompt_path'} == {k:v for k,v in new.items() if k != 'prompt_path'}
    assert hashlib.sha256((ROOT/old['prompt_path']).read_bytes()).hexdigest() == '6f327f7bbfbf18b1acc3c60675c2934ccc01150e1d1fc93ed958894a017fd802'
    prompt = (ROOT/new['prompt_path']).read_text(encoding='utf-8')
    assert prompt.startswith((ROOT/old['prompt_path']).read_text(encoding='utf-8'))
    assert 'Personal Metric extraction contract v1' in prompt
    assert 'Never suppress a genuine' in prompt


def test_new_prompt_uses_same_typed_schema_and_one_provider_call():
    q = f'{LIFT}의 weekly frequency를 산출하고 훈련 적응의 일반 근거를 설명해주세요.'
    raw = draft(q, literature_requested=True, literature_subquestion='훈련 빈도와 일반적인 훈련 적응의 관계')
    raw['evidence_need'].update(answer_type='causal',literature_needed_for_answer=True)
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=SemanticInterpretationDraft.model_validate(raw),usage=None)
    provider = OpenAIQuestionInterpreterProvider(client=client,config_path=ROOT/'config/question_interpreter_semantics_v1_1.json')
    result = provider.invoke(q,canonical_exercises=(LIFT,OTHER))
    assert result.error is None and result.api_requests == 1
    assert result.raw_interpretation['requested_analyses'][0]['operation'] == 'weekly_frequency'
    client.responses.parse.assert_called_once()
    assert client.responses.parse.call_args.kwargs['instructions'] == provider.prompt


@pytest.mark.parametrize('operation', ['weekly_volume','weekly_frequency','training_gap','estimated_1rm'])
def test_personal_computation_and_general_literature_remain_separate(operation):
    q = f'{LIFT}의 {operation.replace("_"," ")}를 집계하고, 일반적인 훈련 적응과 회복에 관한 근거를 알려주세요.'
    raw = draft(q,operation,literature_requested=True,literature_subquestion='일반적인 저항훈련 적응과 회복에 관한 문헌')
    raw['evidence_need'].update(answer_type='advisory',literature_needed_for_answer=True)
    r = run(q,raw)
    assert r.execution_status == 'ready'
    assert [a.operation.value for a in r.interpretation.requested_analyses] == [operation]
    assert InterpretationRouter().route(r.interpretation).route == 'hybrid'


def test_independently_requested_metrics_are_not_silently_dropped():
    q = f'{LIFT}의 weekly volume과 weekly frequency를 각각 산출하고 일반 문헌도 정리해주세요.'
    raw = draft(q,'weekly_volume',literature_requested=True,literature_subquestion='훈련 볼륨과 빈도의 일반 근거')
    raw['evidence_need'].update(answer_type='evaluative',literature_needed_for_answer=True)
    raw['requested_analyses'].append(dict(operation='weekly_frequency',n_sessions=None,source_text='weekly frequency'))
    r = run(q,raw)
    assert r.execution_status == 'clarification_required'
    assert {a.operation.value for a in r.interpretation.requested_analyses} == {'weekly_volume','weekly_frequency'}
    assert not InterpretationRouter().route(r.interpretation).selected_tools
    assert r.phase_a_semantics.evidence_need.required_sources == ['training_records','literature']


def test_live_comparison_fixture_is_frozen_and_has_true_multi_metric_cases():
    path = ROOT/'tests/fixtures/interpreter_extraction_smoke_v1.json'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == '6d07cb74db25fc88b7e7577845fede8fe5f1f8ba6735b80c2d779aedc1c76c99'
    cases = json.loads(path.read_text(encoding='utf-8'))['cases']
    assert len(cases) == 8
    assert sum(len(x['intended_analyses']) > 1 for x in cases) == 2


def test_explicit_service_version_selects_new_provider_without_changing_defaults(monkeypatch):
    from src.api import dependencies
    monkeypatch.setenv('PGPASSWORD','synthetic-password')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-key')
    monkeypatch.setenv('AGENTIC_RAG_QUESTION_INTERPRETATION','phase_a')
    monkeypatch.setenv('AGENTIC_RAG_PHASE_A_EXTRACTION_VERSION','personal_metric_v1')
    monkeypatch.delenv('AGENTIC_RAG_RETRIEVAL_CONFIG',raising=False)
    monkeypatch.setattr(dependencies,'PsycopgTrainingRepository',lambda **kwargs:Repository())
    monkeypatch.setattr(dependencies,'TrainingLogTool',MagicMock())
    monkeypatch.setattr(dependencies,'MetricTool',MagicMock())
    monkeypatch.setattr(dependencies.LiteratureTool,'from_postgres',MagicMock(return_value=MagicMock()))
    for name in ('OpenAIRuntimeEvidenceProvider','OpenAIEvidenceRecoveryProvider','OpenAIFinalAnswerProvider','OpenAILiteratureQueryProvider'):
        monkeypatch.setattr(dependencies,name,MagicMock())
    provider=MagicMock()
    monkeypatch.setattr(dependencies,'OpenAIQuestionInterpreterProvider',provider)
    runtime=dependencies._build_runtime_workflow()
    try:
        assert provider.call_args.kwargs['config_path'].name == 'question_interpreter_semantics_v1_1.json'
        assert runtime._workflow.interpreter.semantics_enabled
        provider.return_value.invoke.assert_not_called()
    finally:
        runtime.close()
