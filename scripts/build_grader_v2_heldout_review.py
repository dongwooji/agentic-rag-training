"""Validate draft held-out cases and generate the human-review checkpoint."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v2_review import (
    REVIEW_REPORT_PATH,
    render_human_review_report,
    validate_heldout_drafts,
)


def main() -> None:
    validation = validate_heldout_drafts(PROJECT_ROOT)
    if not validation["all_checks_passed"]:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        raise SystemExit("Held-out draft validation failed")
    output = PROJECT_ROOT / REVIEW_REPORT_PATH
    output.write_text(render_human_review_report(PROJECT_ROOT), encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    print(f"Human-review checkpoint written to: {output}")
    print("Status remains draft_human_review_required; no baseline was run or frozen.")


if __name__ == "__main__":
    main()
