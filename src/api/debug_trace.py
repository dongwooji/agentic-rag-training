"""Allowlisted semantic trace, never raw records, prompts or exceptions."""
import os
import re
from src.observability import REQUEST_ID, log_failure
from src.interpretation.semantic_contracts import PhaseASemantics, ARGUMENT_FIELDS


def _map(value):
    return value if isinstance(value, dict) else {}


def _array(value):
    return value if isinstance(value, list) else []


def _text(value, limit=1500):
    if not isinstance(value, str):
        return None
    for name in ("OPENAI_API_KEY", "PGPASSWORD"):
        secret = os.environ.get(name)
        if secret and len(secret) >= 4:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    value = re.sub(r"(?i)(?:api[_ -]?key|OPENAI_API_KEY|PGPASSWORD|DB_PASSWORD|password)\s*[:=]\s*[^\s,;]+", "[REDACTED]", value)
    return value[:limit]


def _date(value):
    return value if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def _evidence(hits):
    return [dict(chunk_id=_text(x.get("chunk_id"), 160), paper_id=_text(x.get("paper_id"), 160),
            title=_text(x.get("title"), 600), section=_text(x.get("section"), 250),
            rank=x.get("final_rank", x.get("rank", index)),
            fusion_score=x.get("fusion_score"), sources=[s for s in x.get("sources", []) if s in {"initial", "recovery"}])
        for index, x in enumerate((hits or [])[:10], 1) if isinstance(x, dict)]


def _summary(raw):
    time = _map(raw.get("time_condition"))
    operations = {"estimated_1rm", "first_last_n_session_median_e1rm", "weekly_volume", "weekly_frequency", "training_gap", "plateau_candidates"}
    return dict(personal_record_requested=raw.get("personal_record_requested") if isinstance(raw.get("personal_record_requested"), bool) else None,
        literature_requested=raw.get("literature_requested") if isinstance(raw.get("literature_requested"), bool) else None,
        canonical_exercise_name=_text(raw.get("canonical_exercise_name"), 160),
        candidate_exercises=[_text(x, 160) for x in _array(raw.get("candidate_exercises"))[:12] if isinstance(x, str)],
        time_condition=dict(scope=time.get("scope") if time.get("scope") in {"explicit", "all_records", "unresolved"} else None,
            start_date=_date(time.get("start_date")), end_date=_date(time.get("end_date"))),
        analyses=[x["operation"] for x in _array(raw.get("requested_analyses"))[:6]
                  if isinstance(x, dict) and x.get("operation") in operations],
        candidate_count=len(_array(raw.get("candidate_exercises"))))


def _phase_a(value):
    """Typed allowlist projection; free text is restricted to original spans."""
    from pydantic import ValidationError
    try:
        observation = PhaseASemantics.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        observation = PhaseASemantics()
    result = observation.model_dump(mode="json")
    result["evidence_need"]["source_spans"] = [
        _text(x, 600) for x in observation.evidence_need.source_spans[:8]]
    result["argument_resolution"]["unresolved_fields"] = [
        x for x in observation.argument_resolution.unresolved_fields if x in ARGUMENT_FIELDS]
    for field in ("anchor_date", "start_date", "end_date"):
        result["default_time_window"][field] = _date(result["default_time_window"][field])
    return result


