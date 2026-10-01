"""Deterministic channel mapping, not raw-question keyword routing."""

from src.interpretation.contracts import QuestionInterpretation
from .contracts import RoutingPlan, RoutingStatus, RuleMatch, TaskType


class InterpretationRouter:
    def route(self, interpretation: QuestionInterpretation) -> RoutingPlan:
        if interpretation.clarification_required:
            task, tools, status = TaskType.AMBIGUOUS, (), RoutingStatus.AMBIGUOUS
        elif interpretation.personal_record_requested:
            tools = ("query_training_log",)
            if interpretation.requested_analyses:
                tools += ("compute_metrics",)
            if interpretation.literature_requested:
                tools += ("search_literature",)
                task = TaskType.HYBRID
            else:
                task = TaskType.LOG_METRIC if interpretation.requested_analyses else TaskType.LOG_LOOKUP
            status = RoutingStatus.PLANNED
        elif interpretation.literature_requested:
            task, tools, status = TaskType.LITERATURE_ONLY, ("search_literature",), RoutingStatus.PLANNED
        else:
            task, tools, status = TaskType.UNSUPPORTED, (), RoutingStatus.UNSUPPORTED
        return RoutingPlan(
            router_version="interpretation_router_phase_a_v1", status=status, route=task.value, task_type=task,
            selected_tools=tools, execution_order=tools, confidence=1.0,
            routing_reason="Deterministic mapping of validated interpretation channels; confidence is rule mapping, not semantic accuracy.",
            rule_match=RuleMatch(rule_id=f"I-{task.value}", normalized_question=interpretation.normalized_question,
                                 signals={"structured_log": interpretation.personal_record_requested,
                                          "metric": bool(interpretation.requested_analyses), "literature": interpretation.literature_requested},
                                 matched_patterns={}),
            unsupported_reason="질문 조건 확인이 필요합니다." if status is not RoutingStatus.PLANNED else None,
        )
