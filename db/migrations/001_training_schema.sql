BEGIN;

CREATE SCHEMA IF NOT EXISTS training;

CREATE TABLE IF NOT EXISTS training.schema_migrations (
    migration_id text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS training.preprocessing_runs (
    preprocessing_version text PRIMARY KEY,
    source_path text NOT NULL,
    source_file_sha256 char(64) NOT NULL,
    processed_file_sha256 char(64) NOT NULL,
    lineage_file_sha256 char(64) NOT NULL,
    raw_row_count integer NOT NULL CHECK (raw_row_count > 0),
    processed_row_count integer NOT NULL CHECK (processed_row_count > 0),
    exact_duplicate_count integer NOT NULL CHECK (exact_duplicate_count >= 0),
    alias_shadow_count integer NOT NULL CHECK (alias_shadow_count >= 0),
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT preprocessing_source_hash_format
        CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT preprocessing_processed_hash_format
        CHECK (processed_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT preprocessing_lineage_hash_format
        CHECK (lineage_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT preprocessing_row_reconciliation
        CHECK (
            processed_row_count + exact_duplicate_count + alias_shadow_count
            = raw_row_count
        )
);

CREATE TABLE IF NOT EXISTS training.workout_sessions (
    session_id text PRIMARY KEY,
    started_at timestamp without time zone NOT NULL,
    workout_name text NOT NULL CHECK (btrim(workout_name) <> ''),
    preprocessing_version text NOT NULL
        REFERENCES training.preprocessing_runs(preprocessing_version),
    UNIQUE (started_at)
);

CREATE TABLE IF NOT EXISTS training.exercises (
    exercise_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name text NOT NULL UNIQUE CHECK (btrim(canonical_name) <> '')
);

CREATE TABLE IF NOT EXISTS training.exercise_aliases (
    raw_name text NOT NULL CHECK (raw_name <> ''),
    preprocessing_version text NOT NULL
        REFERENCES training.preprocessing_runs(preprocessing_version),
    normalized_name text NOT NULL CHECK (normalized_name <> ''),
    exercise_id bigint NOT NULL
        REFERENCES training.exercises(exercise_id),
    name_normalization_applied boolean NOT NULL,
    alias_mapping_applied boolean NOT NULL,
    observed_raw_row_count integer NOT NULL CHECK (observed_raw_row_count > 0),
    PRIMARY KEY (raw_name, preprocessing_version),
    CONSTRAINT exercise_alias_has_transformation
        CHECK (name_normalization_applied OR alias_mapping_applied)
);

CREATE TABLE IF NOT EXISTS training.workout_sets (
    set_id text PRIMARY KEY,
    representative_source_row integer NOT NULL UNIQUE
        CHECK (representative_source_row > 0),
    session_id text NOT NULL
        REFERENCES training.workout_sessions(session_id),
    exercise_id bigint NOT NULL
        REFERENCES training.exercises(exercise_id),
    raw_exercise_name text NOT NULL,
    normalized_exercise_name text NOT NULL,
    name_normalization_applied boolean NOT NULL,
    alias_mapping_applied boolean NOT NULL,
    set_order integer NOT NULL CHECK (set_order > 0),
    weight numeric(14, 3) NOT NULL CHECK (weight >= 0),
    weight_unit text NOT NULL,
    reps integer NOT NULL CHECK (reps >= 0),
    distance numeric(14, 3) NOT NULL CHECK (distance >= 0),
    distance_unit text NOT NULL,
    seconds integer NOT NULL CHECK (seconds >= 0),
    notes text,
    workout_notes text,
    raw_row_count integer NOT NULL CHECK (raw_row_count > 0),
    exact_duplicate_extra_count integer NOT NULL
        CHECK (exact_duplicate_extra_count >= 0),
    alias_shadow_count integer NOT NULL CHECK (alias_shadow_count >= 0),
    is_outlier boolean NOT NULL,
    outlier_reason text,
    has_set_order_collision boolean NOT NULL,
    set_order_collision_group_id text,
    include_in_volume_metrics boolean NOT NULL,
    include_in_e1rm boolean NOT NULL,
    e1rm_formula_version text NOT NULL,
    validation_reason text,
    source_file_sha256 char(64) NOT NULL,
    preprocessing_version text NOT NULL
        REFERENCES training.preprocessing_runs(preprocessing_version),
    CONSTRAINT workout_set_source_hash_format
        CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT workout_set_row_reconciliation
        CHECK (
            raw_row_count
            = 1 + exact_duplicate_extra_count + alias_shadow_count
        ),
    CONSTRAINT workout_set_outlier_reason_consistency
        CHECK (is_outlier = (outlier_reason IS NOT NULL)),
    CONSTRAINT workout_set_collision_consistency
        CHECK (
            has_set_order_collision
            = (set_order_collision_group_id IS NOT NULL)
        ),
    CONSTRAINT workout_set_e1rm_requires_volume_eligibility
        CHECK (NOT include_in_e1rm OR include_in_volume_metrics)
);

CREATE TABLE IF NOT EXISTS training.row_lineage (
    source_row integer PRIMARY KEY CHECK (source_row > 0),
    raw_record_id text NOT NULL UNIQUE,
    set_id text NOT NULL
        REFERENCES training.workout_sets(set_id) ON DELETE CASCADE,
    representative_source_row integer NOT NULL
        CHECK (representative_source_row > 0),
    row_status text NOT NULL CHECK (
        row_status IN ('kept', 'excluded_exact_duplicate', 'excluded_alias_shadow')
    ),
    include_in_processed boolean NOT NULL,
    is_exact_duplicate boolean NOT NULL,
    exact_duplicate_of_source_row integer,
    is_alias_shadow boolean NOT NULL,
    alias_shadow_of_source_row integer,
    raw_exercise_name text NOT NULL,
    normalized_exercise_name text NOT NULL,
    name_normalization_applied boolean NOT NULL,
    alias_mapping_applied boolean NOT NULL,
    is_outlier boolean NOT NULL,
    outlier_reason text,
    has_set_order_collision boolean NOT NULL,
    set_order_collision_group_id text,
    include_in_volume_metrics boolean NOT NULL,
    include_in_e1rm boolean NOT NULL,
    validation_reason text,
    source_file_sha256 char(64) NOT NULL,
    preprocessing_version text NOT NULL
        REFERENCES training.preprocessing_runs(preprocessing_version),
    CONSTRAINT lineage_source_hash_format
        CHECK (source_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT lineage_status_consistency CHECK (
        (row_status = 'kept' AND include_in_processed)
        OR (row_status <> 'kept' AND NOT include_in_processed)
    ),
    CONSTRAINT lineage_exact_duplicate_consistency CHECK (
        is_exact_duplicate = (row_status = 'excluded_exact_duplicate')
        AND is_exact_duplicate = (exact_duplicate_of_source_row IS NOT NULL)
    ),
    CONSTRAINT lineage_alias_shadow_consistency CHECK (
        is_alias_shadow = (row_status = 'excluded_alias_shadow')
        AND is_alias_shadow = (alias_shadow_of_source_row IS NOT NULL)
    ),
    CONSTRAINT lineage_single_exclusion_reason CHECK (
        NOT (is_exact_duplicate AND is_alias_shadow)
    ),
    CONSTRAINT lineage_outlier_reason_consistency
        CHECK (is_outlier = (outlier_reason IS NOT NULL)),
    CONSTRAINT lineage_collision_consistency
        CHECK (
            has_set_order_collision
            = (set_order_collision_group_id IS NOT NULL)
        ),
    CONSTRAINT lineage_e1rm_requires_volume_eligibility
        CHECK (NOT include_in_e1rm OR include_in_volume_metrics)
);

CREATE INDEX IF NOT EXISTS idx_workout_sessions_started_at
    ON training.workout_sessions (started_at);
CREATE INDEX IF NOT EXISTS idx_workout_sets_session
    ON training.workout_sets (session_id);
CREATE INDEX IF NOT EXISTS idx_workout_sets_exercise
    ON training.workout_sets (exercise_id);
CREATE INDEX IF NOT EXISTS idx_workout_sets_session_exercise
    ON training.workout_sets (session_id, exercise_id);
CREATE INDEX IF NOT EXISTS idx_workout_sets_metric_flags
    ON training.workout_sets (include_in_volume_metrics, include_in_e1rm);
CREATE INDEX IF NOT EXISTS idx_row_lineage_set
    ON training.row_lineage (set_id);
CREATE INDEX IF NOT EXISTS idx_row_lineage_status
    ON training.row_lineage (row_status);
CREATE INDEX IF NOT EXISTS idx_exercise_aliases_exercise
    ON training.exercise_aliases (exercise_id);

CREATE OR REPLACE VIEW training.workout_set_details AS
SELECT
    ws.set_id,
    ws.representative_source_row,
    s.started_at,
    s.workout_name,
    e.canonical_name AS exercise_name,
    ws.set_order,
    ws.weight,
    ws.weight_unit,
    ws.reps,
    ws.distance,
    ws.distance_unit,
    ws.seconds,
    ws.is_outlier,
    ws.outlier_reason,
    ws.has_set_order_collision,
    ws.include_in_volume_metrics,
    ws.include_in_e1rm,
    ws.preprocessing_version
FROM training.workout_sets AS ws
JOIN training.workout_sessions AS s USING (session_id)
JOIN training.exercises AS e USING (exercise_id);

COMMENT ON TABLE training.workout_sets IS
    'One canonical logical set per processed_set_id; duplicate and alias collapses are retained as counts.';
COMMENT ON TABLE training.row_lineage IS
    'One row per source CSV record, preserving every raw-to-processed mapping.';
COMMENT ON COLUMN training.workout_sets.weight_unit IS
    'Source unit is unverified in preprocessing_v1 and is stored as unknown_source_unit.';
COMMENT ON COLUMN training.workout_sets.set_order IS
    'Not globally unique: two known collision groups are intentionally retained and flagged.';

INSERT INTO training.schema_migrations (migration_id)
VALUES ('001_training_schema')
ON CONFLICT (migration_id) DO NOTHING;

COMMIT;
