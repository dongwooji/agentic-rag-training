from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.report import write_review
from src.evaluation.validation import validate_dataset, write_validation_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the Phase 5 draft without running a retrieval system."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/evaluation/eval_dataset_v1_draft.json"),
    )
    parser.add_argument(
        "--validation-report",
        type=Path,
        default=Path("reports/evaluation_set_validation.json"),
    )
    parser.add_argument(
        "--review-report",
        type=Path,
        default=Path("reports/EVALUATION_SET_REVIEW.md"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(__file__).resolve().parents[1]
    dataset_path = (workspace / args.dataset).resolve()
    report = validate_dataset(dataset_path, workspace)
    write_validation_report(report, workspace / args.validation_report)
    write_review(
        dataset_path,
        workspace / "data/literature/processed/chunks.jsonl",
        workspace / args.review_report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["all_automated_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
