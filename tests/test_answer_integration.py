import pytest
import hashlib
from pathlib import Path

from src.answer.generator import FinalResponseLayer

from answer_test_support import FakeFinalAnswerProvider, draft_payload
from graph_test_support import (
    HYBRID_QUESTION,
    INITIAL_QUERY,
    LITERATURE_QUESTION,
    LITERATURE_SUBQUESTION,
    build_workflow,
    hybrid_tool_inputs,
    literature_hit,
    recovery_payload,
)


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_HASHES = {
    "data/evaluation/eval_dataset_v1.manifest.json": (
        "4abd207041c4dd69df73e85e6d50b9288fe9f0d9c19f1ce33d06ae62e008d972"
    ),
    "data/literature/manifests/corpus_v1.json": (
        "84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66"
    ),
    "reports/baselines/hybrid_baseline_v1/manifest.json": (
        "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
    ),
    "reports/baselines/router_baseline_v1/manifest.json": (
        "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
    ),
    "reports/baselines/grader_baseline_v1/manifest.json": (
        "e32ab0476ba7ac82b4f16768e08a4233ff3695012426a0e036587c864bf093ba"
    ),
    "reports/baselines/grader_v2_baseline/manifest.json": (
        "870764d296dd469050d4699140cf5ef0508eb8d31585214082c0d8df3b963a5d"
    ),
    "reports/baselines/grader_v2_1_baseline/manifest.json": (
        "b96fc923d4ce2fe82f6d10e2dfe26e4b8cb257aa7752bd39e2743b8b49264556"
    ),
}


def _invoke(workflow):
    return workflow.invoke(
        LITERATURE_QUESTION,
        literature_subquestion=LITERATURE_SUBQUESTION,
        initial_query=INITIAL_QUERY,
    )


def test_answer_ready_terminal_runs_final_response_node_once() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(
            literature="검색된 문헌에서 VBT 최대근력 결과를 확인했습니다.",
            chunk_ids=["initial-1"],
        )
    )
    workflow, *_ = build_workflow(
        literature_responses=[[literature_hit("initial-1", 1)]],
        grader_results=[True],
        recovery_payloads=[],
        final_response_layer=FinalResponseLayer(provider),
    )
    state = _invoke(workflow)

    assert state["final_status"] == "answer_ready"
    assert state["final_response"]["final_status"] == "answer_ready"
    assert state["final_response"]["used_literature_chunk_ids"] == ["initial-1"]
    assert len(provider.calls) == 1


def test_hybrid_graph_response_preserves_structured_and_literature_provenance() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(
            record="기록과 계산 지표를 확인했습니다.",
            literature="periodization 문헌 근거를 확인했습니다.",
            integrated="개인 관찰과 일반 문헌은 구분해서 해석해야 합니다.",
            tool_ids=["tool-result-01-query_training_log", "tool-result-02-compute_metrics"],
            chunk_ids=["periodization-1"],
        )
    )
    workflow, *_ = build_workflow(
        literature_responses=[[literature_hit("periodization-1", 1)]],
        grader_results=[True],
        recovery_payloads=[],
        final_response_layer=FinalResponseLayer(provider),
    )
    state = workflow.invoke(
        HYBRID_QUESTION,
        literature_subquestion="periodization이 최대근력에 미치는 효과는?",
        initial_query="periodization maximal strength",
        tool_inputs=hybrid_tool_inputs(),
    )

    response = state["final_response"]
    assert response["provenance"]["channel_mode"] == "hybrid"
    assert len(response["used_tool_results"]) == 2
    assert response["used_literature_chunk_ids"] == ["periodization-1"]
    assert "당신의 기록에서는" in response["answer_text"]
    assert "문헌에서는" in response["answer_text"]


def test_two_failed_recoveries_render_abstention_without_answer_provider() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(literature="호출되면 안 됩니다.", chunk_ids=["initial"])
    )
    workflow, *_ = build_workflow(
        literature_responses=[
            [literature_hit("initial", 1)],
            [literature_hit("recovery-1", 1)],
            [literature_hit("recovery-2", 1)],
        ],
        grader_results=[False, False, False],
        recovery_payloads=[
            recovery_payload("VBT trained athletes strength trial"),
            recovery_payload("VBT maximal strength trained adults review"),
        ],
        final_response_layer=FinalResponseLayer(provider),
    )
    state = _invoke(workflow)

    assert state["final_status"] == "abstain_ready"
    assert state["final_response"]["final_status"] == "abstain_ready"
    assert "추가 검색을 2회" in state["final_response"]["answer_text"]
    assert provider.calls == []
    assert len(state["fused_evidence"]) <= 10


def test_graph_execution_failure_renders_sanitized_response() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(literature="호출되면 안 됩니다.", chunk_ids=["x"])
    )
    workflow, *_ = build_workflow(
        literature_responses=[],
        grader_results=[],
        recovery_payloads=[],
        final_response_layer=FinalResponseLayer(provider),
    )
    state = workflow.invoke(HYBRID_QUESTION)

    assert state["final_status"] == "execution_failure"
    assert state["final_response"]["final_status"] == "execution_failure"
    assert "tool_execution_setup_failure" not in state["final_response"]["answer_text"]
    assert provider.calls == []


def test_final_response_node_is_in_compiled_graph() -> None:
    workflow, *_ = build_workflow(
        literature_responses=[], grader_results=[], recovery_payloads=[]
    )
    graph = workflow.graph.get_graph()
    assert "generate_final_response" in graph.nodes
    assert any(
        edge.source == "generate_final_response" and edge.target == "__end__"
        for edge in graph.edges
    )


@pytest.mark.requires_local_artifacts
def test_frozen_artifact_manifests_remain_unchanged_after_answer_layer() -> None:
    for relative_path, expected_hash in FROZEN_MANIFEST_HASHES.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected_hash