def _build_debug_trace(state):
    interpretation = _map(state.get("question_interpretation"))
    interpreted = _map(state.get("interpretation_trace"))
    raw = _map(_map(interpreted.get("provider")).get("raw_interpretation")) or interpretation
    normalization = _map(interpretation.get("date_range_normalization"))
    diagnostic = _map(interpretation.get("date_validation"))
    check_ids = {"date_grounding", "invalid_calendar_date", "reversed_date_range", "ambiguous_date_range", "missing_date_year"}
    interpreter = dict(output_summary=_summary(raw), validation=dict(
        status=state.get("interpretation_status") if state.get("interpretation_status") in {"ready", "failure", "clarification_required"} else "not_used",
        check_id=diagnostic.get("check_id") if diagnostic.get("check_id") in check_ids else None,
        normalized_summary=_summary(interpretation),
        date_mismatch=normalization.get("mismatch") is True,
        personal_record_mismatch=_map(interpretation.get("personal_record_normalization")).get("mismatch") is True,
        record_coverage_status="no_records" if state.get("record_coverage_status") == "no_records" else None))
    semantics = interpretation.get("phase_a_semantics") or interpreted.get("phase_a_semantics")
    if semantics is not None:
        interpreter["phase_a"] = _phase_a(semantics)
    selection = _map(state.get("first_query_selection"))
    step2, step3 = _map(selection.get("step2")), _map(selection.get("step3"))
    initial_search = any(isinstance(record, dict) and record.get("tool") == "search_literature" and record.get("phase") != "recovery"
                         for record in state.get("tool_results", []))
    queries = dict(korean=_text(step2.get("actual_query")), english=_text(step3.get("actual_query")),
        dense=_text(state.get("initial_query")) if initial_search else None,
        bm25=_text(state.get("initial_bm25_query") or state.get("initial_query")) if initial_search else None,
        step2_fallback=step2.get("fallback"), step3_fallback=step3.get("fallback"))
    retrievals = []
    for record in state.get("tool_results", []):
        if not isinstance(record, dict) or record.get("tool") != "search_literature":
            continue
        result = _map(record.get("result"))
        phase = "recovery" if record.get("phase") == "recovery" else "initial"
        retry = record.get("retry_count", 0)
        if phase == "recovery" and record.get("status") in {"failure", "configuration_error", "dependency_error", "execution_error"}:
            retry += 1
        # LiteratureTool stores actual queries in provenance or result; use the
        # corresponding history only when those fields are absent.
        history = state.get("query_history", [])
        provenance = _map(record.get("provenance"))
        query = result.get("query") or provenance.get("query")
        if not query:
            recovered = [x for x in state.get("recovery_history", []) if x.get("attempt") == retry]
            query = _map(recovered[0].get("result")).get("recovery_query") if recovered else None
            query = query or (history[retry] if isinstance(retry, int) and retry < len(history) else state.get("initial_query"))
        retrievals.append(dict(phase=phase, attempt=retry, query=_text(query), evidence=_evidence(result.get("hits"))))
    graders = []
    for entry in state.get("grader_history", [])[:3]:
        grade = _map(entry.get("result"))
        components = [dict(component_id=_text(x.get("component_id"), 16), requirement=_text(x.get("requirement"), 600),
            status=x.get("status") if x.get("status") in {"supported", "missing"} else "unknown",
            supporting_chunk_ids=[_text(cid, 160) for cid in x.get("supporting_chunk_ids", [])[:20]])
            for x in grade.get("components", [])[:8] if isinstance(x, dict)]
        graders.append(dict(attempt=entry.get("retry_count", 0), query=_text(entry.get("query")),
            execution_status=grade.get("execution_status") if grade.get("execution_status") in {"completed", "failure"} else "unknown",
            evidence_sufficient=grade.get("evidence_sufficient") is True, components=components,
            missing_components=[x for x in components if x["status"] == "missing"], supplied_evidence=_evidence(entry.get("evidence"))))
    recovery = []
    seen = {x["chunk_id"] for entry in retrievals if entry["phase"] == "initial" for x in entry["evidence"]}
    for entry in state.get("recovery_history", [])[:2]:
        result = _map(entry.get("result"))
        attempt = entry.get("attempt")
        matches = [x for x in retrievals if x["phase"] == "recovery" and x["attempt"] == attempt]
        retrieved = matches[0]["evidence"] if matches else []
        new_evidence = [x for x in retrieved if x["chunk_id"] not in seen]
        seen.update(x["chunk_id"] for x in retrieved)
        recovery.append(dict(attempt=attempt, status=result.get("execution_status") if result.get("execution_status") in {
            "query_ready", "budget_exhausted", "failure"} else "unknown", query=_text(result.get("recovery_query")),
            retrieved_evidence=retrieved, new_evidence=new_evidence))
    fusions = [dict(attempt=index, evidence=_evidence(_map(value).get("evidence")))
               for index, value in enumerate(state.get("fusion_history", [])[:2], 1)]
    trace = dict(version="query_debug_trace_v1", request_id=REQUEST_ID.get(), interpreter=interpreter,
        initial_queries=queries, retrievals=retrievals[:3], graders=graders, recovery=recovery, fusions=fusions,
        final_evidence=_evidence(state.get("fused_evidence") or state.get("literature_evidence")))
    return trace


def build_debug_trace(state):
    try:
        return _build_debug_trace(state)
    except (TypeError, ValueError, AttributeError, KeyError) as error:
        log_failure(stage="debug_trace", check_id="trace_projection_failure", exception=error)
        interpreter = {"validation": {"status": "unavailable"}}
        semantics = (_map(state.get("question_interpretation")).get("phase_a_semantics") or
                     _map(state.get("interpretation_trace")).get("phase_a_semantics"))
        if semantics is not None:
            interpreter["phase_a"] = _phase_a(semantics)
        return dict(version="query_debug_trace_v1", request_id=REQUEST_ID.get(),
            interpreter=interpreter, initial_queries={},
            retrievals=[], graders=[], recovery=[], fusions=[], final_evidence=[])
