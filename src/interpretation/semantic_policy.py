"""Deterministic evidence policy, independent of argument completeness."""

from datetime import date, timedelta
import json
from pathlib import Path
import re

from .contracts import InterpretationDraft, SemanticInterpretationDraft, TimeCondition
from .semantic_contracts import (ARGUMENT_FIELDS, PURPOSE_FIELDS, ArgumentResolution,
    DefaultTimeWindow, EvidenceNeed, PhaseASemantics, ScopeObservation)
from .date_ranges import explicit_period, full_dates, DateExpressionError
from .validator import _personal_record_requirement, _SESSION_NUMBER

CONFIG = Path(__file__).resolve().parents[2] / "config/phase_a_semantics_v1.json"

# Detection only: unsupported relative constraints must never become all records.
_TEMPORAL_CONSTRAINT = re.compile(
    r"최근|요즘|지난|이번|다음|현재|오늘|어제|내일|전보다|이전|이후|전후|"
    r"작년|재작년|올해|내년|분기|상반기|하반기|요일|주말|평일|기간별|구간별|"
    r"\d+\s*(?:일|주|개월|달|년)|\b(?:recent|last|past|next|today|yesterday|before|after|current)\b", re.I)


def normalize_record_time(question, draft, need, observation):
    """Resolve omitted calendar time, never invent or discard a user period."""
    if observation.scope.out_of_scope or observation.evidence_need.status != "resolved":
        return draft
    if "training_records" not in observation.evidence_need.required_sources:
        return draft
    # Do not resolve time while the requested computation itself is unsettled.
    if not draft.requested_analyses:
        return draft
    time = draft.time_condition
    if time.start_date or time.end_date or observation.default_time_window.applied:
        return draft
    try:
        if explicit_period(question) or full_dates(question):
            return draft
    except DateExpressionError:
        return draft
    operations = {x.operation.value for x in draft.requested_analyses}
    # A first/last comparison selects sessions within the underlying record set;
    # it is not an absolute calendar range. Keep all other time constraints.
    remaining = question
    session_comparison = operations == {"first_last_n_session_median_e1rm"}
    if session_comparison:
        remaining = re.sub(r"(?:처음|최초|첫|초반|최근|마지막|first|last|latest)\s*"
                           r"(?:\d+|하나|한|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉|열)"
                           r"\s*(?:[- ]?session|세션)", "", remaining, flags=re.I)
    if _TEMPORAL_CONSTRAINT.search(remaining):
        return draft
    if time.source_text and not (session_comparison and time.source_text in question):
        return draft
    if need["requires_current_window"] and not session_comparison:
        return draft
    return draft.model_copy(update={"time_condition": TimeCondition(
        scope="all_records", start_date=None, end_date=None, source_text=""),
        "unresolved_fields": [x for x in draft.unresolved_fields
                              if x.field not in {"time_condition", "start_date", "end_date"}]})


def interpret_need(question, raw, *, source, observation):
    """No phrase-based channel Router. Only typed meaning chooses literature."""
    if source == "rule":
        draft = InterpretationDraft.model_validate(raw)
        need = dict(answer_type="descriptive", training_records_needed=True,
            literature_needed_for_answer=False, explicit_literature_request="none", source_spans=[question],
            purpose_unresolved=False, requires_current_window=False, out_of_scope_reason=None)
    else:
        parsed = SemanticInterpretationDraft.model_validate(raw)
        need = parsed.evidence_need.model_dump()
        draft = InterpretationDraft.model_validate(parsed.model_dump(exclude={"evidence_need"}))
    if any(not span.strip() or span not in question for span in need["source_spans"]):
        raise ValueError("evidence_source_span_not_grounded")
    out = need["answer_type"] == "out_of_scope"
    required, optional = [], []
    # The purpose contract preserves personal need even if executable arguments
    # are not yet extractable. Candidate presence alone remains insufficient.
    personal = need["training_records_needed"] or _personal_record_requirement(question, draft).derived_value
    if personal and not out:
        required.append("training_records")
    required_lit = need["literature_needed_for_answer"] or need["explicit_literature_request"] == "required"
    if required_lit and not out:
        required.append("literature")
    elif need["explicit_literature_request"] == "optional" and not out:
        optional.append("literature")
    purpose_unresolved = need["purpose_unresolved"] or (not out and not (required or optional))
    observation.evidence_need = EvidenceNeed(status="clarification_required" if purpose_unresolved else "resolved",
        answer_type=need["answer_type"], literature_needed_for_answer=need["literature_needed_for_answer"],
        explicit_literature_request=need["explicit_literature_request"], required_sources=required,
        optional_sources=optional, source_spans=need["source_spans"],
        unresolved_fields=["evidence_need"] if purpose_unresolved else [])
    observation.scope = ScopeObservation(out_of_scope=None if purpose_unresolved else out,
        reason_code=need["out_of_scope_reason"])
    if out:
        observation.argument_resolution = ArgumentResolution(status="not_applicable")
    # Preserve the LLM's supplied personal flag for mismatch provenance.
    draft = draft.model_copy(update={"literature_requested": "literature" in required + optional})
    return draft, need


