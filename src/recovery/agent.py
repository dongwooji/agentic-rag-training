"""Fail-closed orchestration for literature recovery-query generation."""

from __future__ import annotations

import json
from typing import Any, Protocol

from .contracts import (
    EvidenceRecoveryInput,
    EvidenceRecoveryResult,
    RecoveryError,
    RecoveryErrorCode,
    RecoveryExecutionStatus,
    RecoveryProvenance,
    RecoveryQueryDraft,
    RecoveryTokenUsage,
)
from .provider import OpenAIEvidenceRecoveryProvider, RecoveryProviderResult
from .query_validator import (
    RecoveryQueryValidationError,
    validate_recovery_query,
)


class EvidenceRecoveryProvider(Protocol):
    model: str
    prompt_sha256: str
    config_sha256: str

    def invoke(
        self,
        recovery_input: EvidenceRecoveryInput,
    ) -> RecoveryProviderResult: ...


def _build_provenance(
    recovery_input: EvidenceRecoveryInput,
    *,
    model: str,
    prompt_sha256: str,
    config_sha256: str,
    normalized_recovery_query: str | None = None,
    response_id: str | None = None,
    latency_ms: float = 0.0,
    token_usage: RecoveryTokenUsage | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> RecoveryProvenance:
    return RecoveryProvenance(
        model=model,
        prompt_sha256=prompt_sha256,
        config_sha256=config_sha256,
        available_missing_component_ids=[
            item.component_id for item in recovery_input.missing_components
        ],
        previous_query=recovery_input.previous_query,
        query_history=list(recovery_input.query_history),
        retry_count=recovery_input.retry_count,
        max_retry=recovery_input.max_retry,
        normalized_recovery_query=normalized_recovery_query,
        response_id=response_id,
        latency_ms=latency_ms,
        token_usage=token_usage or RecoveryTokenUsage(),
        response_metadata=response_metadata or {},
    )


def _failure_result(
    recovery_input: EvidenceRecoveryInput,
    *,
    error_code: RecoveryErrorCode,
    error_detail: str,
    model: str,
    prompt_sha256: str,
    config_sha256: str,
    response_id: str | None = None,
    latency_ms: float = 0.0,
    token_usage: RecoveryTokenUsage | None = None,
    response_metadata: dict[str, Any] | None = None,
    status: RecoveryExecutionStatus = RecoveryExecutionStatus.FAILURE,
) -> EvidenceRecoveryResult:
    return EvidenceRecoveryResult(
        target_component_ids=[],
        recovery_query=None,
        preserved_terms=[],
        execution_status=status,
        error=RecoveryError(code=error_code, detail=error_detail),
        provenance=_build_provenance(
            recovery_input,
            model=model,
            prompt_sha256=prompt_sha256,
            config_sha256=config_sha256,
            response_id=response_id,
            latency_ms=latency_ms,
            token_usage=token_usage,
            response_metadata=response_metadata,
        ),
    )


class EvidenceRecoveryAgent:
    """Generate at most one validated literature query per invocation."""

    def __init__(self, provider: EvidenceRecoveryProvider) -> None:
        self.provider = provider

    @classmethod
    def openai(cls, **provider_kwargs: Any) -> "EvidenceRecoveryAgent":
        return cls(OpenAIEvidenceRecoveryProvider(**provider_kwargs))

    def generate(
        self,
        recovery_input: EvidenceRecoveryInput,
    ) -> EvidenceRecoveryResult:
        if recovery_input.retry_count >= recovery_input.max_retry:
            return _failure_result(
                recovery_input,
                error_code=RecoveryErrorCode.BUDGET_EXHAUSTED,
                error_detail=(
                    "recovery query budget exhausted: "
                    f"retry_count={recovery_input.retry_count}, "
                    f"max_retry={recovery_input.max_retry}"
                ),
                model=self.provider.model,
                prompt_sha256=self.provider.prompt_sha256,
                config_sha256=self.provider.config_sha256,
                status=RecoveryExecutionStatus.BUDGET_EXHAUSTED,
                response_metadata={"provider_invoked": False},
            )

        try:
            call = self.provider.invoke(recovery_input)
        except Exception as exc:
            return _failure_result(
                recovery_input,
                error_code=RecoveryErrorCode.PROVIDER_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
                model=self.provider.model,
                prompt_sha256=self.provider.prompt_sha256,
                config_sha256=self.provider.config_sha256,
                response_metadata={"provider_invoked": True},
            )

        if call.error_code is not None or call.raw_output_text is None:
            return _failure_result(
                recovery_input,
                error_code=call.error_code or RecoveryErrorCode.PROVIDER_FAILURE,
                error_detail=call.error_detail or "provider returned no output",
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
                response_metadata=call.response_metadata,
            )

        try:
            raw_draft = json.loads(call.raw_output_text)
            draft = RecoveryQueryDraft.model_validate(raw_draft)
        except Exception as exc:
            return _failure_result(
                recovery_input,
                error_code=RecoveryErrorCode.SCHEMA_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
                response_metadata=call.response_metadata,
            )

        try:
            validated = validate_recovery_query(recovery_input, draft)
        except RecoveryQueryValidationError as exc:
            return _failure_result(
                recovery_input,
                error_code=exc.code,
                error_detail=exc.detail,
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
                response_metadata=call.response_metadata,
            )
        except Exception as exc:
            return _failure_result(
                recovery_input,
                error_code=RecoveryErrorCode.VALIDATION_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
                response_metadata=call.response_metadata,
            )

        return EvidenceRecoveryResult(
            target_component_ids=validated.target_component_ids,
            recovery_query=validated.recovery_query,
            preserved_terms=validated.preserved_terms,
            execution_status=RecoveryExecutionStatus.QUERY_READY,
            error=None,
            provenance=_build_provenance(
                recovery_input,
                model=call.model,
                prompt_sha256=call.prompt_sha256,
                config_sha256=call.config_sha256,
                normalized_recovery_query=validated.normalized_recovery_query,
                response_id=call.response_id,
                latency_ms=call.latency_ms,
                token_usage=call.token_usage,
                response_metadata=call.response_metadata,
            ),
        )


def generate_recovery_query(
    recovery_input: EvidenceRecoveryInput,
    provider: EvidenceRecoveryProvider,
) -> EvidenceRecoveryResult:
    return EvidenceRecoveryAgent(provider).generate(recovery_input)
