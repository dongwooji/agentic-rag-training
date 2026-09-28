from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.grading.provider_v2 import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    OpenAIGraderV2Backend,
    load_grader_v2_config,
)
from src.grading.v2_contracts import (
    EvidenceAssessmentDraftV2,
    EvidenceGradeV2,
    GraderV2Input,
    finalize_v2_assessment,
)
from src.grading.v2_review import (
    MODEL_INPUT_FIELDS,
    build_heldout_model_inputs,
    sha256_file,
    validate_heldout_drafts,
)


ROOT = Path(__file__).resolve().parents[1]


def valid_assessment(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "literature_scope_only": True,
        "structured_evidence_handling": (
            "excluded_assumed_evaluated_separately"
        ),
        "literature_subquestion": "Does the literature resolve the question?",
        "question_polarity": "affirmative",
        "surface_proposition_resolution": "supported",
        "relevant_evidence_present": True,
        "answer_to_literature_question_supported": True,
        "limitation_or_context_supported": "not_required",
        "population_scope": "adequate",
        "outcome_scope": "adequate",
        "terminology_scope": "adequate",
        "multi_evidence_status": "not_required",
        "reason": "The supplied literature directly resolves the requested claim.",
        "confidence": 0.8,
    }
    value.update(overrides)
    return value


def test_v2_prompt_and_config_are_preregistered_and_pinned() -> None:
    config, prompt, prompt_path = load_grader_v2_config()
    assert config["grader_version"] == "grader_v2_candidate_v1"
    assert config["design_revision"] == "pre_evaluation_final_r2"
    assert config["model"] == "gpt-5.4-mini-2026-03-17"
    assert sha256_file(ROOT / "config/grader_v2.json") == EXPECTED_CONFIG_SHA256
    assert sha256_file(prompt_path) == EXPECTED_PROMPT_SHA256
    assert "Never require a literature chunk to contain the user's own records" in prompt
    assert "Treat a mismatch as an independent diagnostic" in prompt
    assert "G2H-LIT-001" not in prompt
    assert "HYB-005" not in prompt


def test_strict_typed_output_and_invalid_schema_rejection() -> None:
    draft = EvidenceAssessmentDraftV2.model_validate(valid_assessment())
    grade = finalize_v2_assessment(draft)
    assert grade.verdict.value == "sufficient"
    assert grade.insufficiency_reason.value == "none"
    with pytest.raises(ValidationError):
        EvidenceAssessmentDraftV2.model_validate(
            {**valid_assessment(), "unexpected": "forbidden"}
        )
    with pytest.raises(ValidationError):
        EvidenceAssessmentDraftV2.model_validate(
            valid_assessment(
                relevant_evidence_present=False,
                answer_to_literature_question_supported=True,
                surface_proposition_resolution="unresolved",
            )
        )
    with pytest.raises(ValidationError):
        EvidenceGradeV2.model_validate(
            {
                **valid_assessment(),
                "verdict": "insufficient",
                "insufficiency_reason": "none",
            }
        )


def test_model_schema_avoids_unsupported_oneof() -> None:
    serialized = json.dumps(EvidenceAssessmentDraftV2.model_json_schema())
    assert "oneOf" not in serialized


def test_negative_challenge_refutation_can_be_sufficient() -> None:
    grade = finalize_v2_assessment(
        EvidenceAssessmentDraftV2.model_validate(
            valid_assessment(
                question_polarity="negative_or_challenge",
                surface_proposition_resolution="refuted",
                limitation_or_context_supported="supported",
            )
        )
    )
    assert grade.verdict.value == "sufficient"


def test_population_mismatch_can_ground_sufficient_bounded_negative_answer() -> None:
    grade = finalize_v2_assessment(
        EvidenceAssessmentDraftV2.model_validate(
            valid_assessment(
                question_polarity="negative_or_challenge",
                surface_proposition_resolution="mixed_or_qualified",
                answer_to_literature_question_supported=True,
                population_scope="mismatch",
                limitation_or_context_supported="supported",
            )
        )
    )
    assert grade.verdict.value == "sufficient"
    assert grade.insufficiency_reason.value == "none"


