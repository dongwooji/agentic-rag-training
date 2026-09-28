"""Reproducible Phase 1 EDA for the workout-log dataset.

This script never writes to the raw CSV. It validates and reads the source, creates
EDA-only in-memory views, and writes the report, audit tables, and figures under
``reports/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from src.metrics.definitions import (
    E1RM_REP_MAX,
    E1RM_REP_MIN,
    E1RM_ROLLING_SESSIONS,
    PLATEAU_MAX_ABS_CHANGE_PCT,
    PLATEAU_MAX_RANGE_PCT,
    PLATEAU_MIN_DAYS,
    PLATEAU_WINDOW_SESSIONS,
    epley_e1rm,
)


EXPECTED_COLUMNS = [
    "Date",
    "Workout Name",
    "Exercise Name",
    "Set Order",
    "Weight",
    "Reps",
    "Distance",
    "Seconds",
    "Notes",
    "Workout Notes",
]

PRIMARY_EXERCISES = [
    "Squat (Barbell)",
    "Incline Bench Press (Barbell)",
    "Seated Shoulder  Press (Barbell)",
    "Bench Press (Barbell)",
    "Deadlift (Barbell)",
]

ADDITIONAL_ALIAS_CANDIDATES = [
    (
        "Lateral Raise (Dumbbell)",
        "Lateral Raise (Dumbbells)",
        "singular/plural equipment label; only a few rows use the singular form",
    ),
    (
        "Curl Dumbbell",
        "Bicep Curl (Dumbbell)",
        "word-order/abbreviation similarity; semantic review required",
    ),
    (
        "Incline Press (Dumbbell)",
        "Low Incline Dumbbell Bench",
        "similar movement wording, but bench angle may differ",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/weightlifting_721_workouts.csv"),
        help="Raw CSV path (read-only).",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=Path("reports"),
        help="Directory for EDA.md, tables, figures, and summary JSON.",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_workouts(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Raw CSV not found: {path}")

    frame = pd.read_csv(path)
    if list(frame.columns) != EXPECTED_COLUMNS:
        raise ValueError(
            "Unexpected schema. "
            f"Expected {EXPECTED_COLUMNS}, received {list(frame.columns)}"
        )

    frame.insert(0, "source_row", np.arange(2, len(frame) + 2, dtype=int))
    frame["date_ts"] = pd.to_datetime(frame["Date"], errors="coerce")
    return frame


def normalize_exercise_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s+([)])", r"\1", normalized)
    normalized = re.sub(r"([(])\s+", r"\1", normalized)
    return normalized.rstrip(".").strip()


def exact_deduplicated_view(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop_duplicates(subset=EXPECTED_COLUMNS, keep="first").copy()


def build_alias_candidates(
    exact_view: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, str], int, int]:
    match_columns = [column for column in EXPECTED_COLUMNS if column != "Exercise Name"]
    pair_counts: Counter[tuple[str, str]] = Counter()
    all_alias_like_groups = 0
    all_alias_like_extra_rows = 0

    for _, group in exact_view.groupby(match_columns, dropna=False, sort=False):
        names = sorted(group["Exercise Name"].astype(str).unique())
        if len(names) < 2:
            continue
        all_alias_like_groups += 1
        all_alias_like_extra_rows += len(group) - 1
        pair_counts.update(combinations(names, 2))

    counts = exact_view["Exercise Name"].value_counts()
    rows: list[dict[str, object]] = []
    for (left, right), matched_rows in pair_counts.items():
        smaller_count = int(min(counts[left], counts[right]))
        coverage = matched_rows / smaller_count if smaller_count else 0.0
        if matched_rows < 10 or coverage < 0.5:
            continue
        normalized_match = normalize_exercise_name(left) == normalize_exercise_name(right)
        priority = "high" if coverage >= 0.8 else "review"
        rows.append(
            {
                "name_a": left,
                "name_b": right,
                "co_recorded_identical_sets": int(matched_rows),
                "smaller_name_set_count": smaller_count,
                "smaller_name_coverage": coverage,
                "normalization_match": normalized_match,
                "review_priority": priority,
                "evidence": "same session/set/value fields, different exercise name",
            }
        )

    observed_pairs = {
        frozenset((str(row["name_a"]), str(row["name_b"]))) for row in rows
    }
    for left, right, evidence in ADDITIONAL_ALIAS_CANDIDATES:
        if left not in counts.index or right not in counts.index:
            continue
        if frozenset((left, right)) in observed_pairs:
            continue
        rows.append(
            {
                "name_a": left,
                "name_b": right,
                "co_recorded_identical_sets": 0,
                "smaller_name_set_count": int(min(counts[left], counts[right])),
                "smaller_name_coverage": 0.0,
                "normalization_match": False,
                "review_priority": "review",
                "evidence": evidence,
            }
        )

    aliases = pd.DataFrame(rows)
    if not aliases.empty:
        aliases = aliases.sort_values(
            ["review_priority", "smaller_name_coverage", "co_recorded_identical_sets"],
            ascending=[True, False, False],
        ).reset_index(drop=True)

    provisional_map: dict[str, str] = {}
    for row in rows:
        if (
            row["review_priority"] != "high"
            or float(row["smaller_name_coverage"]) < 0.8
            or int(row["co_recorded_identical_sets"]) < 10
        ):
            continue
        left = str(row["name_a"])
        right = str(row["name_b"])
        if bool(row["normalization_match"]):
            def label_quality(name: str) -> tuple[bool, bool, int]:
                return (
                    name != name.strip(),
                    bool(re.search(r"\s+\)", name)),
                    -int(counts[name]),
                )

            target = min((left, right), key=label_quality)
            source = right if target == left else left
        else:
            source, target = (
                (left, right) if counts[left] <= counts[right] else (right, left)
            )
        provisional_map[source] = target

    return aliases, provisional_map, all_alias_like_groups, all_alias_like_extra_rows


def provisional_alias_adjusted_view(
    exact_view: pd.DataFrame, alias_map: dict[str, str]
) -> pd.DataFrame:
    adjusted = exact_view.copy()
    adjusted["Exercise Name"] = adjusted["Exercise Name"].replace(alias_map)
    return adjusted.drop_duplicates(subset=EXPECTED_COLUMNS, keep="first").copy()


def robust_weight_flags(exact_view: pd.DataFrame) -> pd.DataFrame:
    result = exact_view.copy()
    positive = result["Weight"].where(result["Weight"] > 0)
    group_median = positive.groupby(result["Exercise Name"]).transform("median")
    group_count = positive.groupby(result["Exercise Name"]).transform("count")

    absolute_deviation = (positive - group_median).abs()
    group_mad = absolute_deviation.groupby(result["Exercise Name"]).transform("median")
    robust_z = 0.6745 * (positive - group_median) / group_mad.replace(0, np.nan)
    ratio_to_median = positive / group_median.replace(0, np.nan)

    hard_extreme = result["Weight"] >= 1000
    within_exercise_extreme = (
        (group_count >= 20)
        & (robust_z > 12)
        & (ratio_to_median > 2.5)
    )
    result["weight_group_median"] = group_median
    result["weight_robust_z"] = robust_z
    result["weight_ratio_to_median"] = ratio_to_median
    result["high_confidence_weight_outlier"] = (
        hard_extreme | within_exercise_extreme
    ).fillna(False)
    return result


def build_outlier_candidates(flagged: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in flagged.itertuples(index=False):
        reasons: list[str] = []
        if pd.isna(row.date_ts):
            reasons.append("unparseable_date")
        if row.Weight < 0:
            reasons.append("negative_weight")
        if row.Reps < 0:
            reasons.append("negative_reps")
        if row._4 <= 0:  # Set Order is the fourth source column after source_row.
            reasons.append("non_positive_set_order")
        if bool(row.high_confidence_weight_outlier):
            if row.Weight >= 1000:
                reasons.append("weight_ge_1000")
            else:
                reasons.append("within_exercise_weight_spike")
        if row.Reps >= 25:
            reasons.append("reps_ge_25")
        if row.Reps == 0 and row.Weight > 0 and row.Seconds == 0 and row.Distance == 0:
            reasons.append("positive_weight_with_zero_reps")
        if not reasons:
            continue
        rows.append(
            {
                "source_row": int(row.source_row),
                "date": row.Date,
                "workout_name": row._2,
                "exercise_name": row._3,
                "set_order": int(row._4),
                "weight": float(row.Weight),
                "reps": int(row.Reps),
                "distance": float(row.Distance),
                "seconds": int(row.Seconds),
                "reason": "; ".join(reasons),
            }
        )
    return pd.DataFrame(rows)


def build_set_order_collisions(exact_view: pd.DataFrame) -> pd.DataFrame:
    keys = ["Date", "Workout Name", "Exercise Name", "Set Order"]
    sizes = exact_view.groupby(keys, dropna=False).size().rename("collision_group_size")
    collisions = sizes.loc[sizes.gt(1)].reset_index()
    if collisions.empty:
        return pd.DataFrame(
            columns=[
                "source_row",
                *keys,
                "Weight",
                "Reps",
                "Distance",
                "Seconds",
                "Notes",
                "Workout Notes",
                "collision_group_size",
            ]
        )
    result = exact_view.merge(collisions, on=keys, how="inner")
    return result[
        [
            "source_row",
            *keys,
            "Weight",
            "Reps",
            "Distance",
            "Seconds",
            "Notes",
            "Workout Notes",
            "collision_group_size",
        ]
    ].sort_values(keys + ["source_row"])


def calculate_e1rm(weight: pd.Series, reps: pd.Series) -> pd.Series:
    return epley_e1rm(weight, reps)


def add_metric_columns(flagged: pd.DataFrame) -> pd.DataFrame:
    result = flagged.copy()
    result["e1rm_eligible"] = (
        result["Weight"].gt(0)
        & result["Reps"].between(E1RM_REP_MIN, E1RM_REP_MAX)
        & ~result["high_confidence_weight_outlier"]
    )
    result["estimated_1rm"] = calculate_e1rm(result["Weight"], result["Reps"]).where(
        result["e1rm_eligible"]
    )
    result["volume_load"] = (
        result["Weight"].clip(lower=0) * result["Reps"].clip(lower=0)
    ).where(~result["high_confidence_weight_outlier"], 0.0)
    result["week_start"] = result["date_ts"].dt.to_period("W-SUN").dt.start_time
    return result


def build_exercise_summary(metric_view: pd.DataFrame) -> pd.DataFrame:
    summary = (
        metric_view.groupby("Exercise Name", dropna=False)
        .agg(
            set_count=("source_row", "size"),
            session_count=("date_ts", "nunique"),
            workout_day_count=("date_ts", lambda x: x.dt.normalize().nunique()),
            first_record=("date_ts", "min"),
            last_record=("date_ts", "max"),
            positive_weight_sets=("Weight", lambda x: int(x.gt(0).sum())),
            e1rm_eligible_sets=("e1rm_eligible", "sum"),
            e1rm_eligible_sessions=(
                "date_ts",
                lambda x: x[metric_view.loc[x.index, "e1rm_eligible"]].nunique(),
            ),
            max_recorded_weight=("Weight", "max"),
            max_recorded_reps=("Reps", "max"),
        )
        .reset_index()
    )
    summary["recording_span_days"] = (
        summary["last_record"] - summary["first_record"]
    ).dt.days
    summary["positive_weight_set_ratio"] = (
        summary["positive_weight_sets"] / summary["set_count"]
    )
    return summary.sort_values(
        ["session_count", "set_count"], ascending=False
    ).reset_index(drop=True)


def summarize_session_view(frame: pd.DataFrame, label: str) -> dict[str, object]:
    per_session = (
        frame.groupby("date_ts")
        .agg(
            set_count=("source_row", "size"),
            exercise_name_count=("Exercise Name", "nunique"),
        )
        .reset_index()
    )
    return {
        "view": label,
        "rows": int(len(frame)),
        "sessions": int(len(per_session)),
        "median_sets_per_session": float(per_session["set_count"].median()),
        "p25_sets_per_session": float(per_session["set_count"].quantile(0.25)),
        "p75_sets_per_session": float(per_session["set_count"].quantile(0.75)),
        "max_sets_per_session": int(per_session["set_count"].max()),
        "median_exercises_per_session": float(
            per_session["exercise_name_count"].median()
        ),
        "max_exercises_per_session": int(per_session["exercise_name_count"].max()),
    }


def session_frequency_tables(metric_view: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    sessions = pd.Series(
        pd.DatetimeIndex(metric_view["date_ts"].dropna().unique()).sort_values(),
        name="date_ts",
    )
    session_frame = sessions.to_frame()
    session_frame["week_start"] = (
        session_frame["date_ts"].dt.to_period("W-SUN").dt.start_time
    )
    session_frame["month_start"] = session_frame["date_ts"].dt.to_period("M").dt.start_time

    weekly = session_frame.groupby("week_start").size().rename("session_count").to_frame()
    all_weeks = pd.date_range(weekly.index.min(), weekly.index.max(), freq="W-MON")
    weekly = weekly.reindex(all_weeks, fill_value=0).rename_axis("week_start").reset_index()
    weekly["rolling_4_week_mean"] = weekly["session_count"].rolling(4, min_periods=1).mean()

    monthly = session_frame.groupby("month_start").size().rename("session_count").to_frame()
    all_months = pd.date_range(monthly.index.min(), monthly.index.max(), freq="MS")
    monthly = monthly.reindex(all_months, fill_value=0).rename_axis("month_start").reset_index()
    return weekly, monthly


def build_session_metrics(metric_view: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for exercise in PRIMARY_EXERCISES:
        selected = metric_view.loc[metric_view["Exercise Name"].eq(exercise)].copy()
        if selected.empty:
            continue
        session = (
            selected.groupby("date_ts")
            .agg(
                set_count=("source_row", "size"),
                rep_count=("Reps", lambda x: int(x.clip(lower=0).sum())),
                volume_load=("volume_load", "sum"),
                top_set_weight=("Weight", "max"),
                e1rm_eligible_sets=("estimated_1rm", "count"),
                session_best_e1rm=("estimated_1rm", "max"),
            )
            .reset_index()
            .sort_values("date_ts")
        )
        session["exercise_name"] = exercise
        session["e1rm_rolling_median_5_sessions"] = session[
            "session_best_e1rm"
        ].rolling(E1RM_ROLLING_SESSIONS, min_periods=3).median()
        frames.append(session)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_weekly_metrics(metric_view: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for exercise in PRIMARY_EXERCISES:
        selected = metric_view.loc[metric_view["Exercise Name"].eq(exercise)].copy()
        if selected.empty:
            continue
        weekly = (
            selected.groupby("week_start")
            .agg(
                set_count=("source_row", "size"),
                rep_count=("Reps", lambda x: int(x.clip(lower=0).sum())),
                volume_load=("volume_load", "sum"),
                frequency=("date_ts", "nunique"),
            )
            .sort_index()
        )
        all_weeks = pd.date_range(weekly.index.min(), weekly.index.max(), freq="W-MON")
        weekly = weekly.reindex(all_weeks, fill_value=0).rename_axis("week_start").reset_index()
        weekly["exercise_name"] = exercise
        weekly["volume_load_rolling_4_week_mean"] = weekly["volume_load"].rolling(
            4, min_periods=1
        ).mean()
        weekly["frequency_rolling_4_week_mean"] = weekly["frequency"].rolling(
            4, min_periods=1
        ).mean()
        weekly["set_count_rolling_4_week_mean"] = weekly["set_count"].rolling(
            4, min_periods=1
        ).mean()
        frames.append(weekly)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_trend_summary(session_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for exercise, group in session_metrics.groupby("exercise_name", sort=False):
        eligible = group.dropna(subset=["session_best_e1rm"]).sort_values("date_ts")
        if eligible.empty:
            continue
        window = min(5, len(eligible))
        first_median = float(eligible["session_best_e1rm"].head(window).median())
        latest_median = float(eligible["session_best_e1rm"].tail(window).median())
        peak_index = eligible["session_best_e1rm"].idxmax()
        peak = float(eligible.loc[peak_index, "session_best_e1rm"])
        latest = float(eligible.iloc[-1]["session_best_e1rm"])
        rows.append(
            {
                "exercise_name": exercise,
                "eligible_sessions": int(len(eligible)),
                "first_session": eligible.iloc[0]["date_ts"],
                "last_session": eligible.iloc[-1]["date_ts"],
                "first_5_session_median_e1rm": first_median,
                "last_5_session_median_e1rm": latest_median,
                "first_to_last_window_change_pct": 100
                * (latest_median / first_median - 1),
                "peak_session_e1rm": peak,
                "peak_date": eligible.loc[peak_index, "date_ts"],
                "latest_session_e1rm": latest,
                "latest_vs_peak_pct": 100 * (latest / peak - 1),
            }
        )
    return pd.DataFrame(rows)


def build_training_gaps(metric_view: pd.DataFrame) -> pd.DataFrame:
    sessions = pd.Series(
        pd.DatetimeIndex(metric_view["date_ts"].dropna().unique()).sort_values(),
        name="next_session",
    )
    gaps = pd.DataFrame(
        {
            "previous_session": sessions.shift(1),
            "next_session": sessions,
        }
    ).dropna()
    gaps["gap_hours"] = (
        gaps["next_session"] - gaps["previous_session"]
    ).dt.total_seconds() / 3600
    gaps["gap_days"] = gaps["gap_hours"] / 24
    gaps["calendar_day_difference"] = (
        gaps["next_session"].dt.normalize() - gaps["previous_session"].dt.normalize()
    ).dt.days
    return gaps.sort_values("gap_hours", ascending=False).reset_index(drop=True)


def build_gap_performance_comparison(
    gaps: pd.DataFrame, session_metrics: pd.DataFrame, max_distance_days: int = 60
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for gap_rank, gap in gaps.head(10).iterrows():
        for exercise, metrics in session_metrics.groupby("exercise_name", sort=False):
            eligible = metrics.dropna(subset=["session_best_e1rm"]).sort_values("date_ts")
            before = eligible.loc[eligible["date_ts"].le(gap["previous_session"])].tail(1)
            after = eligible.loc[eligible["date_ts"].ge(gap["next_session"])].head(1)
            if before.empty or after.empty:
                continue
            before_distance = (
                gap["previous_session"] - before.iloc[0]["date_ts"]
            ).total_seconds() / 86400
            after_distance = (
                after.iloc[0]["date_ts"] - gap["next_session"]
            ).total_seconds() / 86400
            if before_distance > max_distance_days or after_distance > max_distance_days:
                continue
            before_value = float(before.iloc[0]["session_best_e1rm"])
            after_value = float(after.iloc[0]["session_best_e1rm"])
            rows.append(
                {
                    "gap_rank": int(gap_rank + 1),
                    "gap_start": gap["previous_session"],
                    "gap_end": gap["next_session"],
                    "gap_days": float(gap["gap_days"]),
                    "exercise_name": exercise,
                    "pre_session": before.iloc[0]["date_ts"],
                    "post_session": after.iloc[0]["date_ts"],
                    "pre_e1rm": before_value,
                    "post_e1rm": after_value,
                    "change_pct": 100 * (after_value / before_value - 1),
                    "pre_distance_days": before_distance,
                    "post_distance_days": after_distance,
                }
            )
    return pd.DataFrame(rows)


def _window_is_plateau(values: np.ndarray, dates: pd.Series) -> tuple[bool, float, float]:
    span_days = int((dates.iloc[-1] - dates.iloc[0]).days)
    if span_days < PLATEAU_MIN_DAYS:
        return False, math.nan, math.nan
    start_level = float(np.median(values[:3]))
    end_level = float(np.median(values[-3:]))
    if start_level <= 0:
        return False, math.nan, math.nan
    change_pct = 100 * (end_level / start_level - 1)
    median_level = float(np.median(values))
    range_pct = 100 * (float(np.max(values)) - float(np.min(values))) / median_level
    qualifies = (
        abs(change_pct) <= PLATEAU_MAX_ABS_CHANGE_PCT
        and range_pct <= PLATEAU_MAX_RANGE_PCT
    )
    return qualifies, change_pct, range_pct


def detect_plateau_candidates(session_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for exercise, group in session_metrics.groupby("exercise_name", sort=False):
        eligible = (
            group.dropna(subset=["session_best_e1rm"])
            .sort_values("date_ts")
            .reset_index(drop=True)
        )
        qualifying: list[tuple[int, int]] = []
        for start in range(0, len(eligible) - PLATEAU_WINDOW_SESSIONS + 1):
            for end in range(start + PLATEAU_WINDOW_SESSIONS - 1, len(eligible)):
                window = eligible.iloc[start : end + 1]
                qualifies, _, _ = _window_is_plateau(
                    window["session_best_e1rm"].to_numpy(), window["date_ts"]
                )
                if qualifies:
                    qualifying.append((start, end))

        qualifying.sort(
            key=lambda item: (
                -(item[1] - item[0] + 1),
                -int((eligible.iloc[item[1]]["date_ts"] - eligible.iloc[item[0]]["date_ts"]).days),
                item[0],
            )
        )
        selected: list[tuple[int, int]] = []
        for start, end in qualifying:
            overlaps = any(not (end < chosen_start or start > chosen_end) for chosen_start, chosen_end in selected)
            if not overlaps:
                selected.append((start, end))

        for start, end in sorted(selected):
            candidate = eligible.iloc[start : end + 1]
            values = candidate["session_best_e1rm"].to_numpy()
            start_level = float(np.median(values[:3]))
            end_level = float(np.median(values[-3:]))
            median_level = float(np.median(values))
            rows.append(
                {
                    "exercise_name": exercise,
                    "start_date": candidate.iloc[0]["date_ts"],
                    "end_date": candidate.iloc[-1]["date_ts"],
                    "duration_days": int(
                        (candidate.iloc[-1]["date_ts"] - candidate.iloc[0]["date_ts"]).days
                    ),
                    "session_count": int(len(candidate)),
                    "start_3_session_median_e1rm": start_level,
                    "end_3_session_median_e1rm": end_level,
                    "change_pct": 100 * (end_level / start_level - 1),
                    "within_period_range_pct": 100
                    * (float(np.max(values)) - float(np.min(values)))
                    / median_level,
                    "selection_rule": (
                        f"longest non-overlapping contiguous segment; "
                        f">={PLATEAU_WINDOW_SESSIONS} sessions; "
                        f">={PLATEAU_MIN_DAYS} days; |change|<="
                        f"{PLATEAU_MAX_ABS_CHANGE_PCT:.1f}%; range<="
                        f"{PLATEAU_MAX_RANGE_PCT:.1f}%"
                    ),
                }
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "exercise_name",
                "start_date",
                "end_date",
                "duration_days",
                "session_count",
                "start_3_session_median_e1rm",
                "end_3_session_median_e1rm",
                "change_pct",
                "within_period_range_pct",
                "selection_rule",
            ]
        )
    return pd.DataFrame(rows).sort_values(
        ["duration_days", "session_count"], ascending=False
    ).reset_index(drop=True)


def summarize_plateau_load(
    plateau_candidates: pd.DataFrame, weekly_metrics: pd.DataFrame
) -> dict[str, object] | None:
    if plateau_candidates.empty:
        return None
    candidate = None
    exercise_weeks = pd.DataFrame()
    for _, possible in plateau_candidates.iterrows():
        possible_exercise = str(possible["exercise_name"])
        possible_start = pd.Timestamp(possible["start_date"])
        possible_end = pd.Timestamp(possible["end_date"])
        possible_duration = max((possible_end - possible_start).days, 1)
        possible_weeks = weekly_metrics.loc[
            weekly_metrics["exercise_name"].eq(possible_exercise)
        ].copy()
        if possible_weeks.empty:
            continue
        required_history_start = possible_start - pd.Timedelta(days=possible_duration)
        if required_history_start >= possible_weeks["week_start"].min():
            candidate = possible
            exercise_weeks = possible_weeks
            break
    if candidate is None:
        return None

    exercise = str(candidate["exercise_name"])
    start = pd.Timestamp(candidate["start_date"])
    end = pd.Timestamp(candidate["end_date"])
    duration = max((end - start).days, 1)
    during = exercise_weeks.loc[exercise_weeks["week_start"].between(start, end)]
    before = exercise_weeks.loc[
        exercise_weeks["week_start"].between(start - pd.Timedelta(days=duration), start, inclusive="left")
    ]

    def mean_or_nan(frame: pd.DataFrame, column: str) -> float:
        return float(frame[column].mean()) if not frame.empty else math.nan

    return {
        "exercise_name": exercise,
        "start_date": start,
        "end_date": end,
        "duration_days": int(candidate["duration_days"]),
        "change_pct": float(candidate["change_pct"]),
        "before_weekly_volume_mean": mean_or_nan(before, "volume_load"),
        "during_weekly_volume_mean": mean_or_nan(during, "volume_load"),
        "before_weekly_frequency_mean": mean_or_nan(before, "frequency"),
        "during_weekly_frequency_mean": mean_or_nan(during, "frequency"),
        "before_weekly_sets_mean": mean_or_nan(before, "set_count"),
        "during_weekly_sets_mean": mean_or_nan(during, "set_count"),
    }


def save_figures(
    session_metrics: pd.DataFrame,
    weekly_metrics: pd.DataFrame,
    overall_weekly: pd.DataFrame,
    figures_dir: Path,
) -> None:
    figures_dir.mkdir(parents=True, exist_ok=True)

    def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
        filename = "arialbd.ttf" if bold else "arial.ttf"
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / filename), size)
        except OSError:
            return ImageFont.load_default()

    title_font = font(25, bold=True)
    panel_title_font = font(18, bold=True)
    body_font = font(14)
    small_font = font(12)

    def line_points(
        dates: pd.Series,
        values: pd.Series,
        rectangle: tuple[int, int, int, int],
        y_limits: tuple[float, float] | None = None,
    ) -> list[tuple[float, float]]:
        valid = dates.notna() & values.notna()
        if not valid.any():
            return []
        valid_dates = pd.to_datetime(dates.loc[valid])
        valid_values = values.loc[valid].astype(float)
        x0, y0, x1, y1 = rectangle
        date_numbers = valid_dates.astype("int64").to_numpy(dtype=float)
        x_min, x_max = float(date_numbers.min()), float(date_numbers.max())
        if y_limits is None:
            y_min, y_max = float(valid_values.min()), float(valid_values.max())
        else:
            y_min, y_max = y_limits
        if x_max == x_min:
            x_max += 1
        if y_max == y_min:
            y_max += 1
        x_values = x0 + (date_numbers - x_min) / (x_max - x_min) * (x1 - x0)
        y_values = y1 - (valid_values.to_numpy() - y_min) / (y_max - y_min) * (y1 - y0)
        return list(zip(x_values.tolist(), y_values.tolist()))

    def draw_panel_axes(
        draw: ImageDraw.ImageDraw,
        rectangle: tuple[int, int, int, int],
        title: str,
        left_label: str,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> None:
        x0, y0, x1, y1 = rectangle
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = int(y0 + fraction * (y1 - y0))
            draw.line((x0, y, x1, y), fill="#e5e7eb", width=1)
        draw.line((x0, y0, x0, y1), fill="#6b7280", width=1)
        draw.line((x0, y1, x1, y1), fill="#6b7280", width=1)
        draw.text((x0, y0 - 28), title, fill="#111827", font=panel_title_font)
        draw.text((8, y0 + 5), left_label, fill="#374151", font=small_font)
        draw.text((x0, y1 + 5), start_date.strftime("%Y-%m-%d"), fill="#4b5563", font=small_font)
        end_text = end_date.strftime("%Y-%m-%d")
        end_box = draw.textbbox((0, 0), end_text, font=small_font)
        draw.text((x1 - (end_box[2] - end_box[0]), y1 + 5), end_text, fill="#4b5563", font=small_font)

    width = 1500
    panel_height = 230
    top = 85
    bottom_margin = 35
    e1rm_image = Image.new(
        "RGB", (width, top + panel_height * len(PRIMARY_EXERCISES) + bottom_margin), "white"
    )
    draw = ImageDraw.Draw(e1rm_image)
    draw.text(
        (40, 25),
        "Session-best Epley e1RM trends (1-12 reps; flagged spikes excluded)",
        fill="#111827",
        font=title_font,
    )
    for index, exercise in enumerate(PRIMARY_EXERCISES):
        values = session_metrics.loc[
            session_metrics["exercise_name"].eq(exercise)
        ].sort_values("date_ts")
        y_top = top + index * panel_height + 35
        rectangle = (150, y_top, width - 65, y_top + 145)
        eligible = values.dropna(subset=["session_best_e1rm"])
        if eligible.empty:
            continue
        y_min = float(eligible["session_best_e1rm"].min())
        y_max = float(eligible["session_best_e1rm"].max())
        padding = max((y_max - y_min) * 0.08, 1)
        y_limits = (y_min - padding, y_max + padding)
        draw_panel_axes(
            draw,
            rectangle,
            exercise.replace("  ", " "),
            f"{y_min:.0f}-{y_max:.0f}",
            eligible["date_ts"].min(),
            eligible["date_ts"].max(),
        )
        points = line_points(
            eligible["date_ts"], eligible["session_best_e1rm"], rectangle, y_limits
        )
        for x, y in points:
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#93c5fd")
        rolling = values.dropna(subset=["e1rm_rolling_median_5_sessions"])
        rolling_points = line_points(
            rolling["date_ts"],
            rolling["e1rm_rolling_median_5_sessions"],
            rectangle,
            y_limits,
        )
        if len(rolling_points) >= 2:
            draw.line(rolling_points, fill="#1d4ed8", width=3, joint="curve")
    e1rm_image.save(figures_dir / "e1rm_trends.png")

    load_image = Image.new(
        "RGB", (width, top + panel_height * len(PRIMARY_EXERCISES) + bottom_margin), "white"
    )
    draw = ImageDraw.Draw(load_image)
    draw.text(
        (40, 25),
        "Weekly volume load (blue) and frequency (red): trailing 4-week means",
        fill="#111827",
        font=title_font,
    )
    for index, exercise in enumerate(PRIMARY_EXERCISES):
        values = weekly_metrics.loc[
            weekly_metrics["exercise_name"].eq(exercise)
        ].sort_values("week_start")
        if values.empty:
            continue
        y_top = top + index * panel_height + 35
        rectangle = (150, y_top, width - 65, y_top + 145)
        draw_panel_axes(
            draw,
            rectangle,
            exercise.replace("  ", " "),
            "scaled",
            values["week_start"].min(),
            values["week_start"].max(),
        )
        volume_points = line_points(
            values["week_start"], values["volume_load_rolling_4_week_mean"], rectangle
        )
        frequency_points = line_points(
            values["week_start"], values["frequency_rolling_4_week_mean"], rectangle
        )
        if len(volume_points) >= 2:
            draw.line(volume_points, fill="#2563eb", width=3, joint="curve")
        if len(frequency_points) >= 2:
            draw.line(frequency_points, fill="#dc2626", width=3, joint="curve")
        legend_y = y_top - 25
        draw.line((width - 390, legend_y + 8, width - 350, legend_y + 8), fill="#2563eb", width=3)
        draw.text((width - 345, legend_y), "volume", fill="#2563eb", font=small_font)
        draw.line((width - 245, legend_y + 8, width - 205, legend_y + 8), fill="#dc2626", width=3)
        draw.text((width - 200, legend_y), "frequency", fill="#dc2626", font=small_font)
    load_image.save(figures_dir / "weekly_load_frequency.png")

    frequency_image = Image.new("RGB", (1500, 520), "white")
    draw = ImageDraw.Draw(frequency_image)
    draw.text((40, 25), "Overall training-session frequency", fill="#111827", font=title_font)
    rectangle = (120, 95, 1435, 420)
    draw_panel_axes(
        draw,
        rectangle,
        "Weekly unique timestamps and trailing 4-week mean",
        "sessions",
        overall_weekly["week_start"].min(),
        overall_weekly["week_start"].max(),
    )
    bar_points = line_points(
        overall_weekly["week_start"], overall_weekly["session_count"], rectangle
    )
    for x, y in bar_points:
        draw.line((x, rectangle[3], x, y), fill="#bfdbfe", width=4)
    rolling_points = line_points(
        overall_weekly["week_start"], overall_weekly["rolling_4_week_mean"], rectangle
    )
    if len(rolling_points) >= 2:
        draw.line(rolling_points, fill="#0f766e", width=4, joint="curve")
    frequency_image.save(figures_dir / "overall_session_frequency.png")


def markdown_table(frame: pd.DataFrame, columns: Iterable[str] | None = None) -> str:
    selected = frame.loc[:, list(columns)] if columns is not None else frame.copy()
    if selected.empty:
        return "_해당 조건에 맞는 행이 없습니다._"
    formatted = selected.copy()
    for column in formatted.columns:
        if pd.api.types.is_datetime64_any_dtype(formatted[column]):
            formatted[column] = formatted[column].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_float_dtype(formatted[column]):
            formatted[column] = formatted[column].map(
                lambda value: "" if pd.isna(value) else f"{value:,.2f}"
            )
    formatted = formatted.fillna("")

    def escape(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    header = "| " + " | ".join(escape(column) for column in formatted.columns) + " |"
    separator = "| " + " | ".join("---" for _ in formatted.columns) + " |"
    body = [
        "| " + " | ".join(escape(value) for value in row) + " |"
        for row in formatted.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *body])


def format_pct(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.2f}%" if denominator else "n/a"


def write_report(
    report_path: Path,
    input_path: Path,
    raw_hash: str,
    raw: pd.DataFrame,
    exact_view: pd.DataFrame,
    alias_adjusted: pd.DataFrame,
    aliases: pd.DataFrame,
    alias_map: dict[str, str],
    alias_like_groups: int,
    alias_like_extra_rows: int,
    outliers: pd.DataFrame,
    set_order_collisions: pd.DataFrame,
    exercise_summary: pd.DataFrame,
    session_view_summary: pd.DataFrame,
    weekly_frequency: pd.DataFrame,
    monthly_frequency: pd.DataFrame,
    trend_summary: pd.DataFrame,
    weekly_metrics: pd.DataFrame,
    gaps: pd.DataFrame,
    gap_comparison: pd.DataFrame,
    plateau_candidates: pd.DataFrame,
    plateau_load: dict[str, object] | None,
) -> None:
    valid_dates = raw["date_ts"].dropna()
    duplicate_extra = int(raw.duplicated(subset=EXPECTED_COLUMNS, keep="first").sum())
    duplicate_all = int(raw.duplicated(subset=EXPECTED_COLUMNS, keep=False).sum())
    duplicate_groups = int(
        raw.groupby(EXPECTED_COLUMNS, dropna=False).size().gt(1).sum()
    )
    unique_sessions = int(raw["date_ts"].nunique())
    unique_days = int(raw["date_ts"].dt.normalize().nunique())
    sessions_per_day = raw[["date_ts"]].drop_duplicates()["date_ts"].dt.normalize().value_counts()
    multi_session_days = int(sessions_per_day.gt(1).sum())

    missing = pd.DataFrame(
        {
            "column": EXPECTED_COLUMNS,
            "missing_count": [int(raw[column].isna().sum()) for column in EXPECTED_COLUMNS],
        }
    )
    missing["missing_pct"] = 100 * missing["missing_count"] / len(raw)

    schema = pd.DataFrame(
        {
            "column": EXPECTED_COLUMNS,
            "dtype": [str(raw[column].dtype) for column in EXPECTED_COLUMNS],
            "non_null_count": [int(raw[column].notna().sum()) for column in EXPECTED_COLUMNS],
            "unique_count": [int(raw[column].nunique(dropna=True)) for column in EXPECTED_COLUMNS],
        }
    )
    sample = raw.head(3)[
        [
            "Date",
            "Workout Name",
            "Exercise Name",
            "Set Order",
            "Weight",
            "Reps",
            "Distance",
            "Seconds",
        ]
    ]

    numeric_summary = raw[["Set Order", "Weight", "Reps", "Distance", "Seconds"]].describe(
        percentiles=[0.01, 0.5, 0.99]
    ).T.reset_index(names="column")

    top_exercises = exercise_summary.head(15).copy()
    primary = exercise_summary.loc[
        exercise_summary["Exercise Name"].isin(PRIMARY_EXERCISES)
    ].copy()
    primary["selection_reason"] = primary["Exercise Name"].map(
        {
            "Squat (Barbell)": "most sessions among interpretable external-load lifts",
            "Incline Bench Press (Barbell)": "long span and high session count",
            "Seated Shoulder  Press (Barbell)": "long span and high session count",
            "Bench Press (Barbell)": "major lift with a long continuous span",
            "Deadlift (Barbell)": "long span despite lower session density",
        }
    )
    primary_order = {name: rank for rank, name in enumerate(PRIMARY_EXERCISES)}
    primary["_order"] = primary["Exercise Name"].map(primary_order)
    primary = primary.sort_values("_order").drop(columns="_order")

    top_aliases = aliases.head(15).copy()
    if not top_aliases.empty:
        top_aliases["smaller_name_coverage_pct"] = 100 * top_aliases[
            "smaller_name_coverage"
        ]

    outlier_display = outliers.head(20).copy()
    collision_group_count = int(
        set_order_collisions[
            ["Date", "Workout Name", "Exercise Name", "Set Order"]
        ].drop_duplicates().shape[0]
    )
    timestamp_workout_conflicts = int(
        exact_view.groupby("date_ts")["Workout Name"].nunique().gt(1).sum()
    )
    longest_gap = gaps.iloc[0]
    top_gap_comparison = gap_comparison.loc[gap_comparison["gap_rank"].eq(1)].sort_values(
        "exercise_name"
    )

    highest_session_exercise = exercise_summary.iloc[0]
    largest_change = trend_summary.sort_values(
        "first_to_last_window_change_pct", ascending=False
    ).iloc[0]

    if plateau_load is not None:
        hybrid_question = (
            f"{plateau_load['exercise_name']}에서 {plateau_load['start_date']:%Y-%m-%d}~"
            f"{plateau_load['end_date']:%Y-%m-%d}에 탐지된 performance plateau candidate 동안 "
            "volume과 frequency는 직전 동기간 대비 어떻게 변했으며, 관련 연구에서는 어떤 "
            "훈련 변수를 고려하는가?"
        )
        plateau_detail = (
            f"동일 길이의 직전 기간과 비교 가능한 가장 긴 후보는 "
            f"**{plateau_load['exercise_name']}**, "
            f"{plateau_load['start_date']:%Y-%m-%d}~{plateau_load['end_date']:%Y-%m-%d} "
            f"({plateau_load['duration_days']}일)이다. 후보 구간의 시작/종료 3-session median "
            f"e1RM 변화는 {plateau_load['change_pct']:.2f}%였다. 직전 동기간 대비 주당 평균 "
            f"volume load는 {plateau_load['before_weekly_volume_mean']:,.1f}에서 "
            f"{plateau_load['during_weekly_volume_mean']:,.1f}, frequency는 "
            f"{plateau_load['before_weekly_frequency_mean']:.2f}에서 "
            f"{plateau_load['during_weekly_frequency_mean']:.2f}로 관찰됐다. 이는 연관 패턴이지 "
            "원인 판정이 아니다."
        )
    else:
        hybrid_question = (
            f"{largest_change['exercise_name']}의 장기 e1RM 변화와 volume/frequency 변화는 "
            "어떻게 함께 나타났으며, 관련 연구에서는 어떤 변수를 고려하는가?"
        )
        plateau_detail = "현재 규칙으로 탐지된 plateau candidate가 없어 규칙 민감도 검토가 필요하다."

    lines = [
        "# Workout Dataset EDA",
        "",
        "> Phase 1 산출물. 원본 CSV는 수정하지 않았으며, 이 보고서의 모든 수치는 "
        "`scripts/eda_workouts.py`로 재생성할 수 있다.",
        "",
        "## 1. Dataset Summary",
        "",
        f"- 입력 파일: `{input_path.as_posix()}`",
        f"- SHA-256: `{raw_hash}`",
        f"- 크기: **{len(raw):,}행 × {len(EXPECTED_COLUMNS)}열**",
        f"- 기록 범위: **{valid_dates.min():%Y-%m-%d %H:%M:%S} ~ "
        f"{valid_dates.max():%Y-%m-%d %H:%M:%S}**",
        f"- 고유 timestamp: **{unique_sessions:,}개**; 고유 운동 날짜: **{unique_days:,}일**",
        f"- 하루에 timestamp가 2개 이상인 날짜: **{multi_session_days:,}일**",
        f"- 원본 Exercise Name: **{raw['Exercise Name'].nunique():,}종**; Workout Name: "
        f"**{raw['Workout Name'].nunique():,}종**",
        "",
        "### Row semantics",
        "",
        "한 행은 특정 workout timestamp 안에서 특정 운동의 한 set 기록으로 보인다. 다만 "
        "exact duplicate와 같은 set이 서로 다른 Exercise Name으로 함께 기록된 alias-shadow 패턴이 "
        "있어, 원본 행 수를 곧바로 실제 수행 set 수로 해석할 수 없다. `Date` timestamp를 잠정 "
        "session key로 사용하지만, 파일명의 `721 workouts`를 실제 session 수로 사용하지 않는다.",
        "",
        "### Schema",
        "",
        markdown_table(schema),
        "",
        "### Sample rows",
        "",
        markdown_table(sample),
        "",
        "### Schema and numeric range",
        "",
        markdown_table(
            numeric_summary,
            ["column", "count", "mean", "std", "min", "1%", "50%", "99%", "max"],
        ),
        "",
        "`Weight` 단위 metadata가 파일에 없으므로 단위를 확정하지 않았다. 보고서와 차트는 "
        "원본 weight unit을 그대로 사용하며 kg/lb 변환을 하지 않는다.",
        "",
        "## 2. Data Quality",
        "",
        "### Missing values",
        "",
        markdown_table(missing, ["column", "missing_count", "missing_pct"]),
        "",
        "Notes 계열을 제외한 핵심 구조·수치 컬럼에는 결측치가 없다. 빈 Notes는 정보 부재이며 "
        "훈련 피로, 통증, 수면 또는 RPE를 보완하는 데이터로 간주할 수 없다.",
        "",
        "### Duplicate structure",
        "",
        f"- exact duplicate 초과 행: **{duplicate_extra:,}행** "
        f"({format_pct(duplicate_extra, len(raw))})",
        f"- duplicate group에 속한 전체 행: **{duplicate_all:,}행**; group: "
        f"**{duplicate_groups:,}개**",
        f"- exact-deduplicated EDA view: **{len(exact_view):,}행**",
        f"- 운동명만 다른 동일 set/value group: **{alias_like_groups:,}개**, 추가 이름 행 "
        f"**{alias_like_extra_rows:,}개**. 일부는 우연히 값이 같은 다른 운동일 수 있어 자동 삭제하지 않는다.",
        f"- high-priority alias 후보 {len(alias_map):,}개만 임시 매핑한 sensitivity view: "
        f"**{len(alias_adjusted):,}행**",
        f"- timestamp 하나에 Workout Name이 2개 이상 연결된 경우: "
        f"**{timestamp_workout_conflicts:,}개**",
        f"- exact duplicate 제거 후 `(Date, Workout Name, Exercise Name, Set Order)` 충돌: "
        f"**{collision_group_count:,}개 group / {len(set_order_collisions):,}행**",
        "",
        "세트 수·volume처럼 중복에 민감한 지표는 exact-deduplicated view를 기본으로 사용했다. "
        "alias-adjusted view는 session pattern의 민감도 확인에만 사용했고, Phase 2 cleaning rule로 "
        "확정하지 않았다.",
        "",
        "### Session pattern sensitivity",
        "",
        markdown_table(session_view_summary),
        "",
        "원본 운동명 기준 exercise count는 alias-shadow 때문에 과대계상될 수 있다. session timestamp "
        "수는 세 view 모두 동일하다.",
        "",
        "### Set-order collision candidates",
        "",
        markdown_table(
            set_order_collisions,
            [
                "source_row",
                "Date",
                "Exercise Name",
                "Set Order",
                "Weight",
                "Reps",
                "collision_group_size",
            ],
        ),
        "",
        "이 충돌은 모두 2017-09-04 `Good Morning (Barbell)`에 있으며 같은 set order에 "
        "서로 다른 reps가 기록됐다. 두 set을 모두 수행한 뒤 set order가 잘못 입력됐을 수도 있고 "
        "수정 이력일 수도 있으므로 Phase 2에서 임의 제거하지 않는다.",
        "",
        "## 3. Exercise Distribution",
        "",
        markdown_table(
            top_exercises,
            [
                "Exercise Name",
                "set_count",
                "session_count",
                "first_record",
                "last_record",
                "recording_span_days",
                "e1rm_eligible_sessions",
            ],
        ),
        "",
        f"가장 많은 session을 가진 raw exercise name은 **{highest_session_exercise['Exercise Name']}** "
        f"({int(highest_session_exercise['session_count']):,} sessions)이다. 이 순위는 exact duplicate만 "
        "제외했으며 alias 통합 전이다.",
        "",
        "## 4. Alias Candidates",
        "",
        "후보는 (1) timestamp, workout, set order, weight, reps, distance, seconds, notes가 같고 "
        "Exercise Name만 다른 co-record 패턴과 (2) 명칭 유사성을 근거로 만들었다. 후보일 뿐 "
        "자동 canonicalization 결과가 아니다.",
        "",
        markdown_table(
            top_aliases,
            [
                "name_a",
                "name_b",
                "co_recorded_identical_sets",
                "smaller_name_coverage_pct",
                "review_priority",
                "evidence",
            ],
        ),
        "",
        "Phase 2에서는 high-priority 쌍을 실제 날짜 범위와 set-by-set으로 검토한 후 "
        "`exercise_aliases` mapping을 확정해야 한다. 특히 `Leg press`/`Leg press (hinge )`와 "
        "`Hammer Curl`/`Hammer Curl (Dumbbell )`은 이름만으로 장비·동작 동일성을 확정하지 않는다.",
        "",
        "## 5. Outlier Candidates",
        "",
        "flag 규칙은 `Weight >= 1000`, 운동 내 median 대비 매우 큰 robust spike, `Reps >= 25`, "
        "음수/비정상 set order, 또는 부하가 있는데 reps·time·distance가 모두 0인 행이다. "
        "flag는 삭제 지시가 아니다.",
        "",
        markdown_table(
            outlier_display,
            [
                "source_row",
                "date",
                "exercise_name",
                "set_order",
                "weight",
                "reps",
                "reason",
            ],
        ),
        "",
        "`Squat (Barbell)`의 2,956 weight 기록은 동일 운동의 주변 값과 규모가 크게 달라 "
        "e1RM/volume 계산에서만 high-confidence spike로 제외했다. `Rope Never Ending`의 "
        "1,000~1,100 값은 장비 척도일 수 있어 별도 운동 간 비교에 사용하지 않는다. 25~60 reps "
        "기록은 실제 고반복 set일 가능성도 있으므로 삭제하지 않고 e1RM 허용 범위에서만 제외했다.",
        "",
        "## 6. Longitudinal Exercise Candidates",
        "",
        "장기간 기록, session 수, 양의 외부부하 기록, 운동 해석 가능성을 함께 고려해 다음 5개를 "
        "우선 후보로 선정했다. 이는 raw Exercise Name 기준이며 Phase 2 alias 확정 후 다시 계산한다.",
        "",
        markdown_table(
            primary,
            [
                "Exercise Name",
                "set_count",
                "session_count",
                "recording_span_days",
                "e1rm_eligible_sessions",
                "selection_reason",
            ],
        ),
        "",
        "`Weighted dips`와 `Chin Up`은 기록량이 많지만 Weight가 추가부하, 총부하, 보조부하 중 "
        "무엇을 의미하는지 metadata로 확정하기 어려워 우선 e1RM 후보에서 제외했다.",
        "",
        "## 7. e1RM / Volume / Frequency Pattern",
        "",
        "EDA working definition은 Epley `weight × (1 + reps / 30)`이며 **1~12 reps**, 양의 "
        "Weight, high-confidence weight spike가 아닌 set만 사용한다. e1RM은 실제 1RM이 아니고 "
        "원본 weight unit을 유지한다. 추세선은 trailing 5-session median이다.",
        "",
        markdown_table(
            trend_summary,
            [
                "exercise_name",
                "eligible_sessions",
                "first_5_session_median_e1rm",
                "last_5_session_median_e1rm",
                "first_to_last_window_change_pct",
                "peak_session_e1rm",
                "peak_date",
                "latest_vs_peak_pct",
            ],
        ),
        "",
        "![Session-best e1RM trends](figures/e1rm_trends.png)",
        "",
        "Weekly volume load는 `Σ(weight × reps)`, frequency는 주간 고유 session timestamp 수로 "
        "계산했다. exact duplicate는 제외했고 선택한 raw exercise name만 포함했다. 0 weight로 "
        "표현된 bodyweight set에는 이 volume 정의를 적용할 수 없다.",
        "",
        "![Weekly volume and frequency](figures/weekly_load_frequency.png)",
        "",
        "전체 주간 session frequency의 median은 "
        f"**{weekly_frequency['session_count'].median():.1f}회**, 최대는 "
        f"**{int(weekly_frequency['session_count'].max())}회**다. 월간 median은 "
        f"**{monthly_frequency['session_count'].median():.1f}회**, 최대는 "
        f"**{int(monthly_frequency['session_count'].max())}회**다.",
        "",
        "![Overall session frequency](figures/overall_session_frequency.png)",
        "",
        "## 8. Training Gaps",
        "",
        f"가장 긴 timestamp 간격은 **{longest_gap['previous_session']:%Y-%m-%d %H:%M} → "
        f"{longest_gap['next_session']:%Y-%m-%d %H:%M}**, "
        f"**{longest_gap['gap_days']:.2f}일**이다.",
        "",
        markdown_table(
            gaps.head(10),
            [
                "previous_session",
                "next_session",
                "gap_days",
                "calendar_day_difference",
            ],
        ),
        "",
        "상위 gap 전후 60일 안에 선택 운동의 e1RM session이 모두 존재하는 경우만 단순 비교했다. "
        "운동 종류·rep range·훈련 구성 차이를 통제하지 않았으므로 detraining 효과나 인과로 "
        "해석하지 않는다.",
        "",
        markdown_table(
            top_gap_comparison,
            [
                "gap_rank",
                "exercise_name",
                "pre_session",
                "post_session",
                "pre_e1rm",
                "post_e1rm",
                "change_pct",
            ],
        ),
        "",
        "## 9. Plateau Candidates",
        "",
        f"규칙: 연속 {PLATEAU_WINDOW_SESSIONS} sessions, 최소 {PLATEAU_MIN_DAYS}일, 시작/종료 "
        f"각 3-session median e1RM 변화 절댓값 ≤ {PLATEAU_MAX_ABS_CHANGE_PCT:.1f}%, "
        f"기간 내 범위 ≤ median의 {PLATEAU_MAX_RANGE_PCT:.1f}%. 가능한 모든 연속 구간 중 "
        "규칙을 만족하는 가장 긴 non-overlapping 구간을 남겼다. 이 규칙은 원인을 설명하지 "
        "않으며 후보만 탐지한다.",
        "",
        markdown_table(
            plateau_candidates.head(15),
            [
                "exercise_name",
                "start_date",
                "end_date",
                "duration_days",
                "session_count",
                "change_pct",
                "within_period_range_pct",
            ],
        ),
        "",
        plateau_detail,
        "",
        "## 10. Dataset Limitations",
        "",
        "- RPE, RIR, 수면, 식단, 체중 시계열, 스트레스, 피로도, 부상 정보가 없다.",
        "- Weight 단위와 bodyweight/assistance/stack-weight 의미가 명시되어 있지 않다.",
        "- exercise alias와 alias-shadow 중복이 많아 Phase 2 canonicalization 전에 aggregate set/volume "
        "수치를 확정할 수 없다.",
        "- workout timestamp를 session으로 간주했지만 원본 시스템의 session ID가 없다.",
        "- e1RM은 공식과 rep cap에 민감하며 실제 1RM이 아니다.",
        "- volume/frequency와 performance의 동시 변화는 인과관계를 의미하지 않는다.",
        "- 단일 사용자의 관찰 로그이므로 인구 수준의 운동 효과를 추론할 수 없다.",
        "",
        "## 11. Candidate Questions",
        "",
        "### DB-only",
        "",
        f"- 가장 긴 운동 공백은 언제였고 정확히 몇 일이었는가? (현재 관찰값: "
        f"{longest_gap['previous_session']:%Y-%m-%d}~{longest_gap['next_session']:%Y-%m-%d})",
        f"- session 수가 가장 많은 raw exercise name은 무엇인가? (현재 관찰값: "
        f"{highest_session_exercise['Exercise Name']})",
        "",
        "### Metric",
        "",
        f"- {largest_change['exercise_name']}의 첫 5-session median과 마지막 5-session median e1RM은 "
        "얼마나 달라졌는가?",
        "- 선택 운동의 주간 volume load와 frequency가 가장 낮거나 높은 기간은 언제인가?",
        "",
        "### Literature-only",
        "",
        "- 저항운동 연구에서 training frequency와 strength adaptation의 관계는 어떻게 정의되는가?",
        "- detraining 연구에서는 훈련 중단 기간과 strength 변화의 관계를 어떻게 보고하는가?",
        "",
        "### Hybrid",
        "",
        f"- {hybrid_question}",
        f"- 실제 최장 gap({longest_gap['gap_days']:.2f}일) 전후 수행은 어떻게 달라졌으며, "
        "detraining 문헌은 이러한 관찰을 해석할 때 어떤 한계를 제시하는가?",
        "",
        "### Unanswerable / Abstention",
        "",
        "- 최근 피로 누적 때문에 성능이 떨어진 것인가?",
        "- 수면 부족이 특정 plateau candidate의 원인인가?",
        "- 당시 RPE가 상승했기 때문에 중량 증가가 멈춘 것인가?",
        "",
        "위 질문은 현재 로그에 필요한 변수가 없어 원인을 판단할 수 없다고 답해야 한다.",
        "",
        "## 12. Recommended Demo Candidates",
        "",
        f"1. **Plateau hybrid trace:** {hybrid_question}",
        f"2. **Training-gap hybrid trace:** {longest_gap['previous_session']:%Y-%m-%d}~"
        f"{longest_gap['next_session']:%Y-%m-%d}의 실제 gap 전후 metric을 계산한 뒤 detraining "
        "문헌을 검색한다.",
        "3. **Reliability/abstention trace:** ‘피로 누적으로 성능이 떨어졌는가?’에 대해 데이터에 "
        "RPE·수면·피로도가 없음을 근거로 인과 판단을 거부한다.",
        "4. **Data-quality trace:** Squat 2,956 weight spike를 그대로 계산했을 때와 flag 후 결과를 "
        "비교해 provenance와 validation의 필요성을 보여준다.",
        "",
        "대표 데모는 Phase 2 alias/outlier 정책 검토 후 최종 확정한다.",
        "",
        "## Reproduction",
        "",
        "```powershell",
        "python scripts/eda_workouts.py",
        "```",
        "",
        "생성 표는 `reports/tables/`, 차트는 `reports/figures/`, 기계 판독용 핵심 요약은 "
        "`reports/eda_summary.json`에 저장된다.",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(input_path: Path, reports_dir: Path) -> dict[str, object]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    tables_dir = reports_dir / "tables"
    figures_dir = reports_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    source_hash_before = sha256_file(input_path)
    raw = load_workouts(input_path)
    exact_view = exact_deduplicated_view(raw)
    aliases, alias_map, alias_like_groups, alias_like_extra_rows = build_alias_candidates(
        exact_view
    )
    alias_adjusted = provisional_alias_adjusted_view(exact_view, alias_map)
    flagged = robust_weight_flags(exact_view)
    outliers = build_outlier_candidates(flagged)
    set_order_collisions = build_set_order_collisions(exact_view)
    metric_view = add_metric_columns(flagged)

    exercise_summary = build_exercise_summary(metric_view)
    session_view_summary = pd.DataFrame(
        [
            summarize_session_view(raw, "raw"),
            summarize_session_view(exact_view, "exact_deduplicated"),
            summarize_session_view(alias_adjusted, "provisional_alias_adjusted"),
        ]
    )
    weekly_frequency, monthly_frequency = session_frequency_tables(metric_view)
    session_metrics = build_session_metrics(metric_view)
    weekly_metrics = build_weekly_metrics(metric_view)
    trend_summary = build_trend_summary(session_metrics)
    gaps = build_training_gaps(metric_view)
    gap_comparison = build_gap_performance_comparison(gaps, session_metrics)
    plateau_candidates = detect_plateau_candidates(session_metrics)
    plateau_load = summarize_plateau_load(plateau_candidates, weekly_metrics)

    save_figures(session_metrics, weekly_metrics, weekly_frequency, figures_dir)

    outputs = {
        "exercise_summary.csv": exercise_summary,
        "alias_candidates.csv": aliases,
        "outlier_candidates.csv": outliers,
        "set_order_collision_candidates.csv": set_order_collisions,
        "session_view_summary.csv": session_view_summary,
        "weekly_session_frequency.csv": weekly_frequency,
        "monthly_session_frequency.csv": monthly_frequency,
        "primary_exercise_session_metrics.csv": session_metrics,
        "primary_exercise_weekly_metrics.csv": weekly_metrics,
        "primary_exercise_trend_summary.csv": trend_summary,
        "training_gaps.csv": gaps,
        "gap_performance_comparison.csv": gap_comparison,
        "plateau_candidates.csv": plateau_candidates,
    }
    for filename, frame in outputs.items():
        frame.to_csv(tables_dir / filename, index=False, encoding="utf-8")

    source_hash_after = sha256_file(input_path)
    if source_hash_before != source_hash_after:
        raise RuntimeError("Raw input hash changed while EDA was running.")

    summary = {
        "input_path": input_path.as_posix(),
        "input_sha256": source_hash_before,
        "rows": int(len(raw)),
        "columns": int(len(EXPECTED_COLUMNS)),
        "date_min": raw["date_ts"].min().isoformat(),
        "date_max": raw["date_ts"].max().isoformat(),
        "unique_timestamps": int(raw["date_ts"].nunique()),
        "unique_workout_days": int(raw["date_ts"].dt.normalize().nunique()),
        "unique_exercise_names": int(raw["Exercise Name"].nunique()),
        "unique_workout_names": int(raw["Workout Name"].nunique()),
        "exact_duplicate_extra_rows": int(
            raw.duplicated(subset=EXPECTED_COLUMNS, keep="first").sum()
        ),
        "exact_deduplicated_rows": int(len(exact_view)),
        "alias_like_groups": int(alias_like_groups),
        "alias_like_extra_rows": int(alias_like_extra_rows),
        "provisional_alias_map": alias_map,
        "provisional_alias_adjusted_rows": int(len(alias_adjusted)),
        "outlier_candidate_rows": int(len(outliers)),
        "set_order_collision_groups": int(
            set_order_collisions[
                ["Date", "Workout Name", "Exercise Name", "Set Order"]
            ].drop_duplicates().shape[0]
        ),
        "primary_exercises": PRIMARY_EXERCISES,
        "longest_gap_days": float(gaps.iloc[0]["gap_days"]),
        "longest_gap_start": gaps.iloc[0]["previous_session"].isoformat(),
        "longest_gap_end": gaps.iloc[0]["next_session"].isoformat(),
        "plateau_candidate_count": int(len(plateau_candidates)),
        "e1rm_definition": {
            "formula": "weight * (1 + reps / 30)",
            "rep_min": E1RM_REP_MIN,
            "rep_max": E1RM_REP_MAX,
            "rolling_sessions": E1RM_ROLLING_SESSIONS,
        },
    }
    (reports_dir / "eda_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    write_report(
        reports_dir / "EDA.md",
        input_path,
        source_hash_before,
        raw,
        exact_view,
        alias_adjusted,
        aliases,
        alias_map,
        alias_like_groups,
        alias_like_extra_rows,
        outliers,
        set_order_collisions,
        exercise_summary,
        session_view_summary,
        weekly_frequency,
        monthly_frequency,
        trend_summary,
        weekly_metrics,
        gaps,
        gap_comparison,
        plateau_candidates,
        plateau_load,
    )
    return summary


def main() -> None:
    args = parse_args()
    summary = write_outputs(args.input, args.reports_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
