"""Build deterministic processed and row-lineage views from the raw workout CSV."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .policy import (
    DEFAULT_ALIAS_PATH,
    DEFAULT_POLICY_PATH,
    build_alias_map,
    load_alias_decisions,
    load_policy,
    normalize_exercise_display,
    normalize_exercise_key,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_PATH = PROJECT_ROOT / "data" / "raw" / "weightlifting_721_workouts.csv"
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_raw_workouts(path: Path, expected_columns: list[str]) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Raw workout CSV not found: {path}")
    frame = pd.read_csv(path)
    if list(frame.columns) != expected_columns:
        raise ValueError(
            "Unexpected raw schema. "
            f"Expected {expected_columns}, received {list(frame.columns)}"
        )
    frame.insert(0, "source_row", np.arange(2, len(frame) + 2, dtype=int))
    frame["_date_ts"] = pd.to_datetime(frame["Date"], errors="coerce")
    invalid_dates = int(frame["_date_ts"].isna().sum())
    if invalid_dates:
        raise ValueError(f"Found {invalid_dates} unparseable Date values.")
    return frame


def _stable_scalar(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if not math.isfinite(number):
            return str(number)
        return number
    return str(value)


def _stable_digest(prefix: str, values: Iterable[Any], length: int = 24) -> str:
    payload = json.dumps(
        [_stable_scalar(value) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def _hash_rows(frame: pd.DataFrame, columns: list[str], prefix: str) -> pd.Series:
    return frame.loc[:, columns].apply(
        lambda row: _stable_digest(prefix, row.tolist()), axis=1
    )


def _outlier_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if float(row["Weight"]) >= 1000:
        reasons.append("extreme_weight_ge_1000")
    if int(row["Reps"]) >= 25:
        reasons.append("high_reps_ge_25")
    if (
        float(row["Weight"]) > 0
        and int(row["Reps"]) == 0
        and float(row["Distance"]) == 0
        and int(row["Seconds"]) == 0
    ):
        reasons.append("positive_weight_zero_reps")
    if any(
        float(row[column]) < 0 for column in ["Weight", "Reps", "Distance", "Seconds"]
    ):
        reasons.append("negative_numeric_value")
    if int(row["Set Order"]) <= 0:
        reasons.append("non_positive_set_order")
    return ";".join(reasons)


def _metric_blocked(row: pd.Series) -> bool:
    return bool(
        float(row["Weight"]) >= 1000
        or (
            float(row["Weight"]) > 0
            and int(row["Reps"]) == 0
            and float(row["Distance"]) == 0
            and int(row["Seconds"]) == 0
        )
        or any(
            float(row[column]) < 0
            for column in ["Weight", "Reps", "Distance", "Seconds"]
        )
    )


def _lineage_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if bool(row["is_exact_duplicate"]):
        reasons.append("exact_duplicate")
    if bool(row["is_alias_shadow"]):
        reasons.append("alias_shadow")
    if str(row["outlier_reason"]):
        reasons.extend(str(row["outlier_reason"]).split(";"))
    if bool(row["has_set_order_collision"]):
        reasons.append("set_order_collision")
    return ";".join(dict.fromkeys(reasons))


def _processed_reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if int(row["exact_duplicate_extra_count"]):
        reasons.append(
            f"collapsed_exact_duplicates:{int(row['exact_duplicate_extra_count'])}"
        )
    if int(row["alias_shadow_count"]):
        reasons.append(f"collapsed_alias_shadows:{int(row['alias_shadow_count'])}")
    if str(row["outlier_reason"]):
        reasons.extend(str(row["outlier_reason"]).split(";"))
    if bool(row["has_set_order_collision"]):
        reasons.append("set_order_collision")
    return ";".join(dict.fromkeys(reasons))


def build_preprocessed_views(
    raw: pd.DataFrame,
    policy: dict[str, Any],
    alias_decisions: list[dict[str, str]],
    source_sha256: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return one-row-per-logical-set output and one-row-per-raw-row lineage."""

    expected_columns = list(policy["expected_columns"])
    missing = sorted(set(["source_row", "_date_ts", *expected_columns]).difference(raw.columns))
    if missing:
        raise ValueError(f"Raw frame is missing preprocessing columns: {missing}")

    frame = raw.copy()
    alias_map = build_alias_map(alias_decisions)
    frame["_normalized_exercise_name"] = frame["Exercise Name"].map(
        normalize_exercise_display
    )
    frame["_exercise_name_key"] = frame["Exercise Name"].map(normalize_exercise_key)
    frame["_canonical_exercise_name"] = [
        alias_map.get(key, normalized)
        for key, normalized in zip(
            frame["_exercise_name_key"], frame["_normalized_exercise_name"]
        )
    ]
    frame["_name_normalization_applied"] = frame["Exercise Name"].ne(
        frame["_normalized_exercise_name"]
    )
    frame["_alias_mapping_applied"] = frame["_canonical_exercise_name"].ne(
        frame["_normalized_exercise_name"]
    )

    frame["session_id"] = frame.apply(
        lambda row: _stable_digest(
            "session", [row["Date"], row["Workout Name"]]
        ),
        axis=1,
    )

    frame["_raw_record_key"] = _hash_rows(frame, expected_columns, "raw")
    frame["_exact_representative_source_row"] = frame.groupby(
        "_raw_record_key", sort=False
    )["source_row"].transform("min")
    frame["is_exact_duplicate"] = frame["source_row"].ne(
        frame["_exact_representative_source_row"]
    )

    frame["_notes_key"] = frame["Notes"].fillna("")
    frame["_workout_notes_key"] = frame["Workout Notes"].fillna("")
    logical_columns = [
        "Date",
        "Workout Name",
        "_canonical_exercise_name",
        "Set Order",
        "Weight",
        "Reps",
        "Distance",
        "Seconds",
        "_notes_key",
        "_workout_notes_key",
    ]
    frame["processed_set_id"] = _hash_rows(frame, logical_columns, "set")
    frame["representative_source_row"] = frame.groupby(
        "processed_set_id", sort=False
    )["source_row"].transform("min")
    frame["include_in_processed"] = frame["source_row"].eq(
        frame["representative_source_row"]
    )
    frame["is_alias_shadow"] = (
        ~frame["is_exact_duplicate"] & ~frame["include_in_processed"]
    )
    frame["exact_duplicate_of_source_row"] = frame[
        "_exact_representative_source_row"
    ].where(frame["is_exact_duplicate"], pd.NA).astype("Int64")
    frame["alias_shadow_of_source_row"] = frame["representative_source_row"].where(
        frame["is_alias_shadow"], pd.NA
    ).astype("Int64")

    frame["row_status"] = np.select(
        [frame["include_in_processed"], frame["is_exact_duplicate"]],
        ["kept", "excluded_exact_duplicate"],
        default="excluded_alias_shadow",
    )
    frame["outlier_reason"] = frame.apply(_outlier_reason, axis=1)
    frame["is_outlier"] = frame["outlier_reason"].ne("")
    frame["_metric_blocked"] = frame.apply(_metric_blocked, axis=1)

    representative = frame.loc[frame["include_in_processed"]].copy()
    collision_columns = ["session_id", "_canonical_exercise_name", "Set Order"]
    representative["_set_order_collision_count"] = representative.groupby(
        collision_columns, sort=False
    )["processed_set_id"].transform("size")
    representative["has_set_order_collision"] = representative[
        "_set_order_collision_count"
    ].gt(1)
    representative["set_order_collision_group_id"] = representative.apply(
        lambda row: _stable_digest(
            "collision",
            [row["session_id"], row["_canonical_exercise_name"], row["Set Order"]],
        )
        if bool(row["has_set_order_collision"])
        else "",
        axis=1,
    )
    collision_lookup = representative.set_index("processed_set_id")[
        ["has_set_order_collision", "set_order_collision_group_id"]
    ]
    frame["has_set_order_collision"] = frame["processed_set_id"].map(
        collision_lookup["has_set_order_collision"]
    ).astype(bool)
    frame["set_order_collision_group_id"] = frame["processed_set_id"].map(
        collision_lookup["set_order_collision_group_id"]
    )

    group_counts = frame.groupby("processed_set_id", sort=False).agg(
        raw_row_count=("source_row", "size"),
        exact_duplicate_extra_count=("is_exact_duplicate", "sum"),
        alias_shadow_count=("is_alias_shadow", "sum"),
    )
    representative = representative.drop(
        columns=["_set_order_collision_count", "has_set_order_collision", "set_order_collision_group_id"]
    ).merge(group_counts, left_on="processed_set_id", right_index=True, how="left")
    representative = representative.merge(
        collision_lookup,
        left_on="processed_set_id",
        right_index=True,
        how="left",
    )

    representative["include_in_volume_metrics"] = (
        representative["Weight"].gt(0)
        & representative["Reps"].gt(0)
        & ~representative["_metric_blocked"]
    )
    e1rm_policy = policy["metrics"]["estimated_1rm"]
    representative["include_in_e1rm"] = (
        representative["Weight"].gt(0)
        & representative["Reps"].between(
            int(e1rm_policy["rep_min"]), int(e1rm_policy["rep_max"])
        )
        & ~representative["_metric_blocked"]
    )
    representative["validation_reason"] = representative.apply(
        _processed_reason, axis=1
    )

    processed = pd.DataFrame(
        {
            "processed_set_id": representative["processed_set_id"],
            "representative_source_row": representative["source_row"].astype(int),
            "session_id": representative["session_id"],
            "session_timestamp": representative["_date_ts"].dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "workout_name": representative["Workout Name"],
            "raw_exercise_name": representative["Exercise Name"],
            "normalized_exercise_name": representative["_normalized_exercise_name"],
            "canonical_exercise_name": representative["_canonical_exercise_name"],
            "name_normalization_applied": representative[
                "_name_normalization_applied"
            ].astype(bool),
            "alias_mapping_applied": representative["_alias_mapping_applied"].astype(bool),
            "set_order": representative["Set Order"].astype(int),
            "weight": representative["Weight"].astype(float),
            "weight_unit": policy["units"]["weight"],
            "reps": representative["Reps"].astype(int),
            "distance": representative["Distance"].astype(float),
            "distance_unit": policy["units"]["distance"],
            "seconds": representative["Seconds"].astype(int),
            "notes": representative["Notes"].fillna(""),
            "workout_notes": representative["Workout Notes"].fillna(""),
            "raw_row_count": representative["raw_row_count"].astype(int),
            "exact_duplicate_extra_count": representative[
                "exact_duplicate_extra_count"
            ].astype(int),
            "alias_shadow_count": representative["alias_shadow_count"].astype(int),
            "is_outlier": representative["is_outlier"].astype(bool),
            "outlier_reason": representative["outlier_reason"],
            "has_set_order_collision": representative[
                "has_set_order_collision"
            ].astype(bool),
            "set_order_collision_group_id": representative[
                "set_order_collision_group_id"
            ],
            "include_in_volume_metrics": representative[
                "include_in_volume_metrics"
            ].astype(bool),
            "include_in_e1rm": representative["include_in_e1rm"].astype(bool),
            "e1rm_formula_version": e1rm_policy["formula_version"],
            "validation_reason": representative["validation_reason"],
            "source_file_sha256": source_sha256,
            "preprocessing_version": policy["version"],
        }
    ).sort_values(
        ["session_timestamp", "representative_source_row"], ignore_index=True
    )

    frame["include_in_volume_metrics"] = (
        frame["include_in_processed"]
        & frame["Weight"].gt(0)
        & frame["Reps"].gt(0)
        & ~frame["_metric_blocked"]
    )
    frame["include_in_e1rm"] = (
        frame["include_in_processed"]
        & frame["Weight"].gt(0)
        & frame["Reps"].between(
            int(e1rm_policy["rep_min"]), int(e1rm_policy["rep_max"])
        )
        & ~frame["_metric_blocked"]
    )
    frame["validation_reason"] = frame.apply(_lineage_reason, axis=1)

    lineage = pd.DataFrame(
        {
            "source_row": frame["source_row"].astype(int),
            "raw_record_id": frame["source_row"].map(lambda value: f"raw_{int(value):06d}"),
            "processed_set_id": frame["processed_set_id"],
            "representative_source_row": frame["representative_source_row"].astype(int),
            "row_status": frame["row_status"],
            "include_in_processed": frame["include_in_processed"].astype(bool),
            "is_exact_duplicate": frame["is_exact_duplicate"].astype(bool),
            "exact_duplicate_of_source_row": frame["exact_duplicate_of_source_row"],
            "is_alias_shadow": frame["is_alias_shadow"].astype(bool),
            "alias_shadow_of_source_row": frame["alias_shadow_of_source_row"],
            "raw_exercise_name": frame["Exercise Name"],
            "normalized_exercise_name": frame["_normalized_exercise_name"],
            "canonical_exercise_name": frame["_canonical_exercise_name"],
            "name_normalization_applied": frame["_name_normalization_applied"].astype(bool),
            "alias_mapping_applied": frame["_alias_mapping_applied"].astype(bool),
            "is_outlier": frame["is_outlier"].astype(bool),
            "outlier_reason": frame["outlier_reason"],
            "has_set_order_collision": frame["has_set_order_collision"].astype(bool),
            "set_order_collision_group_id": frame["set_order_collision_group_id"],
            "include_in_volume_metrics": frame["include_in_volume_metrics"].astype(bool),
            "include_in_e1rm": frame["include_in_e1rm"].astype(bool),
            "validation_reason": frame["validation_reason"],
            "source_file_sha256": source_sha256,
            "preprocessing_version": policy["version"],
        }
    ).sort_values("source_row", ignore_index=True)

    return processed, lineage


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
    temporary.replace(path)


