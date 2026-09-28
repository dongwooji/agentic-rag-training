"""Validation and reporting for preprocessing_v1."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

import pandas as pd

from .policy import normalize_exercise_display, normalize_exercise_key


def _reason_counts(values: pd.Series) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for value in values.fillna(""):
        counts.update(reason for reason in str(value).split(";") if reason)
    return dict(sorted(counts.items()))


def _assert_checks(checks: dict[str, bool]) -> None:
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise ValueError(f"Preprocessing validation failed: {failed}")


def build_validation_summary(
    raw: pd.DataFrame,
    processed: pd.DataFrame,
    lineage: pd.DataFrame,
    policy: dict[str, Any],
    alias_decisions: list[dict[str, str]],
    source_sha256: str,
) -> dict[str, Any]:
    expected_columns = list(policy["expected_columns"])
    raw_duplicate_count = int(raw.duplicated(subset=expected_columns, keep="first").sum())

    status_counts = {
        str(key): int(value)
        for key, value in lineage["row_status"].value_counts().sort_index().items()
    }
    raw_rows = int(len(raw))
    lineage_rows = int(len(lineage))
    processed_rows = int(len(processed))
    exact_duplicate_rows = int(lineage["is_exact_duplicate"].sum())
    alias_shadow_rows = int(lineage["is_alias_shadow"].sum())

    collision_ids = processed.loc[
        processed["has_set_order_collision"], "set_order_collision_group_id"
    ]
    collision_group_count = int(collision_ids[collision_ids.ne("")].nunique())
    collision_row_count = int(processed["has_set_order_collision"].sum())

    merge_decisions = [row for row in alias_decisions if row["decision"] == "merge"]
    defer_decisions = [row for row in alias_decisions if row["decision"] == "defer"]

    merge_checks: list[bool] = []
    defer_checks: list[bool] = []
    for decision in merge_decisions:
        mask = lineage["raw_exercise_name"].map(normalize_exercise_key).eq(
            decision["raw_name_key"]
        )
        if mask.any():
            merge_checks.append(
                lineage.loc[mask, "canonical_exercise_name"]
                .eq(decision["canonical_name"])
                .all()
            )
    for decision in defer_decisions:
        mask = lineage["raw_exercise_name"].map(normalize_exercise_key).eq(
            decision["raw_name_key"]
        )
        if mask.any():
            defer_checks.append(
                lineage.loc[mask, "canonical_exercise_name"]
                .eq(normalize_exercise_display(decision["raw_name"]))
                .all()
            )

    checks = {
        "raw_rows_equal_lineage_rows": raw_rows == lineage_rows,
        "source_row_is_unique": bool(lineage["source_row"].is_unique),
        "processed_set_id_is_unique": bool(processed["processed_set_id"].is_unique),
        "kept_rows_equal_processed_rows": int(lineage["include_in_processed"].sum())
        == processed_rows,
        "logical_ids_reconcile": int(lineage["processed_set_id"].nunique())
        == processed_rows,
        "every_processed_id_has_lineage": set(processed["processed_set_id"])
        == set(lineage["processed_set_id"]),
        "raw_row_counts_reconcile": int(processed["raw_row_count"].sum()) == raw_rows,
        "collapsed_count_equation": processed_rows
        + exact_duplicate_rows
        + alias_shadow_rows
        == raw_rows,
        "per_set_count_equation": bool(
            processed["raw_row_count"]
            .eq(
                1
                + processed["exact_duplicate_extra_count"]
                + processed["alias_shadow_count"]
            )
            .all()
        ),
        "exact_duplicates_match_raw": exact_duplicate_rows == raw_duplicate_count,
        "exact_duplicate_aggregates_reconcile": int(
            processed["exact_duplicate_extra_count"].sum()
        )
        == exact_duplicate_rows,
        "alias_shadow_aggregates_reconcile": int(processed["alias_shadow_count"].sum())
        == alias_shadow_rows,
        "source_hash_is_constant": lineage["source_file_sha256"].nunique() == 1
        and processed["source_file_sha256"].nunique() == 1
        and lineage["source_file_sha256"].iloc[0] == source_sha256
        and processed["source_file_sha256"].iloc[0] == source_sha256,
        "preprocessing_version_is_constant": lineage["preprocessing_version"].nunique()
        == 1
        and processed["preprocessing_version"].nunique() == 1
        and lineage["preprocessing_version"].iloc[0] == policy["version"]
        and processed["preprocessing_version"].iloc[0] == policy["version"],
        "session_count_preserved": int(processed["session_id"].nunique())
        == int(raw["_date_ts"].nunique()),
        "metric_flags_reconcile": int(lineage["include_in_e1rm"].sum())
        == int(processed["include_in_e1rm"].sum())
        and int(lineage["include_in_volume_metrics"].sum())
        == int(processed["include_in_volume_metrics"].sum()),
        "merge_rules_applied": all(merge_checks),
        "deferred_rules_not_merged": all(defer_checks),
        "collision_ids_present_when_flagged": bool(
            processed.loc[
                processed["has_set_order_collision"], "set_order_collision_group_id"
            ].ne("").all()
        ),
    }
    _assert_checks(checks)

    alias_shadow_by_canonical = {
        str(key): int(value)
        for key, value in lineage.loc[lineage["is_alias_shadow"]]
        .groupby("canonical_exercise_name")
        .size()
        .sort_values(ascending=False)
        .items()
    }

    summary: dict[str, Any] = {
        "preprocessing_version": policy["version"],
        "source": {
            "path": policy["source_file"],
            "sha256": source_sha256,
            "rows": raw_rows,
            "columns": len(expected_columns),
            "date_min": raw["_date_ts"].min().isoformat(),
            "date_max": raw["_date_ts"].max().isoformat(),
            "unique_timestamps": int(raw["_date_ts"].nunique()),
            "unique_workout_days": int(raw["_date_ts"].dt.normalize().nunique()),
            "raw_exercise_name_count": int(raw["Exercise Name"].nunique()),
        },
        "reconciliation": {
            "raw_rows": raw_rows,
            "lineage_rows": lineage_rows,
            "processed_rows": processed_rows,
            "excluded_exact_duplicate_rows": exact_duplicate_rows,
            "excluded_alias_shadow_rows": alias_shadow_rows,
            "equation": (
                f"{processed_rows} processed + {exact_duplicate_rows} exact duplicate + "
                f"{alias_shadow_rows} alias shadow = {raw_rows} raw"
            ),
            "row_status_counts": status_counts,
        },
        "canonicalization": {
            "raw_exercise_name_count": int(raw["Exercise Name"].nunique()),
            "normalized_exercise_name_count": int(
                lineage["normalized_exercise_name"].nunique()
            ),
            "canonical_exercise_name_count": int(
                processed["canonical_exercise_name"].nunique()
            ),
            "merge_decision_count": len(merge_decisions),
            "defer_decision_count": len(defer_decisions),
            "name_normalization_raw_rows": int(
                lineage["name_normalization_applied"].sum()
            ),
            "alias_mapping_raw_rows": int(lineage["alias_mapping_applied"].sum()),
            "alias_shadow_by_canonical_name": alias_shadow_by_canonical,
        },
        "flags": {
            "outlier_raw_rows": int(lineage["is_outlier"].sum()),
            "outlier_processed_rows": int(processed["is_outlier"].sum()),
            "outlier_reason_counts_raw": _reason_counts(lineage["outlier_reason"]),
            "outlier_reason_counts_processed": _reason_counts(
                processed["outlier_reason"]
            ),
            "set_order_collision_groups": collision_group_count,
            "set_order_collision_processed_rows": collision_row_count,
        },
        "metric_eligibility": {
            "volume_eligible_processed_rows": int(
                processed["include_in_volume_metrics"].sum()
            ),
            "e1rm_eligible_processed_rows": int(processed["include_in_e1rm"].sum()),
            "e1rm_formula_version": policy["metrics"]["estimated_1rm"][
                "formula_version"
            ],
            "e1rm_rep_min": int(policy["metrics"]["estimated_1rm"]["rep_min"]),
            "e1rm_rep_max": int(policy["metrics"]["estimated_1rm"]["rep_max"]),
            "weight_unit": policy["units"]["weight"],
        },
        "processed_schema": list(processed.columns),
        "lineage_schema": list(lineage.columns),
        "checks": checks,
        "all_checks_passed": all(checks.values()),
    }
    return summary


def _markdown_table(rows: Iterable[dict[str, Any]], columns: list[str]) -> str:
    materialized = list(rows)
    if not materialized:
        return "_해당 항목이 없습니다._"

    def escape(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(escape(row.get(column, "")) for column in columns) + " |"
        for row in materialized
    )
    return "\n".join(lines)


def render_validation_report(
    summary: dict[str, Any],
    alias_decisions: list[dict[str, str]],
    policy: dict[str, Any],
) -> str:
    reconciliation = summary["reconciliation"]
    canonicalization = summary["canonicalization"]
    flags = summary["flags"]
    metrics = summary["metric_eligibility"]
    checks = [
        {"check": name, "result": "PASS" if passed else "FAIL"}
        for name, passed in summary["checks"].items()
    ]
    status_rows = [
        {"row_status": name, "count": count}
        for name, count in reconciliation["row_status_counts"].items()
    ]
    outlier_rows = [
        {"reason": name, "raw_rows": count}
        for name, count in flags["outlier_reason_counts_raw"].items()
    ]
    alias_rows = [
        {
            "raw_name": row["raw_name"],
            "canonical_name": row["canonical_name"],
            "decision": row["decision"],
            "rationale": row["rationale"],
        }
        for row in alias_decisions
    ]
    alias_shadow_rows = [
        {"canonical_name": name, "collapsed_shadow_rows": count}
        for name, count in canonicalization["alias_shadow_by_canonical_name"].items()
    ]

    lines = [
        "# Preprocessing v1 Validation",
        "",
        "> Phase 2 산출물. 원본 CSV는 수정하지 않았으며 모든 원본 행은 "
        "`data/processed/row_lineage.csv`에서 추적할 수 있다.",
        "",
        "## 1. Policy",
        "",
        f"- 버전: `{summary['preprocessing_version']}`",
        f"- session key: `{', '.join(policy['session_key'])}` (원본에 session ID가 없어 잠정 사용)",
        f"- Weight 단위: `{metrics['weight_unit']}`; 단위 변환 없음",
        "- exact duplicate: 원본 lineage에는 보존하고 processed view에서는 첫 source row만 대표로 유지",
        "- alias: `merge` 결정만 적용; `defer` 후보는 서로 다른 운동명으로 유지",
        "- 이상치와 set-order 충돌: 삭제하지 않고 명시적 flag로 유지",
        f"- e1RM eligibility: Epley `{policy['metrics']['estimated_1rm']['formula']}`, "
        f"{metrics['e1rm_rep_min']}~{metrics['e1rm_rep_max']} reps",
        "",
        "## 2. Row Reconciliation",
        "",
        f"**{reconciliation['equation']}**",
        "",
        _markdown_table(status_rows, ["row_status", "count"]),
        "",
        "`workout_sets.csv`는 logical set당 한 행이고, `row_lineage.csv`는 raw row당 한 행이다. "
        "제외된 행도 `processed_set_id`를 통해 대표 logical set으로 연결된다.",
        "",
        "## 3. Exercise Canonicalization",
        "",
        f"- raw Exercise Name: **{canonicalization['raw_exercise_name_count']}종**",
        f"- formatting-normalized name: **{canonicalization['normalized_exercise_name_count']}종**",
        f"- processed canonical name: **{canonicalization['canonical_exercise_name_count']}종**",
        f"- merge 결정: **{canonicalization['merge_decision_count']}개**; defer 결정: "
        f"**{canonicalization['defer_decision_count']}개**",
        f"- 실제 alias-shadow로 접힌 행: **{reconciliation['excluded_alias_shadow_rows']}행**",
        "",
        _markdown_table(alias_rows, ["raw_name", "canonical_name", "decision", "rationale"]),
        "",
        "### Collapsed alias-shadow rows",
        "",
        _markdown_table(alias_shadow_rows, ["canonical_name", "collapsed_shadow_rows"]),
        "",
        "`Leg press`/`Leg press (hinge)`와 `Hammer Curl`/`Hammer Curl (Dumbbell)`은 "
        "장비·동작 동일성이 확정되지 않아 통합하지 않았다.",
        "",
        "## 4. Validation Flags",
        "",
        f"- outlier raw rows: **{flags['outlier_raw_rows']}행**; processed rows: "
        f"**{flags['outlier_processed_rows']}행**",
        f"- set-order collision: **{flags['set_order_collision_groups']}개 group / "
        f"{flags['set_order_collision_processed_rows']} processed rows**",
        "",
        _markdown_table(outlier_rows, ["reason", "raw_rows"]),
        "",
        "Weight ≥ 1000 또는 positive weight/zero reps 행은 metric에서 제외하지만 processed "
        "데이터 자체에서는 제거하지 않는다. 25회 이상 reps는 volume eligibility를 유지하고 "
        "e1RM eligibility만 제외한다.",
        "",
        "## 5. Metric Eligibility",
        "",
        f"- volume-eligible processed rows: **{metrics['volume_eligible_processed_rows']:,}행**",
        f"- e1RM-eligible processed rows: **{metrics['e1rm_eligible_processed_rows']:,}행**",
        f"- e1RM formula version: `{metrics['e1rm_formula_version']}`",
        "",
        "이 단계에서는 metric 값을 생성하지 않는다. Phase 8 Metric Tool이 이 eligibility와 "
        "versioned formula를 사용하도록 준비한 것이다.",
        "",
        "## 6. Output Files",
        "",
        f"- `{summary['output_files']['workout_sets']['path']}` — logical set rows, SHA-256 "
        f"`{summary['output_files']['workout_sets']['sha256']}`",
        f"- `{summary['output_files']['row_lineage']['path']}` — raw-row lineage, SHA-256 "
        f"`{summary['output_files']['row_lineage']['sha256']}`",
        "",
        "## 7. Validation Checks",
        "",
        _markdown_table(checks, ["check", "result"]),
        "",
        f"전체 결과: **{'PASS' if summary['all_checks_passed'] else 'FAIL'}**",
        "",
        "## 8. Remaining Review Gate",
        "",
        "Phase 3 PostgreSQL로 넘어가기 전에 다음을 사람이 검토한다.",
        "",
        "1. 13개 merge 결정과 4개 defer 결정",
        "2. Weight 단위를 계속 unknown으로 유지할지 여부",
        "3. Weight ≥ 1000 세 행의 metric 제외 정책",
        "4. set-order collision 행을 모두 유지하는 정책",
        "5. processed row reconciliation과 주요 운동 session 수",
        "",
        "검토 후 `preprocessing_v1`을 동결하고 PostgreSQL schema/load로 넘어간다.",
        "",
        "## Reproduction",
        "",
        "```powershell",
        "python scripts/preprocess_workouts.py",
        "```",
        "",
    ]
    return "\n".join(lines)
