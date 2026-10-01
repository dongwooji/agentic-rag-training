"""Dataset-scoped relationship policy, no DB/API and no frozen mutations."""

from dataclasses import replace

import pytest

from test_question_interpretation import attach, draft, FakeInterpreterProvider
from test_tool_input_resolver import FakeExerciseRepository
from graph_test_support import build_workflow
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.exercise_relations import RuntimeExerciseRelations
from src.graph.runtime_group_training import RuntimeGroupTrainingTool
from src.tools.training_log import TrainingLogInput
from src.tools.contracts import success_response, failure_response, ToolError, ToolErrorCode
from src.tools.metric import MetricTool, MetricInput


NAMES = ["Hammer Curl", "Hammer Curl (Dumbbell)", "Incline Bench Press (Barbell)",
         "Seated Cable Row (close Grip)", "Hammer seated row (CLOSE GRIP)"]


@pytest.mark.parametrize("question", [
    "내 인클라인 벤치프레스의 기록을 조회해줘.",
    "내 Incline Bench Press의 기록을 조회해줘.",
    "내 시티드 케이블 로우 기록 좀 확인해줘.",
    "내 Seated Cable Row 기록 좀 확인해줘.",
    "내 Seated Row 기록 좀 확인해줘.",
    "내 해머 시티드 로우 기록 좀 확인해줘.",
    "내 Hammer seated row 기록 좀 확인해줘.",
])
def test_unspecified_equipment_grip_cannot_execute(question):
    assert RuntimeExerciseRelations().restriction(question) is not None
    mention = question.removeprefix("내 ").split("의 기록")[0].split(" 기록")[0]
    candidate = ("Incline Bench Press (Barbell)" if "인클라인" in question or "Incline" in question
                 else "Hammer seated row (CLOSE GRIP)" if "해머" in question or "Hammer" in question
                 else "Seated Cable Row (close Grip)")
    provider = FakeInterpreterProvider(draft(exercise_mention=mention, canonical_exercise_name=candidate, requested_analyses=[]))
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=provider)
    legacy, training, metric, literature, grader, recovery = build_workflow(
        literature_responses=[], grader_results=[], recovery_payloads=[])
    state = attach(legacy, component).invoke(question)
    assert state["interpretation_status"] == "clarification_required"
    assert state["question_interpretation"]["canonical_exercise_name"] is None
    assert training.calls == metric.calls == literature.calls == grader.calls == recovery.calls == []


@pytest.mark.parametrize("question", [
    "내 바벨 인클라인 벤치프레스 기록 확인해줘.",
    "내 Incline Bench Press (Barbell) 기록 확인해줘.",
    "내 덤벨 인클라인 벤치프레스 기록 확인해줘.",
    "내 시티드 케이블 로우 클로즈그립 기록 확인해줘.",
    "내 Seated Cable Row (Close Grip) 기록 확인해줘.",
    "내 Seated Cable Row (close Grip) 기록 확인해줘.",
    "내 해머 시티드 로우 클로즈그립 기록 확인해줘.",
    "내 Hammer seated row (CLOSE GRIP) 기록 확인해줘.",
])
def test_explicit_qualifier_not_blocked(question):
    assert RuntimeExerciseRelations().restriction(question) is None


def test_hammer_ambiguity_requires_selection_without_second_llm():
    output = draft(exercise_mention="해머컬", canonical_exercise_name=None, exercise_resolution_status="ambiguous",
                   requested_analyses=[], clarification_required=True,
                   unresolved_fields=[{"field": "canonical_exercise_name", "reason": "two labels"}])
    provider = FakeInterpreterProvider(output)
    result = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=provider).interpret("내 해머컬 기록 좀 확인해줘.")
    assert result.execution_status == "clarification_required"
    assert result.interpretation.canonical_exercise_name is None
    assert result.interpretation.candidate_exercises == NAMES[:2]
    assert len(provider.calls) == 1


