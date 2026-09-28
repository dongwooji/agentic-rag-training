"""Load and validate the versioned preprocessing policy."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = PROJECT_ROOT / "config" / "preprocessing_v1.json"
DEFAULT_ALIAS_PATH = PROJECT_ROOT / "config" / "exercise_aliases_v1.csv"
VALID_ALIAS_DECISIONS = {"merge", "defer", "reject"}


def normalize_exercise_display(value: str) -> str:
    """Apply formatting-only cleanup while preserving human-readable casing."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s+([)])", r"\1", normalized)
    normalized = re.sub(r"([(])\s+", r"\1", normalized)
    return normalized


def normalize_exercise_key(value: str) -> str:
    """Return a case-insensitive lookup key for alias-policy matching."""

    return normalize_exercise_display(value).casefold()


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        policy: dict[str, Any] = json.load(handle)

    required = {
        "version",
        "source_file",
        "expected_columns",
        "session_key",
        "units",
        "alias_policy",
        "outlier_policy",
        "metrics",
    }
    missing = sorted(required.difference(policy))
    if missing:
        raise ValueError(f"Policy is missing required keys: {missing}")
    if policy["version"] != "preprocessing_v1":
        raise ValueError(f"Unsupported preprocessing policy version: {policy['version']}")
    return policy


def load_alias_decisions(path: Path = DEFAULT_ALIAS_PATH) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    required_columns = {
        "raw_name",
        "canonical_name",
        "decision",
        "evidence",
        "rationale",
    }
    if not rows:
        raise ValueError("Alias decision file is empty.")
    missing_columns = sorted(required_columns.difference(rows[0]))
    if missing_columns:
        raise ValueError(f"Alias file is missing columns: {missing_columns}")

    merge_targets: dict[str, str] = {}
    validated: list[dict[str, str]] = []
    for row in rows:
        decision = row["decision"].strip().casefold()
        if decision not in VALID_ALIAS_DECISIONS:
            raise ValueError(
                f"Invalid alias decision {row['decision']!r} for {row['raw_name']!r}"
            )
        raw_name = row["raw_name"]
        canonical_name = normalize_exercise_display(row["canonical_name"])
        key = normalize_exercise_key(raw_name)
        if decision == "merge":
            previous = merge_targets.get(key)
            if previous is not None and previous != canonical_name:
                raise ValueError(
                    f"Conflicting merge targets for {raw_name!r}: {previous!r}, "
                    f"{canonical_name!r}"
                )
            merge_targets[key] = canonical_name
        validated.append(
            {
                "raw_name": raw_name,
                "raw_name_key": key,
                "canonical_name": canonical_name,
                "decision": decision,
                "evidence": row["evidence"],
                "rationale": row["rationale"],
            }
        )
    return validated


def build_alias_map(decisions: list[dict[str, str]]) -> dict[str, str]:
    return {
        row["raw_name_key"]: row["canonical_name"]
        for row in decisions
        if row["decision"] == "merge"
    }
