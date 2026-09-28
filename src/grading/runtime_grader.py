"""Fail-closed orchestration for the Runtime Evidence Grader."""

from __future__ import annotations

import json
from typing import Any, Protocol

from .runtime_contracts import (
    RuntimeErrorCode,
    RuntimeEvidenceAssessmentDraft,
    RuntimeEvidenceGrade,
    RuntimeEvidenceGraderInput,
    RuntimeTokenUsage,
)
from .runtime_finalizer import (
    build_fail_closed_grade,
    finalize_runtime_assessment,
)
from .runtime_provider import (
    OpenAIRuntimeEvidenceProvider,
    RuntimeProviderResult,
)


class RuntimeEvidenceProvider(Protocol):
    model: str
    prompt_sha256: str
    config_sha256: str

    def invoke(
        self, grader_input: RuntimeEvidenceGraderInput
    ) -> RuntimeProviderResult: ...


class RuntimeEvidenceGrader:
    """Assess current literature evidence without recovery or answer generation."""

    def __init__(self, provider: RuntimeEvidenceProvider) -> None:
        self.provider = provider

    @classmethod
    def openai(cls, **provider_kwargs: Any) -> "RuntimeEvidenceGrader":
        return cls(OpenAIRuntimeEvidenceProvider(**provider_kwargs))

    def grade(
        self, grader_input: RuntimeEvidenceGraderInput
    ) -> RuntimeEvidenceGrade:
        try:
            call = self.provider.invoke(grader_input)
        except Exception as exc:
            return build_fail_closed_grade(
                grader_input,
                error_code=RuntimeErrorCode.PROVIDER_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
                model=self.provider.model,
                prompt_sha256=self.provider.prompt_sha256,
                config_sha256=self.provider.config_sha256,
            )

        if call.error_code is not None or call.raw_output_text is None:
            return build_fail_closed_grade(
                grader_input,
                error_code=(
                    call.error_code or RuntimeErrorCode.PROVIDER_FAILURE
                ),
                error_detail=call.error_detail or "provider returned no output",
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
            )

        try:
            raw_draft = json.loads(call.raw_output_text)
            draft = RuntimeEvidenceAssessmentDraft.model_validate(raw_draft)
        except Exception as exc:
            return build_fail_closed_grade(
                grader_input,
                error_code=RuntimeErrorCode.SCHEMA_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
            )

        try:
            return finalize_runtime_assessment(
                grader_input,
                draft,
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
            )
        except Exception as exc:
            return build_fail_closed_grade(
                grader_input,
                error_code=RuntimeErrorCode.FINALIZER_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
            )


def grade_runtime_evidence(
    grader_input: RuntimeEvidenceGraderInput,
    provider: RuntimeEvidenceProvider,
) -> RuntimeEvidenceGrade:
    return RuntimeEvidenceGrader(provider).grade(grader_input)

