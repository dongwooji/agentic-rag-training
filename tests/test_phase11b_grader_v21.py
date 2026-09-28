from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.grading.v21_contracts import (
    EvidenceAssessmentDraftV21,
    GraderV21Input,
    GradingProcedureV21,
    RequiredComponentAssessmentV21,
    finalize_v21_assessment,
)
from src.grading.v21_review import (
    CORPUS_PATH,
    GOLD_PATH,
    INPUT_PATH,
    OLD_V2_BASELINE_MANIFEST_PATH,
    OLD_V2_BASELINE_MANIFEST_SHA256,
    OLD_V2_HELDOUT_MANIFEST_PATH,
    OLD_V2_HELDOUT_MANIFEST_SHA256,
    build_v21_model_inputs,
    find_leakage_hits,
    read_json,
    read_jsonl,
    render_v21_human_review,
    sha256_file,
    validate_v21_design,
)


ROOT = Path(__file__).resolve().parents[1]


def _input(question: str, text: str = "Direct evidence is present.") -> GraderV21Input:
    return GraderV21Input.model_validate(
        {
            "question": question,
            "grading_procedure": GradingProcedureV21().model_dump(mode="json"),
            "retrieved_evidence": [
                {
                    "evidence_rank": 1,
                    "evidence_source": "synthetic_dev_fixture",
                    "chunk_id": "chunk-1",
                    "paper_id": "paper-1",
                    "pmcid": None,
                    "title": "Synthetic fixture",
                    "section": "Results",
                    "year": None,
                    "population": "synthetic",
                    "study_type": "synthetic",
                    "topics": [],
                    "text": text,
                }
            ],
        }
    )


def _assessment(
    *,
    question_span: str,
    status: str,
    evidence: list[dict[str, str]],
    note: str = "Diagnostic note only.",
) -> EvidenceAssessmentDraftV21:
    return EvidenceAssessmentDraftV21.model_validate(
        {
            "literature_scope_only": True,
            "structured_evidence_handling": (
                "excluded_assumed_evaluated_separately"
            ),
            "literature_subquestion": "Synthetic literature subquestion",
            "required_components": [
                {
                    "component_id": "C1",
                    "required": True,
                    "kind": "main_claim",
                    "question_span": question_span,
                    "requirement": "Directly answer the requested component.",
                    "status": status,
                    "evidence": evidence,
                    "assessment_note": note,
                }
            ],
            "confidence": 0.8,
        }
    )


def test_model_schema_has_no_model_decided_verdict_or_completeness() -> None:
    schema = EvidenceAssessmentDraftV21.model_json_schema()
    properties = set(schema["properties"])
    assert "verdict" not in properties
    assert "insufficiency_reason" not in properties
    assert "complete" not in properties
    assert "completeness" not in properties
    assert "reason" not in properties
    assert "required_components" in properties


def test_model_schema_is_strict_output_compatible_without_oneof() -> None:
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(EvidenceAssessmentDraftV21)
    rendered = json.dumps(schema, sort_keys=True)
    assert "oneOf" not in rendered
    assert schema["additionalProperties"] is False


def test_supported_and_mismatch_require_evidence_missing_forbids_it() -> None:
    base = {
        "component_id": "C1",
        "required": True,
        "kind": "main_claim",
        "question_span": "claim",
        "requirement": "claim",
        "assessment_note": "note",
    }
    with pytest.raises(ValidationError):
        RequiredComponentAssessmentV21.model_validate(
            {**base, "status": "supported", "evidence": []}
        )
    with pytest.raises(ValidationError):
        RequiredComponentAssessmentV21.model_validate(
            {
                **base,
                "status": "mismatch",
                "evidence": [],
            }
        )
    with pytest.raises(ValidationError):
        RequiredComponentAssessmentV21.model_validate(
            {
                **base,
                "status": "missing",
                "evidence": [{"chunk_id": "x", "quote": "x"}],
            }
        )


