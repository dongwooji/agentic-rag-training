"""Connection configuration for local PostgreSQL commands."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class DatabaseConfig:
    """Non-secret PostgreSQL connection settings.

    Passwords are deliberately excluded so they are not exposed by ``repr`` or
    accidentally written to logs. Callers pass a password directly to the
    subprocess environment when needed.
    """

    host: str = "localhost"
    port: int = 5432
    database: str = "agentic_rag_training"
    user: str = "agentic_rag_app"

    @classmethod
    def from_environment(cls) -> "DatabaseConfig":
        return cls(
            host=os.environ.get("PGHOST", cls.host),
            port=int(os.environ.get("PGPORT", str(cls.port))),
            database=os.environ.get("PGDATABASE", cls.database),
            user=os.environ.get("PGUSER", cls.user),
        )

    def psql_args(self) -> list[str]:
        return [
            "-h",
            self.host,
            "-p",
            str(self.port),
            "-U",
            self.user,
            "-d",
            self.database,
        ]
