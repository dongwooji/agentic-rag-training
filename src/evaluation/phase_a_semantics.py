"""Deterministic offline scoring. Never imported by runtime or providers."""

from typing import Literal
from pydantic import Field, StrictBool, model_validator
from src.agent.contracts import StrictModel
from src.interpretation.contracts import AnalysisOperation
from src.interpretation.semantic_contracts import AnswerType, EvidenceSource, ARGUMENT_FIELDS, PURPOSE_FIELDS

RecordOperation = Literal["exercise_records", "exercise_first_last", "get_session", "list_sessions"]
BASELINE_SHA = "685e2b9194419fbf757df2df2156299b77e4afe3"


def unique(values, name):
    if len(values) != len(set(values)):
        raise ValueError("duplicate_" + name)


class GoldCase(StrictModel):
    case_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    expected_answer_type: AnswerType | None
    required_evidence_sources: list[EvidenceSource]
    optional_evidence_sources: list[Literal["literature"]]
    acceptable_record_operations: list[RecordOperation | None] = Field(min_length=1)
    acceptable_metric_operation_sets: list[list[AnalysisOperation]] = Field(min_length=1)
    clarification_required: StrictBool
    allowed_clarification_fields: list[str]
    expected_final_behavior: Literal["answer", "clarification", "abstain"]
    expected_default_recent_window: StrictBool

    @model_validator(mode="after")
    def consistent(self):
        unique(self.required_evidence_sources, "required_sources")
        unique(self.optional_evidence_sources, "optional_sources")
        unique(self.acceptable_record_operations, "record_alternatives")
        unique(self.allowed_clarification_fields, "clarification_fields")
        if set(self.required_evidence_sources) & set(self.optional_evidence_sources):
            raise ValueError("source_overlap")
        if None in self.acceptable_record_operations and self.acceptable_record_operations != [None]:
            raise ValueError("null_record_mixed_with_operation")
        alternatives = []
        for ops in self.acceptable_metric_operation_sets:
            unique(ops, "metric_operation")
            alternatives.append(tuple(sorted(x.value for x in ops)))
        unique(alternatives, "metric_alternatives")
        if not set(self.allowed_clarification_fields) <= ARGUMENT_FIELDS | PURPOSE_FIELDS:
            raise ValueError("unknown_clarification_field")
        if self.clarification_required != (self.expected_final_behavior == "clarification"):
            raise ValueError("clarification_behavior_conflict")
        if not self.clarification_required and self.allowed_clarification_fields:
            raise ValueError("clarification_fields_without_clarification")
        if self.clarification_required and not self.allowed_clarification_fields:
            raise ValueError("clarification_needs_allowed_fields")
        if self.expected_answer_type is None and not (self.clarification_required and
                set(self.allowed_clarification_fields) & PURPOSE_FIELDS):
            raise ValueError("null_type_requires_purpose_clarification")
        return self


class GoldFile(StrictModel):
    version: Literal["phase_a_evidence_gold_v1"]
    cases: list[GoldCase] = Field(min_length=1)

    @model_validator(mode="after")
    def ids(self):
        unique([x.case_id for x in self.cases], "case_id")
        return self


def observe_state(case_id, state, *, usage=None, latency_ms=None):
    """Called by a future frozen observer after execution; consumes no Gold."""
    interpretation = state.get("question_interpretation") or {}
    semantics = (interpretation.get("phase_a_semantics") or
                 (state.get("interpretation_trace") or {}).get("phase_a_semantics") or {})
    final = state.get("final_response") or {}
    metadata = (final.get("provenance") or {}).get("response_metadata") or {}
    records = [x for x in state.get("tool_results", []) if isinstance(x, dict)]
    # Entries are execution attempts, not selected plans. Duplicate operations
    # remain visible to the scorer, including unexpected repeated Metric calls.
    log_ops = [x.get("requested_operation") for x in records if x.get("tool") == "query_training_log"]
    metric_ops = [x.get("requested_operation") for x in records if x.get("tool") == "compute_metrics"]
    executed = []
    for record in records:
        source = {"query_training_log": "training_records", "compute_metrics": "training_records",
                  "search_literature": "literature"}.get(record.get("tool"))
        if source and source not in executed:
            executed.append(source)
    inferred = semantics.get("evidence_need") or {}
    # Historical Phase A has no answer_type/evidence_need fields. Do not infer
    # a type from Gold or question wording; type accuracy is unavailable there.
    need_sources = inferred.get("required_sources")
    if need_sources is None:
        need_sources = (["training_records"] if interpretation.get("personal_record_requested") else []) + (
            ["literature"] if interpretation.get("literature_requested") else [])
    terminal = final.get("final_status") or state.get("final_status")
    behavior = ("execution_failure" if terminal not in {"answer_ready", "abstain_ready"} else
                "clarification" if metadata.get("clarification_required") else
                "answer" if terminal == "answer_ready" else "abstain")
    return dict(case_id=case_id, answer_type=inferred.get("answer_type"), answer_type_available=bool(semantics),
        need_sources=need_sources, optional_sources=inferred.get("optional_sources", []),
        executed_sources=executed, record_operations=log_ops, metric_operations=metric_ops,
        clarification_fields=list(dict.fromkeys(x["field"] for x in interpretation.get("unresolved_fields", []))),
        behavior=behavior, terminal=terminal,
        default_recent_window=(semantics.get("default_time_window") or {}).get("applied", False),
        execution_order=[x.get("tool") for x in records], tool_statuses=[x.get("status") for x in records],
        interpreter_usage={key: value for key, value in ((state.get("interpretation_trace") or {}).get("provider") or {}).items()
                           if key in {"model", "prompt_sha256", "config_sha256", "api_requests", "latency_ms", "usage"}},
        usage=usage, latency_ms=latency_ms)