def row(member, identifier, order=1, weight=50):
    return {"set_id": identifier, "session_id": "s1", "started_at": "2018-01-01T10:00:00",
            "workout_name": "Pull", "set_order": order, "weight": weight, "reps": 10,
            "weight_unit": "unknown_source_unit", "exercise_name": member,
            "include_in_e1rm": True, "include_in_volume_metrics": True, "is_outlier": False}


class MemberTool:
    def __init__(self, records, failed=None, truncated=None):
        self.records, self.calls, self.failed, self.truncated = records, [], failed, truncated

    def execute(self, request):
        self.calls.append(request)
        name = request.canonical_exercise_name
        if name == self.failed:
            return failure_response(operation="exercise_records", error=ToolError(ToolErrorCode.DATABASE_ERROR, "fixture"), provenance={})
        return success_response(operation="exercise_records", result={"records": self.records.get(name, []),
            "truncated_at_limit": name == self.truncated}, provenance={"source": name})


def group_tool(records, for_metrics=False, **kwargs):
    member_tool = MemberTool(records, **kwargs)
    return RuntimeGroupTrainingTool(member_tool, RuntimeExerciseRelations(), for_metrics=for_metrics), member_tool


def request(**kwargs):
    return TrainingLogInput(operation="exercise_records", canonical_exercise_name="Hammer Curl (Dumbbell)", limit=5000, **kwargs)


def test_joint_lookup_preserves_rows_sources_and_identical_values():
    records = {name: [row(name, "a" if name == NAMES[0] else "b")] for name in NAMES[:2]}
    tool, port = group_tool(records)
    payload = tool.execute(request(start_date="2018-01-01", end_date="2018-02-01")).to_dict()
    assert payload["success"] and payload["result"]["record_count"] == 2
    assert payload["result"]["cross_label_overlap_signatures"] == 1
    assert {x["stored_exercise_name"] for x in payload["result"]["records"]} == set(NAMES[:2])
    assert {x["set_id"] for x in payload["result"]["records"]} == {"a", "b"}
    assert len(port.calls) == 2
    assert all(call.start_date == "2018-01-01" for call in port.calls)
    assert payload["provenance"]["exercise_group_id"] == "hammer_curl"


def test_cross_label_overlap_blocks_metrics_without_erasing_rows():
    records = {name: [row(name, name)] for name in NAMES[:2]}
    tool, _ = group_tool(records, for_metrics=True)
    assert not tool.execute(request()).success


def test_nonoverlapping_records_use_unchanged_metric_logic():
    records = {NAMES[0]: [row(NAMES[0], "a")], NAMES[1]: [row(NAMES[1], "b", order=2, weight=60)]}
    tool, _ = group_tool(records, for_metrics=True)
    payload = tool.execute(request()).to_dict()
    assert payload["success"]
    metric = MetricTool().execute(MetricInput(operation="weekly_volume", records=payload["result"]["records"],
                                            canonical_exercise_name=NAMES[1])).to_dict()
    assert metric["success"]
    assert metric["result"]["weeks"][0]["volume_load"] == 1100


@pytest.mark.parametrize("kwargs", [{"failed": "Hammer Curl"}, {"truncated": "Hammer Curl (Dumbbell)"}])
def test_member_failure_or_truncation_fail_closed(kwargs):
    tool, _ = group_tool({}, **kwargs)
    assert not tool.execute(request()).success


def test_other_exercises_passthrough_no_added_lookup():
    tool, port = group_tool({})
    tool.execute(replace(request(), canonical_exercise_name="Bench Press (Barbell)"))
    assert len(port.calls) == 1


def test_group_scope_is_not_a_general_equipment_inference():
    policy = RuntimeExerciseRelations()
    assert policy.group_for("해머컬")["id"] == "hammer_curl"
    assert policy.group_for("케이블 해머컬") is None
    assert policy.group_for("Incline Bench Press") is None


def test_missing_group_member_requires_clarification():
    provider = FakeInterpreterProvider(draft(exercise_mention="해머컬", canonical_exercise_name=None,
                                             exercise_resolution_status="ambiguous", requested_analyses=[]))
    result = QuestionInterpreter(exercise_repository=FakeExerciseRepository([NAMES[1]]), provider=provider).interpret("내 해머컬 기록 확인해줘.")
    assert result.execution_status == "clarification_required"


def test_explicit_cable_hammer_cannot_use_group_even_if_mention_is_narrow():
    provider = FakeInterpreterProvider(draft(exercise_mention="해머컬", canonical_exercise_name=NAMES[1], requested_analyses=[]))
    result = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=provider).interpret("내 케이블 해머컬 기록 확인해줘.")
    assert result.execution_status == "clarification_required"


def test_group_lookup_through_existing_executor_and_graph():
    records = {NAMES[0]: [row(NAMES[0], "a")], NAMES[1]: [row(NAMES[1], "b", order=2)]}
    port = MemberTool(records)
    legacy, _, metric, literature, grader, recovery = build_workflow(
        literature_responses=[], grader_results=[], recovery_payloads=[])
    legacy.nodes.tool_executor._tools["query_training_log"] = port
    provider = FakeInterpreterProvider(draft(exercise_mention="해머컬", canonical_exercise_name="Hammer Curl", requested_analyses=[]))
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=provider)
    workflow = attach(legacy, component)
    pending = workflow.invoke("내 해머컬 기록 확인해줘.")
    identifier = pending["final_response"]["provenance"]["response_metadata"]["clarification_id"]
    assert port.calls == []
    state = workflow.clarify(identifier, "3")
    assert state["final_status"] == "answer_ready"
    assert state["tool_results"][0]["result"]["record_count"] == 2
    assert len(port.calls) == 2 and len(provider.calls) == 1
    assert metric.calls == literature.calls == grader.calls == recovery.calls == []
    assert state["max_retry"] == 2
    assert state["exercise_relation_policy"]["runtime_exercise_relations_sha256"]


def test_overlapping_group_blocks_downstream_metric_in_graph():
    records = {name: [row(name, name)] for name in NAMES[:2]}
    legacy, _, metric, *_ = build_workflow(literature_responses=[], grader_results=[], recovery_payloads=[])
    legacy.nodes.tool_executor._tools["query_training_log"] = MemberTool(records)
    output = draft(exercise_mention="해머컬", canonical_exercise_name="Hammer Curl", requested_analyses=[
        {"operation": "weekly_volume", "n_sessions": None, "source_text": "주간 훈련량"}])
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=FakeInterpreterProvider(output))
    workflow = attach(legacy, component)
    pending = workflow.invoke("내 해머컬 주간 훈련량을 계산해줘.")
    identifier = pending["final_response"]["provenance"]["response_metadata"]["clarification_id"]
    state = workflow.clarify(identifier, "3")
    assert state["final_status"] == "execution_failure"
    assert metric.calls == []


def test_group_does_not_resolve_unclear_period():
    output = draft(exercise_mention="해머컬", canonical_exercise_name="Hammer Curl", requested_analyses=[],
                   time_condition={"scope": "unresolved", "start_date": None, "end_date": None, "source_text": "요즘"})
    component = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=FakeInterpreterProvider(output))
    result = component.interpret("내 요즘 해머컬 기록 확인해줘.")
    assert result.execution_status == "clarification_required"
    assert result.interpretation.canonical_exercise_name is None


def test_explicit_grip_casing_uses_actual_stored_label():
    for spelling in ("Close Grip", "close Grip"):
        question = f"내 Seated Cable Row ({spelling}) 기록 확인해줘."
        output = draft(exercise_mention=f"Seated Cable Row ({spelling})", canonical_exercise_name=NAMES[3], requested_analyses=[])
        result = QuestionInterpreter(exercise_repository=FakeExerciseRepository(NAMES), provider=FakeInterpreterProvider(output)).interpret(question)
        assert result.execution_status == "ready"
        assert result.interpretation.canonical_exercise_name == NAMES[3]
