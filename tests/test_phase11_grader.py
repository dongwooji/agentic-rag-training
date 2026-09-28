from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.grading.baseline import (
    GRADER_INPUT_FIELDS,
    build_case_results,
    load_frozen_grader_inputs,
    load_or_create_grade_checkpoint,
    produce_grades,
    run_grader_baseline,
)
from src.grading.contracts import (
    EvidenceGrade,
    GraderCallResult,
    GraderInput,
    GraderUsage,
)
from src.grading.evaluation import evaluate_grader
from src.grading.provider import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    OpenAIGraderBackend,
    load_grader_config,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


def sufficient_grade() -> dict[str, Any]:
    return {
        "verdict": "sufficient",
        "reason_code": "sufficient",
        "missing_evidence_type": "none",
        "reason": "The supplied chunks collectively cover the literature question.",
        "confidence": 0.8,
    }


def insufficient_grade() -> dict[str, Any]:
    return {
        "verdict": "insufficient",
        "reason_code": "missing_required_evidence",
        "missing_evidence_type": "required_claim",
        "reason": "An essential literature claim lacks direct support.",
        "confidence": 0.9,
    }


class SequenceBackend:
    def __init__(self, grades: list[dict[str, Any]]) -> None:
        self.grades = list(grades)
        self.inputs: list[GraderInput] = []

    def invoke(self, grader_input: GraderInput) -> GraderCallResult:
        self.inputs.append(grader_input)
        return GraderCallResult(
            raw_grade=self.grades.pop(0),
            model="fake-grader",
            response_id=f"resp_{len(self.inputs)}",
            latency_ms=2.5,
            usage=GraderUsage(
                input_tokens=100,
                cached_input_tokens=20,
                output_tokens=25,
                reasoning_tokens=0,
                total_tokens=125,
                estimated_cost_usd=0.001,
            ),
        )


class ErrorAfterOneBackend:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, grader_input: GraderInput) -> GraderCallResult:
        self.calls += 1
        if self.calls == 1:
            return GraderCallResult(
                raw_grade=sufficient_grade(),
                model="fake-grader",
                response_id="resp_ok",
                latency_ms=1.0,
                usage=GraderUsage(),
            )
        return GraderCallResult(
            model="fake-grader",
            latency_ms=1.0,
            usage=GraderUsage(),
            error="RateLimitError: test",
        )


def test_preregistered_grader_prompt_and_config_hashes_are_pinned() -> None:
    config, prompt, prompt_path = load_grader_config()
    assert config["grader_version"] == "grader_baseline_v1"
    assert config["model"] == "gpt-5.4-mini-2026-03-17"
    assert sha256_file(ROOT / "config/grader_v1.json") == EXPECTED_CONFIG_SHA256
    assert sha256_file(prompt_path) == EXPECTED_PROMPT_SHA256
    assert "LIT-001" not in prompt
    assert "HYB-008" not in prompt
    assert "CompleteEvidence" not in prompt


def test_frozen_input_builder_exposes_full_top10_without_gold() -> None:
    frozen = load_frozen_grader_inputs(ROOT)
    assert len(frozen["grader_inputs"]) == 18
    assert [item["case_id"] for item in frozen["grader_inputs"]][:2] == [
        "LIT-001",
        "LIT-002",
    ]
    banned = {
        "case_id",
        "category",
        "gold",
        "answer_criteria",
        "limitations",
        "literature_evidence_groups",
        "chunk_ids",
    }

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()

    for item in frozen["grader_inputs"]:
        model_input = item["grader_input"]
        assert item["grader_input_fields"] == GRADER_INPUT_FIELDS
        assert not (keys(model_input) & banned)
        assert len(model_input["retrieved_evidence"]) == 10
        assert all(chunk["text"] for chunk in model_input["retrieved_evidence"])
        assert [
            chunk["retrieval"]["hybrid_rank"]
            for chunk in model_input["retrieved_evidence"]
        ] == list(range(1, 11))


def test_typed_grade_contract_enforces_bounded_semantics() -> None:
    assert EvidenceGrade.model_validate(sufficient_grade()).verdict.value == "sufficient"
    assert (
        EvidenceGrade.model_validate(insufficient_grade()).reason_code.value
        == "missing_required_evidence"
    )
    invalid = sufficient_grade()
    invalid["missing_evidence_type"] = "required_claim"
    with pytest.raises(ValidationError):
        EvidenceGrade.model_validate(invalid)
    invalid = insufficient_grade()
    invalid["reason_code"] = "sufficient"
    invalid["missing_evidence_type"] = "none"
    with pytest.raises(ValidationError):
        EvidenceGrade.model_validate(invalid)
    with pytest.raises(ValidationError):
        EvidenceGrade.model_validate({**sufficient_grade(), "extra": True})


def test_grade_schema_avoids_unsupported_oneof() -> None:
    serialized = json.dumps(EvidenceGrade.model_json_schema())
    assert "oneOf" not in serialized


def test_confusion_matrix_and_rates() -> None:
    values = [
        ("sufficient", "sufficient"),
        ("insufficient", "sufficient"),
        ("insufficient", "insufficient"),
        ("sufficient", "insufficient"),
    ]
    rows = [
        {
            "case_id": f"C-{index}",
            "gold_verdict": gold,
            "predicted_verdict": predicted,
            "reason_code": "sufficient" if predicted == "sufficient" else "missing_required_evidence",
        }
        for index, (gold, predicted) in enumerate(values)
    ]
    aggregate = evaluate_grader(rows)["aggregate"]
    assert aggregate["true_sufficient"] == 1
    assert aggregate["false_sufficient"] == 1
    assert aggregate["true_insufficient"] == 1
    assert aggregate["false_insufficient"] == 1
    assert aggregate["accuracy"] == 0.5
    assert aggregate["false_sufficient_rate"] == 0.5
    assert aggregate["false_insufficient_rate"] == 0.5


