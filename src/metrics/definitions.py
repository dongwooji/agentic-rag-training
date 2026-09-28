"""Single source for the Phase 1/2 deterministic metric definitions."""

from __future__ import annotations

from typing import Any

from src.preprocessing.policy import load_policy


_POLICY = load_policy()
_E1RM_POLICY = _POLICY["metrics"]["estimated_1rm"]
E1RM_FORMULA_VERSION = "epley_v1"
E1RM_REP_MIN = int(_E1RM_POLICY["rep_min"])
E1RM_REP_MAX = int(_E1RM_POLICY["rep_max"])
E1RM_ROLLING_SESSIONS = 5

# Frozen EDA plateau-candidate rule. It describes a pattern, not a diagnosis.
PLATEAU_WINDOW_SESSIONS = 8
PLATEAU_MIN_DAYS = 42
PLATEAU_MAX_ABS_CHANGE_PCT = 2.5
PLATEAU_MAX_RANGE_PCT = 12.0

if _E1RM_POLICY.get("formula_version") != E1RM_FORMULA_VERSION:
    raise RuntimeError("preprocessing_v1 e1RM formula version changed")
if _E1RM_POLICY.get("formula") != "weight * (1 + reps / 30)":
    raise RuntimeError("preprocessing_v1 Epley formula changed")


def epley_e1rm(weight: Any, reps: Any) -> Any:
    """Apply the exact preprocessing_v1 formula to scalars or vector values."""

    return weight * (1 + reps / 30.0)