@pytest.mark.parametrize("scope_field", ["population_scope", "outcome_scope", "terminology_scope"])
def test_supported_bounded_answer_keeps_scope_mismatch_diagnostic(
    scope_field: str,
) -> None:
    grade = finalize_v2_assessment(
        EvidenceAssessmentDraftV2.model_validate(
            valid_assessment(
                question_polarity="negative_or_challenge",
                surface_proposition_resolution="refuted",
                answer_to_literature_question_supported=True,
                **{scope_field: "mismatch"},
            )
        )
    )
    assert grade.verdict.value == "sufficient"
    assert grade.insufficiency_reason.value == "none"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"limitation_or_context_supported": "missing"},
            "missing_limitation_or_context",
        ),
        (
            {
                "population_scope": "mismatch",
                "answer_to_literature_question_supported": False,
                "surface_proposition_resolution": "unresolved",
            },
            "population_mismatch",
        ),
        (
            {
                "outcome_scope": "mismatch",
                "answer_to_literature_question_supported": False,
                "surface_proposition_resolution": "unresolved",
            },
            "outcome_mismatch",
        ),
        (
            {
                "terminology_scope": "mismatch",
                "answer_to_literature_question_supported": False,
                "surface_proposition_resolution": "unresolved",
            },
            "scope_mismatch",
        ),
        ({"multi_evidence_status": "incomplete"}, "multi_evidence_incomplete"),
        (
            {
                "answer_to_literature_question_supported": False,
                "surface_proposition_resolution": "unresolved",
            },
            "missing_main_claim",
        ),
        (
            {
                "relevant_evidence_present": False,
                "answer_to_literature_question_supported": False,
                "surface_proposition_resolution": "unresolved",
                "population_scope": "not_applicable",
                "outcome_scope": "not_applicable",
                "terminology_scope": "not_applicable",
            },
            "no_relevant_evidence",
        ),
    ],
)
def test_component_failures_produce_bounded_insufficient_reason(
    overrides: dict[str, Any], reason: str
) -> None:
    grade = finalize_v2_assessment(
        EvidenceAssessmentDraftV2.model_validate(valid_assessment(**overrides))
    )
    assert grade.verdict.value == "insufficient"
    assert grade.insufficiency_reason.value == reason


def test_hybrid_inputs_enforce_literature_only_scope() -> None:
    raw_inputs = json.loads(
        (ROOT / "data/evaluation/grader_v2_heldout_inputs_draft.json").read_text(
            encoding="utf-8"
        )
    )
    model_inputs = build_heldout_model_inputs(ROOT)
    hybrid_ids = {
        item["case_id"] for item in raw_inputs["cases"] if item["category"] == "hybrid"
    }
    for record in model_inputs:
        if record["case_id"] not in hybrid_ids:
            continue
        typed = GraderV2Input.model_validate(record["grader_input"])
        assert typed.grading_procedure.evidence_channel == "literature_only"
        assert (
            typed.grading_procedure.structured_evidence_handling
            == "excluded_assumed_evaluated_separately"
        )


def test_model_inputs_have_full_text_and_no_gold_leakage() -> None:
    banned = {
        "case_id",
        "category",
        "diagnostic_tags",
        "proposed_verdict",
        "proposed_insufficiency_reason",
        "expected_components",
        "evidence_contract",
        "supporting_chunk_ids",
        "withheld_reference_chunk_ids",
        "human_review_status",
    }

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value), set())
        return set()

    model_inputs = build_heldout_model_inputs(ROOT)
    assert len(model_inputs) == 14
    for item in model_inputs:
        assert item["grader_input_fields"] == MODEL_INPUT_FIELDS
        assert not (keys(item["grader_input"]) & banned)
        assert len(item["grader_input"]["retrieved_evidence"]) == 5
        assert all(
            chunk["text"] for chunk in item["grader_input"]["retrieved_evidence"]
        )


def test_heldout_checkpoint_records_review_round_and_valid_lifecycle() -> None:
    result = validate_heldout_drafts(ROOT)
    assert result["all_checks_passed"] is True
    assert result["summary"]["candidate_cases"] == 14
    assert result["summary"]["human_review_approved"] == 12
    assert result["summary"]["human_review_pending"] == 2
    assert result["summary"]["proposed_sufficient"] == 7
    assert result["summary"]["proposed_insufficient"] == 7
    assert result["checks"]["no_existing_eval_gold_chunk_reuse"] is True
    assert result["checks"]["withheld_cases_have_leakage_guards"] is True
    assert result["checks"]["lit007_quantitative_answer_has_leakage_guard"] is True
    assert result["checks"]["lit007_is_genuine_missing_main_claim"] is True
    assert result["checks"]["human_review_round_2_statuses_recorded"] is True
    assert (
        result["checks"]["withheld_answer_phrases_absent_from_model_surface"]
        is True
    )
    assert result["checks"]["superseded_design_provenance_preserved"] is True
    assert result["checks"]["grader_v2_baseline_state_valid"] is True


