"""No evaluation labels or historical case data belong in this contract."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from src.grading.runtime_contracts import (
    RuntimeStrictModel, RuntimeComponentStatus, RuntimeEvidenceGrade,
    RuntimeEvidenceGraderInput,
)

VERSION = "runtime_evidence_grader_fixed_requirements_v1"
QuestionSource = Literal["step2", "phase_a"]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Requirement(RuntimeStrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    topic: str = Field(min_length=1, max_length=450)
    direction: Literal["open", "supporting", "opposing"]
    source_span: str = Field(min_length=1, max_length=1500)
    direction_span: str | None = Field(max_length=2000)

    def render(self) -> str:
        if self.direction == "open":
            return f"{self.topic}에 대해 문헌은 무엇을 말하는가?"
        side = "지지" if self.direction == "supporting" else "반대"
        return f"사용자가 명시적으로 요청한 {self.topic}의 {side} 근거는 무엇인가?"


class RequirementSet(RuntimeStrictModel):
    version: Literal["runtime_evidence_grader_fixed_requirements_v1"] = VERSION
    question_source: QuestionSource
    literature_question: str = Field(min_length=1, max_length=1500)
    original_question_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    components: list[Requirement] = Field(min_length=1, max_length=8)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self):
        ids = [x.component_id for x in self.components]
        if ids != [f"C{i}" for i in range(1, len(ids) + 1)]:
            raise ValueError("requirements must use contiguous C1..Cn order")
        if self.sha256 != requirement_hash(self.model_dump(exclude={"sha256"})):
            raise ValueError("fixed requirement hash mismatch")
        return self


def requirement_hash(value: dict) -> str:
    return text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class FixedGraderInput(RuntimeEvidenceGraderInput):
    question_source: QuestionSource
    requirements: RequirementSet | None = None

    @model_validator(mode="after")
    def validate_fixed_context(self):
        if self.requirements is not None:
            fixed = self.requirements
            if (fixed.original_question_sha256 != text_hash(self.question)
                    or fixed.literature_question != self.literature_subquestion
                    or fixed.question_source != self.question_source):
                raise ValueError("fixed requirements belong to a different question")
        return self


class InitialComponent(Requirement):
    status: RuntimeComponentStatus
    supporting_chunk_ids: list[str] = Field(max_length=20)


class InitialAssessment(RuntimeStrictModel):
    components: list[InitialComponent] = Field(min_length=1, max_length=8)


class ReassessmentComponent(RuntimeStrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    status: RuntimeComponentStatus
    supporting_chunk_ids: list[str] = Field(max_length=20)


class Reassessment(RuntimeStrictModel):
    components: list[ReassessmentComponent] = Field(min_length=1, max_length=8)


class FixedEvidenceGrade(RuntimeEvidenceGrade):
    requirements: RequirementSet | None = None


def select_question(state: dict) -> tuple[str, QuestionSource]:
    step2 = (state.get("first_query_selection") or {}).get("step2") or {}
    if step2.get("validation_status") == "passed" and str(step2.get("actual_query") or "").strip():
        return step2["actual_query"].strip(), "step2"
    context = state.get("requirement_context") or {}
    question = str(context.get("literature_subquestion") or "").strip()
    if context.get("validated") is True and question:
        return question, "phase_a"
    raise ValueError("no validated literature question for fixed requirements")
