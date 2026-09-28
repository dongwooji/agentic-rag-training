"""Create the application role and database with an existing admin account."""

from __future__ import annotations

from pathlib import Path

from .config import DatabaseConfig
from .psql import run_psql, sql_literal, validate_identifier


def bootstrap_database(
    *,
    admin_config: DatabaseConfig,
    admin_password: str,
    app_user: str,
    app_password: str,
    database: str,
    psql_path: str | Path | None = None,
) -> None:
    """Create or update the login role and create the database when absent."""

    validate_identifier(app_user, "role name")
    validate_identifier(database, "database name")

    role_sql = f"""
DO $bootstrap$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {sql_literal(app_user)}) THEN
        EXECUTE format(
            'ALTER ROLE %I WITH LOGIN PASSWORD %L',
            {sql_literal(app_user)},
            {sql_literal(app_password)}
        );
    ELSE
        EXECUTE format(
            'CREATE ROLE %I LOGIN PASSWORD %L',
            {sql_literal(app_user)},
            {sql_literal(app_password)}
        );
    END IF;
END
$bootstrap$;
"""
    run_psql(
        config=admin_config,
        sql=role_sql,
        password=admin_password,
        psql_path=psql_path,
    )

    existence_sql = (
        "SELECT datname FROM pg_database "
        f"WHERE datname = {sql_literal(database)};"
    )
    result = run_psql(
        config=admin_config,
        sql=existence_sql,
        password=admin_password,
        psql_path=psql_path,
    )
    if database not in result.stdout.split():
        create_sql = (
            f'CREATE DATABASE "{database}" '
            f'OWNER "{app_user}" ENCODING \'UTF8\' TEMPLATE template0;'
        )
        run_psql(
            config=admin_config,
            sql=create_sql,
            password=admin_password,
            psql_path=psql_path,
        )
    else:
        owner_sql = f'ALTER DATABASE "{database}" OWNER TO "{app_user}";'
        run_psql(
            config=admin_config,
            sql=owner_sql,
            password=admin_password,
            psql_path=psql_path,
        )
