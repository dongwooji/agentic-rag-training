"""Generate and execute the deterministic Phase 3 PostgreSQL load."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import DatabaseConfig
from .psql import run_psql, sql_literal


WORKOUT_SET_COLUMNS = (
    "processed_set_id",
    "representative_source_row",
    "session_id",
    "session_timestamp",
    "workout_name",
    "raw_exercise_name",
    "normalized_exercise_name",
    "canonical_exercise_name",
    "name_normalization_applied",
    "alias_mapping_applied",
    "set_order",
    "weight",
    "weight_unit",
    "reps",
    "distance",
    "distance_unit",
    "seconds",
    "notes",
    "workout_notes",
    "raw_row_count",
    "exact_duplicate_extra_count",
    "alias_shadow_count",
    "is_outlier",
    "outlier_reason",
    "has_set_order_collision",
    "set_order_collision_group_id",
    "include_in_volume_metrics",
    "include_in_e1rm",
    "e1rm_formula_version",
    "validation_reason",
    "source_file_sha256",
    "preprocessing_version",
)

LINEAGE_COLUMNS = (
    "source_row",
    "raw_record_id",
    "processed_set_id",
    "representative_source_row",
    "row_status",
    "include_in_processed",
    "is_exact_duplicate",
    "exact_duplicate_of_source_row",
    "is_alias_shadow",
    "alias_shadow_of_source_row",
    "raw_exercise_name",
    "normalized_exercise_name",
    "canonical_exercise_name",
    "name_normalization_applied",
    "alias_mapping_applied",
    "is_outlier",
    "outlier_reason",
    "has_set_order_collision",
    "set_order_collision_group_id",
    "include_in_volume_metrics",
    "include_in_e1rm",
    "validation_reason",
    "source_file_sha256",
    "preprocessing_version",
)


@dataclass(frozen=True)
class LoadInputs:
    project_root: Path
    workout_sets_path: Path
    lineage_path: Path
    validation_path: Path
    validation: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.validation["preprocessing_version"])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_header(path: Path) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return tuple(next(csv.reader(source)))


def load_inputs(project_root: str | Path) -> LoadInputs:
    root = Path(project_root).resolve()
    validation_path = root / "reports" / "preprocessing_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    workout_sets_path = root / validation["output_files"]["workout_sets"]["path"]
    lineage_path = root / validation["output_files"]["row_lineage"]["path"]

    if _read_header(workout_sets_path) != WORKOUT_SET_COLUMNS:
        raise ValueError(f"Unexpected processed workout schema: {workout_sets_path}")
    if _read_header(lineage_path) != LINEAGE_COLUMNS:
        raise ValueError(f"Unexpected lineage schema: {lineage_path}")

    expected_workout_hash = validation["output_files"]["workout_sets"]["sha256"]
    expected_lineage_hash = validation["output_files"]["row_lineage"]["sha256"]
    actual_workout_hash = _sha256_file(workout_sets_path)
    actual_lineage_hash = _sha256_file(lineage_path)
    if actual_workout_hash != expected_workout_hash:
        raise ValueError("workout_sets.csv SHA-256 does not match validation metadata")
    if actual_lineage_hash != expected_lineage_hash:
        raise ValueError("row_lineage.csv SHA-256 does not match validation metadata")
    if not validation.get("all_checks_passed"):
        raise ValueError("Preprocessing validation did not pass; database load refused")

    return LoadInputs(
        project_root=root,
        workout_sets_path=workout_sets_path,
        lineage_path=lineage_path,
        validation_path=validation_path,
        validation=validation,
    )


def _text_stage(name: str, columns: tuple[str, ...]) -> str:
    definitions = ",\n    ".join(f'"{column}" text' for column in columns)
    return f"CREATE TEMP TABLE {name} (\n    {definitions}\n) ON COMMIT DROP;"


def _copy_from_stdin(table: str, path: Path) -> str:
    """Embed UTF-8 CSV input so Windows Unicode paths never reach ``psql``."""

    csv_text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if any(line == r"\." for line in csv_text.splitlines()):
        raise ValueError(f"CSV contains a psql end-of-data marker: {path}")
    return (
        f"COPY {table} FROM STDIN "
        "WITH (FORMAT CSV, HEADER TRUE, NULL '', ENCODING 'UTF8');\n"
        f"{csv_text.rstrip(chr(10))}\n"
        "\\."
    )


def build_load_sql(inputs: LoadInputs, *, replace: bool = False) -> str:
    """Build a transactional load with database-side reconciliation assertions."""

    summary = inputs.validation
    reconciliation = summary["reconciliation"]
    source = summary["source"]
    flags = summary["flags"]
    eligibility = summary["metric_eligibility"]
    status_counts = reconciliation["row_status_counts"]
    workout_hash = summary["output_files"]["workout_sets"]["sha256"]
    lineage_hash = summary["output_files"]["row_lineage"]["sha256"]

    if replace:
        load_guard = """