def score(gold, observations):
    gold = GoldFile.model_validate(gold)
    unique([x["case_id"] for x in observations], "observation_case")
    if [x.case_id for x in gold.cases] != [x["case_id"] for x in observations]:
        raise ValueError("one_observation_per_frozen_case_in_order_required")
    rows = []
    for case, actual in zip(gold.cases, observations):
        required, optional = set(case.required_evidence_sources), set(case.optional_evidence_sources)
        actual_metrics = actual["metric_operations"]
        metric_ok = len(actual_metrics) == len(set(actual_metrics)) and any(
            set(actual_metrics) == {x.value for x in alternative} for alternative in case.acceptable_metric_operation_sets)
        record_ops = actual["record_operations"]
        record_ok = (len(record_ops) == 0 and case.acceptable_record_operations == [None]) or (
            len(record_ops) == 1 and record_ops[0] in case.acceptable_record_operations)
        clarification_ok = (actual["behavior"] == "clarification") == case.clarification_required
        if actual["behavior"] == "clarification":
            clarification_ok = clarification_ok and bool(actual["clarification_fields"]) and set(actual["clarification_fields"]) <= set(case.allowed_clarification_fields)
        missing_execution = len(required - set(actual["executed_sources"]))
        final_correct = actual["behavior"] == case.expected_final_behavior
        if case.expected_final_behavior == "answer":
            final_correct = final_correct and not missing_execution and record_ok and metric_ok
        elif case.expected_final_behavior == "clarification":
            final_correct = final_correct and clarification_ok and record_ok and metric_ok
        rows.append(dict(case_id=case.case_id, pair_id=case.pair_id,
            required_channel_omissions=len(required - set(actual["need_sources"])),
            required_execution_omissions=missing_execution,
            unnecessary_channel_additions=len((set(actual["need_sources"]) | set(actual.get("optional_sources", []))) - (required | optional)),
            record_operation_correct=record_ok, metric_operations_correct=metric_ok,
            clarification_correct=clarification_ok,
            answer_type_correct=(actual["answer_type"] == case.expected_answer_type) if actual.get("answer_type_available") else None,
            final_behavior_correct=bool(final_correct),
            default_recent_window_correct=actual["default_recent_window"] == case.expected_default_recent_window,
            unjustified_clarification_or_abstain=(case.expected_final_behavior == "answer" and actual["behavior"] in {"clarification", "abstain"}),
            execution_failure=actual["behavior"] == "execution_failure"))
    totals = {key: sum(row[key] for row in rows if row[key] is not None) for key in rows[0] if key not in {"case_id", "pair_id"}}
    totals["cases"] = len(rows)
    totals["answer_type_scored_cases"] = sum(row["answer_type_correct"] is not None for row in rows)
    totals["final_behavior_accuracy"] = totals["final_behavior_correct"] / len(rows)
    return dict(totals=totals, rows=rows)


def compare(before, after):
    if before["totals"]["cases"] != after["totals"]["cases"]:
        raise ValueError("different_denominator")
    a, b = before["totals"], after["totals"]
    criteria = dict(required_channel_omission_decreased=b["required_channel_omissions"] < a["required_channel_omissions"],
        unjustified_clarification_abstain_decreased=b["unjustified_clarification_or_abstain"] < a["unjustified_clarification_or_abstain"],
        final_behavior_accuracy_not_decreased=b["final_behavior_accuracy"] >= a["final_behavior_accuracy"],
        execution_failure_not_increased=b["execution_failure"] <= a["execution_failure"])
    return dict(criteria=criteria, accepted=all(criteria.values()))
