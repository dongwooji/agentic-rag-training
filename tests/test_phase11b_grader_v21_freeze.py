from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.grading.v21_baseline as v21_baseline_module

from src.grading.contracts import GraderUsage
from src.grading.provider_v21 import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    GraderV21ProviderResult,
    load_grader_v21_config,
)
from src.grading.v21_baseline import (
    CHECKPOINT_VERSION,
    MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE,
    load_frozen_v21_model_inputs,
    produce_v21_grades,
    run_grader_v21_baseline,
    validate_frozen_v21_baseline,
)
from src.grading.v21_contracts import (
    EvidenceAssessmentDraftV21,
    finalize_v21_assessment,
)
from src.grading.v21_freeze import (
    CONFIG_SHA256,
    CONTRACT_FINALIZER_SHA256,
    PROMPT_SHA256,
    freeze_grader_v21_heldout,
    validate_frozen_v21_heldout,
)
from src.grading.v21_review import sha256_file


ROOT = Path(__file__).resolve().parents[1]


class _FakeBackend:
    def __init__(self) -> None:
        self.calls = 0
        self.questions: list[str] = []

    def invoke(self, grader_input):
        self.calls += 1
        self.questions.append(grader_input.question)
        first_evidence = grader_input.retrieved_evidence[0]
        first_span = grader_input.question.split()[0]
        quote = first_evidence.text[: min(80, len(first_evidence.text))]
        draft = EvidenceAssessmentDraftV21.model_validate(
            {
                "literature_scope_only": True,
                "structured_evidence_handling": (
                    "excluded_assumed_evaluated_separately"
                ),
                "literature_subquestion": grader_input.question,
                "required_components": [
                    {
                        "component_id": "C1",
                        "required": True,
                        "kind": "main_claim",
                        "question_span": first_span,
                        "requirement": "Synthetic test-only component.",
                        "status": "supported",
                        "evidence": [
                            {
                                "chunk_id": first_evidence.chunk_id,
                                "quote": quote,
                            }
                        ],
                        "assessment_note": "Synthetic test-only result.",
                    }
                ],
                "confidence": 0.5,
            }
        )
        return GraderV21ProviderResult(
            raw_output_text=draft.model_dump_json(),
            raw_response_output=[
                {"type": "message", "status": "completed"}
            ],
            response_metadata={
                "id": f"fake-{self.calls}",
                "model": "fake-grader-v21",
                "status": "completed",
            },
            model="fake-grader-v21",
            response_id=f"fake-{self.calls}",
            latency_ms=1.0,
            usage=GraderUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                estimated_cost_usd=0.00001,
            ),
        )


class _FirstQuoteInvalidBackend(_FakeBackend):
    def invoke(self, grader_input):
        result = super().invoke(grader_input)
        if self.calls != 1:
            return result
        payload = json.loads(result.raw_output_text or "{}")
        payload["required_components"][0]["evidence"][0]["quote"] = (
            "This quotation does not occur in the supplied chunk."
        )
        return GraderV21ProviderResult(
            raw_output_text=json.dumps(payload, ensure_ascii=False),
            raw_response_output=result.raw_response_output,
            response_metadata=result.response_metadata,
            model=result.model,
            response_id=result.response_id,
            latency_ms=result.latency_ms,
            usage=result.usage,
        )


def _empty_checkpoint(directory: Path) -> None:
    directory.mkdir(parents=True)
    for name in (
        "request_attempts.jsonl",
        "raw_provider_outputs.jsonl",
        "grader_outputs.jsonl",
        "validation_failures.jsonl",
        "operational_failures.jsonl",
    ):
        (directory / name).touch()


def test_v21_preregistered_config_prompt_and_contract_hashes() -> None:
    config, prompt, prompt_path = load_grader_v21_config(
        ROOT / "config/grader_v2_1.json"
    )
    assert config["model"] == "gpt-5.4-mini-2026-03-17"
    assert config["max_retries"] == 0
    assert config["controls"]["one_provider_request_per_case"] is True
    assert prompt
    assert EXPECTED_CONFIG_SHA256 == CONFIG_SHA256
    assert EXPECTED_PROMPT_SHA256 == PROMPT_SHA256
    assert sha256_file(prompt_path) == PROMPT_SHA256
    assert sha256_file(ROOT / "src/grading/v21_contracts.py") == CONTRACT_FINALIZER_SHA256


