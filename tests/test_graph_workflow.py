from graph_test_support import (
    HYBRID_QUESTION,
    INITIAL_QUERY,
    LITERATURE_QUESTION,
    LITERATURE_SUBQUESTION,
    build_workflow,
    hybrid_tool_inputs,
    literature_hit,
)


def test_initial_sufficient_evidence_reaches_answer_ready_without_recovery() -> None:
    initial = [literature_hit(f"initial-{rank}", rank) for rank in range(1, 4)]
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[initial],
        grader_results=[True],
        recovery_payloads=[],
    )
    state = workflow.invoke(
        LITERATURE_QUESTION,
        literature_subquestion=LITERATURE_SUBQUESTION,
        initial_query=INITIAL_QUERY,
    )

    assert state["final_status"] == "answer_ready"
    assert state["retry_count"] == 0
    assert state["query_history"] == [INITIAL_QUERY]
    assert len(literature.calls) == 1
    assert len(grader.calls) == 1
    assert recovery.calls == []
    assert state["fusion_history"] == []


def test_hybrid_preserves_log_metric_results_and_grades_literature_only() -> None:
    initial = [literature_hit("initial", 1)]
    workflow, training, metric, _, grader, _ = build_workflow(
        literature_responses=[initial],
        grader_results=[True],
        recovery_payloads=[],
    )
    state = workflow.invoke(
        HYBRID_QUESTION,
        literature_subquestion="periodization이 최대근력에 미치는 효과는?",
        initial_query="periodization maximal strength",
        tool_inputs=hybrid_tool_inputs(),
    )

    assert state["final_status"] == "answer_ready"
    assert state["task_type"] == "hybrid"
    assert [item["tool"] for item in state["tool_results"]] == [
        "query_training_log",
        "compute_metrics",
        "search_literature",
    ]
    assert state["tool_results"][0]["result"]["records"][0]["set_id"] == "set-1"
    assert state["tool_results"][1]["result"]["e1rm"] == 116.6667
    assert len(training.calls) == len(metric.calls) == 1
    assert grader.calls[0].evidence_channel == "literature_only"
    assert not hasattr(grader.calls[0], "log_evidence")


def test_empty_initial_literature_ends_safely_without_grader_or_recovery() -> None:
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[[]],
        grader_results=[],
        recovery_payloads=[],
    )
    state = workflow.invoke(
        LITERATURE_QUESTION,
        literature_subquestion=LITERATURE_SUBQUESTION,
        initial_query=INITIAL_QUERY,
    )

    assert state["final_status"] == "abstain_ready"
    assert state["retry_count"] == 0
    assert len(literature.calls) == 1
    assert grader.calls == []
    assert recovery.calls == []
    assert state["errors"][-1]["code"] == "empty_initial_literature"


def test_missing_typed_log_inputs_is_structured_execution_failure() -> None:
    workflow, *_ = build_workflow(
        literature_responses=[],
        grader_results=[],
        recovery_payloads=[],
    )
    state = workflow.invoke(HYBRID_QUESTION)
    assert state["final_status"] == "execution_failure"
    assert state["errors"][-1]["code"] == "tool_execution_setup_failure"
