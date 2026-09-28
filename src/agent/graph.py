"""Fixed, acyclic LangGraph workflow for typed planning and execution."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .contracts import PlannerDraft
from .executor import DeterministicToolExecutor
from .planner import PlannerBackend


class AgentState(TypedDict, total=False):
    question: str
    raw_plan: dict[str, Any] | None
    plan: dict[str, Any] | None
    selected_tools: list[str]
    execution_order: list[str]
    tool_inputs: dict[str, dict[str, Any]]
    tool_results: list[dict[str, Any]]
    errors: list[dict[str, str]]
    planner_metrics: dict[str, Any]
    final_status: str


class PlanningAgentWorkflow:
    """Question -> LLM plan -> validation -> deterministic execution -> status."""

    def __init__(
        self,
        *,
        backend: PlannerBackend,
        executor: DeterministicToolExecutor | None = None,
        execute_tools: bool = False,
    ) -> None:
        self.backend = backend
        self.executor = executor or DeterministicToolExecutor()
        self.execute_tools = execute_tools
        builder = StateGraph(AgentState)
        builder.add_node("planner", self._planner)
        builder.add_node("validate_plan", self._validate_plan)
        builder.add_node("execute_plan", self._execute_plan)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "planner")
        builder.add_edge("planner", "validate_plan")
        builder.add_edge("validate_plan", "execute_plan")
        builder.add_edge("execute_plan", "finalize")
        builder.add_edge("finalize", END)
        self.graph = builder.compile()

    def _planner(self, state: AgentState) -> AgentState:
        result = self.backend.invoke(state["question"])
        errors = list(state.get("errors", []))
        if result.error:
            errors.append({"stage": "planner", "message": result.error})
        return {
            "raw_plan": result.raw_plan,
            "errors": errors,
            "planner_metrics": {
                "model": result.model,
                "response_id": result.response_id,
                "latency_ms": result.latency_ms,
                "usage": result.usage.model_dump(mode="json"),
            },
        }

    @staticmethod
    def _validate_plan(state: AgentState) -> AgentState:
        errors = list(state.get("errors", []))
        raw_plan = state.get("raw_plan")
        if raw_plan is None:
            if not errors:
                errors.append(
                    {"stage": "validation", "message": "planner returned no plan"}
                )
            return {
                "plan": None,
                "selected_tools": [],
                "execution_order": [],
                "tool_inputs": {},
                "errors": errors,
            }
        try:
            plan = PlannerDraft.model_validate(raw_plan)
        except ValidationError as exc:
            errors.append(
                {"stage": "validation", "message": str(exc)}
            )
            return {
                "plan": None,
                "selected_tools": [],
                "execution_order": [],
                "tool_inputs": {},
                "errors": errors,
            }
        return {
            "plan": plan.model_dump(mode="json"),
            "selected_tools": plan.selected_tools,
            "execution_order": plan.execution_order,
            "tool_inputs": plan.tool_inputs,
            "errors": errors,
        }

    def _execute_plan(self, state: AgentState) -> AgentState:
        raw_plan = state.get("plan")
        if raw_plan is None:
            return {"tool_results": []}
        plan = PlannerDraft.model_validate(raw_plan)
        results = self.executor.execute(plan, enabled=self.execute_tools)
        errors = list(state.get("errors", []))
        for result in results:
            if result["status"] in {
                "configuration_error",
                "dependency_error",
                "execution_error",
                "failure",
            }:
                errors.append(
                    {
                        "stage": "execution",
                        "message": f"{result['tool']}: {result.get('error')}",
                    }
                )
        return {"tool_results": results, "errors": errors}

    @staticmethod
    def _finalize(state: AgentState) -> AgentState:
        errors = state.get("errors", [])
        plan_value = state.get("plan")
        if plan_value is None:
            stage = errors[0]["stage"] if errors else "validation"
            status = "planner_error" if stage == "planner" else "validation_error"
            return {"final_status": status}
        plan = PlannerDraft.model_validate(plan_value)
        if any(item["stage"] == "execution" for item in errors):
            status = "execution_error"
        elif plan.task_type == "unsupported":
            status = "unsupported"
        elif plan.task_type == "ambiguous":
            status = "ambiguous"
        elif any(
            item.get("status") == "not_executed_plan_only"
            for item in state.get("tool_results", [])
        ):
            status = "planned"
        else:
            status = "executed"
        return {"final_status": status}

    def invoke(self, question: str) -> AgentState:
        if not isinstance(question, str) or not question.strip():
            return {
                "question": question if isinstance(question, str) else "",
                "raw_plan": None,
                "plan": None,
                "selected_tools": [],
                "execution_order": [],
                "tool_inputs": {},
                "tool_results": [],
                "errors": [
                    {
                        "stage": "input",
                        "message": "question must be a non-empty string",
                    }
                ],
                "planner_metrics": {},
                "final_status": "invalid_input",
            }
        initial: AgentState = {
            "question": question.strip(),
            "errors": [],
            "tool_results": [],
        }
        return self.graph.invoke(initial)
