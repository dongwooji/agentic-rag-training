"""Validate the Grader v2.1 design and render its human-review artifact."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v21_review import (  # noqa: E402
    REVIEW_REPORT_PATH,
    render_v21_human_review,
    validate_v21_design,
)


def main() -> None:
    validation = validate_v21_design(PROJECT_ROOT)
    if not validation["all_checks_passed"]:
        raise SystemExit(
            "Grader v2.1 design validation failed: "
            + ", ".join(validation["failed_checks"])
        )
    output = PROJECT_ROOT / REVIEW_REPORT_PATH
    output.write_text(render_v21_human_review(PROJECT_ROOT), encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    print(f"Human-review artifact written to: {output}")
    print("No held-out freeze or API evaluation was performed.")


if __name__ == "__main__":
    main()
