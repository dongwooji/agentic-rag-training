"""Grader evidence-sufficiency evaluation metric tests.

Moved verbatim from the historical Phase 11A Grader test module (preserved in the
``legacy-pre-retrieval-v2`` tag); ``evaluate_grader`` is kept as reusable
evaluation infrastructure.
"""

from __future__ import annotations

from src.grading.evaluation import evaluate_grader


def test_confusion_matrix_and_rates() -> None:
    values = [
        ("sufficient", "sufficient"),
        ("insufficient", "sufficient"),
        ("insufficient", "insufficient"),
        ("sufficient", "insufficient"),
    ]
    rows = [
        {
            "case_id": f"C-{index}",
            "gold_verdict": gold,
            "predicted_verdict": predicted,
            "reason_code": "sufficient" if predicted == "sufficient" else "missing_required_evidence",
        }
        for index, (gold, predicted) in enumerate(values)
    ]
    aggregate = evaluate_grader(rows)["aggregate"]
    assert aggregate["true_sufficient"] == 1
    assert aggregate["false_sufficient"] == 1
    assert aggregate["true_insufficient"] == 1
    assert aggregate["false_insufficient"] == 1
    assert aggregate["accuracy"] == 0.5
    assert aggregate["false_sufficient_rate"] == 0.5
    assert aggregate["false_insufficient_rate"] == 0.5
