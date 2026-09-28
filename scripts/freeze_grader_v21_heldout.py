"""Freeze the fully human-approved Grader v2.1 held-out set."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.v21_freeze import freeze_grader_v21_heldout
from src.grading.v21_review import sha256_file


def main() -> None:
    target = PROJECT_ROOT / "data/evaluation/grader_v2_1_heldout_v1"
    manifest = freeze_grader_v21_heldout(PROJECT_ROOT, output_dir=target)
    print(f"Frozen Grader v2.1 held-out set written to: {target}")
    print(f"Human review approved: {manifest['human_review']['approved']}/12")
    print(f"Frozen artifacts: {len(manifest['artifacts'])}")
    print(f"Manifest SHA-256: {sha256_file(target / 'manifest.json')}")
    print("No API evaluation was executed by this freeze command.")


if __name__ == "__main__":
    main()
