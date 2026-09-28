"""Apply the literature migration and load the validated Phase 4 corpus."""

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
from src.database.psql import resolve_psql
from src.literature.database import load_literature_database


def parse_args() -> argparse.Namespace:
    defaults = DatabaseConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--user", default=defaults.user)
    parser.add_argument("--psql", type=Path)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace only the existing literature schema data transactionally.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = os.environ.get("PGPASSWORD")
    if password is None:
        password = getpass(f"Password for PostgreSQL role {args.user}: ")
    config = DatabaseConfig(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
    )
    output = load_literature_database(
        project_root=PROJECT_ROOT,
        config=config,
        password=password,
        psql_path=resolve_psql(args.psql),
        replace=args.replace,
    )
    print(output.strip())
    print("Phase 4 literature PostgreSQL load completed.")


if __name__ == "__main__":
    main()