def test_finalizer_requires_question_span_supplied_chunk_and_exact_quote() -> None:
    grader_input = _input("필요한 claim을 확인해줘.")
    bad_span = _assessment(
        question_span="없는 span",
        status="supported",
        evidence=[{"chunk_id": "chunk-1", "quote": "Direct evidence"}],
    )
    with pytest.raises(ValueError, match="question_span"):
        finalize_v21_assessment(grader_input, bad_span)

    bad_chunk = _assessment(
        question_span="claim",
        status="supported",
        evidence=[{"chunk_id": "not-supplied", "quote": "Direct evidence"}],
    )
    with pytest.raises(ValueError, match="unsupplied"):
        finalize_v21_assessment(grader_input, bad_chunk)

    bad_quote = _assessment(
        question_span="claim",
        status="supported",
        evidence=[{"chunk_id": "chunk-1", "quote": "invented quotation"}],
    )
    with pytest.raises(ValueError, match="quote"):
        finalize_v21_assessment(grader_input, bad_quote)


def test_free_text_note_never_changes_verdict() -> None:
    grader_input = _input("필요한 claim을 확인해줘.")
    missing = _assessment(
        question_span="claim",
        status="missing",
        evidence=[],
        note="Everything is complete and should pass.",
    )
    grade = finalize_v21_assessment(grader_input, missing)
    assert grade.verdict.value == "insufficient"
    assert grade.failed_component_ids == ["C1"]


def test_regression_g2h_lit_002_specific_limitation_stays_missing() -> None:
    question = (
        "8주간 1·3·5세트 저항훈련 연구에서 근력과 근비대 결과는 어떻게 "
        "달랐으며, 저자가 결과 해석에 명시한 측정상의 한계는 무엇인가?"
    )
    grader_input = _input(
        question,
        "Strength and hypertrophy results were reported. The sample was small.",
    )
    assessment = EvidenceAssessmentDraftV21.model_validate(
        {
            "literature_scope_only": True,
            "structured_evidence_handling": (
                "excluded_assumed_evaluated_separately"
            ),
            "literature_subquestion": "근력·근비대 결과와 특정 측정 한계",
            "required_components": [
                {"component_id": "C1", "required": True, "kind": "outcome", "question_span": "근력", "requirement": "근력 결과", "status": "supported", "evidence": [{"chunk_id": "chunk-1", "quote": "Strength"}], "assessment_note": "Direct."},
                {"component_id": "C2", "required": True, "kind": "outcome", "question_span": "근비대", "requirement": "근비대 결과", "status": "supported", "evidence": [{"chunk_id": "chunk-1", "quote": "hypertrophy"}], "assessment_note": "Direct."},
                {"component_id": "C3", "required": True, "kind": "limitation_or_context", "question_span": "측정상의 한계", "requirement": "질문이 요구한 특정 측정 한계", "status": "missing", "evidence": [], "assessment_note": "A small sample is a different limitation and cannot substitute."}
            ],
            "confidence": 0.9,
        }
    )
    grade = finalize_v21_assessment(grader_input, assessment)
    assert grade.verdict.value == "insufficient"
    assert grade.insufficiency_reason.value == "missing_required_component"
    assert grade.failed_component_ids == ["C3"]


