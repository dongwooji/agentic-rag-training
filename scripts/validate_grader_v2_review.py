"""Read-only validation for the Grader v2 human-review checkpoint."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v2_review import validate_heldout_drafts


def main() -> None:
    validation = validate_heldout_drafts(PROJECT_ROOT)
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    if not validation["all_checks_passed"]:
        raise SystemExit(
            "Grader v2 draft validation failed: "
            + ", ".join(validation["failed_checks"])
        )


if __name__ == "__main__":
    main()
