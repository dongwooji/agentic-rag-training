"""Bounded LangGraph orchestration for the runtime Agentic RAG components."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from pydantic import BaseModel

from langgraph.graph import END, START, StateGraph

from src.agent.executor import DeterministicToolExecutor
from src.recovery.contracts import MAX_RETRY
from src.routing.deterministic import DeterministicRouter

from .nodes import (
    LiteratureToolPort,
    RecoveryAgentPort,
    RouterPort,
    RuntimeGraderPort,
    ToolInputResolverPort,
    WorkflowNodes,
)
from .state import AgenticRAGState


class FinalResponseLayerPort(Protocol):
    def respond(self, state: Mapping[str, Any]) -> Any: ...


def _response_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
    else:
        payload = value
    if not isinstance(payload, Mapping):
        raise TypeError("Final response layer must return a mapping-like result")
    return dict(payload)


class AgenticRAGWorkflow:
    """Route, execute, grade, recover, and render one terminal response."""

    def __init__(
        self,
        *,
        tool_executor: DeterministicToolExecutor,
        literature_tool: LiteratureToolPort,
        runtime_grader: RuntimeGraderPort,
        recovery_agent: RecoveryAgentPort,
        router: RouterPort | None = None,
        tool_input_resolver: ToolInputResolverPort | None = None,
        final_response_layer: FinalResponseLayerPort | None = None,
    ) -> None:
        self.final_response_layer = final_response_layer
        self.nodes = WorkflowNodes(
            router=router or DeterministicRouter(),
            tool_executor=tool_executor,
            literature_tool=literature_tool,
            runtime_grader=runtime_grader,
            recovery_agent=recovery_agent,
            tool_input_resolver=tool_input_resolver,
        )
        builder = StateGraph(AgenticRAGState)
        builder.add_node("route_question", self.nodes.route_question)
        builder.add_node("resolve_tool_inputs", self.nodes.resolve_tool_inputs)
        builder.add_node("execute_initial_tools", self.nodes.execute_initial_tools)
        builder.add_node("grade_evidence", self.nodes.grade_evidence)
        builder.add_node("generate_recovery_query", self.nodes.generate_recovery_query)
        builder.add_node(
            "search_recovery_literature",
            self.nodes.search_recovery_literature,
        )
        builder.add_node("fuse_evidence", self.nodes.fuse_evidence)
        builder.add_node("generate_final_response", self.generate_final_response)

        builder.add_edge(START, "route_question")
        builder.add_conditional_edges(
            "route_question",
            self._after_route,
            {"resolve": "resolve_tool_inputs", "end": "generate_final_response"},
        )
        builder.add_conditional_edges(
            "resolve_tool_inputs",
            self._after_tool_input_resolution,
            {"execute": "execute_initial_tools", "end": "generate_final_response"},
        )
        builder.add_conditional_edges(
            "execute_initial_tools",
            self._after_initial_tools,
            {"grade": "grade_evidence", "end": "generate_final_response"},
        )
        builder.add_conditional_edges(
            "grade_evidence",
            self._after_grade,
            {"recover": "generate_recovery_query", "end": "generate_final_response"},
        )
        builder.add_conditional_edges(
            "generate_recovery_query",
            self._after_recovery_query,
            {"search": "search_recovery_literature", "end": "generate_final_response"},
        )
        builder.add_conditional_edges(
            "search_recovery_literature",
            self._after_recovery_search,
            {"fuse": "fuse_evidence", "end": "generate_final_response"},
        )
        builder.add_conditional_edges(
            "fuse_evidence",
            self._after_fusion,
            {"grade": "grade_evidence", "end": "generate_final_response"},
        )
        builder.add_edge("generate_final_response", END)
        self.graph = builder.compile()

    def generate_final_response(
        self,
        state: AgenticRAGState,
    ) -> AgenticRAGState:
        if self.final_response_layer is None:
            return {"final_response": None}
        try:
            payload = _response_payload(self.final_response_layer.respond(state))
            status = str(payload.get("final_status") or "execution_failure")
            if status not in {"answer_ready", "abstain_ready", "execution_failure"}:
                raise ValueError("Final response returned an unknown status")
            return {"final_response": payload, "final_status": status}
        except Exception as exc:
            from src.answer.contracts import (
                ChannelMode,
                FinalAnswerErrorCode,
                FinalAnswerInput,
                FinalResponseStatus,
            )
            from src.answer.abstention import build_execution_failure_response

            fallback_input = FinalAnswerInput(
                original_question=str(state.get("original_question") or "처리할 수 없는 질문"),
                task_type=str(state.get("task_type") or "unknown"),
                channel_mode=ChannelMode.NO_EVIDENCE,
            )
            response = build_execution_failure_response(
                input_data=fallback_input,
                graph_terminal_status=FinalResponseStatus.EXECUTION_FAILURE,
                graph_errors=[
                    dict(item)
                    for item in state.get("errors", [])
                    if isinstance(item, Mapping)
                ],
                error_code=FinalAnswerErrorCode.GRAPH_EXECUTION_FAILURE,
                internal_error_detail=f"{type(exc).__name__}: {exc}",
            )
            return {
                "final_response": response.model_dump(mode="json"),
                "final_status": "execution_failure",
            }

    @staticmethod
    def _terminal(state: AgenticRAGState) -> bool:
        return state.get("final_status") in {
            "answer_ready",
            "abstain_ready",
            "execution_failure",
        }

    @classmethod
    def _after_route(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "resolve"

    @classmethod
    def _after_tool_input_resolution(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "execute"

    @classmethod
    def _after_initial_tools(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "grade"

    @classmethod
    def _after_grade(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "recover"

    @classmethod
    def _after_recovery_query(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "search"

    @classmethod
    def _after_recovery_search(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "fuse"

    @classmethod
    def _after_fusion(cls, state: AgenticRAGState) -> str:
        return "end" if cls._terminal(state) else "grade"

    def invoke(
        self,
        question: str,
        *,
        literature_subquestion: str | None = None,
        initial_query: str | None = None,
        tool_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> AgenticRAGState:
        if not isinstance(question, str) or not question.strip():
            invalid_state: AgenticRAGState = {
                "original_question": question if isinstance(question, str) else "",
                "route": {},
                "task_type": "invalid",
                "selected_tools": [],
                "execution_order": [],
                "tool_inputs": {},
                "tool_input_resolution": None,
                "tool_results": [],
                "literature_subquestion": "",
                "initial_query": "",
                "current_query": "",
                "query_history": [],
                "literature_evidence": [],
                "recovery_evidence": [],
                "fused_evidence": [],
                "fusion_history": [],
                "grader_result": None,
                "recovery_result": None,
                "missing_components": [],
                "retry_count": 0,
                "max_retry": MAX_RETRY,
                "errors": [
                    {
                        "stage": "input",
                        "code": "invalid_question",
                        "message": "question must be non-empty text",
                    }
                ],
                "final_status": "execution_failure",
                "final_response": None,
            }
            if self.final_response_layer is not None:
                invalid_state.update(self.generate_final_response(invalid_state))
            return invalid_state

        original = question.strip()
        subquestion = (
            literature_subquestion.strip()
            if isinstance(literature_subquestion, str)
            and literature_subquestion.strip()
            else ""
        )
        query = (
            initial_query.strip()
            if isinstance(initial_query, str) and initial_query.strip()
            else subquestion or original
        )
        initial_state: AgenticRAGState = {
            "original_question": original,
            "route": {},
            "task_type": "",
            "selected_tools": [],
            "execution_order": [],
            "tool_inputs": {
                str(key): dict(value) for key, value in (tool_inputs or {}).items()
            },
            "tool_input_resolution": None,
            "tool_results": [],
            "literature_subquestion": subquestion,
            "initial_query": query,
            "current_query": query,
            "query_history": [],
            "literature_evidence": [],
            "recovery_evidence": [],
            "fused_evidence": [],
            "fusion_history": [],
            "grader_result": None,
            "recovery_result": None,
            "missing_components": [],
            "retry_count": 0,
            "max_retry": MAX_RETRY,
            "errors": [],
            "final_response": None,
        }
        return self.graph.invoke(initial_state, config={"recursion_limit": 32})
