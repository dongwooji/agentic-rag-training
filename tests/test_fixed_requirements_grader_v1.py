"""Synthetic-only contracts; no corpus, Gold, provider or evaluation inputs."""
import json
from types import SimpleNamespace

import pytest

from src.grading.fixed_requirements_v1.contracts import (
    FixedGraderInput, RequirementSet, Reassessment, select_question,
)
from src.grading.fixed_requirements_v1.grader import FixedRequirementsGrader
from src.grading.fixed_requirements_v1.provider import FixedRequirementsProvider
from src.grading.runtime_contracts import RuntimeTokenUsage
from src.grading.runtime_provider import RuntimeProviderResult

QUESTION = "휴식 길이의 영향과 적용 범위에 대해 문헌은 무엇을 말하나요?"


def chunk(cid="s1", rank=1):
    return dict(chunk_id=cid,paper_id="synthetic-paper",title="Synthetic study",section="Results",
                text="The shorter interval did not improve the measured outcome in this adult sample.",
                retrieval=dict(rank=rank),corpus_version="synthetic")


def data(**extra):
    return FixedGraderInput(question=QUESTION,literature_subquestion=QUESTION,question_source="step2",
                            supplied_chunks=[chunk()],**extra)


def component(cid="C1", topic="휴식 길이의 영향", status="supported", ids=None, **extra):
    return dict(component_id=cid,topic=topic,direction="open",source_span="휴식 길이의 영향",
                direction_span=None,status=status,supporting_chunk_ids=["s1"] if ids is None else ids,**extra)


class Provider:
    model="synthetic-model"
    prompt_sha256="a"*64
    config_sha256="b"*64

    def __init__(self,*responses):self.responses=list(responses);self.calls=[]
    def invoke(self, value):
        self.calls.append(value)
        response=self.responses.pop(0)
        if isinstance(response,Exception):raise response
        return RuntimeProviderResult(json.dumps(response),self.model,self.prompt_sha256,self.config_sha256,
            response_id=None,latency_ms=1.0,token_usage=RuntimeTokenUsage(),response_metadata={})


def initial(provider=None):
    p=provider or Provider(dict(components=[component()]))
    return FixedRequirementsGrader(p).grade_fixed(data()),p


def test_neutral_question_and_first_grade_share_one_call():
    grade,p=initial()
    assert len(p.calls)==1
    assert grade.evidence_sufficient
    assert grade.components[0].requirement=="휴식 길이의 영향에 대해 문헌은 무엇을 말하는가?"
    assert grade.requirements.components[0].direction=="open"
    assert RequirementSet.model_validate(grade.requirements.model_dump()).sha256==grade.requirements.sha256


@pytest.mark.parametrize("direction",["supporting","opposing"])
def test_explicit_direction_kept_with_original_span(direction):
    q="이 주장을 지지하거나 반대하는 근거를 찾아줘"
    c=component();c.update(direction=direction,direction_span="지지하거나 반대하는 근거")
    p=Provider(dict(components=[c]))
    grade=FixedRequirementsGrader(p).grade_fixed(data().model_copy(update={"question":q}))
    assert grade.evidence_sufficient
    assert grade.requirements.components[0].direction==direction


@pytest.mark.parametrize("change",[
    {"source_span":"원문에 없는 주제"},
    {"direction":"supporting","direction_span":None},
    {"direction":"opposing","direction_span":"없는 명시 요청"},
    {"direction_span":"휴식"},
    {"component_id":"C2"},
])
def test_invalid_initial_requirements_fail_without_retry(change):
    c=component();c.update(change);p=Provider(dict(components=[c]))
    result=FixedRequirementsGrader(p).grade_fixed(data())
    assert result.execution_status=="failure"
    assert not result.evidence_sufficient
    assert len(p.calls)==1


def test_all_fixed_components_reassessed_on_current_chunks_without_carry_forward():
    c2=component("C2",topic="적용 범위");c2["source_span"]="적용 범위"
    p=Provider(dict(components=[component(),c2]),dict(components=[
        dict(component_id="C1",status="supported",supporting_chunk_ids=["s1"]),
        dict(component_id="C2",status="supported",supporting_chunk_ids=["s2"])]))
    grader=FixedRequirementsGrader(p);first=grader.grade_fixed(data())
    follow=data(requirements=first.requirements).model_copy(update={"supplied_chunks":[type(data().supplied_chunks[0])(**chunk("s2"))]})
    second=grader.grade_fixed(follow)
    assert second.requirements.sha256==first.requirements.sha256
    assert second.missing_component_ids==["C1"]
    assert second.components[1].status=="supported"
    assert len(p.calls)==2