def test_freeze_contains_12_approved_gold_isolation_and_snapshots(tmp_path: Path) -> None:
    frozen_dir = tmp_path / "grader_v2_1_heldout_v1"
    manifest = freeze_grader_v21_heldout(ROOT, output_dir=frozen_dir)
    validated = validate_frozen_v21_heldout(ROOT, frozen_dir=frozen_dir)
    assert manifest["human_review"]["approved"] == 12
    assert manifest["human_review"]["pending"] == 0
    assert validated["manifest_sha256"] == sha256_file(frozen_dir / "manifest.json")
    assert {
        "prompt.md",
        "config.json",
        "v21_contracts.py",
        "provider_v21.py",
        "v21_freeze.py",
        "v21_baseline.py",
        "run_grader_v21_baseline.py",
    } <= {item["path"] for item in manifest["artifacts"]}
    records = [
        json.loads(line)
        for line in (frozen_dir / "model_inputs.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert len(records) == 12
    serialized_inputs = json.dumps(
        [item["grader_input"] for item in records], ensure_ascii=False
    )
    for forbidden in (
        "proposed_verdict",
        "required_components",
        "supporting_chunk_ids",
        "human_review_status",
    ):
        assert forbidden not in serialized_inputs
    with pytest.raises(FileExistsError):
        freeze_grader_v21_heldout(ROOT, output_dir=frozen_dir)


def test_fake_first_baseline_calls_each_case_once_and_freezes(tmp_path: Path) -> None:
    frozen_dir = tmp_path / "heldout"
    output_dir = tmp_path / "grader_v2_1_baseline"
    freeze_grader_v21_heldout(ROOT, output_dir=frozen_dir)
    backend = _FakeBackend()
    manifest, metrics = run_grader_v21_baseline(
        ROOT,
        output_dir=output_dir,
        backend=backend,
        frozen_dir=frozen_dir,
    )
    validated = validate_frozen_v21_baseline(output_dir)
    performance = json.loads(
        (output_dir / "performance.json").read_text(encoding="utf-8")
    )
    assert backend.calls == 12
    assert performance["request_count"] == 12
    assert performance["successful_request_count"] == 12
    assert performance["validation_failure_count"] == 0
    assert performance["provider_retry_count"] == 0
    assert metrics["aggregate"]["case_count"] == 12
    assert manifest["status"] == "frozen"
    assert validated["manifest_sha256"] == sha256_file(output_dir / "manifest.json")
    with pytest.raises(FileExistsError):
        run_grader_v21_baseline(
            ROOT,
            output_dir=output_dir,
            backend=backend,
            frozen_dir=frozen_dir,
        )
    assert backend.calls == 12


def test_raw_output_is_checkpointed_before_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_dir = tmp_path / "heldout"
    freeze_grader_v21_heldout(ROOT, output_dir=frozen_dir)
    records = load_frozen_v21_model_inputs(
        ROOT, frozen_dir=frozen_dir
    )["grader_inputs"][:1]
    checkpoint_dir = tmp_path / "checkpoint"
    _empty_checkpoint(checkpoint_dir)
    observed: list[dict] = []

    def failing_finalizer(grader_input, draft):
        raw_lines = (
            checkpoint_dir / "raw_provider_outputs.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        observed.append(json.loads(raw_lines[-1]))
        raise ValueError("synthetic grounding failure")

    monkeypatch.setattr(
        v21_baseline_module,
        "finalize_v21_assessment",
        failing_finalizer,
    )
    backend = _FakeBackend()
    outputs, failures = produce_v21_grades(
        records,
        backend,
        checkpoint_dir=checkpoint_dir,
    )

    assert outputs == []
    assert len(failures) == 1
    assert observed[0]["checkpointed_before_finalizer"] is True
    assert observed[0]["raw_output_text"]
    assert observed[0]["raw_typed_output"]["required_components"]
    assert observed[0]["response_metadata"]["status"] == "completed"
    assert observed[0]["grader_metrics"]["usage"]["total_tokens"] == 15


def test_grounding_failure_continues_to_next_case(tmp_path: Path) -> None:
    frozen_dir = tmp_path / "heldout"
    freeze_grader_v21_heldout(ROOT, output_dir=frozen_dir)
    records = load_frozen_v21_model_inputs(
        ROOT, frozen_dir=frozen_dir
    )["grader_inputs"][:2]
    checkpoint_dir = tmp_path / "checkpoint"
    _empty_checkpoint(checkpoint_dir)
    backend = _FirstQuoteInvalidBackend()

    outputs, failures = produce_v21_grades(
        records,
        backend,
        checkpoint_dir=checkpoint_dir,
    )

    assert backend.calls == 2
    assert [item["case_id"] for item in failures] == ["V21H-LIT-001"]
    assert failures[0]["failure_type"] == (
        MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE
    )
    assert failures[0]["validation_stage"] == "grounding_finalizer"
    assert [item["case_id"] for item in outputs] == ["V21H-LIT-002"]
    assert len(
        (checkpoint_dir / "raw_provider_outputs.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 2


def test_legacy_failed_first_case_is_never_requested_again(
    tmp_path: Path,
) -> None:
    frozen_dir = tmp_path / "heldout"
    output_dir = tmp_path / "grader_v2_1_baseline"
    freeze_grader_v21_heldout(ROOT, output_dir=frozen_dir)
    loaded = load_frozen_v21_model_inputs(ROOT, frozen_dir=frozen_dir)
    records = loaded["grader_inputs"]
    frozen = loaded["frozen"]
    config, _, _ = load_grader_v21_config(
        ROOT / "config/grader_v2_1.json"
    )
    checkpoint_dir = output_dir.parent / f".{output_dir.name}_checkpoint"
    checkpoint_dir.mkdir()
    metadata = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "grader_version": "grader_v2_1_baseline",
        "model": config["model"],
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "heldout_manifest_sha256": frozen["manifest_sha256"],
        "input_bundle_sha256": frozen["manifest"][
            "model_input_bundle_sha256"
        ],
        "case_count": 12,
        "status": "in_progress",
        "created_at_utc": "2026-09-03T09:30:28+00:00",
        "controls": {
            "gold_loaded_before_all_predictions": False,
            "one_provider_request_per_case": True,
            "automatic_provider_retry": False,
            "post_result_tuning": False,
        },
    }
    (checkpoint_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )
    first_attempt = {
        "case_id": "V21H-LIT-001",
        "input_sha256": records[0]["input_sha256"],
        "request_ordinal": 1,
        "started_at_utc": "2026-09-03T09:30:28+00:00",
        "provider_retry_count": 0,
    }
    (checkpoint_dir / "request_attempts.jsonl").write_text(
        json.dumps(first_attempt, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (checkpoint_dir / "grader_outputs.jsonl").touch()
    legacy_failure = {
        **first_attempt,
        "error_classification": "typed_output_or_validation",
        "error": "ValueError: C2 quote is not in the cited chunk",
        "latency_ms": 12876.755,
    }
    (checkpoint_dir / "operational_failures.jsonl").write_text(
        json.dumps(legacy_failure, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    backend = _FakeBackend()
    _, metrics = run_grader_v21_baseline(
        ROOT,
        output_dir=output_dir,
        backend=backend,
        frozen_dir=frozen_dir,
    )

    assert backend.calls == 11
    assert records[0]["grader_input"]["question"] not in backend.questions
    attempts = [
        json.loads(line)
        for line in (output_dir / "request_attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(attempts) == 12
    assert [item["case_id"] for item in attempts].count("V21H-LIT-001") == 1
    assert metrics["aggregate"]["case_count"] == 12
    assert metrics["aggregate"]["binary_valid_case_count"] == 11
    assert metrics["aggregate"]["invalid_count"] == 1
    assert metrics["invalid_cases"] == ["V21H-LIT-001"]
    assert sum(
        metrics["aggregate"][key]
        for key in (
            "true_sufficient",
            "false_sufficient",
            "true_insufficient",
            "false_insufficient",
        )
    ) == 11
    assert metrics["aggregate"]["accuracy"] == pytest.approx(
        (
            metrics["aggregate"]["true_sufficient"]
            + metrics["aggregate"]["true_insufficient"]
        )
        / 12
    )
    assert metrics["aggregate"]["binary_valid_accuracy"] == pytest.approx(
        (
            metrics["aggregate"]["true_sufficient"]
            + metrics["aggregate"]["true_insufficient"]
        )
        / 11
    )
    failure = json.loads(
        (output_dir / "validation_failures.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert failure["case_id"] == "V21H-LIT-001"
    assert failure["failure_type"] == (
        MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE
    )
    assert failure["raw_provider_output_checkpointed"] is False
    reproducibility = json.loads(
        (output_dir / "reproducibility.json").read_text(encoding="utf-8")
    )
    provenance = reproducibility["harness_provenance"]
    assert provenance["patch_scope"] == (
        "observability_and_checkpoint_resume_only"
    )
    assert provenance["grading_semantics_changed"] is False
    assert provenance["frozen_evaluation_inputs_changed"] is False
    assert provenance["v21h_lit_001_re_requested"] is False
    report = (output_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "observability/resume-only patch" in report
    assert "V21H-LIT-001 was not requested or evaluated again" in report
