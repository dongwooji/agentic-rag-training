from src.answer.contracts import FinalAnswerErrorCode
from src.answer.generator import FinalResponseLayer

from answer_test_support import (
    FakeFinalAnswerProvider,
    answer_state,
    draft_payload,
)


def test_literature_only_answer_uses_only_declared_chunk() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(
            literature="제공된 연구에서는 VBT 관련 최대근력 결과가 보고되었습니다.",
            chunk_ids=["chunk-1"],
        )
    )
    result = FinalResponseLayer(provider).respond(answer_state(literature=True))

    assert result.final_status == "answer_ready"
    assert result.answer_text.startswith("문헌에서는")
    assert "당신의 기록에서는" not in result.answer_text
    assert result.used_literature_chunk_ids == ["chunk-1"]
    assert "chunk-2" in result.provenance.available_literature_chunk_ids
    assert "chunk-2" not in result.used_literature_chunk_ids
    assert result.provenance.used_literature_sources[0]["paper_id"] == "paper-chunk-1"
    assert result.provenance.used_literature_sources[0]["corpus_version"] == "literature_corpus_v1"
    assert provider.calls[0].channel_mode == "literature_only"


def test_log_metric_only_answer_uses_structured_results_without_literature() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(
            record="조회된 세트와 Epley e1RM 계산 결과가 있습니다.",
            tool_ids=["tool-result-01-query_training_log", "tool-result-02-compute_metrics"],
        )
    )
    result = FinalResponseLayer(provider).respond(answer_state(structured=True))

    assert result.final_status == "answer_ready"
    assert result.answer_text.startswith("당신의 기록에서는")
    assert "문헌에서는" not in result.answer_text
    assert [item.tool for item in result.used_tool_results] == [
        "query_training_log",
        "compute_metrics",
    ]
    assert result.used_literature_chunk_ids == []


def test_hybrid_answer_keeps_record_literature_and_synthesis_separate() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(
            record="기록에서 계산된 e1RM 결과가 있습니다.",
            literature="제공된 문헌에는 VBT 최대근력 결과가 있습니다.",
            integrated="두 채널은 각각 개인 관찰과 일반 연구 근거로 해석해야 합니다.",
            tool_ids=["tool-result-02-compute_metrics"],
            chunk_ids=["chunk-1"],
        )
    )
    result = FinalResponseLayer(provider).respond(
        answer_state(structured=True, literature=True)
    )

    assert result.final_status == "answer_ready"
    assert "당신의 기록에서는" in result.answer_text
    assert "문헌에서는" in result.answer_text
    assert "두 정보를 함께 보면" in result.answer_text
    assert any("인과관계" in item for item in result.limitations)
    assert any("개인에게" in item for item in result.limitations)
    assert result.provenance.channel_mode == "hybrid"


def test_unavailable_evidence_id_fails_closed_instead_of_becoming_used() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(
            literature="근거가 있다고 주장합니다.",
            chunk_ids=["not-supplied"],
        )
    )
    result = FinalResponseLayer(provider).respond(answer_state(literature=True))

    assert result.final_status == "execution_failure"
    assert result.error is not None
    assert result.error.code == FinalAnswerErrorCode.GROUNDING_VALIDATION_FAILURE
    assert result.used_literature_chunk_ids == []
    assert "not-supplied" not in result.answer_text


def test_provider_failure_is_sanitized_and_fail_closed() -> None:
    result = FinalResponseLayer(
        FakeFinalAnswerProvider(error="secret stack trace and provider key")
    ).respond(answer_state(literature=True))

    assert result.final_status == "execution_failure"
    assert "secret" not in result.answer_text
    assert result.error is not None
    assert result.error.code == FinalAnswerErrorCode.PROVIDER_FAILURE
    assert "secret" in (result.provenance.internal_error_detail or "")


def test_top_ten_is_the_maximum_evidence_exposed_to_provider() -> None:
    provider = FakeFinalAnswerProvider(
        draft_payload(literature="상위 근거를 사용했습니다.", chunk_ids=["chunk-1"])
    )
    FinalResponseLayer(provider).respond(
        answer_state(literature=True, literature_count=12)
    )
    assert len(provider.calls[0].literature_evidence) == 10
    assert provider.calls[0].literature_evidence[-1].chunk_id == "chunk-10"
