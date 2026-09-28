"""Read-only validation for the Grader v2.1 design checkpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v21_review import validate_v21_design  # noqa: E402


def main() -> None:
    result = validate_v21_design(PROJECT_ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["all_checks_passed"]:
        raise SystemExit(
            "Grader v2.1 design validation failed: "
            + ", ".join(result["failed_checks"])
        )


if __name__ == "__main__":
    main()
