"""Code-owned source references, with unchanged evidence sufficiency semantics."""
import re
from typing import Literal

from pydantic import Field, model_validator
from src.grading.runtime_contracts import RuntimeEvidenceGraderInput, RuntimeStrictModel, RuntimeComponentStatus
from src.grading.fixed_requirements_v1.contracts import (
    Requirement, RequirementSet as V1RequirementSet, text_hash, select_question as select_v1,
)

VERSION = "runtime_evidence_grader_fixed_requirements_v1_1"
QuestionSource = Literal["step2", "phase_a", "original_fallback"]


class RequirementSet(V1RequirementSet):
    version: Literal["runtime_evidence_grader_fixed_requirements_v1_1"] = VERSION
    question_source: QuestionSource


class FixedGraderInput(RuntimeEvidenceGraderInput):
    question_source: QuestionSource
    requirements: RequirementSet | None = None

    @model_validator(mode="after")
    def validate_fixed_context(self):
        fixed = self.requirements
        if fixed is not None and (fixed.original_question_sha256 != text_hash(self.question)
                or fixed.literature_question != self.literature_subquestion
                or fixed.question_source != self.question_source):
            raise ValueError("fixed requirements belong to a different question")
        return self


def source_catalog(text, prefix):
    # Lossless slices, not normalized/paraphrased text. Decimal periods stay intact.
    return {f"{prefix}{index}": match.group(0)
            for index, match in enumerate(re.finditer(r"[^!?;\n]+[!?;\n]*", text), 1)
            if match.group(0).strip()}


class ReferencedComponent(RuntimeStrictModel):
    component_id: str = Field(pattern=r"^C[1-8]$")
    topic: str = Field(min_length=1, max_length=450)
    direction: Literal["open", "supporting", "opposing"]
    source_id: str = Field(pattern=r"^L[1-9][0-9]*$")
    direction_source_id: str | None = Field(pattern=r"^Q[1-9][0-9]*$")
    status: RuntimeComponentStatus
    supporting_chunk_ids: list[str] = Field(max_length=20)


class ReferencedAssessment(RuntimeStrictModel):
    components: list[ReferencedComponent] = Field(min_length=1, max_length=8)


def resolve_component(item, data):
    sources = source_catalog(data.literature_subquestion, "L")
    directions = source_catalog(data.question, "Q")
    if item.source_id not in sources:
        raise ValueError("unknown literature source reference")
    if item.direction == "open":
        if item.direction_source_id is not None:
            raise ValueError("open requirements cannot claim directional source text")
        direction_span = None
    else:
        if item.direction_source_id not in directions:
            raise ValueError("directional requirements need an original-question reference")
        direction_span = directions[item.direction_source_id]
    return Requirement(component_id=item.component_id, topic=item.topic, direction=item.direction,
                       source_span=sources[item.source_id], direction_span=direction_span)


def select_question(state):
    try:
        return select_v1(state)
    except ValueError:
        pass
    step2 = (state.get("first_query_selection") or {}).get("step2") or {}
    original = state.get("original_question")
    # Accept only the real query-preparation contract's ORIGINAL fallback, never
    # the rejected generated text. A failed rewrite is not a failed user input.
    if (isinstance(original, str) and original.strip()
            and step2.get("validation_status") == "failed" and step2.get("fallback") is True
            and step2.get("query_source") == "original"
            and step2.get("input_query") == original and step2.get("actual_query") == original):
        return original, "original_fallback"
    raise ValueError("no grounded literature question or original fallback")
