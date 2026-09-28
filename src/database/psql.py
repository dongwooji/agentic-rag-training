"""Safe, shell-free wrappers around the PostgreSQL ``psql`` client."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Mapping

from .config import DatabaseConfig


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(value: str, label: str = "identifier") -> str:
    """Allow the intentionally conservative identifier subset used by setup."""

    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"Invalid PostgreSQL {label}: {value!r}. "
            "Use letters, digits, and underscores, starting with a letter or underscore."
        )
    return value


def sql_literal(value: str) -> str:
    """Return a PostgreSQL string literal for trusted generated SQL."""

    return "'" + value.replace("'", "''") + "'"


def resolve_psql(explicit_path: str | Path | None = None) -> Path:
    """Find ``psql`` from an explicit path, PATH, or standard Windows installs."""

    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"psql was not found at {candidate}")

    on_path = shutil.which("psql")
    if on_path:
        return Path(on_path).resolve()

    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidates = list((program_files / "PostgreSQL").glob("*/bin/psql.exe"))
    if candidates:
        def version_key(path: Path) -> tuple[int, ...]:
            try:
                return tuple(int(part) for part in path.parents[1].name.split("."))
            except ValueError:
                return (0,)

        return max(candidates, key=version_key).resolve()

    raise FileNotFoundError(
        "psql was not found. Add PostgreSQL's bin directory to PATH or pass --psql."
    )


def run_psql(
    *,
    config: DatabaseConfig,
    sql: str,
    password: str | None,
    psql_path: str | Path | None = None,
    extra_env: Mapping[str, str] | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute SQL without using a shell or putting the password on the command line."""

    executable = resolve_psql(psql_path)
    command = [
        str(executable),
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        *config.psql_args(),
        "-f",
        "-",
    ]
    environment = os.environ.copy()
    if password is not None:
        environment["PGPASSWORD"] = password
    if extra_env:
        environment.update(extra_env)

    result = subprocess.run(
        command,
        input=sql,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
        capture_output=capture_output,
    )
    if result.returncode != 0:
        details = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise RuntimeError(
            f"psql failed for {config.user}@{config.host}:{config.port}/"
            f"{config.database} (exit {result.returncode})\n{details}"
        )
    return result
