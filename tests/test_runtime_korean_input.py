from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from graph_test_support import build_workflow, literature_hit
from src.graph.runtime_tool_input_resolver import RuntimeToolInputResolver
from src.graph.tool_input_resolver import ToolInputResolver
from src.routing.contracts import RouterInput
from src.routing.deterministic import DeterministicRouter
from src.routing.runtime import RUNTIME_ROUTER_VERSION, RuntimeRouter
from src.routing.runtime_language import InputLanguageError, RuntimeQuestionNormalizer
from test_tool_input_resolver import FakeExerciseRepository, FakeProvider, _request


QUESTION = "내 데드리프트의 처음 3세션과 최근 3세션의 median e1RM을 비교해줘."
TOOLS = ["query_training_log", "compute_metrics"]


def resolver(provider=None, names=None):
    return RuntimeToolInputResolver(ToolInputResolver(
        exercise_repository=FakeExerciseRepository(names), provider=provider,
    ))


@pytest.mark.parametrize("question", [
    QUESTION,
    "제 데드 리프트의 첫 세 세션과 마지막 세 세션의 추정 1RM 중앙값을 비교해줘.",
    "나의 데드리프트에서 처음 ３세션과 최근 ３세션의 median e1RM을 비교해줘.",
    "내 기록에서 Deadlift의 처음 3-session과 최근 3-session median e1RM을 비교해줘.",
    "내 운동 기록에서 Deadlift (Barbell)의 처음 3-session과 최근 3-session median e1RM을 비교해줘.",
])
def test_korean_variations_resolve_the_same_three_session_request(question):
    provider = FakeProvider(error="provider must not be used")
    route = RuntimeRouter().route(RouterInput(question))
    assert route.route == "log_metric"
    assert route.selected_tools == tuple(TOOLS)
    result = resolver(provider).resolve(_request(question, TOOLS))
    assert result["execution_status"] == "ready"
    assert result["provenance"]["method"] == "deterministic"
    assert result["provenance"]["original_question"] == question
    metric = result["tool_inputs"]["compute_metrics"]
    assert metric["operation"] == "first_last_n_session_median_e1rm"
    assert metric["n_sessions"] == 3
    assert metric["canonical_exercise_name"] == "Deadlift (Barbell)"
    assert provider.calls == []


@pytest.mark.parametrize(("question", "operation", "exercise"), [
    ("내 벤치프레스의 주간 훈련량을 계산해줘.", "weekly_volume", "Bench Press (Barbell)"),
    ("저의 벤치 프레스 주별 볼륨을 계산해줘.", "weekly_volume", "Bench Press (Barbell)"),
    ("내 벤치프레스의 주간훈련량을 계산해줘.", "weekly_volume", "Bench Press (Barbell)"),
    ("제 스쿼트의 주간 빈도를 보여줘.", "weekly_frequency", "Squat (Barbell)"),
    ("내 기록에서 데드리프트의 주별 수행 횟수를 계산해줘.", "weekly_frequency", "Deadlift (Barbell)"),
    ("내 스쿼트의 훈련 공백을 계산해줘.", "training_gap", "Squat (Barbell)"),
    ("내 운동 기록 전체에서 가장 긴 운동 공백을 계산해줘.", "training_gap", None),
    ("제 인클라인 벤치프레스의 e1RM을 계산해줘.", "estimated_1rm", "Incline Bench Press (Barbell)"),
])
def test_korean_metrics_keep_existing_operation_semantics(question, operation, exercise):
    names = ["Deadlift (Barbell)", "Bench Press (Barbell)", "Squat (Barbell)", "Incline Bench Press (Barbell)"]
    result = resolver(names=names).resolve(_request(question, TOOLS))
    assert result["execution_status"] == "ready"
    metric = result["tool_inputs"]["compute_metrics"]
    assert metric["operation"] == operation
    assert metric["canonical_exercise_name"] == exercise


@pytest.mark.parametrize("question", [
    "내 데드리프트의 처음 세 세션과 최근 네 세션의 e1RM을 비교해줘.",
    "내 데드리프트의 처음 세션과 최근 세션의 e1RM을 비교해줘.",
    "내 데드리프트의 처음 3세션과 최근 세션의 e1RM을 비교해줘.",
    "내 데드리프트의 주간 훈련량과 e1RM을 계산해줘.",
    "내 덤벨 벤치프레스의 e1RM을 계산해줘.",
    "내 루마니안 데드리프트의 e1RM을 계산해줘.",
    "내 데드리프트(스모)의 e1RM을 계산해줘.",
    "내 Bench Press (Dumbbell)의 e1RM을 계산해줘.",
    "내 스쿼트와 데드리프트의 e1RM을 계산해줘.",
])
def test_ambiguous_inputs_fail_before_provider_or_tools(question):
    provider = FakeProvider(error="must not be called")
    result = resolver(provider).resolve(_request(question, TOOLS))
    assert result["execution_status"] == "failure"
    assert result["error"]["code"] == "ambiguous_tool_arguments"
    assert result["tool_inputs"] == {}
    assert provider.calls == []


def test_alias_target_still_requires_exact_database_validation():
    result = resolver(names=["Bench Press (Barbell)"]).resolve(_request(QUESTION, TOOLS))
    assert result["execution_status"] == "failure"
    assert result["error"]["code"] == "unknown_exercise"


@pytest.mark.parametrize("question", ["내 기록을 조회해줘.", "제 운동 기록을 조회해줘.", "저의 훈련 로그를 조회해줘."])
def test_personal_record_phrases_resolve_session_lookup(question):
    result = resolver().resolve(_request(question, ["query_training_log"], task_type="log_lookup"))
    assert result["execution_status"] == "ready"
    assert result["tool_inputs"]["query_training_log"]["operation"] == "list_sessions"


