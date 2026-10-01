"""Phase A.1: real canonical labels, synthetic provider outputs, no API/DB."""

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from test_question_interpretation import draft, attach
from test_tool_input_resolver import FakeExerciseRepository
from graph_test_support import build_workflow
from src.interpretation.exercise_catalog import RepositoryExerciseCatalog, candidate_schema
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import InterpreterProviderResult, OpenAIQuestionInterpreterProvider
from src.interpretation.validator import GroundingError, validate_interpretation
from src.graph.tool_input_resolver import ExerciseCanonicalizer


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def actual_names():
    with (ROOT / "data/processed/workout_sets.csv").open(encoding="utf-8-sig", newline="") as handle:
        return sorted({row["canonical_exercise_name"] for row in csv.DictReader(handle)})


class CatalogProvider:
    def __init__(self, output):
        self.output = output
        self.calls = []

    def invoke(self, question, *, canonical_exercises):
        self.calls.append((question, canonical_exercises))
        return InterpreterProviderResult(raw_interpretation=self.output, api_requests=1)


def run(question, output, names):
    provider = CatalogProvider(output)
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(names), provider=provider)
    return component.interpret(question), provider


def lookup(mention, candidate, status="resolved", **kwargs):
    return draft(exercise_mention=mention, canonical_exercise_name=candidate,
                 exercise_resolution_status=status, requested_analyses=[], **kwargs)


@pytest.mark.parametrize("mention,canonical", [
    ("데드리프트", "Deadlift (Barbell)"),
    ("벤치프레스", "Bench Press (Barbell)"),
    ("벤치 프레스", "Bench Press (Barbell)"),
    ("스쿼트", "Squat (Barbell)"),
])
def test_existing_clear_korean_alias_rule_zero_llm(mention, canonical, actual_names):
    result, provider = run(f"내 {mention}의 기록을 조회해줘.", None, actual_names)
    assert result.execution_status == "ready"
    assert result.interpretation.canonical_exercise_name == canonical
    assert result.interpretation.exercise_mention == mention
    assert provider.calls == []


@pytest.mark.parametrize("mention,canonical", [
    ("인클라인 벤치프레스", "Incline Bench Press (Barbell)"),
    ("덤벨 해머컬", "Hammer Curl (Dumbbell)"),
    ("중량 딥스", "Weighted dips"),
    ("데드", "Deadlift (Barbell)"),
    ("벤치", "Bench Press (Barbell)"),
])
def test_same_interpreter_selects_actual_dataset_candidate_once(mention, canonical, actual_names):
    assert canonical in actual_names
    question = f"내 {mention} 기록 좀 확인해줘."
    result, provider = run(question, lookup(mention, canonical), actual_names)
    assert result.execution_status == "ready"
    assert result.interpretation.exercise_mention == mention
    assert result.interpretation.exercise_resolution_status == "resolved"
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == tuple(actual_names)
    assert result.provider.response_metadata["canonical_catalog_count"] == 70


@pytest.mark.parametrize("mention", ["해머컬", "딥스", "데드", "벤치"])
def test_ambiguous_selection_never_executes_tools(mention, actual_names):
    # Ambiguity is supplied by the typed Interpreter, not an alias guessing rule.
    assert "Hammer Curl" in actual_names and "Hammer Curl (Dumbbell)" in actual_names
    question = f"내 {mention} 기록 좀 확인해줘."
    provider = CatalogProvider(lookup(mention, None, "ambiguous"))
    legacy, training, metric, literature, grader, recovery = build_workflow(
        literature_responses=[], grader_results=[], recovery_payloads=[])
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(actual_names), provider=provider)
    state = attach(legacy, component).invoke(question)
    assert state["interpretation_status"] == "clarification_required"
    assert training.calls == metric.calls == literature.calls == grader.calls == recovery.calls == []
    assert len(provider.calls) == 1


@pytest.mark.parametrize("candidate,status", [(None, "not_found"), ("Invented Cosmic Lift", "resolved")])
def test_unknown_or_hallucinated_candidate_cannot_execute(candidate, status, actual_names):
    result, _ = run("내 우주리프트 기록 좀 확인해줘.", lookup("우주리프트", candidate, status), actual_names)
    assert result.execution_status == "clarification_required"
    assert result.interpretation.canonical_exercise_name is None


def test_candidate_schema_enforces_membership(actual_names):
    schema = candidate_schema(tuple(actual_names))
    with pytest.raises(ValueError):
        schema.model_validate(lookup("우주리프트", "Invented Cosmic Lift"))
    assert schema.model_validate(lookup("데드", "Deadlift (Barbell)")).canonical_exercise_name == "Deadlift (Barbell)"


