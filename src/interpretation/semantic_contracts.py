"""Versioned meaning/argument observations; no Tool execution policy."""

from typing import Literal
from pydantic import Field, model_validator
from src.agent.contracts import StrictModel

AnswerType = Literal["descriptive", "evaluative", "causal", "advisory", "general", "out_of_scope"]
EvidenceSource = Literal["training_records", "literature"]
ArgumentField = Literal["canonical_exercise_name", "candidate_exercises", "exercise_resolution_status",
    "time_condition", "start_date", "end_date", "n_sessions", "requested_analyses", "comparison",
    "record_operation", "session_id", "literature_subquestion", "user_assumptions"]
ScopeReason = Literal["unsupported_domain", "unsupported_medical_request", "unsupported_personalized_prescription", "unsupported_data_or_operation"]
ARGUMENT_FIELDS = frozenset({"canonical_exercise_name", "candidate_exercises", "exercise_resolution_status",
    "time_condition", "start_date", "end_date", "n_sessions", "requested_analyses", "comparison",
    "record_operation", "session_id", "literature_subquestion", "user_assumptions"})
PURPOSE_FIELDS = frozenset({"question", "evidence_need"})


class EvidenceNeedDraft(StrictModel):
    answer_type: AnswerType | None
    training_records_needed: bool
    literature_needed_for_answer: bool
    explicit_literature_request: Literal["none", "optional", "required"]
    source_spans: list[str] = Field(min_length=1, max_length=8)
    purpose_unresolved: bool
    requires_current_window: bool
    out_of_scope_reason: ScopeReason | None

    @model_validator(mode="after")
    def consistent_purpose(self):
        if self.answer_type is None and not self.purpose_unresolved:
            raise ValueError("purpose_status_conflict")
        if (self.answer_type == "out_of_scope") != (self.out_of_scope_reason is not None):
            raise ValueError("scope_reason_conflict")
        return self


class EvidenceNeed(StrictModel):
    status: Literal["resolved", "clarification_required", "unavailable"] = "unavailable"
    answer_type: AnswerType | None = None
    literature_needed_for_answer: bool | None = None
    explicit_literature_request: Literal["none", "optional", "required"] | None = None
    required_sources: list[EvidenceSource] = Field(default_factory=list)
    optional_sources: list[Literal["literature"]] = Field(default_factory=list)
    source_spans: list[str] = Field(default_factory=list)
    unresolved_fields: list[Literal["question", "evidence_need"]] = Field(default_factory=list)


class ArgumentResolution(StrictModel):
    status: Literal["ready", "clarification_required", "not_applicable", "not_started", "failure"] = "not_started"
    unresolved_fields: list[ArgumentField] = Field(default_factory=list)


class DefaultTimeWindow(StrictModel):
    applied: bool = False
    policy: Literal["recent_record_anchored"] | None = None
    trigger: Literal["relative_recent", "operation_default"] | None = None
    recent_window_weeks: int | None = Field(default=None, ge=1)
    anchor_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ScopeObservation(StrictModel):
    out_of_scope: bool | None = None
    reason_code: ScopeReason | None = None


class PhaseASemantics(StrictModel):
    version: Literal["phase_a_semantics_v1"] = "phase_a_semantics_v1"
    parsing_source: Literal["rule", "llm"] | None = None
    evidence_need: EvidenceNeed = Field(default_factory=EvidenceNeed)
    argument_resolution: ArgumentResolution = Field(default_factory=ArgumentResolution)
    default_time_window: DefaultTimeWindow = Field(default_factory=DefaultTimeWindow)
    scope: ScopeObservation = Field(default_factory=ScopeObservation)
