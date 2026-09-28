"""Run three development-only real-component End-to-End smoke cases."""

from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.config import DatabaseConfig
from src.smoke.e2e import DEFAULT_OUTPUT, run_end_to_end_smoke


def parse_args() -> argparse.Namespace:
    defaults = DatabaseConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--user", default=defaults.user)
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=PROJECT_ROOT / "data/models/huggingface",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--api-min-interval-seconds",
        type=float,
        default=float(os.environ.get("OPENAI_SMOKE_MIN_INTERVAL_SECONDS", "6.2")),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        "Secrets are read without echo, used only in process memory, and never "
        "written to the smoke report.",
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

    cases = run_end_to_end_smoke(
        database_config=DatabaseConfig(
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user,
        ),
        postgres_password=postgres_password,
        openai_api_key=api_key,
        model_cache=args.model_cache.resolve(),
        output_path=args.output.resolve(),
        min_interval_seconds=args.api_min_interval_seconds,
    )
    print(f"Smoke report written to: {args.output.resolve()}")
    for item in cases:
        print(
            f"{item['case_id']}: status={item['final_status']}, "
            f"retries={item['retry_count']}, api_requests={item['api_request_count']}"
        )


if __name__ == "__main__":
    main()
