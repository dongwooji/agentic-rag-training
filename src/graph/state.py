"""Shared state contract for the runtime Agentic RAG LangGraph."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


FinalStatus = Literal[
    "answer_ready",
    "abstain_ready",
    "execution_failure",
]


class GraphError(TypedDict):
    stage: str
    code: str
    message: str


class AgenticRAGState(TypedDict, total=False):
    original_question: str
    route: dict[str, Any]
    task_type: str
    selected_tools: list[str]
    execution_order: list[str]
    tool_inputs: dict[str, dict[str, Any]]
    tool_input_resolution: dict[str, Any] | None
    tool_results: list[dict[str, Any]]
    literature_subquestion: str
    initial_query: str
    current_query: str
    first_query_selection: dict[str, Any]
    initial_bm25_query: str
    query_history: list[str]
    literature_evidence: list[dict[str, Any]]
    recovery_evidence: list[dict[str, Any]]
    fused_evidence: list[dict[str, Any]]
    fusion_history: list[dict[str, Any]]
    grader_result: dict[str, Any] | None
    recovery_result: dict[str, Any] | None
    missing_components: list[dict[str, str]]
    retry_count: int
    max_retry: int
    errors: list[GraphError]
    final_status: FinalStatus
    final_response: dict[str, Any] | None
