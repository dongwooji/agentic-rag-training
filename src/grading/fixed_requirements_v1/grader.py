"""Freeze a first assessment; reassess every fixed item on current chunks."""
from __future__ import annotations

import json
from src.grading.runtime_contracts import RuntimeEvidenceAssessmentDraft, RuntimeErrorCode
from src.grading.runtime_finalizer import build_fail_closed_grade, finalize_runtime_assessment
from .contracts import (
    FixedEvidenceGrade, FixedGraderInput, InitialAssessment, Reassessment,
    Requirement, RequirementSet, requirement_hash, text_hash,
)


class FixedRequirementsGrader:
    def __init__(self, provider):
        self.provider = provider

    def grade_fixed(self, grader_input: FixedGraderInput) -> FixedEvidenceGrade:
        fixed = grader_input.requirements
        call = None
        try:
            grader_input = FixedGraderInput.model_validate(grader_input.model_dump())
        except Exception as exc:
            return self._failure(grader_input, RuntimeErrorCode.INPUT_FAILURE, str(exc), None)
        try:
            # Revalidation detects in-memory mutations as well as serialized corruption.
            call = self.provider.invoke(grader_input)
            if call.error_code is not None or call.raw_output_text is None:
                return self._failure(grader_input, call.error_code or RuntimeErrorCode.PROVIDER_FAILURE,
                                     call.error_detail or "no assessment returned", call)
            raw = json.loads(call.raw_output_text)
            if fixed is None:
                draft = InitialAssessment.model_validate(raw)
                requirements = []
                for item in draft.components:
                    if item.source_span not in grader_input.literature_subquestion:
                        raise ValueError("requirement source span is absent from the literature question")
                    if item.direction == "open":
                        if item.direction_span is not None:
                            raise ValueError("open requirements cannot claim directional source text")
                    elif not item.direction_span or item.direction_span not in grader_input.question:
                        raise ValueError("directional requirements need an explicit original-question span")
                    requirements.append(Requirement.model_validate(item.model_dump(exclude={"status", "supporting_chunk_ids"})))
                body = dict(version="runtime_evidence_grader_fixed_requirements_v1",
                            question_source=grader_input.question_source,
                            literature_question=grader_input.literature_subquestion,
                            original_question_sha256=text_hash(grader_input.question),
                            components=[x.model_dump() for x in requirements])
                fixed = RequirementSet(**body, sha256=requirement_hash(body))
            else:
                draft = Reassessment.model_validate(raw)
            if [x.component_id for x in draft.components] != [x.component_id for x in fixed.components]:
                raise ValueError("assessment must cover every fixed component exactly once in order")
            normalized = RuntimeEvidenceAssessmentDraft.model_validate({"components": [
                dict(component_id=r.component_id, requirement=r.render(), status=a.status,
                     supporting_chunk_ids=a.supporting_chunk_ids)
                for r, a in zip(fixed.components, draft.components, strict=True)
            ]})
            result = finalize_runtime_assessment(grader_input, normalized, **self._provenance(call))
            return FixedEvidenceGrade(**result.model_dump(), requirements=fixed)
        except Exception as exc:
            code = RuntimeErrorCode.SCHEMA_FAILURE if call is not None else RuntimeErrorCode.PROVIDER_FAILURE
            return self._failure(grader_input, code, f"{type(exc).__name__}: {exc}", call)

    @staticmethod
    def _provenance(call):
        return {key:getattr(call, key) for key in ["model", "prompt_sha256", "config_sha256", "response_id", "latency_ms", "token_usage"]}

    def _failure(self, data, code, detail, call):
        provenance = self._provenance(call) if call is not None else dict(
            model=self.provider.model, prompt_sha256=self.provider.prompt_sha256,
            config_sha256=self.provider.config_sha256)
        result = build_fail_closed_grade(data, error_code=code, error_detail=detail, **provenance)
        fixed = None if code == RuntimeErrorCode.INPUT_FAILURE else data.requirements
        return FixedEvidenceGrade(**result.model_dump(), requirements=fixed)
