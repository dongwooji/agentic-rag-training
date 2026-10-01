"""Phase A only: offline contracts, grounding, routing and vertical slice."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from graph_test_support import build_workflow, literature_hit
from test_tool_input_resolver import FakeExerciseRepository
from src.interpretation.contracts import AnalysisOperation, InterpretationDraft
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult, OpenAIQuestionInterpreterProvider
from src.interpretation.rule_parser import RuleQuestionParser
from src.graph.interpreted_workflow import InterpretedWorkflow


CANONICAL = "내 운동 기록에서 Deadlift (Barbell)의 처음 3-session과 최근 3-session median e1RM을 비교해줘."
PARAPHRASE = "내 데드리프트 초반 세 세션이랑 마지막 세 세션의 추정 1RM 중앙값을 비교해줘."


def draft(**updates):
    value = dict(
        personal_record_requested=True, literature_requested=False,
        exercise_mention="데드리프트", canonical_exercise_name="Deadlift (Barbell)",
        exercise_resolution_status="resolved",
        time_condition={"scope": "all_records", "start_date": None, "end_date": None, "source_text": ""},
        record_operation="exercise_records", session_id=None,
        requested_analyses=[{"operation": "first_last_n_session_median_e1rm", "n_sessions": 3, "source_text": "초반 세 세션이랑 마지막 세 세션"}],
        literature_subquestion=None, user_assumptions=[], unresolved_fields=[], clarification_required=False,
    )
    value.update(updates)
    return value


class FakeInterpreterProvider:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    def invoke(self, question, *, canonical_exercises):
        self.calls.append(question)
        return InterpreterProviderResult(raw_interpretation=self.output, error=self.error,
                                         response_id="fixture-1", api_requests=1)


def interpreter(provider=None, names=None):
    return QuestionInterpreter(exercise_repository=FakeExerciseRepository(names), provider=provider)


def attach(workflow, component):
    return InterpretedWorkflow(interpreter=component, tool_executor=workflow.nodes.tool_executor,
                               literature_tool=workflow.nodes.literature_tool,
                               runtime_grader=workflow.nodes.runtime_grader,
                               recovery_agent=workflow.nodes.recovery_agent,
                               final_response_layer=workflow.final_response_layer)


def test_operation_enum_matches_real_metric_tool():
    from src.tools.metric import MetricOperation
    assert {x.value for x in AnalysisOperation} == {x.value for x in MetricOperation}
    with pytest.raises(ValueError):
        InterpretationDraft.model_validate(draft(requested_analyses=[{"operation": "diagnose", "n_sessions": None, "source_text": ""}]))


def test_canonical_question_fast_path_never_calls_provider():
    provider = FakeInterpreterProvider(error="must not call")
    result = interpreter(provider).interpret(CANONICAL)
    assert result.execution_status == "ready"
    assert result.interpretation.parsing_source == "rule"
    assert result.interpretation.requested_analyses[0].n_sessions == 3
    assert provider.calls == []


def test_paraphrase_fallback_one_call_with_raw_question():
    provider = FakeInterpreterProvider(draft())
    result = interpreter(provider).interpret(PARAPHRASE)
    assert result.execution_status == "ready"
    assert result.interpretation.parsing_source == "llm"
    assert provider.calls == [PARAPHRASE]
    assert result.provider.api_requests == 1


@pytest.mark.parametrize("suffix", [" 그리고 논문도 설명해줘", " 수면의 영향도 알려줘", " 주간 훈련량도 계산해줘", " 모두 실패했다고 가정해줘"])
def test_partial_rule_parse_is_not_complete(suffix):
    assert RuleQuestionParser().parse(CANONICAL.rstrip(".") + suffix) is None


@pytest.mark.parametrize("updates", [
    {"exercise_mention": "벤치프레스", "canonical_exercise_name": "Bench Press (Barbell)"},
    {"canonical_exercise_name": "Bench Press (Barbell)"},
    {"time_condition": {"scope": "explicit", "start_date": "2018-01-01", "end_date": "2018-03-31", "source_text": ""}},
    {"requested_analyses": [{"operation": "first_last_n_session_median_e1rm", "n_sessions": 5, "source_text": "초반 세 세션이랑 마지막 세 세션"}]},
])
def test_invented_exercise_dates_or_n_rejected(updates):
    result = interpreter(FakeInterpreterProvider(draft(**updates))).interpret(PARAPHRASE)
    assert result.execution_status == "failure"
    assert result.error_code == "ungrounded_interpretation"


def test_unknown_canonical_is_clarification():
    result = interpreter(names=["Bench Press (Barbell)"]).interpret(CANONICAL)
    assert result.execution_status == "clarification_required"


def test_missing_n_cannot_use_metric_default():
    question = "내 데드리프트의 처음과 최근 median e1RM을 비교해줘."
    output = draft(requested_analyses=[{"operation": "first_last_n_session_median_e1rm", "n_sessions": None, "source_text": "처음과 최근 median e1RM"}])
    result = interpreter(FakeInterpreterProvider(output)).interpret(question)
    assert result.execution_status == "clarification_required"
    assert any(x.field == "n_sessions" for x in result.interpretation.unresolved_fields)


def test_literature_request_omission_cannot_execute():
    question = PARAPHRASE + " 논문과 함께 설명해줘."
    result = interpreter(FakeInterpreterProvider(draft())).interpret(question)
    assert result.execution_status == "clarification_required"
    assert any(x.field == "literature_requested" for x in result.interpretation.unresolved_fields)


def test_multiple_metrics_are_not_silently_reduced():
    question = "내 데드리프트의 weekly volume과 weekly frequency를 계산해줘."
    output = draft(requested_analyses=[{"operation": x, "n_sessions": None, "source_text": x} for x in ("weekly_volume", "weekly_frequency")])
    # Grounding text is the question wording, not enum spelling.
    for item in output["requested_analyses"]:
        item["source_text"] = item["operation"].replace("_", " ")
    result = interpreter(FakeInterpreterProvider(output)).interpret(question)
    assert result.execution_status == "clarification_required"
    assert any(x.field == "requested_analyses" for x in result.interpretation.unresolved_fields)


@pytest.mark.parametrize("output,error", [(None, "provider unavailable"), ({"bad": True}, None)])
def test_provider_and_schema_fail_closed(output, error):
    result = interpreter(FakeInterpreterProvider(output, error)).interpret(PARAPHRASE)
    assert result.execution_status == "failure"


def test_single_metric_vertical_slice_uses_existing_executor_once():
    legacy, training, metric, literature, grader, recovery = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    state = attach(legacy, interpreter()).invoke(CANONICAL)
    assert state["final_status"] == "answer_ready"
    assert state["interpretation_status"] == "ready"
    assert len(training.calls) == len(metric.calls) == 1
    assert metric.calls[0].n_sessions == 3
    assert literature.calls == grader.calls == recovery.calls == []
    assert state["tool_input_resolution"]["provenance"]["method"] == "interpretation_binding"


def test_fallback_hybrid_preserves_literature_channel_and_no_second_resolver():
    question = PARAPHRASE + " 점진적 과부하 논문도 함께 설명해줘."
    output = draft(literature_requested=True, literature_subquestion="점진적 과부하에 관한 문헌 근거")
    provider = FakeInterpreterProvider(output)
    legacy, training, metric, literature, grader, _ = build_workflow(literature_responses=[[literature_hit("one", 1)]], grader_results=[True], recovery_payloads=[])
    state = attach(legacy, interpreter(provider)).invoke(question)
    assert state["final_status"] == "answer_ready"
    assert state["route"]["route"] == "hybrid"
    assert len(training.calls) == len(metric.calls) == len(literature.calls) == 1
    assert provider.calls == [question]
    assert grader.calls[0].literature_subquestion == output["literature_subquestion"]
    assert literature.calls[0].query == question
    assert state["max_retry"] == 2


def test_clarification_is_distinct_and_calls_no_tools_or_answer_provider():
    question = "내 데드리프트의 처음과 최근 median e1RM을 비교해줘."
    output = draft(requested_analyses=[{"operation": "first_last_n_session_median_e1rm", "n_sessions": None, "source_text": "처음과 최근 median e1RM"}])
    final_layer = MagicMock()
    legacy, training, metric, literature, grader, recovery = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[], final_response_layer=final_layer)
    state = attach(legacy, interpreter(FakeInterpreterProvider(output))).invoke(question)
    assert state["interpretation_status"] == "clarification_required"
    assert state["final_status"] == "abstain_ready"
    assert "확인" in state["final_response"]["answer_text"]
    assert training.calls == metric.calls == literature.calls == grader.calls == recovery.calls == []
    final_layer.respond.assert_not_called()


def test_provider_uses_typed_output_and_no_retry_with_mock_client():
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=InterpretationDraft.model_validate(draft()), id="r1", model="fixture", status="completed", usage=SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30))
    provider = OpenAIQuestionInterpreterProvider(client=client)
    result = provider.invoke(PARAPHRASE, canonical_exercises=("Deadlift (Barbell)",))
    assert result.api_requests == 1
    assert result.usage.total_tokens == 30
    assert issubclass(client.responses.parse.call_args.kwargs["text_format"], InterpretationDraft)
    assert provider.config["max_retries"] == 0
    assert client.responses.parse.call_count == 1


@pytest.mark.parametrize("body,operation", [
    ("weekly volume을 계산해줘.", "weekly_volume"),
    ("weekly frequency를 계산해줘.", "weekly_frequency"),
    ("training gap을 계산해줘.", "training_gap"),
    ("기록을 조회해줘.", None),
])
def test_allowlisted_simple_operations_fast_path(body, operation):
    provider = FakeInterpreterProvider(error="must not call")
    result = interpreter(provider).interpret("내 Deadlift (Barbell)의 " + body)
    assert result.execution_status == "ready"
    assert provider.calls == []
    assert [x.operation.value for x in result.interpretation.requested_analyses] == ([operation] if operation else [])


def test_explicit_range_is_grounded_and_bound():
    from src.graph.interpretation_binding import bind_interpretation
    from src.routing.interpretation_router import InterpretationRouter
    question = "내 Deadlift (Barbell)의 2018-01-01부터 2018-03-31까지 weekly volume을 계산해줘."
    result = interpreter().interpret(question)
    assert result.execution_status == "ready"
    args = bind_interpretation(result.interpretation, InterpretationRouter().route(result.interpretation))
    assert args["compute_metrics"]["start_date"] == "2018-01-01"
    assert args["compute_metrics"]["end_date"] == "2018-03-31"


def test_period_omission_in_model_output_requires_clarification():
    output = draft(time_condition={"scope": "all_records", "start_date": None, "end_date": None, "source_text": ""})
    result = interpreter(FakeInterpreterProvider(output)).interpret(PARAPHRASE + " 2018-01-01부터 2018-03-31까지")
    assert result.execution_status == "clarification_required"


def test_personal_calculation_cannot_leak_into_literature_scope():
    output = draft(literature_requested=True, literature_subquestion="내 기록의 e1RM 변화를 계산해줘.")
    result = interpreter(FakeInterpreterProvider(output)).interpret(PARAPHRASE + " 문헌도 설명해줘.")
    assert result.execution_status == "clarification_required"


def test_invalid_provider_output_stops_vertical_slice_without_tools():
    legacy, training, metric, literature, grader, recovery = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    state = attach(legacy, interpreter(FakeInterpreterProvider(draft(canonical_exercise_name="Squat (Barbell)")))).invoke(PARAPHRASE)
    assert state["final_status"] == "execution_failure"
    assert training.calls == metric.calls == literature.calls == grader.calls == recovery.calls == []


def test_literature_only_uses_no_structured_tools():
    question = "근비대 논문에서 실패지점 훈련의 한계를 설명해줘."
    output = draft(personal_record_requested=False, literature_requested=True,
                   exercise_mention=None, canonical_exercise_name=None, record_operation=None,
                   requested_analyses=[], literature_subquestion=question)
    legacy, training, metric, literature, grader, _ = build_workflow(literature_responses=[[literature_hit("one", 1)]], grader_results=[True], recovery_payloads=[])
    state = attach(legacy, interpreter(FakeInterpreterProvider(output))).invoke(question)
    assert state["final_status"] == "answer_ready"
    assert training.calls == metric.calls == []
    assert len(literature.calls) == 1


def test_api_phase_a_factory_does_not_construct_old_resolver_provider(monkeypatch):
    from src.api import dependencies
    monkeypatch.setenv("PGPASSWORD", "fixture-password")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    monkeypatch.setenv("AGENTIC_RAG_QUESTION_INTERPRETATION", "phase_a")
    monkeypatch.setattr(dependencies, "PsycopgTrainingRepository", lambda **kwargs: FakeExerciseRepository())
    monkeypatch.setattr(dependencies, "LiteratureTool", SimpleNamespace(from_postgres=lambda **kwargs: MagicMock()))
    for name in ("OpenAIRuntimeEvidenceProvider", "OpenAIEvidenceRecoveryProvider", "OpenAIFinalAnswerProvider", "OpenAIQuestionInterpreterProvider"):
        monkeypatch.setattr(dependencies, name, lambda **kwargs: MagicMock())
    old = MagicMock(side_effect=AssertionError("legacy provider must not be constructed"))
    monkeypatch.setattr(dependencies, "OpenAIToolInputProvider", old)
    runtime = dependencies._build_runtime_workflow()
    try:
        assert isinstance(runtime._workflow, InterpretedWorkflow)
        old.assert_not_called()
    finally:
        runtime.close()


def test_mock_records_real_metric_and_final_response_through_http(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    from src.api.dependencies import get_workflow
    from src.answer.generator import FinalResponseLayer
    from src.agent.executor import DeterministicToolExecutor
    from src.tools.metric import MetricTool
    from src.tools.training_log import TrainingLogTool
    from test_phase8_tools import FakeTrainingRepository
    from answer_test_support import FakeFinalAnswerProvider, draft_payload
    repository = FakeTrainingRepository()
    # This fixture has one eligible set, so N=1 produces equal first/last medians.
    question = "내 Bench Press (Barbell)의 처음 1-session과 최근 1-session median e1RM을 비교해줘."
    answer_provider = FakeFinalAnswerProvider(draft_payload(record="기록의 계산 결과를 확인했습니다.",
        tool_ids=["tool-result-01-query_training_log", "tool-result-02-compute_metrics"]))
    legacy, _, _, literature, grader, recovery = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    workflow = InterpretedWorkflow(interpreter=QuestionInterpreter(exercise_repository=repository),
        tool_executor=DeterministicToolExecutor(training_log_tool=TrainingLogTool(repository), metric_tool=MetricTool(), literature_tool=literature),
        literature_tool=literature, runtime_grader=grader, recovery_agent=recovery,
        final_response_layer=FinalResponseLayer(answer_provider))
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: workflow
    with TestClient(app) as client:
        response = client.post("/query", json={"question": question})
    assert response.status_code == 200
    assert response.json()["final_status"] == "answer_ready"
    assert response.json()["tools_used"] == ["query_training_log", "compute_metrics"]
    metric_result = answer_provider.calls[0].structured_evidence[1].result
    assert metric_result["first_median_e1rm"] == pytest.approx(100 * (1 + 5 / 30))
    assert metric_result["last_median_e1rm"] == metric_result["first_median_e1rm"]
    assert len(answer_provider.calls) == 1


def test_truncated_training_result_blocks_metric_execution():
    from src.tools.contracts import success_response
    legacy, training, metric, *_ = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    training.execute = lambda request: success_response(operation="exercise_records", result={"records": [], "truncated_at_limit": True}, provenance={})
    state = attach(legacy, interpreter()).invoke(CANONICAL)
    assert state["final_status"] == "execution_failure"
    assert metric.calls == []


def test_missing_or_refused_typed_provider_output_does_not_retry():
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=None, id="refusal", status="completed", usage=None)
    result = OpenAIQuestionInterpreterProvider(client=client).invoke(PARAPHRASE, canonical_exercises=("Deadlift (Barbell)",))
    assert result.error is not None
    assert result.response_id == "refusal"
    assert result.api_requests == 1
    assert client.responses.parse.call_count == 1


def test_user_assumption_is_preserved_as_original_span():
    question = PARAPHRASE + " 모든 세트를 실패까지 수행했다고 가정해줘."
    assumption = "모든 세트를 실패까지 수행했다고 가정"
    result = interpreter(FakeInterpreterProvider(draft(user_assumptions=[{"text": assumption, "source_text": assumption}]))).interpret(question)
    assert result.execution_status == "ready"
    assert result.interpretation.user_assumptions[0].text == assumption


@pytest.mark.parametrize("question", [None, "", "   "])
def test_malformed_question_calls_no_provider(question):
    provider = FakeInterpreterProvider(draft())
    assert interpreter(provider).interpret(question).execution_status == "failure"
    assert provider.calls == []


def test_sdk_client_initialization_disables_automatic_retries(monkeypatch):
    import openai
    factory = MagicMock()
    monkeypatch.setattr(openai, "OpenAI", factory)
    OpenAIQuestionInterpreterProvider(api_key="fixture-key")
    assert factory.call_args.kwargs["max_retries"] == 0


def test_phase_a_frozen_manifests_and_legacy_execution_contracts_unchanged():
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    expected = {
        "reports/baselines/end_to_end_baseline_v1/manifest.json": "fd6d45414010033d61971fbddbea26fcd19c485aa51b2cb64c2ef0caff3478e6",
        "reports/baselines/end_to_end_baseline_v2/manifest.json": "c1e5c4c2defee539edb9980c62b79da003b33b4db2e1186314fb884706904225",
        "reports/baselines/router_baseline_v1/manifest.json": "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c",
        "src/graph/tool_input_resolver.py": "3372eb8972b2510496467fdd98df0fc56db427ceca3a70648ebfa40d3c10c301",
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest
