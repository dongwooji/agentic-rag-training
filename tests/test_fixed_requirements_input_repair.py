"""Fresh synthetic regressions, never frozen evaluation questions or Gold."""
import pytest

from src.grading.fixed_requirements_v1.contracts import select_question
from src.grading.fixed_requirements_v1.grader import FixedRequirementsGrader
from test_fixed_requirements_grader_v1 import Provider, chunk, component, data


def fallback_state():
    question = "내 최근 기록을 요약하고 휴식 간격의 효과도 설명해 줘."
    return dict(original_question=question, first_query_selection=dict(step2=dict(
        input_query=question, actual_query=question, generated_query="검증에 실패한 별도 문장",
        query_source="original", validation_status="failed", fallback=True)))


def test_original_failure_reproduction_fallback_is_valid_retrieval_but_rejected_by_v1():
    with pytest.raises(ValueError, match="no validated"):
        select_question(fallback_state())


def test_original_failure_reproduction_paraphrased_source_rejected_by_v1():
    c = component()
    c["source_span"] = "휴식 간격에 따른 효과"
    grade = FixedRequirementsGrader(Provider(dict(components=[c]))).grade_fixed(data())
    assert grade.error_code == "schema_failure"


def test_fallback_input_connects_without_blessing_failed_generated_text():
    from src.grading.fixed_requirements_v1_1.contracts import select_question as fixed_select
    state = fallback_state()
    assert fixed_select(state) == (state["original_question"], "original_fallback")
    state["first_query_selection"]["step2"]["actual_query"] = "원문이 아닌 임의 텍스트"
    with pytest.raises(ValueError):
        fixed_select(state)


def test_source_reference_resolves_to_exact_code_owned_span():
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    from src.grading.fixed_requirements_v1_1.contracts import FixedGraderInput
    c = dict(component_id="C1", topic="휴식 간격의 효과", direction="open", source_id="L1",
             direction_source_id=None, status="supported", supporting_chunk_ids=["s1"])
    p = Provider(dict(components=[c]))
    value = FixedGraderInput(**data().model_dump())
    grade = Repaired(p).grade_fixed(value)
    assert grade.execution_status == "completed"
    assert grade.requirements.components[0].source_span in value.literature_subquestion
    assert grade.requirements.components[0].direction == "open"
    assert len(p.calls) == 1


@pytest.mark.parametrize("field,value", [("source_id", "L99"), ("direction_source_id", "Q99")])
def test_unknown_or_unjustified_reference_still_fails(field, value):
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    from src.grading.fixed_requirements_v1_1.contracts import FixedGraderInput
    c = dict(component_id="C1", topic="휴식", direction="open", source_id="L1",
             direction_source_id=None, status="supported", supporting_chunk_ids=["s1"])
    c[field] = value
    grade = Repaired(Provider(dict(components=[c]))).grade_fixed(FixedGraderInput(**data().model_dump()))
    assert grade.execution_status == "failure"
    assert grade.error_code == "schema_failure"


def ref_component(**updates):
    value = dict(component_id="C1", topic="휴식 간격", direction="open", source_id="L1",
                 direction_source_id=None, status="supported", supporting_chunk_ids=["s1"])
    value.update(updates)
    return value


def repaired_data():
    from src.grading.fixed_requirements_v1_1.contracts import FixedGraderInput
    return FixedGraderInput(**data().model_dump())


def test_real_query_generation_fallback_contract_reaches_graph_grader():
    from src.retrieval.literature_query import generate_korean, QueryProviderResult
    from src.graph.nodes import WorkflowNodes
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    from graph_test_support import literature_hit
    state = fallback_state()
    class Generator:
        def invoke(self, _):
            return QueryProviderResult(dict(has_personal_context=True, literature_query="2019년 내 기록"))
    selection = generate_korean(Generator(), state["original_question"])
    assert selection["validation_status"] == "failed"
    state.update(first_query_selection=dict(step2=selection), literature_subquestion="기록과 문헌 요청",
                 fused_evidence=[literature_hit("s1", 1)], grader_history=[], retry_count=0, max_retry=2)
    node = object.__new__(WorkflowNodes)
    p = Provider(dict(components=[ref_component()]))
    node.runtime_grader = Repaired(p)
    result = node.grade_evidence(state)
    assert result["final_status"] == "answer_ready"
    assert result["literature_requirements"]["question_source"] == "original_fallback"
    assert result["literature_requirements"]["literature_question"] == state["original_question"]
    assert selection["validation_status"] == "failed"  # Rejected rewrite stays rejected.
    assert len(p.calls) == 1


def test_validated_phase_a_precedes_original_fallback_and_missing_source_still_rejected():
    from src.grading.fixed_requirements_v1_1.contracts import select_question as fixed_select
    state = fallback_state()
    state["requirement_context"] = dict(validated=True, literature_subquestion="반복 간격의 효과")
    assert fixed_select(state) == ("반복 간격의 효과", "phase_a")
    with pytest.raises(ValueError):
        fixed_select({})


def test_repaired_reassessment_freezes_all_requirements_and_membership():
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    from src.grading.fixed_requirements_v1_1.contracts import FixedGraderInput
    p = Provider(dict(components=[ref_component()]), dict(components=[
        dict(component_id="C1", status="supported", supporting_chunk_ids=["absent"])]))
    grader = Repaired(p)
    first = grader.grade_fixed(repaired_data())
    second = grader.grade_fixed(FixedGraderInput(**repaired_data().model_dump(exclude={"requirements"}), requirements=first.requirements))
    assert second.requirements.sha256 == first.requirements.sha256
    assert second.missing_component_ids == ["C1"]
    assert not second.evidence_sufficient


@pytest.mark.parametrize("items", [[], [dict(component_id="C2", status="missing", supporting_chunk_ids=[])],
    [dict(component_id="C1", status="missing", supporting_chunk_ids=[])] * 2])
def test_repaired_reassessment_cannot_omit_rename_or_duplicate(items):
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    from src.grading.fixed_requirements_v1_1.contracts import FixedGraderInput
    p = Provider(dict(components=[ref_component()]), dict(components=items))
    grader = Repaired(p); first = grader.grade_fixed(repaired_data())
    follow = FixedGraderInput(**repaired_data().model_dump(exclude={"requirements"}), requirements=first.requirements)
    result = grader.grade_fixed(follow)
    assert result.execution_status == "failure"
    assert result.requirements.sha256 == first.requirements.sha256


def test_repaired_provider_supplies_catalog_and_accepts_reference_not_copied_text():
    import json
    from types import SimpleNamespace
    from src.grading.fixed_requirements_v1_1.provider import FixedRequirementsProvider
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    calls = []
    response = SimpleNamespace(output_text=json.dumps(dict(components=[ref_component()])), id="mock", model="mock", usage=None)
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: (calls.append(kw) or response)))
    result = Repaired(FixedRequirementsProvider(client=client)).grade_fixed(repaired_data())
    assert result.execution_status == "completed"
    wire = json.loads(calls[0]["input"])
    assert wire["literature_source_catalog"]["L1"] == repaired_data().literature_subquestion
    fields = calls[0]["text"]["format"]["schema"]["$defs"]["ReferencedComponent"]["properties"]
    assert "source_id" in fields and "source_span" not in fields
    assert len(calls) == 1


@pytest.mark.parametrize("debug", [False, True])
def test_new_requirements_trace_serializes_through_rest_boundary(debug):
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    from src.api.dependencies import get_workflow
    from src.grading.fixed_requirements_v1_1.grader import FixedRequirementsGrader as Repaired
    from test_api_query import _state, FakeWorkflow
    grade = Repaired(Provider(dict(components=[ref_component()]))).grade_fixed(repaired_data())
    state = _state("answer_ready")
    state["literature_requirements"] = grade.requirements.model_dump(mode="json")
    app = create_app()
    app.dependency_overrides[get_workflow] = lambda: FakeWorkflow(state)
    with TestClient(app) as client:
        response = client.post("/query", json=dict(question="합성 문헌 질문", debug=debug))
    assert response.status_code == 200
    body = response.json()
    if debug:
        block = body["response_metadata"]["trace"]["grader_requirements_v1"]
        assert block["version"] == "runtime_evidence_grader_fixed_requirements_v1_1"
        assert block["components"][0]["source_span"] == repaired_data().literature_subquestion
    else:
        assert "trace" not in (body.get("response_metadata") or {})
