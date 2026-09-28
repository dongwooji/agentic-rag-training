"""Run and freeze the preregistered Grader v2 first held-out evaluation."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.grading.provider_v2 import OpenAIGraderV2Backend
from src.grading.v2_baseline import run_grader_v2_baseline
from src.grading.v2_review import sha256_file


def main() -> None:
    output = PROJECT_ROOT / "reports/baselines/grader_v2_baseline"
    if output.exists():
        raise SystemExit(f"Refusing to overwrite immutable Grader v2 baseline: {output}")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OpenAI API key is read without echo and passed only in process memory.")
        api_key = getpass.getpass("OpenAI API key: ")
    if not api_key.strip():
        raise SystemExit("A non-empty OpenAI API key is required.")
    backend = OpenAIGraderV2Backend(api_key=api_key.strip())
    manifest, metrics = run_grader_v2_baseline(
        PROJECT_ROOT, output_dir=output, backend=backend
    )
    aggregate = metrics["aggregate"]
    print(f"Frozen Grader v2 baseline written to: {output}")
    print(
        f"Accuracy: {aggregate['true_sufficient'] + aggregate['true_insufficient']}"
        f"/{aggregate['case_count']} ({aggregate['accuracy']:.6f})"
    )
    print(
        "Confusion matrix — "
        f"TS={aggregate['true_sufficient']}, "
        f"FS={aggregate['false_sufficient']}, "
        f"TI={aggregate['true_insufficient']}, "
        f"FI={aggregate['false_insufficient']}"
    )
    print(f"Frozen artifacts: {len(manifest['artifacts'])}")
    print(f"Manifest SHA-256: {sha256_file(output / 'manifest.json')}")


if __name__ == "__main__":
    main()

