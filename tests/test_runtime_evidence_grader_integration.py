import json
from pathlib import Path

from src.grading.runtime_contracts import (
    RuntimeErrorCode,
    RuntimeEvidenceGraderInput,
    RuntimeExecutionStatus,
    RuntimeTokenUsage,
)
from src.grading.runtime_grader import RuntimeEvidenceGrader
from src.grading.runtime_provider import (
    OpenAIRuntimeEvidenceProvider,
    RuntimeProviderResult,
)


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_HASHES = {
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


def _chunk(chunk_id: str, rank: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_id": f"paper-{rank}",
        "pmcid": f"PMC{rank}",
        "title": f"Paper {rank}",
        "section": "Results",
        "text": f"Source text {rank}.",
        "retrieval": {"rank": rank, "dense_rank": rank + 1, "bm25_rank": rank},
        "corpus_version": "literature_corpus_v1",
    }


def _input() -> RuntimeEvidenceGraderInput:
    return RuntimeEvidenceGraderInput.model_validate(
        {
            "question": "내 훈련 기록과 문헌을 함께 보면 어떤가?",
            "literature_subquestion": "이 훈련 방식이 최대근력을 향상시키는가?",
            "supplied_chunks": [_chunk("chunk-1", 1), _chunk("chunk-2", 2)],
        }
    )


def _payload() -> dict:
    return {
        "components": [
            {
                "component_id": "C1",
                "requirement": "최대근력 결과에 대한 직접 근거",
                "status": "supported",
                "supporting_chunk_ids": ["chunk-1"],
            }
        ]
    }


class FakeProvider:
    model = "test-model"
    prompt_sha256 = "a" * 64
    config_sha256 = "b" * 64

    def __init__(
        self,
        *,
        payload: dict | str | None = None,
        error_code: RuntimeErrorCode | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.payload = _payload() if payload is None else payload
        self.error_code = error_code
        self.raises = raises
        self.calls = 0
        self.seen_input: RuntimeEvidenceGraderInput | None = None

    def invoke(self, grader_input: RuntimeEvidenceGraderInput) -> RuntimeProviderResult:
        self.calls += 1
        self.seen_input = grader_input
        if self.raises is not None:
            raise self.raises
        raw = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return RuntimeProviderResult(
            raw_output_text=None if self.error_code else raw,
            model=self.model,
            prompt_sha256=self.prompt_sha256,
            config_sha256=self.config_sha256,
            response_id="resp-test",
            latency_ms=12.5,
            token_usage=RuntimeTokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            response_metadata={"response_received": True},
            error_code=self.error_code,
            error_detail="simulated provider failure" if self.error_code else None,
        )


def test_valid_provider_output_is_finalized_with_provenance() -> None:
    provider = FakeProvider()
    grade = RuntimeEvidenceGrader(provider).grade(_input())

    assert provider.calls == 1
    assert grade.execution_status == RuntimeExecutionStatus.COMPLETED
    assert grade.evidence_sufficient is True
    assert grade.components[0].supporting_chunk_ids == ["chunk-1"]
    assert grade.components[0].supporting_evidence[0].text == "Source text 1."
    assert grade.provenance.response_id == "resp-test"
    assert grade.provenance.token_usage.total_tokens == 15


def test_provider_failure_is_fail_closed_without_retry_or_crash() -> None:
    provider = FakeProvider(error_code=RuntimeErrorCode.PROVIDER_FAILURE)
    grade = RuntimeEvidenceGrader(provider).grade(_input())

    assert provider.calls == 1
    assert grade.execution_status == RuntimeExecutionStatus.FAILURE
    assert grade.evidence_sufficient is False
    assert grade.error_code == RuntimeErrorCode.PROVIDER_FAILURE
    assert grade.components == []


def test_schema_failure_is_fail_closed() -> None:
    provider = FakeProvider(payload={"global_verdict": "sufficient"})
    grade = RuntimeEvidenceGrader(provider).grade(_input())

    assert provider.calls == 1
    assert grade.execution_status == RuntimeExecutionStatus.FAILURE
    assert grade.evidence_sufficient is False
    assert grade.error_code == RuntimeErrorCode.SCHEMA_FAILURE


def test_provider_exception_is_fail_closed_and_invoked_once() -> None:
    provider = FakeProvider(raises=ConnectionError("offline"))
    grade = RuntimeEvidenceGrader(provider).grade(_input())

    assert provider.calls == 1
    assert grade.execution_status == RuntimeExecutionStatus.FAILURE
    assert grade.error_code == RuntimeErrorCode.PROVIDER_FAILURE


def test_openai_provider_transport_failure_makes_one_request_and_fails_closed() -> None:
    class RaisingResponses:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            assert json.loads(kwargs["input"])["evidence_channel"] == "literature_only"
            raise ConnectionError("offline")

    class FakeClient:
        def __init__(self) -> None:
            self.responses = RaisingResponses()

    client = FakeClient()
    provider = OpenAIRuntimeEvidenceProvider(client=client)
    grade = RuntimeEvidenceGrader(provider).grade(_input())

    assert client.responses.calls == 1
    assert grade.execution_status == RuntimeExecutionStatus.FAILURE
    assert grade.evidence_sufficient is False
    assert grade.error_code == RuntimeErrorCode.PROVIDER_FAILURE


def test_hybrid_structured_evidence_never_enters_provider_input() -> None:
    provider = FakeProvider()
    grader_input = _input()
    RuntimeEvidenceGrader(provider).grade(grader_input)

    assert provider.seen_input is grader_input
    serialized = provider.seen_input.model_dump_json()
    provider_payload = json.loads(serialized)
    assert provider_payload["evidence_channel"] == "literature_only"
    assert set(provider_payload) == {
        "question",
        "literature_subquestion",
        "evidence_channel",
        "structured_channel_policy",
        "supplied_chunks",
    }
    assert "log_and_metric_evidence_excluded" in provider_payload[
        "structured_channel_policy"
    ]


def test_frozen_grader_artifacts_remain_unchanged() -> None:
    import hashlib

    for relative_path, expected_hash in FROZEN_MANIFEST_HASHES.items():
        content = (ROOT / relative_path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == expected_hash
