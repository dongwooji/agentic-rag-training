"""Verify or execute the preregistered frozen End-to-End baseline v1."""

from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.config import DatabaseConfig
from src.evaluation.end_to_end_runner import (
    DEFAULT_CHECKPOINT_DIR,
    DEFAULT_OUTPUT_DIR,
    run_end_to_end_baseline,
    verify_only,
)


def parse_args() -> argparse.Namespace:
    defaults = DatabaseConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify the frozen protocol without DB, model, or API calls",
    )
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--user", default=defaults.user)
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=PROJECT_ROOT / "data/models/huggingface",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument(
        "--api-min-interval-seconds",
        type=float,
        default=None,
        help="defaults to the frozen 6.2-second protocol value",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.verify_only:
        print(json.dumps(verify_only(), ensure_ascii=False, indent=2))
        return

    print(
        "This starts the single preregistered End-to-End baseline. Secrets are read "
        "without echo, used only in process memory, and never written to artifacts.",
        flush=True,
    )
    postgres_password = os.environ.get("PGPASSWORD")
    if postgres_password is None:
        postgres_password = getpass(f"Password for PostgreSQL role {args.user}: ")
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key is None:
        api_key = getpass("OpenAI API key: ")
    if not postgres_password.strip() or not api_key.strip():
        raise SystemExit("Both PostgreSQL password and OpenAI API key are required.")

    result = run_end_to_end_baseline(
        database_config=DatabaseConfig(
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user,
        ),
        postgres_password=postgres_password,
        openai_api_key=api_key,
        model_cache=args.model_cache.resolve(),
        output_dir=args.output_dir.resolve(),
        checkpoint_dir=args.checkpoint_dir.resolve(),
        min_interval_seconds=args.api_min_interval_seconds,
    )
    overall = result["metrics"]["overall"]
    print(f"Frozen baseline written to: {result['output_dir']}")
    print(f"Cases: {overall['case_count']}")
    print(f"Manifest SHA-256: {result['manifest_sha256']}")


if __name__ == "__main__":
    main()