TRUNCATE TABLE
    training.row_lineage,
    training.workout_sets,
    training.exercise_aliases,
    training.workout_sessions,
    training.exercises,
    training.preprocessing_runs
RESTART IDENTITY;
"""
    else:
        load_guard = """
DO $load_guard$
BEGIN
    IF EXISTS (SELECT 1 FROM training.preprocessing_runs) THEN
        RAISE EXCEPTION
            'training schema already contains data; rerun with --replace for a full reload';
    END IF;
END
$load_guard$;
"""

    set_stage = _text_stage("staging_workout_sets", WORKOUT_SET_COLUMNS)
    lineage_stage = _text_stage("staging_row_lineage", LINEAGE_COLUMNS)
    set_copy = _copy_from_stdin("staging_workout_sets", inputs.workout_sets_path)
    lineage_copy = _copy_from_stdin("staging_row_lineage", inputs.lineage_path)
    return f"""\
BEGIN;
{load_guard}
{set_stage}
{lineage_stage}

{set_copy}
{lineage_copy}

INSERT INTO training.preprocessing_runs (
    preprocessing_version,
    source_path,
    source_file_sha256,
    processed_file_sha256,
    lineage_file_sha256,
    raw_row_count,
    processed_row_count,
    exact_duplicate_count,
    alias_shadow_count
)
VALUES (
    {sql_literal(inputs.version)},
    {sql_literal(str(source['path']))},
    {sql_literal(str(source['sha256']))},
    {sql_literal(str(workout_hash))},
    {sql_literal(str(lineage_hash))},
    {int(reconciliation['raw_rows'])},
    {int(reconciliation['processed_rows'])},
    {int(reconciliation['excluded_exact_duplicate_rows'])},
    {int(reconciliation['excluded_alias_shadow_rows'])}
);

INSERT INTO training.workout_sessions (
    session_id,
    started_at,
    workout_name,
    preprocessing_version
)
SELECT DISTINCT
    session_id,
    session_timestamp::timestamp,
    workout_name,
    preprocessing_version
FROM staging_workout_sets;

INSERT INTO training.exercises (canonical_name)
SELECT DISTINCT canonical_exercise_name
FROM staging_workout_sets
ORDER BY canonical_exercise_name;

INSERT INTO training.exercise_aliases (
    raw_name,
    preprocessing_version,
    normalized_name,
    exercise_id,
    name_normalization_applied,
    alias_mapping_applied,
    observed_raw_row_count
)
SELECT
    l.raw_exercise_name,
    l.preprocessing_version,
    l.normalized_exercise_name,
    e.exercise_id,
    bool_or(l.name_normalization_applied::boolean),
    bool_or(l.alias_mapping_applied::boolean),
    count(*)::integer
FROM staging_row_lineage AS l
JOIN training.exercises AS e
    ON e.canonical_name = l.canonical_exercise_name
WHERE
    l.name_normalization_applied::boolean
    OR l.alias_mapping_applied::boolean
GROUP BY
    l.raw_exercise_name,
    l.preprocessing_version,
    l.normalized_exercise_name,
    e.exercise_id;

