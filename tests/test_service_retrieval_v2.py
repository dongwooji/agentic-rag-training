"""Service factory and real FastAPI boundary with synthetic providers/tools.

No network, OpenAI, PostgreSQL, corpus files or evaluation inputs are accessed.
"""

from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from answer_test_support import FakeFinalAnswerProvider, draft_payload
from graph_test_support import build_workflow, literature_hit, LITERATURE_QUESTION
from src.api import dependencies
from src.api.app import create_app
from src.retrieval.literature_query import QueryProviderResult
from src.retrieval.runtime_config import load_retrieval_config

ROOT = Path(__file__).resolve().parents[1]


def test_service_final_behavior_preserves_operating_corpus_and_frozen_final():
    service = load_retrieval_config(dependencies.DEFAULT_SERVICE_RETRIEVAL_CONFIG)
    frozen_path = ROOT / 'config/retrieval_final_v2_corpus_v2_v1.json'
    final = load_retrieval_config(frozen_path)
    assert sha256(frozen_path.read_bytes()).hexdigest() == '004a65821809219bb2823cadcfe77dd98d6a329987a909cfcebcbb1507cd68fe'
    assert service.query_mode == final.query_mode == 'translated_both'
    assert service.dense == final.dense
    assert service.bm25 == final.bm25
    assert service.rrf == final.rrf
    assert service.corpus_version == 'literature_corpus_v1'
    historical = load_retrieval_config(ROOT / 'config/retrieval_h0_v1.json')
    assert service.embedding_run_id == historical.embedding_run_id
    assert service.validation == historical.validation


@pytest.mark.parametrize('generation_failure', [False, True])
@pytest.mark.parametrize('translation_failure', [False, True])
def test_service_factory_rest_step2_step3_retrieval_and_fallbacks(monkeypatch, generation_failure, translation_failure):
    monkeypatch.setenv('PGPASSWORD', 'synthetic')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic')
    monkeypatch.delenv('AGENTIC_RAG_RETRIEVAL_CONFIG', raising=False)
    monkeypatch.delenv('AGENTIC_RAG_QUESTION_INTERPRETATION', raising=False)
    base, training, metric, lit, grader, recovery = build_workflow(
        literature_responses=[[literature_hit('synthetic-parent', 1)]],
        grader_results=[True], recovery_payloads=[])
    lit.close = Mock()
    factory = Mock(return_value=lit)
    monkeypatch.setattr(dependencies.LiteratureTool, 'from_postgres', factory)
    monkeypatch.setattr(dependencies, 'PsycopgTrainingRepository', Mock())
    monkeypatch.setattr(dependencies, 'TrainingLogTool', lambda repository: training)
    monkeypatch.setattr(dependencies, 'MetricTool', lambda: metric)
    monkeypatch.setattr(dependencies, 'RuntimeEvidenceGrader', lambda provider: grader)
    monkeypatch.setattr(dependencies, 'EvidenceRecoveryAgent', lambda provider: base.nodes.recovery_agent)
    monkeypatch.setattr(dependencies, 'OpenAIFinalAnswerProvider', lambda **kwargs: FakeFinalAnswerProvider(
        draft_payload(literature='합성 문헌 근거를 확인했습니다.', chunk_ids=['synthetic-parent'])))
    for name in ('OpenAIRuntimeEvidenceProvider', 'OpenAIEvidenceRecoveryProvider', 'OpenAIToolInputProvider'):
        monkeypatch.setattr(dependencies, name, Mock())
    events, generated = [], '합성 VBT 연구 질문'
    actual_step2 = LITERATURE_QUESTION if generation_failure else generated
    translated = 'Synthetic VBT research question'

    def generate(text):
        events.append(('step2', text))
        assert lit.calls == []
        return QueryProviderResult({'has_personal_context': True,
                                    'literature_query': '2033-04-17 개인 기록' if generation_failure else generated})

    def translate(text):
        events.append(('step3', text))
        assert lit.calls == []
        return QueryProviderResult({'literature_query': '17 extra participants' if translation_failure else translated})

    monkeypatch.setattr(dependencies, 'OpenAILiteratureQueryProvider',
                        lambda stage, **kwargs: Mock(invoke=generate if stage == 'generation' else translate))
    runtime = dependencies._build_runtime_workflow()
    states = []

    class Capture:
        def invoke(self, question):
            state = runtime.invoke(question)
            states.append(state)
            return state

    app = create_app()
    app.dependency_overrides[dependencies.get_workflow] = lambda: Capture()
    try:
        with TestClient(app) as client:
            response = client.post('/query', json={'question': LITERATURE_QUESTION})
        assert response.status_code == 200
        assert response.json()['final_status'] == 'answer_ready', states
        assert events == [('step2', LITERATURE_QUESTION), ('step3', actual_step2)]
        expected = actual_step2 if translation_failure else translated
        assert len(lit.calls) == 1
        assert lit.calls[0].query == lit.calls[0].bm25_query == expected
        selection = states[0]['first_query_selection']
        assert selection['step2']['fallback'] is generation_failure
        assert selection['step3']['fallback'] is translation_failure
        assert selection['step2']['fallback_reasons'] == (['personal_date'] if generation_failure else [])
        assert selection['step3']['fallback_reasons'] == (['number_preservation'] if translation_failure else [])
        assert selection['step2']['actual_query'] == actual_step2
        assert selection['step3']['actual_query'] == expected
        assert factory.call_args.kwargs['retrieval_config'] == load_retrieval_config(dependencies.DEFAULT_SERVICE_RETRIEVAL_CONFIG)
        assert training.calls == metric.calls == recovery.calls == []
        assert len(grader.calls) == 1
    finally:
        runtime.close()