@pytest.mark.parametrize("items",[
    [],
    [dict(component_id="C2",status="supported",supporting_chunk_ids=["s1"])],
    [dict(component_id="C1",status="supported",supporting_chunk_ids=["s1"])]*2,
    [dict(component_id="C1",status="supported",supporting_chunk_ids=["s1"],requirement="변경 시도")],
])
def test_reassessment_cannot_omit_add_or_rewrite_requirements(items):
    first,_=initial();p=Provider(dict(components=items))
    result=FixedRequirementsGrader(p).grade_fixed(data(requirements=first.requirements))
    assert result.execution_status=="failure"
    assert result.requirements.sha256==first.requirements.sha256
    assert len(p.calls)==1


def test_in_memory_mutation_rejected_before_provider_call():
    first,_=initial();d=data(requirements=first.requirements)
    d.requirements.components[0].topic="바뀐 주제"
    p=Provider();result=FixedRequirementsGrader(p).grade_fixed(d)
    assert result.error_code=="input_failure"
    assert not p.calls


def test_other_question_cannot_reuse_requirements():
    first,_=initial()
    with pytest.raises(ValueError):
        FixedGraderInput(**{**data().model_dump(),"question":"다른 질문", "requirements":first.requirements})


def test_missing_and_unknown_chunks_are_not_sufficient():
    for c in [component(status="missing",ids=[]),component(ids=["not-supplied"])]:
        grade,_=initial(Provider(dict(components=[c])))
        assert not grade.evidence_sufficient
        assert grade.missing_component_ids==["C1"]


def test_provider_failure_is_separate_from_semantic_missing():
    p=Provider(RuntimeError("synthetic provider failure"));g=FixedRequirementsGrader(p).grade_fixed(data())
    assert g.execution_status=="failure" and g.components==[]
    assert g.error_code=="provider_failure" and len(p.calls)==1


def test_question_source_selection_and_validation_gate():
    state=dict(first_query_selection=dict(step2=dict(validation_status="passed",actual_query=QUESTION)),
               requirement_context=dict(validated=True,literature_subquestion="별도 질문"))
    assert select_question(state)==(QUESTION,"step2")
    state["first_query_selection"]["step2"]["validation_status"]="failed"
    assert select_question(state)==("별도 질문","phase_a")
    state["requirement_context"]["validated"]=False
    with pytest.raises(ValueError):select_question(state)


def test_provider_uses_initial_then_id_only_schema_in_single_calls():
    calls=[]
    response=SimpleNamespace(output_text=json.dumps(dict(components=[component()])),id="synthetic",model="synthetic",usage=None)
    client=SimpleNamespace(responses=SimpleNamespace(create=lambda **kw:(calls.append(kw) or response)))
    p=FixedRequirementsProvider(client=client)
    g=FixedRequirementsGrader(p).grade_fixed(data())
    assert g.execution_status=="completed"
    assert calls[0]["text"]["format"]["name"]=="InitialAssessment"
    p.invoke(data(requirements=g.requirements))
    assert calls[1]["text"]["format"]["name"]=="Reassessment"
    assert set(Reassessment.model_json_schema()["$defs"]["ReassessmentComponent"]["properties"])=={"component_id","status","supporting_chunk_ids"}
    assert calls[1]["max_output_tokens"]==1800


def test_graph_opt_in_records_fixed_state_and_trace_without_changing_old_shape():
    from src.graph.nodes import WorkflowNodes
    from src.api.debug_trace import build_debug_trace
    from graph_test_support import literature_hit
    p=Provider(dict(components=[component()]),dict(components=[dict(component_id="C1",status="missing",supporting_chunk_ids=[])]))
    node=object.__new__(WorkflowNodes);node.runtime_grader=FixedRequirementsGrader(p)
    state=dict(original_question=QUESTION,literature_subquestion="개인 기록의 계산 요청",first_query_selection={"step2":{"validation_status":"passed","actual_query":QUESTION}},
        fused_evidence=[literature_hit("s1",1)],grader_history=[],retry_count=0,max_retry=2)
    state.update(node.grade_evidence(state))
    assert state["final_status"]=="answer_ready"
    assert state["literature_subquestion"]==QUESTION
    assert state["literature_requirements"]["sha256"]
    trace=build_debug_trace(state)
    assert trace["grader_requirements_v1"]["sha256"]==state["literature_requirements"]["sha256"]
    state.pop("final_status");state["retry_count"]=2
    state.update(node.grade_evidence(state))
    assert state["final_status"]=="abstain_ready"
    assert p.calls[1].requirements is not None
    assert "grader_requirements_v1" not in build_debug_trace({})
