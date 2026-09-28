from dataclasses import dataclass, field

from src.answer.contracts import AnswerTokenUsage
from src.smoke.e2e import (
    SMOKE_CASES,
    SharedProviderPacer,
    TracedProvider,
    _redact,
    render_report,
    validate_smoke_case_isolation,
    verify_frozen_integrity,
)


@dataclass
class FakeProviderResult:
    raw_output_text: str
    model: str = "fake-model"
    response_id: str = "response-id"
    latency_ms: float = 1.5
    token_usage: AnswerTokenUsage = field(
        default_factory=lambda: AnswerTokenUsage(
            input_tokens=5, output_tokens=3, total_tokens=8
        )
    )
    response_metadata: dict = None
    error_code: None = None
    error_detail: None = None

    def __post_init__(self) -> None:
        self.response_metadata = {"response_received": True}


class FakeProvider:
    model = "fake-model"
    prompt_sha256 = "a" * 64
    config_sha256 = "b" * 64

    def invoke(self, value):
        return FakeProviderResult(raw_output_text="secret raw model output")


def test_smoke_questions_are_unique_and_isolated_from_frozen_inputs() -> None:
    validate_smoke_case_isolation()
    assert len(SMOKE_CASES) == 3
    assert len({item.question for item in SMOKE_CASES}) == 3


def test_readable_frozen_dependencies_match_expected_hashes() -> None:
    assert len(verify_frozen_integrity()) == 7


def test_provider_trace_does_not_persist_raw_output() -> None:
    events = []
    traced = TracedProvider(
        "final_answer", FakeProvider(), SharedProviderPacer(0.0), events
    )
    result = traced.invoke({"input": "safe"})
    assert result.raw_output_text == "secret raw model output"
    assert "raw_output_text" not in traced.calls[0]
    assert "secret" not in str(traced.calls[0])
    assert events[0]["sequence"] == 1
    assert events[0]["component"] == "final_answer"


def test_redaction_removes_api_keys_and_password_values() -> None:
    payload = _redact(
        {"message": "OpenAI sk-abcdefghijklmnop password=my-secret"}
    )
    assert "abcdefghijklmnop" not in payload["message"]
    assert "my-secret" not in payload["message"]


def test_report_is_explicitly_smoke_only() -> None:
    report = render_report(
        [], hashes={}, min_interval_seconds=6.2, started_at="2026-01-01T00:00:00Z"
    )
    assert "not a performance evaluation" in report
    assert "not a frozen End-to-End baseline artifact" in report
    assert "API key" in report
