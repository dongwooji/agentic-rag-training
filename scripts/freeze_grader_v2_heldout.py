"""Finalize human review and freeze the Grader v2 held-out set."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v2_freeze import freeze_grader_v2_heldout
from src.grading.v2_review import sha256_file


def main() -> None:
    target = PROJECT_ROOT / "data/evaluation/grader_v2_heldout_v1"
    manifest = freeze_grader_v2_heldout(PROJECT_ROOT, output_dir=target)
    print(f"Frozen Grader v2 held-out set written to: {target}")
    print(f"Human review approved: {manifest['human_review']['approved']}/14")
    print(f"Frozen artifacts: {len(manifest['artifacts'])}")
    print(f"Manifest SHA-256: {sha256_file(target / 'manifest.json')}")
    print("No Grader v2 API evaluation was executed by this freeze command.")


if __name__ == "__main__":
    main()