def test_explicit_dates_and_original_question_are_preserved():
    question = "내 벤치프레스의 2018-01-01부터 2018-03-31까지 주간 훈련량을 계산해줘."
    result = resolver().resolve(_request(question, TOOLS))
    metric = result["tool_inputs"]["compute_metrics"]
    assert metric["start_date"] == "2018-01-01"
    assert metric["end_date"] == "2018-03-31"
    assert result["provenance"]["original_question"] == question
    assert result["provenance"]["language_replacements"]


@pytest.mark.parametrize(("question", "expected"), [
    ("내 Bench Press 훈련량 변화와 근력 향상의 관계를 내 기록과 문헌을 함께 사용해서 설명해줘.", "hybrid"),
    ("내 벤치프레스의 주간 훈련량을 계산하고 문헌도 함께 설명해줘.", "hybrid"),
    ("제 운동 기록을 조회해줘.", "log_lookup"),
    ("저의 훈련 로그를 조회해줘.", "log_lookup"),
    ("제 스쿼트의 주간훈련량을 계산해줘.", "log_metric"),
    ("데드리프트의 근력 향상에 관한 문헌을 요약해줘.", "literature_only"),
    ("내 생각에는 데드리프트가 유용한데 관련 문헌을 알려줘.", "literature_only"),
    ("국내 기록적인 근력 향상에 관한 문헌을 알려줘.", "literature_only"),
    ("내 수면 시간 변화를 알려줘.", "unsupported"),
])
def test_runtime_router_intent_combinations(question, expected):
    route = RuntimeRouter().route(RouterInput(question))
    assert route.route == expected
    assert route.router_version == RUNTIME_ROUTER_VERSION


def test_frozen_router_retains_original_behavior():
    assert DeterministicRouter().route(RouterInput(QUESTION)).route == "ambiguous"
    assert RuntimeRouter().route(RouterInput(QUESTION)).route == "log_metric"


@pytest.mark.parametrize(("task_type", "tools"), [("literature_only", ["search_literature"]), ("unsupported", [])])
def test_no_structured_route_bypasses_normalization_and_provider(task_type, tools):
    provider = FakeProvider(error="must not call")
    result = resolver(provider).resolve(_request("덤벨 벤치프레스 문헌", tools, task_type=task_type))
    assert result["execution_status"] == "not_required"
    assert provider.calls == []


def test_korean_log_metric_graph_reaches_tools_without_provider():
    workflow, training, metric, literature, grader, recovery = build_workflow(
        literature_responses=[], grader_results=[], recovery_payloads=[],
    )
    workflow.nodes.router = RuntimeRouter()
    workflow.nodes.tool_input_resolver = resolver()
    state = workflow.invoke(QUESTION)
    assert state["final_status"] == "answer_ready"
    assert state["original_question"] == QUESTION
    assert len(training.calls) == len(metric.calls) == 1
    assert metric.calls[0].n_sessions == 3
    assert metric.calls[0].canonical_exercise_name == "Deadlift (Barbell)"
    assert literature.calls == grader.calls == recovery.calls == []


def test_korean_hybrid_graph_keeps_original_literature_query():
    question = "내 데드리프트의 처음 3세션과 최근 3세션 median e1RM을 비교하고, 점진적 과부하 문헌을 설명해줘."
    workflow, training, metric, literature, grader, _ = build_workflow(
        literature_responses=[[literature_hit("initial", 1)]], grader_results=[True], recovery_payloads=[],
    )
    workflow.nodes.router = RuntimeRouter()
    workflow.nodes.tool_input_resolver = resolver()
    state = workflow.invoke(question)
    assert state["final_status"] == "answer_ready"
    assert len(training.calls) == len(metric.calls) == len(literature.calls) == len(grader.calls) == 1
    assert literature.calls[0].query == question
    assert state["max_retry"] == 2
    assert len(state["literature_evidence"]) <= 10


def test_api_runtime_factory_installs_korean_adapters(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.api import dependencies

    monkeypatch.setenv("PGPASSWORD", "fixture-password")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    repository = FakeExerciseRepository()
    monkeypatch.setattr(dependencies, "PsycopgTrainingRepository", lambda **kwargs: repository)
    monkeypatch.setattr(dependencies, "LiteratureTool", SimpleNamespace(from_postgres=lambda **kwargs: MagicMock()))
    for name in ("OpenAIRuntimeEvidenceProvider", "OpenAIEvidenceRecoveryProvider", "OpenAIToolInputProvider", "OpenAIFinalAnswerProvider"):
        monkeypatch.setattr(dependencies, name, lambda **kwargs: MagicMock())
    runtime = dependencies._build_runtime_workflow()
    try:
        assert isinstance(runtime._workflow.nodes.router, RuntimeRouter)
        assert isinstance(runtime._workflow.nodes.tool_input_resolver, RuntimeToolInputResolver)
    finally:
        runtime.close()


def test_frozen_routing_and_preprocessing_inputs_not_edited():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in {
        "config/router_baseline_v1.json": "034f72ea19ca6c364ac96e66402626bfdcc1345be677245202795bbe115acb77",
        "config/exercise_aliases_v1.csv": "a1a850035872f9d829d908da931975261b9c64699d812eafc3a59ac20fa2fdfd",
        "src/routing/deterministic.py": "826d2ef00ba11e6c035a0de58886d1f95b99b04447b609c6a347f76af62fca74",
        "src/graph/tool_input_resolver.py": "3372eb8972b2510496467fdd98df0fc56db427ceca3a70648ebfa40d3c10c301",
    }.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