def test_regression_g2h_lit_004_missing_strength_forces_insufficient() -> None:
    question = (
        "6주간 higher-volume과 higher-load 훈련은 vastus lateralis의 MRI "
        "단면적과 leg-extension 1RM 변화에 각각 어떤 차이를 만들었는가?"
    )
    grader_input = _input(
        question,
        "MRI cross-sectional area increased more with higher volume.",
    )
    assessment = EvidenceAssessmentDraftV21.model_validate(
        {
            "literature_scope_only": True,
            "structured_evidence_handling": (
                "excluded_assumed_evaluated_separately"
            ),
            "literature_subquestion": "MRI와 leg-extension 1RM 결과",
            "required_components": [
                {"component_id": "C1", "required": True, "kind": "outcome", "question_span": "MRI 단면적", "requirement": "MRI 단면적 결과", "status": "supported", "evidence": [{"chunk_id": "chunk-1", "quote": "MRI cross-sectional area increased more with higher volume."}], "assessment_note": "Direct."},
                {"component_id": "C2", "required": True, "kind": "outcome", "question_span": "leg-extension 1RM", "requirement": "leg-extension 1RM 변화 결과", "status": "missing", "evidence": [], "assessment_note": "The strength result is absent although the MRI result is complete."}
            ],
            "confidence": 0.9,
        }
    )
    grade = finalize_v21_assessment(grader_input, assessment)
    assert grade.verdict.value == "insufficient"
    assert grade.failed_component_ids == ["C2"]


def test_all_six_new_dev_cases_validate_end_to_end() -> None:
    data = read_json(ROOT / "data/evaluation/grader_v2_1_dev_cases.json")
    assert len(data["cases"]) == 6
    for case in data["cases"]:
        grader_input = GraderV21Input.model_validate(case["grader_input"])
        assessment = EvidenceAssessmentDraftV21.model_validate(case["assessment"])
        grade = finalize_v21_assessment(grader_input, assessment)
        assert grade.verdict.value == case["expected"]["verdict"]
        assert grade.insufficiency_reason.value == case["expected"]["insufficiency_reason"]
        assert grade.failed_component_ids == case["expected"]["failed_component_ids"]


def test_heldout_candidate_validation_and_gold_separation() -> None:
    result = validate_v21_design(ROOT)
    assert result["all_checks_passed"] is True
    assert result["summary"]["candidate_cases"] == 12
    assert result["summary"]["proposed_sufficient"] == 6
    assert result["summary"]["proposed_insufficient"] == 6
    assert result["summary"]["human_review_approved"] == 12
    assert result["summary"]["human_review_pending"] == 0
    assert result["summary"]["leakage_hits"] == []

    model_inputs = build_v21_model_inputs(ROOT)
    assert len(model_inputs) == 12
    serialized = json.dumps(model_inputs, ensure_ascii=False)
    for banned in (
        "case_id",
        "proposed_verdict",
        "required_components",
        "supporting_chunk_ids",
        "human_review_status",
    ):
        assert f'"{banned}"' not in serialized


def test_leakage_validator_detects_guarded_phrase_on_visible_surface() -> None:
    inputs = read_json(ROOT / INPUT_PATH)
    gold = read_json(ROOT / GOLD_PATH)
    chunks = read_jsonl(ROOT / CORPUS_PATH)
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    mutated = copy.deepcopy(inputs)
    phrase = gold["withheld_answer_leakage_guards"]["V21H-LIT-002"][0]
    case = next(
        item for item in mutated["cases"] if item["case_id"] == "V21H-LIT-002"
    )
    case["question"] += " " + phrase
    hits = find_leakage_hits(
        inputs=mutated, gold=gold, chunk_by_id=chunk_by_id
    )
    assert {item["field"] for item in hits} == {"question"}


def test_human_review_contains_full_text_and_review_status_controls() -> None:
    report = render_v21_human_review(ROOT)
    assert "FINAL — 12/12 HUMAN APPROVED — COMPLETED BEFORE API EVALUATION" in report
    assert "V21H-LIT-001" in report
    assert "V21H-HYB-002" in report
    assert "Top-ranked prescriptions for muscle strength" in report
    assert "did not report participants’ adherence" in report
    assert "12 approved / 0 modified and pending re-review" in report
    assert "[x] APPROVE" in report
    assert "[ ] APPROVE" not in report
    assert "verify this exact artifact before the first API call" in report


