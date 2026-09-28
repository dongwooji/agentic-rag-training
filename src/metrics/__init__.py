"""Shared deterministic metric definitions."""

from .definitions import (
    E1RM_FORMULA_VERSION,
    E1RM_REP_MAX,
    E1RM_REP_MIN,
    E1RM_ROLLING_SESSIONS,
    PLATEAU_MAX_ABS_CHANGE_PCT,
    PLATEAU_MAX_RANGE_PCT,
    PLATEAU_MIN_DAYS,
    PLATEAU_WINDOW_SESSIONS,
    epley_e1rm,
)

__all__ = [
    "E1RM_FORMULA_VERSION",
    "E1RM_REP_MAX",
    "E1RM_REP_MIN",
    "E1RM_ROLLING_SESSIONS",
    "PLATEAU_MAX_ABS_CHANGE_PCT",
    "PLATEAU_MAX_RANGE_PCT",
    "PLATEAU_MIN_DAYS",
    "PLATEAU_WINDOW_SESSIONS",
    "epley_e1rm",
]