INSERT INTO training.workout_sets (
    set_id,
    representative_source_row,
    session_id,
    exercise_id,
    raw_exercise_name,
    normalized_exercise_name,
    name_normalization_applied,
    alias_mapping_applied,
    set_order,
    weight,
    weight_unit,
    reps,
    distance,
    distance_unit,
    seconds,
    notes,
    workout_notes,
    raw_row_count,
    exact_duplicate_extra_count,
    alias_shadow_count,
    is_outlier,
    outlier_reason,
    has_set_order_collision,
    set_order_collision_group_id,
    include_in_volume_metrics,
    include_in_e1rm,
    e1rm_formula_version,
    validation_reason,
    source_file_sha256,
    preprocessing_version
)
SELECT
    s.processed_set_id,
    s.representative_source_row::integer,
    s.session_id,
    e.exercise_id,
    s.raw_exercise_name,
    s.normalized_exercise_name,
    s.name_normalization_applied::boolean,
    s.alias_mapping_applied::boolean,
    s.set_order::integer,
    s.weight::numeric,
    s.weight_unit,
    s.reps::integer,
    s.distance::numeric,
    s.distance_unit,
    s.seconds::integer,
    s.notes,
    s.workout_notes,
    s.raw_row_count::integer,
    s.exact_duplicate_extra_count::integer,
    s.alias_shadow_count::integer,
    s.is_outlier::boolean,
    s.outlier_reason,
    s.has_set_order_collision::boolean,
    s.set_order_collision_group_id,
    s.include_in_volume_metrics::boolean,
    s.include_in_e1rm::boolean,
    s.e1rm_formula_version,
    s.validation_reason,
    s.source_file_sha256,
    s.preprocessing_version
FROM staging_workout_sets AS s
JOIN training.exercises AS e
    ON e.canonical_name = s.canonical_exercise_name;

INSERT INTO training.row_lineage (
    source_row,
    raw_record_id,
    set_id,
    representative_source_row,
    row_status,
    include_in_processed,
    is_exact_duplicate,
    exact_duplicate_of_source_row,
    is_alias_shadow,
    alias_shadow_of_source_row,
    raw_exercise_name,
    normalized_exercise_name,
    name_normalization_applied,
    alias_mapping_applied,
    is_outlier,
    outlier_reason,
    has_set_order_collision,
    set_order_collision_group_id,
    include_in_volume_metrics,
    include_in_e1rm,
    validation_reason,
    source_file_sha256,
    preprocessing_version
)
SELECT
    source_row::integer,
    raw_record_id,
    processed_set_id,
    representative_source_row::integer,
    row_status,
    include_in_processed::boolean,
    is_exact_duplicate::boolean,
    exact_duplicate_of_source_row::integer,
    is_alias_shadow::boolean,
    alias_shadow_of_source_row::integer,
    raw_exercise_name,
    normalized_exercise_name,
    name_normalization_applied::boolean,
    alias_mapping_applied::boolean,
    is_outlier::boolean,
    outlier_reason,
    has_set_order_collision::boolean,
    set_order_collision_group_id,
    include_in_volume_metrics::boolean,
    include_in_e1rm::boolean,
    validation_reason,
    source_file_sha256,
    preprocessing_version
FROM staging_row_lineage;

DO $validate$
DECLARE
    actual bigint;
