"""Execution requirements, independent of natural-language Metric selection."""
import json
from pathlib import Path
import re

CONFIG = Path(__file__).resolve().parents[2] / "config/phase_a_operation_arguments_v1.json"
EXERCISE_FIELDS = frozenset({"canonical_exercise_name", "candidate_exercises",
    "exercise_resolution_status", "exercise_mention", "exercise", "exercise_variant"})
TIME_FIELDS = frozenset({"time_condition", "start_date", "end_date"})


def load_contract():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["version"] != "phase_a_operation_arguments_v1":
        raise ValueError("unknown_operation_argument_contract")
    return config


def record_operation_for_metric(operation):
    return load_contract()["metric_operations"][operation]["record_operation"]


def required_user_arguments(draft, config):
    required = set()
    records = set()
    for analysis in draft.requested_analyses:
        metric = config["metric_operations"][analysis.operation.value]
        required.update(metric["required_arguments"])
        records.add(metric["record_operation"])
    if not draft.requested_analyses and draft.record_operation:
        records.add(draft.record_operation)
    for operation in records:
        required.update(config["record_operations"][operation]["required_arguments"])
    return required.difference(config["automatically_supplied_arguments"])


def normalize_llm_issues(question, draft, issues, *, required, exercise_required, explicit_exercise, n_values):
    """Discard non-inputs; preserve declared constraints with original support.

    Existing validation still verifies values, dates, catalog membership, source
    spans, unsupported operations, and actual missing arguments afterwards.
    """
    constraints = set()
    if explicit_exercise:
        constraints.update(EXERCISE_FIELDS)
    time = draft.time_condition
    if draft.personal_record_requested and (time.scope != "all_records" or
            time.source_text or time.start_date or time.end_date):
        constraints.update(TIME_FIELDS)
    if n_values:
        constraints.add("n_sessions")
    # These identify a comparison constraint, never a particular Metric.
    if re.search(r"비교|보다|comparison|\bversus\b|\bvs\b|처음|최초|첫|초반|\bfirst\b", question, re.I):
        constraints.add("comparison")
    if draft.user_assumptions or re.search(r"가정|전제", question):
        constraints.add("user_assumptions")
    if draft.session_id:
        constraints.add("session_id")
    # Purpose/channel/operation uncertainty is not an extra Tool input.
    structural = {"question", "evidence_need", "requested_analyses", "personal_record_requested",
                  "literature_requested"}
    if not draft.record_operation and not draft.requested_analyses:
        structural.add("record_operation")
    if draft.literature_requested and not (draft.literature_subquestion or "").strip():
        structural.add("literature_subquestion")
    blocking = required | constraints | structural
    if exercise_required:
        blocking.update(EXERCISE_FIELDS)
    if draft.requested_analyses and all(x.n_sessions is not None for x in draft.requested_analyses):
        # Values are subsequently checked against the user's original numbers.
        blocking.discard("n_sessions")
    kept, ignored = [], []
    for issue in issues:
        if issue.field in blocking:
            kept.append(issue)
        else:
            ignored.append(issue.field)
    return kept, sorted(set(ignored)), sorted(constraints)