@pytest.mark.parametrize("mention", ["데드", "데드리프트"])
def test_live_vague_question_resolves_exercise_but_not_period_or_strength_metric(mention, actual_names):
    question = f"내가 예전에 {mention}할 때보다 요즘 얼마나 세졌는지 기록으로 한번 봐줘."
    output = lookup(mention, "Deadlift (Barbell)",
                    time_condition={"scope": "unresolved", "start_date": None, "end_date": None, "source_text": "예전에"},
                    unresolved_fields=[{"field": "requested_analyses", "reason": "근력 비교 지표를 확인해주세요."}])
    result, provider = run(question, output, actual_names)
    assert result.execution_status == "clarification_required"
    assert result.interpretation.canonical_exercise_name == "Deadlift (Barbell)"
    assert not any(item.field == "canonical_exercise_name" for item in result.interpretation.unresolved_fields)
    assert result.interpretation.requested_analyses == []
    assert result.interpretation.time_condition.start_date is None
    assert len(provider.calls) == 1


def test_existing_success_question_still_rule_fast_path(actual_names):
    result, provider = run("내 데드리프트 처음 3세션과 최근 3세션 median e1RM 비교해줘.", None, actual_names)
    assert result.execution_status == "ready"
    assert result.interpretation.requested_analyses[0].n_sessions == 3
    assert provider.calls == []


def test_actual_repository_adapter_read_only_selection():
    repo = MagicMock(spec=["_fetch_all"])
    repo._fetch_all.return_value = [{"canonical_name": "Z"}, {"canonical_name": "A"}, {"canonical_name": "A"}]
    assert RepositoryExerciseCatalog(repo).names() == ("A", "Z")
    sql, params = repo._fetch_all.call_args.args
    assert sql == "SELECT canonical_name FROM training.exercises ORDER BY canonical_name"
    assert params == ()


def test_provider_gets_dynamic_enum_and_catalog_in_same_one_request(actual_names):
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(
        output_parsed=lookup("데드", "Deadlift (Barbell)"), usage=None, status="completed")
    result = OpenAIQuestionInterpreterProvider(client=client).invoke(
        "내 데드 기록 좀 확인해줘.", canonical_exercises=tuple(actual_names))
    assert result.error is None
    assert client.responses.parse.call_count == result.api_requests == 1
    args = client.responses.parse.call_args.kwargs
    assert json.loads(args["input"])["allowed_canonical_exercises"] == actual_names
    assert set(args["text_format"].model_json_schema()["properties"]["canonical_exercise_name"]["anyOf"][0]["enum"]) == set(actual_names)


def test_provider_hallucinated_candidate_is_schema_failure_without_retry(actual_names):
    client = MagicMock()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=lookup("우주리프트", "Invented Cosmic Lift"), usage=None)
    result = OpenAIQuestionInterpreterProvider(client=client).invoke("내 우주리프트 기록 확인해줘.", canonical_exercises=tuple(actual_names))
    assert result.error is not None
    assert result.raw_interpretation is None
    assert client.responses.parse.call_count == 1


def test_previous_literal_grounding_reproduces_reported_failure_paths(actual_names):
    # No live raw output was supplied. These are controlled examples of the
    # old code paths, not claims about the actual provider's raw response.
    canonicalizer = ExerciseCanonicalizer(FakeExerciseRepository(actual_names))
    with pytest.raises(GroundingError):
        validate_interpretation("내가 예전에 데드할 때보다 요즘 얼마나 세졌는지 기록으로 한번 봐줘.",
                                lookup("데드", "Deadlift (Barbell)"), source="llm", canonicalizer=canonicalizer)
    result = validate_interpretation("내가 예전에 데드리프트할 때보다 요즘 얼마나 세졌는지 기록으로 한번 봐줘.",
                                    lookup("데드리프트", "데드리프트"), source="llm", canonicalizer=canonicalizer)
    assert any(item.field == "canonical_exercise_name" for item in result.unresolved_fields)


def test_catalog_failure_calls_no_llm():
    repo = FakeExerciseRepository()
    repo.list_canonical_exercises = MagicMock(side_effect=RuntimeError("catalog unavailable"))
    provider = CatalogProvider(lookup("데드", "Deadlift (Barbell)"))
    result = QuestionInterpreter(exercise_repository=repo, provider=provider).interpret("내 데드 기록 좀 확인해줘.")
    assert result.execution_status == "failure"
    assert provider.calls == []


def test_missing_resolution_status_requires_clarification(actual_names):
    result, _ = run("내 데드 기록 좀 확인해줘.", lookup("데드", "Deadlift (Barbell)", None), actual_names)
    assert result.execution_status == "clarification_required"
