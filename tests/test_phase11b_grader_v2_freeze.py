from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.grading.contracts import GraderCallResult, GraderUsage
from src.grading.v2_baseline import run_grader_v2_baseline
from src.grading.v2_contracts import (
    EvidenceAssessmentDraftV2,
    GraderV2Input,
    finalize_v2_assessment,
)
from src.grading.v2_freeze import (
    DRAFT_GOLD_SHA256,
    DRAFT_INPUT_SHA256,
    build_human_reviewed_final,
    freeze_grader_v2_heldout,
    validate_frozen_heldout,
)
from src.grading.v2_review import all_keys, read_json, read_jsonl, sha256_file


ROOT = Path(__file__).resolve().parents[1]


def test_human_review_finalization_uses_supported_lit010_mapping() -> None:
    inputs, gold, model_inputs, report, finalization = build_human_reviewed_final(ROOT)
    gold_by_id = {item["case_id"]: item for item in gold["cases"]}
    lit010 = gold_by_id["G2H-LIT-010"]
    lit010_c1 = {
        item["component_id"]: item for item in lit010["evidence_contract"]
    }["G2H-LIT-010-C1"]

    assert inputs["status"] == "frozen_human_reviewed"
    assert gold["status"] == "frozen_human_reviewed"
    assert all(item["human_review_status"] == "approved" for item in gold["cases"])
    assert lit010_c1["bundle_status"] == "supported"
    assert lit010["expected_components"]["population_scope"] == "mismatch"
    assert lit010["expected_components"]["surface_proposition_resolution"] == "mixed_or_qualified"
    assert lit010["proposed_verdict"] == "sufficient"
    assert finalization["human_decisions"] == {
        "approved": 14,
        "modify": 0,
        "reject": 0,
        "pending": 0,
        "approved_case_ids": sorted(item["case_id"] for item in gold["cases"]),
    }
    assert "14/14 HUMAN APPROVED" in report
    assert "Pending human review: 0" in report
    assert "G2H-LIT-010-C1" in report
    assert "- Proposed bundle status: `supported`" in report
    assert len(model_inputs) == 14


def test_frozen_heldout_preserves_draft_lineage_and_no_gold_model_input(
    tmp_path: Path,
) -> None:
    target = tmp_path / "heldout"
    manifest = freeze_grader_v2_heldout(ROOT, output_dir=target)
    validated = validate_frozen_heldout(ROOT, frozen_dir=target)
    assert validated["manifest_sha256"] == sha256_file(target / "manifest.json")
    assert manifest["status"] == "frozen"
    assert manifest["human_review"]["approved"] == 14
    assert manifest["lineage"]["draft_input_sha256"] == DRAFT_INPUT_SHA256
    assert manifest["lineage"]["draft_gold_sha256"] == DRAFT_GOLD_SHA256
    assert sha256_file(ROOT / "data/evaluation/grader_v2_heldout_inputs_draft.json") == DRAFT_INPUT_SHA256
    assert sha256_file(ROOT / "data/evaluation/grader_v2_heldout_gold_draft.json") == DRAFT_GOLD_SHA256
    banned = {
        "category",
        "diagnostic_tags",
        "gold",
        "proposed_verdict",
        "proposed_insufficiency_reason",
        "expected_components",
        "evidence_contract",
        "human_review_status",
    }
    for item in read_jsonl(target / "model_inputs.jsonl"):
        assert not (all_keys(item["grader_input"]) & banned)
        GraderV2Input.model_validate(item["grader_input"])


def test_v2_baseline_pipeline_freezes_first_fake_evaluation(tmp_path: Path) -> None:
    frozen_dir = tmp_path / "heldout"
    freeze_grader_v2_heldout(ROOT, output_dir=frozen_dir)
    gold = read_json(frozen_dir / "heldout_gold.json")
    gold_by_question = {
        item["question"]: gold_case
        for item, gold_case in zip(
            read_json(frozen_dir / "heldout_inputs.json")["cases"],
            gold["cases"],
            strict=True,
        )
    }

    class PerfectFakeBackend:
        def invoke(self, grader_input: GraderV2Input) -> GraderCallResult:
            expected = gold_by_question[grader_input.question]
            assessment = EvidenceAssessmentDraftV2.model_validate(
                {
                    "literature_scope_only": True,
                    "structured_evidence_handling": "excluded_assumed_evaluated_separately",
                    "literature_subquestion": expected["literature_subquestion"],
                    **expected["expected_components"],
                    "reason": expected["proposed_rationale"],
                    "confidence": 0.8,
                }
            )
            grade = finalize_v2_assessment(assessment)
            return GraderCallResult(
                raw_grade=grade.model_dump(mode="json"),
                model="fake-grader-v2",
                response_id="fake-response",
                latency_ms=1.0,
                usage=GraderUsage(),
            )

    baseline_dir = tmp_path / "grader_v2_baseline"
    manifest, metrics = run_grader_v2_baseline(
        ROOT,
        output_dir=baseline_dir,
        backend=PerfectFakeBackend(),
        frozen_dir=frozen_dir,
    )
    assert manifest["status"] == "frozen"
    assert metrics["aggregate"]["accuracy"] == 1.0
    assert metrics["aggregate"]["false_sufficient"] == 0
    assert metrics["aggregate"]["false_insufficient"] == 0
    assert read_json(baseline_dir / "performance.json")["request_count"] == 14
    assert read_jsonl(baseline_dir / "operational_failures.jsonl") == []
    assert len(read_jsonl(baseline_dir / "grader_outputs.jsonl")) == 14

