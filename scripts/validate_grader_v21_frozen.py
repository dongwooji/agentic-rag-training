"""Validate frozen Grader v2.1 held-out and optional baseline artifacts."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v21_baseline import validate_frozen_v21_baseline
from src.grading.v21_freeze import validate_frozen_v21_heldout


def main() -> None:
    heldout = validate_frozen_v21_heldout(PROJECT_ROOT)
    result = {
        "heldout_manifest_sha256": heldout["manifest_sha256"],
        "heldout_status": "valid",
    }
    baseline_dir = PROJECT_ROOT / "reports/baselines/grader_v2_1_baseline"
    if baseline_dir.exists():
        baseline = validate_frozen_v21_baseline(baseline_dir)
        result.update(
            {
                "baseline_manifest_sha256": baseline["manifest_sha256"],
                "baseline_status": "valid",
            }
        )
    else:
        result["baseline_status"] = "not_created"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