BEGIN
    SELECT count(*) INTO actual FROM training.workout_sessions;
    IF actual <> {int(source['unique_timestamps'])} THEN
        RAISE EXCEPTION 'session count mismatch: expected %, got %', {int(source['unique_timestamps'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.exercises;
    IF actual <> {int(summary['canonicalization']['canonical_exercise_name_count'])} THEN
        RAISE EXCEPTION 'exercise count mismatch: expected %, got %', {int(summary['canonicalization']['canonical_exercise_name_count'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.workout_sets;
    IF actual <> {int(reconciliation['processed_rows'])} THEN
        RAISE EXCEPTION 'set count mismatch: expected %, got %', {int(reconciliation['processed_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.row_lineage;
    IF actual <> {int(reconciliation['lineage_rows'])} THEN
        RAISE EXCEPTION 'lineage count mismatch: expected %, got %', {int(reconciliation['lineage_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.row_lineage WHERE row_status = 'kept';
    IF actual <> {int(status_counts['kept'])} THEN
        RAISE EXCEPTION 'kept lineage count mismatch: expected %, got %', {int(status_counts['kept'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.row_lineage WHERE is_exact_duplicate;
    IF actual <> {int(reconciliation['excluded_exact_duplicate_rows'])} THEN
        RAISE EXCEPTION 'exact duplicate count mismatch: expected %, got %', {int(reconciliation['excluded_exact_duplicate_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.row_lineage WHERE is_alias_shadow;
    IF actual <> {int(reconciliation['excluded_alias_shadow_rows'])} THEN
        RAISE EXCEPTION 'alias shadow count mismatch: expected %, got %', {int(reconciliation['excluded_alias_shadow_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.workout_sets WHERE is_outlier;
    IF actual <> {int(flags['outlier_processed_rows'])} THEN
        RAISE EXCEPTION 'outlier count mismatch: expected %, got %', {int(flags['outlier_processed_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.workout_sets WHERE has_set_order_collision;
    IF actual <> {int(flags['set_order_collision_processed_rows'])} THEN
        RAISE EXCEPTION 'collision row count mismatch: expected %, got %', {int(flags['set_order_collision_processed_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.workout_sets WHERE include_in_volume_metrics;
    IF actual <> {int(eligibility['volume_eligible_processed_rows'])} THEN
        RAISE EXCEPTION 'volume eligibility mismatch: expected %, got %', {int(eligibility['volume_eligible_processed_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM training.workout_sets WHERE include_in_e1rm;
    IF actual <> {int(eligibility['e1rm_eligible_processed_rows'])} THEN
        RAISE EXCEPTION 'e1RM eligibility mismatch: expected %, got %', {int(eligibility['e1rm_eligible_processed_rows'])}, actual;
    END IF;

    SELECT count(*) INTO actual
    FROM (
        SELECT ws.set_id
        FROM training.workout_sets AS ws
        JOIN training.row_lineage AS rl USING (set_id)
        GROUP BY ws.set_id, ws.raw_row_count
        HAVING count(*) <> ws.raw_row_count
    ) AS mismatches;
    IF actual <> 0 THEN
        RAISE EXCEPTION 'per-set lineage reconciliation failed for % sets', actual;
    END IF;
END
$validate$;

COMMIT;

SELECT json_build_object(
    'preprocessing_version', (SELECT preprocessing_version FROM training.preprocessing_runs),
    'sessions', (SELECT count(*) FROM training.workout_sessions),
    'exercises', (SELECT count(*) FROM training.exercises),
    'aliases', (SELECT count(*) FROM training.exercise_aliases),
    'sets', (SELECT count(*) FROM training.workout_sets),
    'lineage_rows', (SELECT count(*) FROM training.row_lineage)
) AS phase3_load_summary;
"""


def apply_migrations(
    *,
    project_root: str | Path,
    config: DatabaseConfig,
    password: str | None,
    psql_path: str | Path | None = None,
) -> None:
    migration_dir = Path(project_root).resolve() / "db" / "migrations"
    migrations = sorted(migration_dir.glob("*.sql"))
    if not migrations:
        raise FileNotFoundError(f"No migrations found in {migration_dir}")
    for migration in migrations:
        run_psql(
            config=config,
            sql=migration.read_text(encoding="utf-8"),
            password=password,
            psql_path=psql_path,
        )


def load_database(
    *,
    project_root: str | Path,
    config: DatabaseConfig,
    password: str | None,
    psql_path: str | Path | None = None,
    replace: bool = False,
) -> str:
    inputs = load_inputs(project_root)
    apply_migrations(
        project_root=inputs.project_root,
        config=config,
        password=password,
        psql_path=psql_path,
    )
    result = run_psql(
        config=config,
        sql=build_load_sql(inputs, replace=replace),
        password=password,
        psql_path=psql_path,
    )
    return result.stdout
