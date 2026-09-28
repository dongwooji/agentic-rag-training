from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_SETS_PATH = Path("data/processed/workout_sets.csv")
DEFAULT_LINEAGE_PATH = Path("data/processed/row_lineage.csv")


def _as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().eq("true")


def load_sets(path: Path = DEFAULT_SETS_PATH) -> pd.DataFrame:
    frame = pd.read_csv(path, parse_dates=["session_timestamp"])
    for column in ("include_in_volume_metrics", "include_in_e1rm", "is_outlier"):
        frame[column] = _as_bool(frame[column])
    frame["e1rm"] = (
        frame["weight"] * (1 + frame["reps"] / 30)
    ).where(frame["include_in_e1rm"])
    frame["volume_load"] = (
        frame["weight"] * frame["reps"]
    ).where(frame["include_in_volume_metrics"], 0.0)
    return frame


def _timestamp(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _session_best(frame: pd.DataFrame, exercise: str) -> pd.DataFrame:
    selected = frame.loc[
        frame["canonical_exercise_name"].eq(exercise)
        & frame["include_in_e1rm"]
    ]
    return (
        selected.groupby(["session_id", "session_timestamp"], as_index=False)["e1rm"]
        .max()
        .sort_values("session_timestamp")
        .reset_index(drop=True)
    )


def session_summary(frame: pd.DataFrame) -> dict[str, Any]:
    sessions = frame[["session_id", "session_timestamp"]].drop_duplicates()
    return {
        "session_count": int(sessions["session_id"].nunique()),
        "first_session": _timestamp(sessions["session_timestamp"].min()),
        "last_session": _timestamp(sessions["session_timestamp"].max()),
    }


def longest_training_gap(frame: pd.DataFrame) -> dict[str, Any]:
    sessions = (
        frame["session_timestamp"].drop_duplicates().sort_values().reset_index(drop=True)
    )
    gaps = sessions.diff().dt.total_seconds() / 86400
    index = int(gaps.idxmax())
    return {
        "previous_session": _timestamp(sessions.iloc[index - 1]),
        "next_session": _timestamp(sessions.iloc[index]),
        "gap_days": float(gaps.iloc[index]),
        "calendar_day_difference": int(
            (sessions.iloc[index].normalize() - sessions.iloc[index - 1].normalize()).days
        ),
    }


def top_exercise_by_session_count(frame: pd.DataFrame) -> dict[str, Any]:
    counts = (
        frame.groupby("canonical_exercise_name")["session_id"]
        .nunique()
        .reset_index(name="session_count")
        .sort_values(["session_count", "canonical_exercise_name"], ascending=[False, True])
    )
    row = counts.iloc[0]
    return {
        "exercise": str(row["canonical_exercise_name"]),
        "session_count": int(row["session_count"]),
    }


def e1rm_window_summary(
    frame: pd.DataFrame, exercise: str, window_sessions: int = 5
) -> dict[str, Any]:
    values = _session_best(frame, exercise)
    first = float(values.head(window_sessions)["e1rm"].median())
    last = float(values.tail(window_sessions)["e1rm"].median())
    return {
        "exercise": exercise,
        "eligible_sessions": int(len(values)),
        "window_sessions": int(window_sessions),
        "first_window_median_e1rm": first,
        "last_window_median_e1rm": last,
        "change_pct": 100 * (last / first - 1),
    }


def e1rm_peak_and_latest(frame: pd.DataFrame, exercise: str) -> dict[str, Any]:
    values = _session_best(frame, exercise)
    peak = values.loc[values["e1rm"].idxmax()]
    latest = values.iloc[-1]
    return {
        "exercise": exercise,
        "peak_e1rm": float(peak["e1rm"]),
        "peak_session": _timestamp(peak["session_timestamp"]),
        "latest_e1rm": float(latest["e1rm"]),
        "latest_session": _timestamp(latest["session_timestamp"]),
        "latest_vs_peak_pct": 100 * (float(latest["e1rm"]) / float(peak["e1rm"]) - 1),
    }


def lineage_reconciliation(path: Path = DEFAULT_LINEAGE_PATH) -> dict[str, Any]:
    lineage = pd.read_csv(path)
    counts = lineage["row_status"].value_counts()
    return {
        "raw_lineage_rows": int(len(lineage)),
        "processed_rows": int(counts.get("kept", 0)),
        "excluded_exact_duplicate_rows": int(
            counts.get("excluded_exact_duplicate", 0)
        ),
        "excluded_alias_shadow_rows": int(counts.get("excluded_alias_shadow", 0)),
    }


def e1rm_interval_summary(
    frame: pd.DataFrame, exercise: str, start: str, end: str
) -> dict[str, Any]:
    values = _session_best(frame, exercise)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    selected = values.loc[values["session_timestamp"].between(start_ts, end_ts)]
    if len(selected) < 3:
        raise ValueError(f"Not enough e1RM sessions for {exercise} in {start}..{end}")
    start_level = float(selected.head(3)["e1rm"].median())
    end_level = float(selected.tail(3)["e1rm"].median())
    median_level = float(selected["e1rm"].median())
    return {
        "exercise": exercise,
        "start": _timestamp(selected.iloc[0]["session_timestamp"]),
        "end": _timestamp(selected.iloc[-1]["session_timestamp"]),
        "duration_days": int(
            (
                selected.iloc[-1]["session_timestamp"]
                - selected.iloc[0]["session_timestamp"]
            ).days
        ),
        "session_count": int(len(selected)),
        "start_3_session_median_e1rm": start_level,
        "end_3_session_median_e1rm": end_level,
        "change_pct": 100 * (end_level / start_level - 1),
        "within_period_range_pct": 100
        * (float(selected["e1rm"].max()) - float(selected["e1rm"].min()))
        / median_level,
    }


def weekly_load_comparison(
    frame: pd.DataFrame, exercise: str, start: str, end: str
) -> dict[str, Any]:
    selected = frame.loc[frame["canonical_exercise_name"].eq(exercise)].copy()
    selected["week_start"] = (
        selected["session_timestamp"].dt.to_period("W-SUN").dt.start_time
    )
    weekly = (
        selected.groupby("week_start")
        .agg(
            set_count=("processed_set_id", "size"),
            volume_load=("volume_load", "sum"),
            frequency=("session_timestamp", "nunique"),
        )
        .sort_index()
    )
    all_weeks = pd.date_range(weekly.index.min(), weekly.index.max(), freq="W-MON")
    weekly = weekly.reindex(all_weeks, fill_value=0)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    duration = max((end_ts - start_ts).days, 1)
    during = weekly.loc[(weekly.index >= start_ts) & (weekly.index <= end_ts)]
    before = weekly.loc[
        (weekly.index >= start_ts - pd.Timedelta(days=duration))
        & (weekly.index < start_ts)
    ]
    return {
        "exercise": exercise,
        "before_week_count": int(len(before)),
        "during_week_count": int(len(during)),
        "before_weekly_volume_mean": float(before["volume_load"].mean()),
        "during_weekly_volume_mean": float(during["volume_load"].mean()),
        "before_weekly_frequency_mean": float(before["frequency"].mean()),
        "during_weekly_frequency_mean": float(during["frequency"].mean()),
        "before_weekly_sets_mean": float(before["set_count"].mean()),
        "during_weekly_sets_mean": float(during["set_count"].mean()),
    }


def gap_e1rm_comparison(
    frame: pd.DataFrame,
    gap_start: str,
    gap_end: str,
    exercises: list[str],
    max_distance_days: int = 60,
) -> dict[str, Any]:
    start_ts = pd.Timestamp(gap_start)
    end_ts = pd.Timestamp(gap_end)
    comparisons: list[dict[str, Any]] = []
    for exercise in exercises:
        values = _session_best(frame, exercise)
        before = values.loc[values["session_timestamp"].le(start_ts)].tail(1)
        after = values.loc[values["session_timestamp"].ge(end_ts)].head(1)
        if before.empty or after.empty:
            continue
        before_distance = (
            start_ts - before.iloc[0]["session_timestamp"]
        ).total_seconds() / 86400
        after_distance = (
            after.iloc[0]["session_timestamp"] - end_ts
        ).total_seconds() / 86400
        if before_distance > max_distance_days or after_distance > max_distance_days:
            continue
        pre = float(before.iloc[0]["e1rm"])
        post = float(after.iloc[0]["e1rm"])
        comparisons.append(
            {
                "exercise": exercise,
                "pre_session": _timestamp(before.iloc[0]["session_timestamp"]),
                "post_session": _timestamp(after.iloc[0]["session_timestamp"]),
                "pre_e1rm": pre,
                "post_e1rm": post,
                "change_pct": 100 * (post / pre - 1),
            }
        )
    return {
        "gap_start": _timestamp(start_ts),
        "gap_end": _timestamp(end_ts),
        "gap_days": float((end_ts - start_ts).total_seconds() / 86400),
        "comparisons": comparisons,
    }


def outlier_record(frame: pd.DataFrame, representative_source_row: int) -> dict[str, Any]:
    selected = frame.loc[
        frame["representative_source_row"].eq(representative_source_row)
    ]
    if len(selected) != 1:
        raise ValueError(f"Expected one processed row for source row {representative_source_row}")
    row = selected.iloc[0]
    return {
        "representative_source_row": int(row["representative_source_row"]),
        "processed_set_id": str(row["processed_set_id"]),
        "exercise": str(row["canonical_exercise_name"]),
        "session": _timestamp(row["session_timestamp"]),
        "weight": float(row["weight"]),
        "reps": int(row["reps"]),
        "is_outlier": bool(row["is_outlier"]),
        "outlier_reason": str(row["outlier_reason"]),
        "include_in_volume_metrics": bool(row["include_in_volume_metrics"]),
        "include_in_e1rm": bool(row["include_in_e1rm"]),
    }


def compute_reference(
    operation: str,
    parameters: dict[str, Any],
    *,
    sets_path: Path = DEFAULT_SETS_PATH,
    lineage_path: Path = DEFAULT_LINEAGE_PATH,
) -> dict[str, Any]:
    frame = load_sets(sets_path)
    operations = {
        "session_summary": lambda: session_summary(frame),
        "longest_training_gap": lambda: longest_training_gap(frame),
        "top_exercise_by_session_count": lambda: top_exercise_by_session_count(frame),
        "e1rm_window_summary": lambda: e1rm_window_summary(frame, **parameters),
        "e1rm_peak_and_latest": lambda: e1rm_peak_and_latest(frame, **parameters),
        "lineage_reconciliation": lambda: lineage_reconciliation(lineage_path),
        "e1rm_interval_summary": lambda: e1rm_interval_summary(frame, **parameters),
        "weekly_load_comparison": lambda: weekly_load_comparison(frame, **parameters),
        "gap_e1rm_comparison": lambda: gap_e1rm_comparison(frame, **parameters),
        "outlier_record": lambda: outlier_record(frame, **parameters),
    }
    if operation not in operations:
        raise ValueError(f"Unknown reference operation: {operation}")
    return operations[operation]()

