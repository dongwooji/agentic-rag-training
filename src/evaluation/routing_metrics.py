"""Tool-selection metrics for the frozen deterministic Router evaluation."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence


TOOL_IDS = (
    "query_training_log",
    "compute_metrics",
    "search_literature",
)


def expected_task_type(case: Mapping[str, Any]) -> str:
    category = str(case["category"])
    tools = set(case.get("required_tools", []))
    if category == "literature_only":
        return "literature_only"
    if category == "hybrid":
        return "hybrid"
    if category == "unanswerable":
        return "unsupported"
    if category == "log_metric":
        return "log_metric" if "compute_metrics" in tools else "log_lookup"
    raise ValueError(f"Unknown evaluation category: {category}")


def _safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_routing(
    cases: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare fixed predictions with Gold only after every route is produced."""

    by_case = {str(item["case_id"]): item for item in predictions}
    if len(by_case) != len(predictions):
        raise ValueError("Routing predictions contain duplicate case IDs")
    if set(by_case) != {str(case["id"]) for case in cases}:
        raise ValueError("Routing prediction case IDs do not match evaluation cases")

    tool_counts = {
        tool: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for tool in TOOL_IDS
    }
    category_counts: dict[str, dict[str, int]] = {}
    exact = 0
    task_type_correct = 0
    unnecessary = 0
    missing = 0
    predicted_total = 0
    required_total = 0
    decision_correct = 0
    no_tool_total = 0
    no_tool_correct = 0
    per_case: list[dict[str, Any]] = []

    for case in cases:
        case_id = str(case["id"])
        prediction = by_case[case_id]
        expected = tuple(str(item) for item in case.get("required_tools", []))
        predicted = tuple(str(item) for item in prediction.get("selected_tools", []))
        if len(predicted) != len(set(predicted)):
            raise ValueError(f"Prediction contains duplicate Tools: {case_id}")
        unknown = sorted((set(expected) | set(predicted)).difference(TOOL_IDS))
        if unknown:
            raise ValueError(f"Unknown Tool IDs for {case_id}: {unknown}")
        expected_set = set(expected)
        predicted_set = set(predicted)
        missing_tools = sorted(expected_set - predicted_set)
        unnecessary_tools = sorted(predicted_set - expected_set)
        is_exact = expected_set == predicted_set
        expected_type = expected_task_type(case)
        predicted_type = str(prediction["task_type"])
        type_correct = expected_type == predicted_type
        exact += int(is_exact)
        task_type_correct += int(type_correct)
        unnecessary += len(unnecessary_tools)
        missing += len(missing_tools)
        predicted_total += len(predicted_set)
        required_total += len(expected_set)
        if not expected_set:
            no_tool_total += 1
            no_tool_correct += int(not predicted_set and predicted_type == "unsupported")

        category = str(case["category"])
        bucket = category_counts.setdefault(
            category,
            {
                "case_count": 0,
                "exact": 0,
                "task_type_correct": 0,
                "decision_correct": 0,
                "unnecessary": 0,
                "missing": 0,
                "predicted": 0,
                "required": 0,
            },
        )
        bucket["case_count"] += 1
        bucket["exact"] += int(is_exact)
        bucket["task_type_correct"] += int(type_correct)
        bucket["unnecessary"] += len(unnecessary_tools)
        bucket["missing"] += len(missing_tools)
        bucket["predicted"] += len(predicted_set)
        bucket["required"] += len(expected_set)

        for tool in TOOL_IDS:
            expected_positive = tool in expected_set
            predicted_positive = tool in predicted_set
            if expected_positive and predicted_positive:
                key = "tp"
            elif not expected_positive and predicted_positive:
                key = "fp"
            elif expected_positive and not predicted_positive:
                key = "fn"
            else:
                key = "tn"
            tool_counts[tool][key] += 1
            decision_correct += int(key in {"tp", "tn"})
            bucket["decision_correct"] += int(key in {"tp", "tn"})

        failure_parts = []
        if missing_tools:
            failure_parts.append("missing required: " + ", ".join(missing_tools))
        if unnecessary_tools:
            failure_parts.append("unnecessary: " + ", ".join(unnecessary_tools))
        if not type_correct:
            failure_parts.append(
                f"task type {predicted_type} != expected {expected_type}"
            )
        per_case.append(
            {
                "case_id": case_id,
                "category": category,
                "question": case["question"],
                "expected_task_type": expected_type,
                "predicted_task_type": predicted_type,
                "expected_tools": list(expected),
                "predicted_tools": list(predicted),
                "execution_order": list(prediction.get("execution_order", [])),
                "correct": is_exact,
                "task_type_correct": type_correct,
                "matched_rule": prediction["rule_match"]["rule_id"],
                "confidence": prediction["confidence"],
                "routing_reason": prediction["routing_reason"],
                "unsupported_reason": prediction.get("unsupported_reason"),
                "missing_required_tools": missing_tools,
                "unnecessary_tools": unnecessary_tools,
                "failure_reason": "; ".join(failure_parts) if failure_parts else None,
                "rule_match": prediction["rule_match"],
            }
        )

    per_tool = {}
    for tool, counts in tool_counts.items():
        precision = _safe_rate(counts["tp"], counts["tp"] + counts["fp"])
        recall = _safe_rate(counts["tp"], counts["tp"] + counts["fn"])
        f1 = _safe_rate(2 * precision * recall, precision + recall)
        per_tool[tool] = {
            **counts,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    by_category = {}
    for category, counts in category_counts.items():
        case_count = counts["case_count"]
        by_category[category] = {
            **counts,
            "exact_tool_set_match_accuracy": _safe_rate(counts["exact"], case_count),
            "tool_selection_accuracy": _safe_rate(
                counts["decision_correct"], case_count * len(TOOL_IDS)
            ),
            "task_type_accuracy": _safe_rate(
                counts["task_type_correct"], case_count
            ),
            "unnecessary_tool_call_rate": _safe_rate(
                counts["unnecessary"], counts["predicted"]
            ),
            "missing_required_tool_rate": _safe_rate(
                counts["missing"], counts["required"]
            ),
        }

    return {
        "definitions": {
            "tool_selection_accuracy": (
                "Correct binary select/not-select decisions across 30 cases x 3 Tools"
            ),
            "exact_tool_set_match_accuracy": (
                "Cases whose predicted Tool set exactly equals required_tools"
            ),
            "unnecessary_tool_call_rate": (
                "False-positive Tool selections divided by all predicted selections"
            ),
            "missing_required_tool_rate": (
                "False-negative Tool selections divided by all required selections"
            ),
            "unanswerable_no_tool_routing_accuracy": (
                "Gold no-tool cases predicted with no Tools and task_type unsupported"
            ),
        },
        "aggregate": {
            "case_count": len(cases),
            "tool_decision_count": len(cases) * len(TOOL_IDS),
            "tool_selection_accuracy": _safe_rate(
                decision_correct, len(cases) * len(TOOL_IDS)
            ),
            "exact_tool_set_match_count": exact,
            "exact_tool_set_match_accuracy": _safe_rate(exact, len(cases)),
            "task_type_accuracy": _safe_rate(task_type_correct, len(cases)),
            "unnecessary_tool_call_count": unnecessary,
            "unnecessary_tool_call_rate": _safe_rate(
                unnecessary, predicted_total
            ),
            "missing_required_tool_count": missing,
            "missing_required_tool_rate": _safe_rate(missing, required_total),
            "unanswerable_case_count": no_tool_total,
            "unanswerable_no_tool_correct_count": no_tool_correct,
            "unanswerable_no_tool_routing_accuracy": _safe_rate(
                no_tool_correct, no_tool_total
            ),
            "predicted_tool_call_count": predicted_total,
            "required_tool_call_count": required_total,
        },
        "per_tool": per_tool,
        "by_question_type": by_category,
        "rule_distribution": dict(
            Counter(item["matched_rule"] for item in per_case)
        ),
        "per_case": per_case,
    }