def test_openai_backend_uses_strict_schema_and_records_usage() -> None:
    frozen_input = GraderInput.model_validate(
        load_frozen_grader_inputs(ROOT)["grader_inputs"][0]["grader_input"]
    )

    class Responses:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] | None = None

        def create(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return SimpleNamespace(
                output_text=json.dumps(sufficient_grade()),
                output=[],
                model="gpt-5.4-mini-2026-03-17",
                id="resp_test",
                usage=SimpleNamespace(
                    input_tokens=1000,
                    input_tokens_details=SimpleNamespace(cached_tokens=500),
                    output_tokens=100,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=10),
                    total_tokens=1100,
                ),
            )

    responses = Responses()
    backend = OpenAIGraderBackend(client=SimpleNamespace(responses=responses))
    result = backend.invoke(frozen_input)
    assert result.error is None
    assert result.raw_grade == sufficient_grade()
    assert result.usage.cached_input_tokens == 500
    assert result.usage.reasoning_tokens == 10
    assert responses.kwargs is not None
    assert responses.kwargs["text"]["format"]["type"] == "json_schema"
    assert responses.kwargs["text"]["format"]["strict"] is True
    assert responses.kwargs["max_output_tokens"] == 800
    sent = json.loads(responses.kwargs["input"])
    assert set(sent) == set(GRADER_INPUT_FIELDS)
    assert "case_id" not in sent
    assert "gold" not in sent


def test_checkpoint_resume_does_not_repeat_completed_case(tmp_path: Path) -> None:
    inputs = load_frozen_grader_inputs(ROOT)["grader_inputs"][:3]
    checkpoint = tmp_path / "checkpoint"
    metadata = {
        "checkpoint_version": "test",
        "input_bundle_sha256": "test",
    }
    existing = load_or_create_grade_checkpoint(
        checkpoint_dir=checkpoint,
        grader_inputs=inputs,
        expected_metadata=metadata,
    )
    assert existing == []
    failing = ErrorAfterOneBackend()
    with pytest.raises(RuntimeError, match="aborted before freeze"):
        produce_grades(
            inputs,
            failing,
            checkpoint_path=checkpoint / "grader_outputs.jsonl",
        )
    preserved = load_or_create_grade_checkpoint(
        checkpoint_dir=checkpoint,
        grader_inputs=inputs,
        expected_metadata=metadata,
    )
    assert len(preserved) == 1
    resumed_backend = SequenceBackend(
        [insufficient_grade(), sufficient_grade()]
    )
    complete = produce_grades(
        inputs,
        resumed_backend,
        initial_outputs=preserved,
        checkpoint_path=checkpoint / "grader_outputs.jsonl",
    )
    assert len(complete) == 3
    assert len(resumed_backend.inputs) == 2
    assert resumed_backend.inputs[0].question == inputs[1]["grader_input"]["question"]


def test_full_fake_baseline_freezes_once_and_applies_gold_offline(tmp_path: Path) -> None:
    output = tmp_path / "grader_baseline_v1"
    backend = SequenceBackend([sufficient_grade() for _ in range(18)])
    manifest, metrics = run_grader_baseline(
        ROOT, output_dir=output, backend=backend
    )
    aggregate = metrics["aggregate"]
    assert aggregate["gold_sufficient_count"] == 10
    assert aggregate["gold_insufficient_count"] == 8
    assert aggregate["true_sufficient"] == 10
    assert aggregate["false_sufficient"] == 8
    assert aggregate["true_insufficient"] == 0
    assert aggregate["false_insufficient"] == 0
    assert len(backend.inputs) == 18
    assert all(
        set(item.model_dump(mode="json")) == set(GRADER_INPUT_FIELDS)
        for item in backend.inputs
    )
    assert len(manifest["artifacts"]) == 9
    for artifact in manifest["artifacts"]:
        assert sha256_file(output / artifact["path"]) == artifact["sha256"]
    reproduction = json.loads(
        (output / "reproducibility.json").read_text(encoding="utf-8")
    )
    assert reproduction["controls"]["gold_exposed_to_grader"] is False
    assert reproduction["controls"]["phase11b_started"] is False
    with pytest.raises(FileExistsError):
        run_grader_baseline(ROOT, output_dir=output, backend=backend)


def test_build_case_results_uses_frozen_complete_evidence_contract() -> None:
    frozen = load_frozen_grader_inputs(ROOT)
    outputs = [
        {
            "case_id": item["case_id"],
            "validated_grade": sufficient_grade(),
        }
        for item in frozen["grader_inputs"]
    ]
    results = build_case_results(
        cases=frozen["cases"],
        grader_inputs=frozen["grader_inputs"],
        grader_outputs=outputs,
    )
    sufficient_ids = {
        item["case_id"] for item in results if item["gold_verdict"] == "sufficient"
    }
    assert sufficient_ids == {
        "LIT-002",
        "LIT-004",
        "LIT-005",
        "LIT-006",
        "LIT-007",
        "LIT-009",
        "LIT-010",
        "HYB-005",
        "HYB-007",
        "HYB-008",
    }