def test_four_human_review_modifications_have_intended_semantics() -> None:
    inputs = read_json(ROOT / INPUT_PATH)
    gold = read_json(ROOT / GOLD_PATH)
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    gold_by_id = {item["case_id"]: item for item in gold["cases"]}

    lit_006 = input_by_id["V21H-LIT-006"]
    assert "여성 엘리트 선수의 최대근력 향상을 위한" in lit_006["question"]
    assert "intensity, weekly volume, frequency" in lit_006["question"]
    assert "정할 수 있는가" not in lit_006["question"]
    assert gold_by_id["V21H-LIT-006"]["literature_subquestion"] == (
        "여성 엘리트 선수의 최대근력 향상을 위한 최적 저항훈련 "
        "intensity, weekly volume, frequency"
    )
    assert [
        item["question_span"]
        for item in gold_by_id["V21H-LIT-006"]["required_components"]
    ] == ["여성 엘리트 선수", "intensity", "weekly volume", "frequency"]
    assert all(
        "최대근력" in item["requirement"]
        for item in gold_by_id["V21H-LIT-006"]["required_components"]
    )
    assert all(
        item["status"] == "mismatch"
        for item in gold_by_id["V21H-LIT-006"]["required_components"]
    )

    lit_008_ids = set(input_by_id["V21H-LIT-008"]["evidence_bundle_chunk_ids"])
    assert "paper_pmc6692867_results_study_characteristics_001_68c9ef7b" in lit_008_ids
    assert "paper_pmc6692867_discussion_001_60d9e64e" not in lit_008_ids
    assert "paper_pmc6692867_discussion_002_c2c69a70" not in lit_008_ids
    assert "paper_pmc6692867_results_synthesis_of_results_001_6b2d4db1" not in lit_008_ids

    lit_010 = input_by_id["V21H-LIT-010"]
    assert "최적 주간 세트 수를 제시해줘" in lit_010["question"]
    assert "적용할 수 있는가" not in lit_010["question"]
    assert [
        item["question_span"]
        for item in gold_by_id["V21H-LIT-010"]["required_components"]
    ] == ["60세 이상 저항훈련 경험 여성", "quadriceps", "biceps", "triceps"]
    assert all(
        item["status"] == "mismatch"
        for item in gold_by_id["V21H-LIT-010"]["required_components"]
    )

    hyb_002_ids = set(input_by_id["V21H-HYB-002"]["evidence_bundle_chunk_ids"])
    assert "paper_pmc9302196_discussion_time_of_day_001_77db4710" not in hyb_002_ids
    assert "paper_pmc9302196_results_descriptive_characteristics_of_the_umbre_001_b082b905" in hyb_002_ids
    strength = gold_by_id["V21H-HYB-002"]["required_components"][1]
    assert strength["status"] == "missing"
    assert strength["supporting_chunk_ids"] == []
    assert strength["withheld_reference_chunk_ids"] == [
        "paper_pmc10818109_results_strength_time_of_day_001_85241d24"
    ]

    approved = {
        item["case_id"]
        for item in gold["cases"]
        if item["human_review_status"] == "approved"
    }
    assert approved == {
        "V21H-LIT-001",
        "V21H-LIT-002",
        "V21H-LIT-003",
        "V21H-LIT-004",
        "V21H-LIT-005",
        "V21H-LIT-006",
        "V21H-LIT-007",
        "V21H-LIT-008",
        "V21H-LIT-009",
        "V21H-LIT-010",
        "V21H-HYB-001",
        "V21H-HYB-002",
    }


def test_frozen_v2_manifests_remain_unchanged() -> None:
    assert sha256_file(ROOT / OLD_V2_HELDOUT_MANIFEST_PATH) == OLD_V2_HELDOUT_MANIFEST_SHA256
    assert sha256_file(ROOT / OLD_V2_BASELINE_MANIFEST_PATH) == OLD_V2_BASELINE_MANIFEST_SHA256