def test_service_keeps_explicit_config_override(monkeypatch):
    monkeypatch.setenv('PGPASSWORD', 'synthetic')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic')
    override = ROOT / 'config/retrieval_h0_v1.json'
    monkeypatch.setenv('AGENTIC_RAG_RETRIEVAL_CONFIG', str(override))
    monkeypatch.setattr(dependencies, 'PsycopgTrainingRepository', Mock())
    factory = Mock(side_effect=RuntimeError('stop before external calls'))
    monkeypatch.setattr(dependencies.LiteratureTool, 'from_postgres', factory)
    with pytest.raises(dependencies.WorkflowInitializationError):
        dependencies._build_runtime_workflow()
    assert factory.call_args.kwargs['retrieval_config'] == load_retrieval_config(override)


def test_observed_phase_a_provider_forwards_catalog_and_preserves_usage():
    from scripts.observe_service_retrieval_v2 import ObservedProvider, usage_summary
    from src.interpretation.provider import InterpreterProviderResult
    from src.agent.contracts import PlannerUsage

    delegate = Mock(return_value=InterpreterProviderResult(
        raw_interpretation={'synthetic': True}, api_requests=1, response_id='synthetic-response',
        usage=PlannerUsage(input_tokens=7, output_tokens=3, total_tokens=10, estimated_cost_usd=0.0001)))
    events = []
    observed = ObservedProvider(SimpleNamespace(invoke=delegate), 'interpreter', events)
    catalog = ('Synthetic Lift (Barbell)',)
    result = observed.invoke('합성 질문', canonical_exercises=catalog)
    delegate.assert_called_once_with('합성 질문', canonical_exercises=catalog)
    assert result.raw_interpretation == {'synthetic': True}
    assert len(events) == 1
    assert events[0]['stage'] == 'interpreter'
    assert events[0]['usage_available']
    summary = usage_summary(events)
    assert summary['by_stage'] == {'interpreter': 1}
    assert (summary['input_tokens'], summary['output_tokens'], summary['total_tokens']) == (7, 3, 10)
    assert summary['estimated_cost_usd'] == 0.0001
    assert 'raw_interpretation' not in events[0]


def test_rest_observation_rejects_unknown_mode_before_runtime(monkeypatch, tmp_path):
    from scripts.observe_service_retrieval_v2 import rest

    monkeypatch.delenv('AGENTIC_RAG_RETRIEVAL_CONFIG', raising=False)
    monkeypatch.delenv('AGENTIC_RAG_QUESTION_INTERPRETATION', raising=False)
    forbidden = Mock(side_effect=AssertionError('No runtime or provider construction'))
    monkeypatch.setattr(dependencies, 'get_workflow', forbidden)
    with pytest.raises(ValueError, match='Unsupported observation interpretation mode'):
        rest(tmp_path, interpretation_mode='unknown')
    forbidden.assert_not_called()
