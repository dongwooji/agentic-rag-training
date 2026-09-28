"""Enable pgvector and create the isolated Phase 6 retrieval schema."""

from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.config import DatabaseConfig
from src.database.psql import resolve_psql, run_psql


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="agentic_rag_training")
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--app-user", default="agentic_rag_app")
    parser.add_argument("--psql", type=Path)
    return parser.parse_args()


def _pgvector_control_path(psql_path: Path) -> Path | None:
    pg_config = psql_path.with_name("pg_config.exe")
    if not pg_config.is_file():
        return None
    result = subprocess.run(
        [str(pg_config), "--sharedir"],
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()) / "extension" / "vector.control"


def main() -> None:
    args = parse_args()
    psql_path = resolve_psql(args.psql)
    control_path = _pgvector_control_path(psql_path)
    if control_path is not None and not control_path.is_file():
        raise RuntimeError(
            "pgvector server files are not installed. Expected "
            f"{control_path}. Install pgvector for PostgreSQL 18, then rerun this script."
        )

    print(f"Using psql: {psql_path}")
    print("Passwords are read without echo and passed only in child-process memory.")
    admin_password = os.environ.get("PGADMINPASSWORD")
    if admin_password is None:
        admin_password = getpass(
            f"Password for PostgreSQL administrator {args.admin_user}: "
        )
    app_password = os.environ.get("PGPASSWORD")
    if app_password is None:
        app_password = getpass(f"Password for PostgreSQL role {args.app_user}: ")

    admin_config = DatabaseConfig(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.admin_user,
    )
    app_config = DatabaseConfig(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.app_user,
    )
    enable_sql = (PROJECT_ROOT / "db/phase6/001_enable_pgvector.sql").read_text(
        encoding="utf-8"
    )
    schema_sql = (PROJECT_ROOT / "db/phase6/002_dense_retrieval_schema.sql").read_text(
        encoding="utf-8"
    )
    run_psql(
        config=admin_config,
        sql=enable_sql,
        password=admin_password,
        psql_path=psql_path,
    )
    run_psql(
        config=app_config,
        sql=schema_sql,
        password=app_password,
        psql_path=psql_path,
    )
    validation = run_psql(
        config=app_config,
        sql="""
        SELECT json_build_object(
            'pgvector_version', (SELECT extversion FROM pg_extension WHERE extname = 'vector'),
            'retrieval_schema', to_regnamespace('retrieval') IS NOT NULL,
            'embedding_runs_table', to_regclass('retrieval.embedding_runs') IS NOT NULL,
            'chunk_embeddings_table', to_regclass('retrieval.chunk_embeddings') IS NOT NULL
        ) AS phase6_setup_summary;
        """,
        password=app_password,
        psql_path=psql_path,
    )
    print(validation.stdout.strip())
    print("Phase 6 pgvector schema setup completed.")


if __name__ == "__main__":
    main()