def _atomic_write_text(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def run_preprocessing(
    raw_path: Path = DEFAULT_RAW_PATH,
    processed_dir: Path = DEFAULT_PROCESSED_DIR,
    reports_dir: Path = DEFAULT_REPORTS_DIR,
    policy_path: Path = DEFAULT_POLICY_PATH,
    alias_path: Path = DEFAULT_ALIAS_PATH,
) -> dict[str, Any]:
    from .validation import build_validation_summary, render_validation_report

    policy = load_policy(policy_path)
    alias_decisions = load_alias_decisions(alias_path)
    source_hash_before = sha256_file(raw_path)
    raw = load_raw_workouts(raw_path, list(policy["expected_columns"]))
    processed, lineage = build_preprocessed_views(
        raw, policy, alias_decisions, source_hash_before
    )

    summary = build_validation_summary(
        raw=raw,
        processed=processed,
        lineage=lineage,
        policy=policy,
        alias_decisions=alias_decisions,
        source_sha256=source_hash_before,
    )

    workout_sets_path = processed_dir / "workout_sets.csv"
    lineage_path = processed_dir / "row_lineage.csv"
    _atomic_write_csv(processed, workout_sets_path)
    _atomic_write_csv(lineage, lineage_path)

    source_hash_after = sha256_file(raw_path)
    if source_hash_before != source_hash_after:
        raise RuntimeError("Raw input hash changed while preprocessing was running.")

    summary["output_files"] = {
        "workout_sets": {
            "path": workout_sets_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(workout_sets_path),
        },
        "row_lineage": {
            "path": lineage_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(lineage_path),
        },
    }
    summary["policy_files"] = {
        "policy": {
            "path": policy_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(policy_path),
        },
        "alias_decisions": {
            "path": alias_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256_file(alias_path),
        },
    }

    validation_json_path = reports_dir / "preprocessing_validation.json"
    validation_md_path = reports_dir / "PREPROCESSING.md"
    _atomic_write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        validation_json_path,
    )
    _atomic_write_text(
        render_validation_report(summary, alias_decisions, policy),
        validation_md_path,
    )
    return summary
