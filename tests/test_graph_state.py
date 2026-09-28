from typing import get_type_hints

from src.graph.state import AgenticRAGState

from graph_test_support import build_workflow


def test_graph_state_contains_required_orchestration_fields() -> None:
    fields = set(get_type_hints(AgenticRAGState))
    assert {
        "original_question",
        "route",
        "task_type",
        "tool_input_resolution",
        "tool_results",
        "literature_subquestion",
        "initial_query",
        "current_query",
        "query_history",
        "literature_evidence",
        "fused_evidence",
        "grader_result",
        "missing_components",
        "retry_count",
        "max_retry",
        "errors",
        "final_status",
    } <= fields


def test_graph_contains_bounded_recovery_cycle_and_terminal_edges() -> None:
    workflow, *_ = build_workflow(
        literature_responses=[],
        grader_results=[],
        recovery_payloads=[],
    )
    graph = workflow.graph.get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "route_question") in edges
    assert ("route_question", "resolve_tool_inputs") in edges
    assert ("resolve_tool_inputs", "execute_initial_tools") in edges
    assert ("execute_initial_tools", "grade_evidence") in edges
    assert ("grade_evidence", "generate_recovery_query") in edges
    assert ("generate_recovery_query", "search_recovery_literature") in edges
    assert ("search_recovery_literature", "fuse_evidence") in edges
    assert ("fuse_evidence", "grade_evidence") in edges
    assert any(target == "__end__" for _, target in edges)


def test_malformed_question_returns_structured_execution_failure() -> None:
    workflow, *_ = build_workflow(
        literature_responses=[],
        grader_results=[],
        recovery_payloads=[],
    )
    state = workflow.invoke("   ")
    assert state["final_status"] == "execution_failure"
    assert state["errors"] == [
        {
            "stage": "input",
            "code": "invalid_question",
            "message": "question must be non-empty text",
        }
    ]
    assert state["retry_count"] == 0
