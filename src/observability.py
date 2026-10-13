"""Server-only bounded diagnostics. Never log exception text or raw state."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import re
from uuid import uuid4


REQUEST_ID = ContextVar("rag_request_id", default=None)
LOGGER = logging.getLogger("agentic_rag.diagnostics")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,79}\Z")
_STAGES = {"input", "router", "tool_input_resolver", "initial_tools", "grader", "recovery_agent", "recovery_search",
    "evidence_fusion", "final_response", "question_interpretation", "record_coverage", "interpretation_binding",
    "api_query", "api_clarification", "runtime_initialization", "interpreter_provider", "interpretation_validation"}
_CHECKS = {"date_grounding", "invalid_calendar_date", "reversed_date_range", "ambiguous_date_range", "missing_date_year",
    "exercise_span", "exercise_catalog", "numeric_grounding", "numeric_operation", "analysis_grounding", "interpretation_schema",
    "provider_output_failure", "record_coverage_lookup_failure", "interpretation_binding_failure", "runtime_unavailable",
    "api_execution_failure", "response_validation_failure", "router_failure", "invalid_question", "missing_required_argument",
    "resolver_failure", "invalid_resolver_result", "tool_execution_setup_failure", "tool_execution_failure",
    "literature_result_missing", "invalid_literature_result", "empty_initial_literature", "empty_literature_evidence",
    "grader_execution_failure", "grader_failure", "missing_component_contract_failure", "recovery_agent_failure",
    "literature_tool_failure", "fusion_failure", "ungrounded_interpretation", "interpretation_validation_failure",
    "interpreter_provider_failure", "interpreter_provider_unavailable", "clarification_capacity_reached"}
_REASONS = {"date_grounding": "date_not_supported_by_question", "invalid_calendar_date": "invalid_calendar_date",
    "reversed_date_range": "period_start_after_end", "ambiguous_date_range": "multiple_periods_need_confirmation",
    "missing_date_year": "year_not_specified", "numeric_grounding": "session_count_not_supported_by_question",
    "exercise_catalog": "exercise_not_in_catalog", "exercise_span": "exercise_mention_not_in_question"}
_GROUNDING_CHECKS = {
    "exercise mention is absent from the question": ("exercise_span", "exercise_mention"),
    "exercise candidate is not in the stored catalog": ("exercise_catalog", "candidate_exercises"),
    "canonical exercise candidate is not in the stored catalog": ("exercise_catalog", "canonical_exercise_name"),
    "N is absent from the question's session conditions": ("numeric_grounding", "n_sessions"),
    "N is not applicable to this operation": ("numeric_operation", "n_sessions"),
    "analysis source is absent from the question": ("analysis_grounding", "requested_analyses"),
}


@contextmanager
def request_context():
    token = REQUEST_ID.set(uuid4().hex)
    try:
        yield REQUEST_ID.get()
    finally:
        REQUEST_ID.reset(token)


def _identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else "unknown"


def log_failure(*, stage, check_id, field="request", exception=None, exception_type=None):
    """Safe reasons are code-owned, never str(exc), headers or provider payloads."""
    stage = stage if isinstance(stage, str) and stage in _STAGES else "unknown"
    check_id = check_id if isinstance(check_id, str) and check_id in _CHECKS else "unknown"
    event = dict(event="validation_or_execution_failure", request_id=REQUEST_ID.get() or uuid4().hex,
        stage=stage, check_id=check_id, field=_identifier(field),
        exception_type=_identifier(type(exception).__name__ if exception is not None else exception_type or "ComponentFailure"),
        reason=_REASONS.get(check_id, "stage_check_failed"))
    try:
        LOGGER.warning(json.dumps(event, ensure_ascii=False))
    except Exception:
        # A log handler failure must not change execution or trigger retries.
        pass


def log_validation_error(error):
    from pydantic import ValidationError
    if isinstance(error, ValidationError):
        for item in error.errors(include_input=False, include_context=False, include_url=False):
            log_failure(stage="interpretation_validation", check_id="interpretation_schema",
                        field=".".join(str(x) for x in item["loc"]), exception=error)
        return
    check, field = _GROUNDING_CHECKS.get(str(error), ("interpretation_schema", "interpretation"))
    log_failure(stage="interpretation_validation", check_id=getattr(error, "check_id", check),
                field=getattr(error, "field", field), exception=error)


def log_terminal_failure(state):
    if state.get("final_status") != "execution_failure":
        return
    errors = state.get("errors") or [{"stage": "final_response", "code": "response_validation_failure"}]
    for error in errors:
        if not isinstance(error, dict):
            continue
        # Node adapters retain an internal exception prefix; only known types
        # are recovered, never the rest of that potentially sensitive message.
        prefix = str(error.get("message", "")).split(":", 1)[0]
        known = {"ValueError", "TypeError", "RuntimeError", "ValidationError", "KeyError", "GroundingError"}
        log_failure(stage=error.get("stage"), check_id=error.get("code"),
                    exception_type=prefix if prefix in known else "ComponentFailure")
