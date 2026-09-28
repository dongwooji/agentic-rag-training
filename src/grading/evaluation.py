"""Confusion-matrix evaluation for Evidence Grader v1."""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_grader(case_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    true_sufficient = sum(
        item["gold_verdict"] == "sufficient"
        and item["predicted_verdict"] == "sufficient"
        for item in case_results
    )
    false_sufficient = sum(
        item["gold_verdict"] == "insufficient"
        and item["predicted_verdict"] == "sufficient"
        for item in case_results
    )
    true_insufficient = sum(
        item["gold_verdict"] == "insufficient"
        and item["predicted_verdict"] == "insufficient"
        for item in case_results
    )
    false_insufficient = sum(
        item["gold_verdict"] == "sufficient"
        and item["predicted_verdict"] == "insufficient"
        for item in case_results
    )
    count = len(case_results)
    gold_sufficient = true_sufficient + false_insufficient
    gold_insufficient = true_insufficient + false_sufficient
    predicted_sufficient = true_sufficient + false_sufficient
    predicted_insufficient = true_insufficient + false_insufficient
    return {
        "definitions": {
            "true_sufficient": "Gold sufficient and Grader sufficient",
            "false_sufficient": "Gold insufficient but Grader sufficient",
            "true_insufficient": "Gold insufficient and Grader insufficient",
            "false_insufficient": "Gold sufficient but Grader insufficient",
            "false_sufficient_rate": "False Sufficient / all Gold insufficient",
            "false_insufficient_rate": "False Insufficient / all Gold sufficient",
        },
        "aggregate": {
            "case_count": count,
            "gold_sufficient_count": gold_sufficient,
            "gold_insufficient_count": gold_insufficient,
            "predicted_sufficient_count": predicted_sufficient,
            "predicted_insufficient_count": predicted_insufficient,
            "true_sufficient": true_sufficient,
            "false_sufficient": false_sufficient,
            "true_insufficient": true_insufficient,
            "false_insufficient": false_insufficient,
            "accuracy": _ratio(true_sufficient + true_insufficient, count),
            "sufficient_precision": _ratio(
                true_sufficient, predicted_sufficient
            ),
            "sufficient_recall": _ratio(true_sufficient, gold_sufficient),
            "insufficient_precision": _ratio(
                true_insufficient, predicted_insufficient
            ),
            "insufficient_recall": _ratio(
                true_insufficient, gold_insufficient
            ),
            "false_sufficient_rate": _ratio(
                false_sufficient, gold_insufficient
            ),
            "false_insufficient_rate": _ratio(
                false_insufficient, gold_sufficient
            ),
        },
        "reason_distribution": dict(
            sorted(Counter(item["reason_code"] for item in case_results).items())
        ),
        "false_sufficient_cases": [
            item["case_id"]
            for item in case_results
            if item["gold_verdict"] == "insufficient"
            and item["predicted_verdict"] == "sufficient"
        ],
        "false_insufficient_cases": [
            item["case_id"]
            for item in case_results
            if item["gold_verdict"] == "sufficient"
            and item["predicted_verdict"] == "insufficient"
        ],
        "per_case": list(case_results),
    }

