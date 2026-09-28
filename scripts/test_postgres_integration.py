"""Run Phase 3 against an isolated temporary PostgreSQL cluster."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.bootstrap import bootstrap_database
from src.database.config import DatabaseConfig
from src.database.loader import load_database
from src.database.psql import resolve_psql, run_psql
from src.literature.database import load_literature_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psql", type=Path)
    return parser.parse_args()


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except subprocess.CalledProcessError as error:
        details = "\n".join(part for part in (error.stdout, error.stderr) if part)
        raise RuntimeError(f"Command failed: {command[0]}\n{details}") from error


def main() -> None:
    args = parse_args()
    psql_path = resolve_psql(args.psql)
    postgres_bin = psql_path.parent
    initdb = postgres_bin / "initdb.exe"
    postgres = postgres_bin / "postgres.exe"
    pg_isready = postgres_bin / "pg_isready.exe"
    if not initdb.is_file() or not postgres.is_file() or not pg_isready.is_file():
        raise FileNotFoundError(
            "initdb.exe, postgres.exe, and pg_isready.exe are required beside psql.exe"
        )

    port = _available_port()
    with tempfile.TemporaryDirectory(prefix="agentic_rag_pgtest_") as temp_dir:
        temp_root = Path(temp_dir)
        data_dir = temp_root / "data"
        log_path = temp_root / "postgres.log"
        _run(
            [
                str(initdb),
                "-D",
                str(data_dir),
                "-A",
                "trust",
                "-U",
                "postgres",
                "--encoding=UTF8",
                "--no-locale",
            ]
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with log_path.open("w", encoding="utf-8") as log_file:
            server = subprocess.Popen(
                [
                    str(postgres),
                    "-D",
                    str(data_dir),
                    "-p",
                    str(port),
                    "-h",
                    "127.0.0.1",
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            for _ in range(100):
                readiness = subprocess.run(
                    [
                        str(pg_isready),
                        "-h",
                        "127.0.0.1",
                        "-p",
                        str(port),
                    ],
                    capture_output=True,
                )
                if readiness.returncode == 0:
                    break
                if server.poll() is not None:
                    break
                time.sleep(0.1)
            else:
                server.terminate()
                server.wait(timeout=10)
                raise RuntimeError("Temporary PostgreSQL did not become ready")

        if server.poll() is not None:
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))

        try:
            admin = DatabaseConfig(
                host="127.0.0.1", port=port, database="postgres", user="postgres"
            )
            bootstrap_database(
                admin_config=admin,
                admin_password="integration_admin",
                app_user="agentic_rag_app",
                app_password="integration_app",
                database="agentic_rag_training",
                psql_path=psql_path,
            )
            app = DatabaseConfig(
                host="127.0.0.1",
                port=port,
                database="agentic_rag_training",
                user="agentic_rag_app",
            )
            first_output = load_database(
                project_root=PROJECT_ROOT,
                config=app,
                password="integration_app",
                psql_path=psql_path,
            )
            try:
                load_database(
                    project_root=PROJECT_ROOT,
                    config=app,
                    password="integration_app",
                    psql_path=psql_path,
                )
            except RuntimeError as error:
                if "already contains data" not in str(error):
                    raise
            else:
                raise AssertionError("A non-replace reload unexpectedly succeeded")

            replace_output = load_database(
                project_root=PROJECT_ROOT,
                config=app,
                password="integration_app",
                psql_path=psql_path,
                replace=True,
            )
            literature_output = load_literature_database(
                project_root=PROJECT_ROOT,
                config=app,
                password="integration_app",
                psql_path=psql_path,
            )
            try:
                load_literature_database(
                    project_root=PROJECT_ROOT,
                    config=app,
                    password="integration_app",
                    psql_path=psql_path,
                )
            except RuntimeError as error:
                if "literature schema already contains data" not in str(error):
                    raise
            else:
                raise AssertionError("A non-replace literature reload unexpectedly succeeded")
            verification = run_psql(
                config=app,
                password="integration_app",
                psql_path=psql_path,
                sql="""
SELECT
    (SELECT count(*) FROM training.workout_sessions) AS sessions,
    (SELECT count(*) FROM training.exercises) AS exercises,
    (SELECT count(*) FROM training.workout_sets) AS sets,
    (SELECT count(*) FROM training.row_lineage) AS lineage_rows,
    (SELECT count(*) FROM literature.papers) AS papers,
    (SELECT count(*) FROM literature.chunks) AS chunks;
""",
            )
            print(first_output.strip())
            print("Default reload guard passed.")
            print(replace_output.strip())
            print(literature_output.strip())
            print("Literature default reload guard passed.")
            print(verification.stdout.strip())
            print("Temporary PostgreSQL integration test passed.")
        finally:
            server.terminate()
            server.wait(timeout=15)


if __name__ == "__main__":
    main()
