"""Run the versioned Phase 2 workout preprocessing pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.preprocessing.clean_workouts import (
    DEFAULT_ALIAS_PATH,
    DEFAULT_POLICY_PATH,
    DEFAULT_PROCESSED_DIR,
    DEFAULT_RAW_PATH,
    DEFAULT_REPORTS_DIR,
    run_preprocessing,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_RAW_PATH)
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIAS_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_preprocessing(
        raw_path=args.input,
        processed_dir=args.processed_dir,
        reports_dir=args.reports_dir,
        policy_path=args.policy,
        alias_path=args.aliases,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
