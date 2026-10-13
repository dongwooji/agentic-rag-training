"""No Tool selection or calculated metric values belong in this contract."""

from enum import Enum
from typing import Literal

from pydantic import Field
from src.agent.contracts import StrictModel


class AnalysisOperation(str, Enum):
    ESTIMATED_1RM = "estimated_1rm"
    FIRST_LAST_MEDIAN_E1RM = "first_last_n_session_median_e1rm"
    WEEKLY_VOLUME = "weekly_volume"
    WEEKLY_FREQUENCY = "weekly_frequency"
    TRAINING_GAP = "training_gap"
    PLATEAU_CANDIDATES = "plateau_candidates"


class TimeCondition(StrictModel):
    scope: Literal["all_records", "explicit", "unresolved"]
    start_date: str | None
    end_date: str | None
    source_text: str


class AnalysisRequest(StrictModel):
    operation: AnalysisOperation
    n_sessions: int | None = Field(ge=1, le=50)
    source_text: str


class UserAssumption(StrictModel):
    text: str = Field(min_length=1)
    source_text: str = Field(min_length=1)


class UnresolvedField(StrictModel):
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class InterpretationDraft(StrictModel):
    personal_record_requested: bool
    literature_requested: bool
    exercise_mention: str | None
    canonical_exercise_name: str | None
    # Null is retained for existing rule/fixture contracts. LLM schema requires
    # this field explicitly; canonical_exercise_name is a catalog candidate.
    exercise_resolution_status: Literal["resolved", "ambiguous", "not_found"] | None = None
    candidate_exercises: list[str] = Field(default_factory=list, max_length=12)
    time_condition: TimeCondition
    record_operation: Literal["exercise_records", "exercise_first_last", "get_session", "list_sessions"] | None
    session_id: str | None
    requested_analyses: list[AnalysisRequest] = Field(max_length=6)
    literature_subquestion: str | None
    user_assumptions: list[UserAssumption] = Field(max_length=8)
    unresolved_fields: list[UnresolvedField] = Field(max_length=16)
    clarification_required: bool


class PersonalRecordNormalization(StrictModel):
    supplied_value: bool
    derived_value: bool
    mismatch: bool
    signals: list[Literal["record_operation", "requested_analyses", "explicit_personal_request", "exercise_session_context"]]


class DateRangeNormalization(StrictModel):
    supplied_scope: str
    supplied_start_date: str | None
    supplied_end_date: str | None
    derived_start_date: str
    derived_end_date: str
    source_text: str
    mismatch: bool


class DateValidationDiagnostic(StrictModel):
    status: Literal["clarification_required"] = "clarification_required"
    check_id: str
    field: str


class QuestionInterpretation(InterpretationDraft):
    schema_version: Literal["question_interpretation_phase_a_v1"] = "question_interpretation_phase_a_v1"
    parsing_source: Literal["rule", "llm"]
    original_question: str
    normalized_question: str
    applied_policies: list[str] = Field(default_factory=list)
    # Runtime output only: the LLM's InterpretationDraft schema stays unchanged.
    personal_record_normalization: PersonalRecordNormalization | None = None
    date_range_normalization: DateRangeNormalization | None = None
    date_validation: DateValidationDiagnostic | None = None
