"""Opt-in Phase A entry adapter for the unchanged single-metric LangGraph.

Per-request fixed routes avoid shared mutable interpretation state. The legacy
ToolInputResolver is never installed in this path. No batch execution exists.
"""

from src.agent.executor import DeterministicToolExecutor
from src.answer.abstention import build_execution_failure_response
from src.answer.contracts import ChannelMode, FinalAnswerInput, FinalResponse, FinalResponseProvenance, FinalResponseStatus
from src.interpretation.interpreter import QuestionInterpreter
from src.routing.interpretation_router import InterpretationRouter
from src.recovery.contracts import MAX_RETRY
from src.tools.contracts import ToolError, ToolErrorCode, failure_response
from .interpretation_binding import bind_interpretation
from .workflow import AgenticRAGWorkflow


class _FixedRoute:
    def __init__(self, question, plan):
        self.question, self.plan = question, plan

    def route(self, request):
        if request.question != self.question:
            raise ValueError("route_question_mismatch")
        return self.plan


class _CompleteTrainingResult:
    """Do not calculate all-period metrics on a limit-truncated result."""
    def __init__(self, tool):
        self.tool = tool

    def execute(self, request):
        response = self.tool.execute(request)
        payload = response.to_dict() if hasattr(response, "to_dict") else response
        if (payload.get("result") or {}).get("truncated_at_limit"):
            return failure_response(operation=request.operation, error=ToolError(ToolErrorCode.INVALID_INPUT,
                                     "조회 한도에 도달하여 전체 분석 범위를 확인할 수 없습니다. 기간을 좁혀주세요."),
                                    provenance=payload.get("provenance", {}))
        return response


class InterpretedWorkflow:
    def __init__(self, *, interpreter: QuestionInterpreter, tool_executor, literature_tool,
                 runtime_grader, recovery_agent, final_response_layer=None):
        self.interpreter = interpreter
        self.router = InterpretationRouter()
        self.tool_executor = tool_executor
        self.literature_tool = literature_tool
        self.runtime_grader = runtime_grader
        self.recovery_agent = recovery_agent
        self.final_response_layer = final_response_layer

    def invoke(self, question: str):
        result = self.interpreter.interpret(question)
        base = dict(original_question=question if isinstance(question, str) else "", interpretation_status=result.execution_status,
                    question_interpretation=result.interpretation.model_dump(mode="json") if result.interpretation else None,
                    interpretation_trace=result.model_dump(mode="json"), tool_results=[], errors=[], retry_count=0, max_retry=MAX_RETRY,
                    literature_evidence=[], fused_evidence=[], query_history=[], missing_components=[])
        if result.execution_status == "failure":
            base.update(route={"route": "invalid", "task_type": "invalid"}, task_type="invalid", selected_tools=[], execution_order=[])
            return self._failure(base, result.error_code or "interpretation_failure")
        interpretation = result.interpretation
        plan = self.router.route(interpretation)
        base.update(route=plan.to_dict(), task_type=plan.task_type.value, selected_tools=list(plan.selected_tools), execution_order=list(plan.execution_order))
        if interpretation.clarification_required:
            reasons = list(dict.fromkeys(x.reason for x in interpretation.unresolved_fields))
            response = FinalResponse(
                final_status=FinalResponseStatus.ABSTAIN_READY,
                answer_text="실행 전에 다음 조건을 확인해주세요:\n" + "\n".join(f"- {x}" for x in reasons),
                limitations=["필요한 조건을 추측하지 않았으며 Tool을 실행하지 않았습니다."],
                provenance=FinalResponseProvenance(graph_terminal_status=FinalResponseStatus.ABSTAIN_READY,
                                                   channel_mode=ChannelMode.NO_EVIDENCE,
                                                   response_metadata={"response_mode": "clarification_required"}),
            )
            return {**base, "final_status": "abstain_ready", "final_response": response.model_dump(mode="json")}
        try:
            tool_inputs = bind_interpretation(interpretation, plan)
            # Reuse existing executor behavior; only wrap the Training Log port's
            # completeness check. No changes to Tool or Metric semantics.
            ports = self.tool_executor._tools
            executor = DeterministicToolExecutor(training_log_tool=_CompleteTrainingResult(ports["query_training_log"]),
                                                metric_tool=ports["compute_metrics"], literature_tool=ports["search_literature"])
            workflow = AgenticRAGWorkflow(
                tool_executor=executor, literature_tool=self.literature_tool,
                runtime_grader=self.runtime_grader, recovery_agent=self.recovery_agent,
                router=_FixedRoute(question.strip(), plan), tool_input_resolver=None,
                final_response_layer=self.final_response_layer,
            )
            state = workflow.invoke(question, literature_subquestion=interpretation.literature_subquestion,
                                    initial_query=question, tool_inputs=tool_inputs)
            state.update({k: v for k, v in base.items() if k in {"interpretation_status", "question_interpretation", "interpretation_trace"}})
            state["tool_input_resolution"] = {"execution_status": "ready", "tool_inputs": tool_inputs,
                                              "provenance": {"method": "interpretation_binding", "parsing_source": interpretation.parsing_source}}
            return state
        except Exception:
            return self._failure(base, "interpretation_binding_failure")

    @staticmethod
    def _failure(base, code):
        error = {"stage": "question_interpretation", "code": code, "message": "질문 해석 또는 인자 검증에 실패했습니다."}
        response = build_execution_failure_response(
            input_data=FinalAnswerInput(original_question=base["original_question"] or "잘못된 입력", task_type="invalid", channel_mode=ChannelMode.NO_EVIDENCE),
            graph_errors=[error],
        )
        return {**base, "errors": [error], "final_status": "execution_failure", "final_response": response.model_dump(mode="json")}
