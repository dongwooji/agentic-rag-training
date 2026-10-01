"""Validated semantics -> existing typed arguments. No parser/provider here."""

from src.agent.contracts import MetricArguments, TrainingLogArguments
from src.interpretation.contracts import QuestionInterpretation
from src.routing.contracts import RoutingPlan


def bind_interpretation(interpretation: QuestionInterpretation, route: RoutingPlan) -> dict:
    if interpretation.clarification_required or len(interpretation.requested_analyses) > 1:
        raise ValueError("Only settled single-metric interpretations can be bound")
    if route.status.value != "planned":
        return {}
    inputs = {}
    time = interpretation.time_condition
    if "query_training_log" in route.selected_tools:
        operation = interpretation.record_operation
        if interpretation.requested_analyses:
            # Session-only operations can consume the existing sessions adapter.
            operation = "list_sessions" if interpretation.requested_analyses[0].operation.value in {"training_gap", "weekly_frequency"} else "exercise_records"
        training = TrainingLogArguments(operation=operation, canonical_exercise_name=interpretation.canonical_exercise_name,
                                        start_date=time.start_date, end_date=time.end_date,
                                        limit=5000, include_lineage=False)
        inputs["query_training_log"] = training.model_dump(mode="json")
    if "compute_metrics" in route.selected_tools:
        analysis = interpretation.requested_analyses[0]
        data = dict(operation=analysis.operation.value, canonical_exercise_name=interpretation.canonical_exercise_name,
                    start_date=time.start_date, end_date=time.end_date, records_source="query_training_log")
        if analysis.n_sessions is not None:
            data["n_sessions"] = analysis.n_sessions
        inputs["compute_metrics"] = MetricArguments.model_validate(data).model_dump(mode="json")
    return inputs
