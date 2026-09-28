import hashlib
import json
from pathlib import Path

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from src.recovery.agent import EvidenceRecoveryAgent
from src.recovery.contracts import (
    MAX_RETRY,
    EvidenceRecoveryInput,
    RecoveryErrorCode,
    RecoveryExecutionStatus,
    RecoveryQueryDraft,
    RecoveryTokenUsage,
)
from src.recovery.provider import (
    OpenAIEvidenceRecoveryProvider,
    RecoveryProviderResult,
    load_recovery_config,
)
from src.recovery.query_validator import normalize_query


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_HASHES = {
    "reports/baselines/hybrid_baseline_v1/manifest.json": (
        "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
    ),
    "reports/baselines/router_baseline_v1/manifest.json": (
        "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
    ),
    "reports/baselines/grader_baseline_v1/manifest.json": (
        "e32ab0476ba7ac82b4f16768e08a4233ff3695012426a0e036587c864bf093ba"
    ),
    "reports/baselines/grader_v2_baseline/manifest.json": (
        "870764d296dd469050d4699140cf5ef0508eb8d31585214082c0d8df3b963a5d"
    ),
    "reports/baselines/grader_v2_1_baseline/manifest.json": (
        "b96fc923d4ce2fe82f6d10e2dfe26e4b8cb257aa7752bd39e2743b8b49264556"
    ),
}


def _input(
    *,
    multiple: bool = False,
    retry_count: int = 0,
    previous_query: str = "velocity based training strength",
    query_history: list[str] | None = None,
) -> EvidenceRecoveryInput:
    missing = [
        {
            "component_id": "C1",
            "requirement": "VBT가 최대근력을 향상시키는 직접 근거",
        }
    ]
    if multiple:
        missing.append(
            {
                "component_id": "C2",
                "requirement": "훈련 경험자에서 VBT 효과",
            }
        )
    return EvidenceRecoveryInput.model_validate(
        {
            "original_question": (
                "지난 3개월 내 e1RM 기록과 문헌을 보면 VBT가 도움이 되는가?"
            ),
            "literature_subquestion": (
                "훈련 경험자에서 VBT가 최대근력을 향상시키는가?"
            ),
            "missing_components": missing,
            "previous_query": previous_query,
            "query_history": query_history or [],
            "retry_count": retry_count,
            "max_retry": MAX_RETRY,
        }
    )


def _draft(
    *,
    targets: list[str] | None = None,
    query: str = "VBT trained resistance athletes maximal strength",
    terms: list[str] | None = None,
) -> dict:
    return {
        "target_component_ids": targets or ["C1"],
        "recovery_query": query,
        "preserved_terms": ["VBT"] if terms is None else terms,
    }


