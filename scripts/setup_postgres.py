"""Create the project database, apply migrations, and load Phase 2 outputs."""

from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.bootstrap import bootstrap_database
from src.database.config import DatabaseConfig
from src.database.loader import load_database
from src.database.psql import resolve_psql


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--database", default="agentic_rag_training")
    parser.add_argument("--app-user", default="agentic_rag_app")
    parser.add_argument("--psql", type=Path)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing project tables after a successful transactional reload.",
    )
    return parser.parse_args()


def _new_app_password(app_user: str) -> str:
    first = getpass(f"New password for application role {app_user}: ")
    second = getpass("Confirm application role password: ")
    if not first:
        raise ValueError("The application password cannot be empty")
    if first != second:
        raise ValueError("Application passwords do not match")
    return first


def main() -> None:
    args = parse_args()
    psql_path = resolve_psql(args.psql)
    print(f"Using psql: {psql_path}")
    print("Passwords are read without echo and are passed only to child-process memory.")

    admin_password = getpass(f"Password for PostgreSQL administrator {args.admin_user}: ")
    app_password = _new_app_password(args.app_user)
    admin_config = DatabaseConfig(
        host=args.host,
        port=args.port,
        database="postgres",
        user=args.admin_user,
    )
    bootstrap_database(
        admin_config=admin_config,
        admin_password=admin_password,
        app_user=args.app_user,
        app_password=app_password,
        database=args.database,
        psql_path=psql_path,
    )
    print(f"Database {args.database} and role {args.app_user} are ready.")

    app_config = DatabaseConfig(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.app_user,
    )
    output = load_database(
        project_root=PROJECT_ROOT,
        config=app_config,
        password=app_password,
        psql_path=psql_path,
        replace=args.replace,
    )
    print(output.strip())
    print("Phase 3 PostgreSQL setup and validated load completed.")


if __name__ == "__main__":
    main()