def resolve_window(question, draft, need, repository, observation):
    if observation.scope.out_of_scope or observation.evidence_need.status != "resolved":
        return
    if "training_records" not in observation.evidence_need.required_sources:
        return
    try:
        expressed_period = explicit_period(question) or full_dates(question)
    except DateExpressionError:
        # Leave malformed/ambiguous original dates to the existing validator,
        # which maps them to date clarification rather than execution failure.
        return
    if expressed_period:
        return
    # Invented absolute dates must still reach the existing grounding checks.
    if draft.time_condition.start_date or draft.time_condition.end_date:
        return
    operations = {x.operation.value for x in draft.requested_analyses}
    if "first_last_n_session_median_e1rm" in operations or _SESSION_NUMBER.search(question) or re.search(
        r"전체\s*기록|모든\s*기록|최고\s*기록|all\s+(?:records|time)|처음|최초|first|maximum|최댓값", question, re.I):
        return
    # The existing time_condition is scoped to the record request. A recent
    # publication mentioned in another clause must not narrow record coverage.
    time_span = draft.time_condition.source_text.strip()
    relative = bool(time_span and time_span in question and re.search(r"최근|요즘|\brecent\b", time_span, re.I))
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    operation_default = (need["requires_current_window"] and need["answer_type"] in {"evaluative", "causal", "advisory"}
        and bool(operations.intersection(config["default_window_operations"])))
    # Do not treat an unsupported relative-date expression as omitted time.
    if not relative and time_span:
        return
    if not (relative or operation_default):
        return
    reader = getattr(repository, "record_date_bounds", None)
    bounds = reader() if callable(reader) else None
    if not bounds:
        return
    anchor = bounds[1]
    if not isinstance(anchor, date):
        anchor = date.fromisoformat(str(anchor))
    weeks = config["recent_window_weeks"]
    observation.default_time_window = DefaultTimeWindow(applied=True, policy="recent_record_anchored",
        trigger="relative_recent" if relative else "operation_default", recent_window_weeks=weeks,
        anchor_date=anchor.isoformat(), start_date=(anchor - timedelta(days=weeks * 7 - 1)).isoformat(),
        end_date=anchor.isoformat())


def finish_arguments(observation, interpretation):
    if observation.scope.out_of_scope:
        observation.argument_resolution = ArgumentResolution(status="not_applicable")
    elif observation.evidence_need.status != "resolved":
        observation.argument_resolution = ArgumentResolution(status="not_started")
    else:
        fields = list(dict.fromkeys(x.field for x in interpretation.unresolved_fields if x.field not in PURPOSE_FIELDS))
        # Unknown fields are conservatively represented by the existing argument
        # bucket; they are never promoted into a purpose clarification.
        fields = list(dict.fromkeys(x if x in ARGUMENT_FIELDS else "requested_analyses" for x in fields))
        observation.argument_resolution = ArgumentResolution(status="clarification_required" if fields else "ready",
                                                            unresolved_fields=fields)
    interpretation.phase_a_semantics = observation
    return interpretation