def test_hybrid_candidate_balance_and_lit010_option_a() -> None:
    inputs = json.loads(
        (ROOT / "data/evaluation/grader_v2_heldout_inputs_draft.json").read_text(
            encoding="utf-8"
        )
    )
    gold = json.loads(
        (ROOT / "data/evaluation/grader_v2_heldout_gold_draft.json").read_text(
            encoding="utf-8"
        )
    )
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    gold_by_id = {item["case_id"]: item for item in gold["cases"]}
    hybrid_ids = {
        item["case_id"] for item in inputs["cases"] if item["category"] == "hybrid"
    }
    assert hybrid_ids == {
        "G2H-HYB-001",
        "G2H-HYB-002",
        "G2H-HYB-003",
        "G2H-HYB-004",
    }
    assert sum(gold_by_id[item]["proposed_verdict"] == "sufficient" for item in hybrid_ids) == 2
    lit010 = gold_by_id["G2H-LIT-010"]
    assert lit010["proposed_verdict"] == "sufficient"
    assert lit010["expected_components"]["population_scope"] == "mismatch"
    assert lit010["expected_components"]["answer_to_literature_question_supported"] is True
    assert (
        lit010["expected_components"]["surface_proposition_resolution"]
        == "mixed_or_qualified"
    )
    assert lit010["expected_components"]["surface_proposition_resolution"] != "refuted"
    assert lit010["human_review_status"] == "pending"
    assert "bounded_negative" in input_by_id["G2H-LIT-010"]["diagnostic_tags"]


def test_lit007_is_genuine_missing_main_claim_not_outcome_mismatch() -> None:
    inputs = json.loads(
        (ROOT / "data/evaluation/grader_v2_heldout_inputs_draft.json").read_text(
            encoding="utf-8"
        )
    )
    gold = json.loads(
        (ROOT / "data/evaluation/grader_v2_heldout_gold_draft.json").read_text(
            encoding="utf-8"
        )
    )
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    gold_by_id = {item["case_id"]: item for item in gold["cases"]}
    lit006 = gold_by_id["G2H-LIT-006"]
    lit007 = gold_by_id["G2H-LIT-007"]
    lit007_contracts = {
        item["component_id"]: item for item in lit007["evidence_contract"]
    }

    assert lit006["proposed_insufficiency_reason"] == "outcome_mismatch"
    assert lit006["expected_components"]["outcome_scope"] == "mismatch"
    assert lit007["proposed_insufficiency_reason"] == "missing_main_claim"
    assert lit007["expected_components"]["outcome_scope"] == "adequate"
    assert lit007["expected_components"]["relevant_evidence_present"] is True
    assert lit007["expected_components"]["answer_to_literature_question_supported"] is False
    assert lit007_contracts["G2H-LIT-007-C1"]["bundle_status"] == "supported"
    assert lit007_contracts["G2H-LIT-007-C1"]["supporting_chunk_ids"]
    assert lit007_contracts["G2H-LIT-007-C2"]["bundle_status"] == "missing"
    assert lit007_contracts["G2H-LIT-007-C2"]["supporting_chunk_ids"] == []
    assert "부상" not in input_by_id["G2H-LIT-007"]["question"]
    assert "몇 분 또는 몇 퍼센트" in input_by_id["G2H-LIT-007"]["question"]
    assert lit007["human_review_status"] == "pending"


def test_openai_backend_uses_draft_schema_then_deterministic_finalizer() -> None:
    grader_input = GraderV2Input.model_validate(
        build_heldout_model_inputs(ROOT)[0]["grader_input"]
    )

    class Responses:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] | None = None

        def create(self, **kwargs: Any) -> Any:
            self.kwargs = kwargs
            return SimpleNamespace(
                output_text=json.dumps(valid_assessment()),
                output=[],
                model="gpt-5.4-mini-2026-03-17",
                id="resp_v2_test",
                usage=SimpleNamespace(
                    input_tokens=100,
                    input_tokens_details=SimpleNamespace(cached_tokens=0),
                    output_tokens=20,
                    output_tokens_details=SimpleNamespace(reasoning_tokens=0),
                    total_tokens=120,
                ),
            )

    responses = Responses()
    backend = OpenAIGraderV2Backend(client=SimpleNamespace(responses=responses))
    result = backend.invoke(grader_input)
    assert result.error is None
    assert result.raw_grade is not None
    assert result.raw_grade["verdict"] == "sufficient"
    assert result.raw_grade["insufficiency_reason"] == "none"
    assert responses.kwargs is not None
    assert responses.kwargs["text"]["format"]["type"] == "json_schema"
    assert responses.kwargs["text"]["format"]["name"] == "EvidenceAssessmentDraftV2"
    assert responses.kwargs["text"]["format"]["strict"] is True
    sent = json.loads(responses.kwargs["input"])
    assert set(sent) == set(MODEL_INPUT_FIELDS)
    assert "case_id" not in sent
    assert "gold" not in sent


def test_preregistered_config_mutation_is_rejected(tmp_path: Path) -> None:
    config = json.loads((ROOT / "config/grader_v2.json").read_text(encoding="utf-8"))
    config["temperature"] = 0.1
    mutated = tmp_path / "grader_v2.json"
    mutated.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(RuntimeError, match="configuration hash changed"):
        load_grader_v2_config(mutated)