class FakeProvider:
    model = "test-model"
    prompt_sha256 = "a" * 64
    config_sha256 = "b" * 64

    def __init__(
        self,
        *,
        payload: dict | str | None = None,
        error_code: RecoveryErrorCode | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.payload = _draft() if payload is None else payload
        self.error_code = error_code
        self.raises = raises
        self.calls = 0
        self.seen_input: EvidenceRecoveryInput | None = None

    def invoke(self, recovery_input: EvidenceRecoveryInput) -> RecoveryProviderResult:
        self.calls += 1
        self.seen_input = recovery_input
        if self.raises is not None:
            raise self.raises
        raw = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return RecoveryProviderResult(
            raw_output_text=None if self.error_code else raw,
            model=self.model,
            prompt_sha256=self.prompt_sha256,
            config_sha256=self.config_sha256,
            response_id="resp-recovery-test",
            latency_ms=11.5,
            token_usage=RecoveryTokenUsage(
                input_tokens=20,
                output_tokens=8,
                total_tokens=28,
                estimated_cost_usd=0.000051,
            ),
            response_metadata={"response_received": True, "status": "completed"},
            error_code=self.error_code,
            error_detail="simulated provider failure" if self.error_code else None,
        )


def test_recovery_draft_schema_is_minimal_and_strict() -> None:
    schema = to_strict_json_schema(RecoveryQueryDraft)
    assert set(schema["properties"]) == {
        "target_component_ids",
        "recovery_query",
        "preserved_terms",
    }
    assert schema["additionalProperties"] is False
    serialized = json.dumps(schema).lower()
    for forbidden in (
        "gold",
        "verdict",
        "confidence",
        "answer",
        "abstain",
        "tool_selection",
    ):
        assert forbidden not in serialized


def test_single_missing_component_query_is_ready() -> None:
    provider = FakeProvider()
    result = EvidenceRecoveryAgent(provider).generate(_input())

    assert provider.calls == 1
    assert result.execution_status == RecoveryExecutionStatus.QUERY_READY
    assert result.target_component_ids == ["C1"]
    assert result.recovery_query == "VBT trained resistance athletes maximal strength"
    assert result.preserved_terms == ["VBT"]
    assert result.error is None


def test_multiple_related_missing_components_can_share_one_query() -> None:
    provider = FakeProvider(payload=_draft(targets=["C1", "C2"]))
    result = EvidenceRecoveryAgent(provider).generate(_input(multiple=True))
    assert result.execution_status == RecoveryExecutionStatus.QUERY_READY
    assert result.target_component_ids == ["C1", "C2"]


def test_duplicate_target_ids_are_removed_in_order() -> None:
    provider = FakeProvider(payload=_draft(targets=["C1", "C1", "C2", "C1"]))
    result = EvidenceRecoveryAgent(provider).generate(_input(multiple=True))
    assert result.execution_status == RecoveryExecutionStatus.QUERY_READY
    assert result.target_component_ids == ["C1", "C2"]


def test_invalid_target_id_fails_closed() -> None:
    provider = FakeProvider(payload=_draft(targets=["C1", "C3"]))
    result = EvidenceRecoveryAgent(provider).generate(_input(multiple=True))
    assert result.execution_status == RecoveryExecutionStatus.FAILURE
    assert result.error.code == RecoveryErrorCode.INVALID_TARGET_COMPONENT
    assert result.target_component_ids == []
    assert result.recovery_query is None


def test_empty_query_fails_closed() -> None:
    provider = FakeProvider(payload=_draft(query="   ", terms=[]))
    result = EvidenceRecoveryAgent(provider).generate(_input())
    assert result.execution_status == RecoveryExecutionStatus.FAILURE
    assert result.error.code == RecoveryErrorCode.EMPTY_QUERY


def test_previous_query_duplicate_fails_closed() -> None:
    provider = FakeProvider(payload=_draft(query="VBT maximal strength"))
    result = EvidenceRecoveryAgent(provider).generate(
        _input(previous_query="VBT maximal strength")
    )
    assert result.error.code == RecoveryErrorCode.DUPLICATE_QUERY


def test_history_duplicate_ignores_unicode_case_and_whitespace() -> None:
    provider = FakeProvider(payload=_draft(query="ＶＢＴ   MAXIMAL\tSTRENGTH"))
    result = EvidenceRecoveryAgent(provider).generate(
        _input(query_history=["vbt maximal strength"])
    )
    assert normalize_query("ＶＢＴ   MAXIMAL\tSTRENGTH") == "vbt maximal strength"
    assert result.execution_status == RecoveryExecutionStatus.FAILURE
    assert result.error.code == RecoveryErrorCode.DUPLICATE_QUERY


def test_reported_preserved_term_must_exist_in_query() -> None:
    provider = FakeProvider(
        payload=_draft(
            query="trained resistance athletes maximal strength",
            terms=["VBT"],
        )
    )
    result = EvidenceRecoveryAgent(provider).generate(_input())
    assert result.error.code == RecoveryErrorCode.PRESERVED_TERM_MISSING


def test_retry_budget_exhausted_without_provider_call() -> None:
    provider = FakeProvider()
    result = EvidenceRecoveryAgent(provider).generate(_input(retry_count=2))
    assert provider.calls == 0
    assert result.execution_status == RecoveryExecutionStatus.BUDGET_EXHAUSTED
    assert result.error.code == RecoveryErrorCode.BUDGET_EXHAUSTED
    assert result.recovery_query is None
    assert result.provenance.response_metadata == {"provider_invoked": False}


def test_provider_failure_and_exception_are_fail_closed_without_retry() -> None:
    provider_failure = FakeProvider(error_code=RecoveryErrorCode.PROVIDER_FAILURE)
    result = EvidenceRecoveryAgent(provider_failure).generate(_input())
    assert provider_failure.calls == 1
    assert result.execution_status == RecoveryExecutionStatus.FAILURE
    assert result.error.code == RecoveryErrorCode.PROVIDER_FAILURE

    raising_provider = FakeProvider(raises=ConnectionError("offline"))
    raised_result = EvidenceRecoveryAgent(raising_provider).generate(_input())
    assert raising_provider.calls == 1
    assert raised_result.execution_status == RecoveryExecutionStatus.FAILURE
    assert raised_result.error.code == RecoveryErrorCode.PROVIDER_FAILURE


def test_schema_failure_is_fail_closed() -> None:
    provider = FakeProvider(payload={"recovery_query": "VBT strength"})
    result = EvidenceRecoveryAgent(provider).generate(_input())
    assert provider.calls == 1
    assert result.execution_status == RecoveryExecutionStatus.FAILURE
    assert result.error.code == RecoveryErrorCode.SCHEMA_FAILURE


def test_hybrid_contract_excludes_structured_evidence() -> None:
    payload = _input().model_dump()
    payload["log_evidence"] = [{"e1rm": 100.0}]
    with pytest.raises(ValidationError, match="log_evidence"):
        EvidenceRecoveryInput.model_validate(payload)

    provider = FakeProvider()
    recovery_input = _input()
    EvidenceRecoveryAgent(provider).generate(recovery_input)
    provider_payload = provider.seen_input.model_dump()
    assert provider_payload["evidence_channel"] == "literature_only"
    assert set(provider_payload) == {
        "original_question",
        "literature_subquestion",
        "missing_components",
        "previous_query",
        "query_history",
        "retry_count",
        "max_retry",
        "evidence_channel",
        "structured_channel_policy",
    }


def test_provenance_preserves_history_usage_and_provider_metadata() -> None:
    history = ["first query", "second query"]
    provider = FakeProvider()
    result = EvidenceRecoveryAgent(provider).generate(_input(query_history=history))
    provenance = result.provenance
    assert provenance.previous_query == "velocity based training strength"
    assert provenance.query_history == history
    assert provenance.retry_count == 0
    assert provenance.max_retry == 2
    assert provenance.available_missing_component_ids == ["C1"]
    assert provenance.normalized_recovery_query == (
        "vbt trained resistance athletes maximal strength"
    )
    assert provenance.response_id == "resp-recovery-test"
    assert provenance.token_usage.total_tokens == 28
    assert provenance.response_metadata["status"] == "completed"


def test_config_fixes_retry_budget_and_disables_out_of_scope_behaviors() -> None:
    config, prompt, config_hash, prompt_hash = load_recovery_config()
    assert config["max_retries"] == 0
    assert config["max_retry"] == MAX_RETRY == 2
    assert all(value is False for value in config["controls"].values())
    assert len(config_hash) == len(prompt_hash) == 64
    assert "do not answer" in prompt.lower()
    assert "gold label" in prompt.lower()


def test_openai_provider_transport_failure_calls_client_once() -> None:
    class RaisingResponses:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            provider_input = json.loads(kwargs["input"])
            assert provider_input["evidence_channel"] == "literature_only"
            raise ConnectionError("offline")

    class FakeClient:
        def __init__(self) -> None:
            self.responses = RaisingResponses()

    client = FakeClient()
    provider = OpenAIEvidenceRecoveryProvider(client=client)
    result = EvidenceRecoveryAgent(provider).generate(_input())
    assert client.responses.calls == 1
    assert result.execution_status == RecoveryExecutionStatus.FAILURE
    assert result.error.code == RecoveryErrorCode.PROVIDER_FAILURE


def test_frozen_artifact_manifests_remain_unchanged() -> None:
    for relative_path, expected_hash in FROZEN_MANIFEST_HASHES.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected_hash
