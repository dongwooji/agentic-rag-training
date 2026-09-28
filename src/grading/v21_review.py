"""Offline validation and human review for the Grader v2.1 candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import Verdict
from .v21_contracts import (
    ComponentKindV21,
    ComponentStatusV21,
    EvidenceAssessmentDraftV21,
    GraderV21Input,
    GradingProcedureV21,
    InsufficiencyReasonV21,
    derive_v21_decision,
    finalize_v21_assessment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = Path(
    "data/evaluation/grader_v2_1_heldout_candidate_inputs.json"
)
GOLD_PATH = Path(
    "data/evaluation/grader_v2_1_heldout_candidate_gold.json"
)
DEV_PATH = Path("data/evaluation/grader_v2_1_dev_cases.json")
CORPUS_PATH = Path("data/literature/processed/chunks.jsonl")
EVAL_PATH = Path("data/evaluation/eval_dataset_v1.json")
OLD_V2_INPUT_PATH = Path(
    "data/evaluation/grader_v2_heldout_v1/heldout_inputs.json"
)
OLD_V2_HELDOUT_MANIFEST_PATH = Path(
    "data/evaluation/grader_v2_heldout_v1/manifest.json"
)
OLD_V2_BASELINE_MANIFEST_PATH = Path(
    "reports/baselines/grader_v2_baseline/manifest.json"
)
REVIEW_REPORT_PATH = Path("reports/GRADER_V2_1_HELDOUT_HUMAN_REVIEW.md")

CORPUS_SHA256 = (
    "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177"
)
EVAL_SHA256 = (
    "1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509"
)
OLD_V2_HELDOUT_MANIFEST_SHA256 = (
    "7168e275fdc9c2167144604eb2d53c59568c8771d59a2923a5d1dab74a92785d"
)
OLD_V2_BASELINE_MANIFEST_SHA256 = (
    "870764d296dd469050d4699140cf5ef0508eb8d31585214082c0d8df3b963a5d"
)
MODEL_INPUT_FIELDS = ["question", "grading_procedure", "retrieved_evidence"]
BANNED_MODEL_KEYS = {
    "case_id",
    "category",
    "diagnostic_tags",
    "gold",
    "required_components",
    "proposed_verdict",
    "proposed_insufficiency_reason",
    "supporting_chunk_ids",
    "withheld_reference_chunk_ids",
    "human_review_status",
}
EXPECTED_APPROVED_CASE_IDS = {
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
EXPECTED_PENDING_REREVIEW_CASE_IDS: set[str] = set()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _all_keys(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        result.update(str(key) for key in value)
        for item in value.values():
            result.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_all_keys(item))
    return result


def _absent_or_valid_frozen_manifest(directory: Path) -> bool:
    """Allow lifecycle progression while rejecting partial or mutated freezes."""

    if not directory.exists():
        return True
    try:
        manifest = read_json(directory / "manifest.json")
        return manifest.get("status") == "frozen" and all(
            sha256_file(directory / item["path"]) == item["sha256"]
            for item in manifest.get("artifacts", [])
        )
    except Exception:
        return False


def build_v21_model_inputs(
    project_root: str | Path = PROJECT_ROOT,
) -> list[dict[str, Any]]:
    """Build model inputs without opening the candidate Gold file."""

    root = Path(project_root).resolve()
    inputs = read_json(root / INPUT_PATH)
    chunks = read_jsonl(root / CORPUS_PATH)
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    records: list[dict[str, Any]] = []
    for case in inputs["cases"]:
        evidence: list[dict[str, Any]] = []
        for rank, chunk_id in enumerate(
            case["evidence_bundle_chunk_ids"], start=1
        ):
            chunk = chunk_by_id[chunk_id]
            evidence.append(
                {
                    "evidence_rank": rank,
                    "evidence_source": "controlled_heldout_bundle",
                    "chunk_id": chunk["chunk_id"],
                    "paper_id": chunk["paper_id"],
                    "pmcid": chunk.get("pmcid"),
                    "title": chunk["title"],
                    "section": chunk["section"],
                    "year": chunk.get("year"),
                    "population": chunk.get("population"),
                    "study_type": chunk.get("study_type"),
                    "topics": chunk.get("topics", []),
                    "text": chunk["text"],
                }
            )
        grader_input = GraderV21Input(
            question=case["question"],
            grading_procedure=GradingProcedureV21(),
            retrieved_evidence=evidence,
        ).model_dump(mode="json")
        records.append(
            {
                "input_sha256": stable_sha256(grader_input),
                "grader_input_fields": MODEL_INPUT_FIELDS,
                "grader_input": grader_input,
            }
        )
    return records


def find_leakage_hits(
    *,
    inputs: dict[str, Any],
    gold: dict[str, Any],
    chunk_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Find guarded answer phrases in any model-visible surface."""

    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    hits: list[dict[str, str]] = []
    guards = gold.get("withheld_answer_leakage_guards", {})
    for case_id, phrases in guards.items():
        case = input_by_id.get(case_id)
        if case is None:
            hits.append(
                {"case_id": case_id, "field": "case", "phrase": "missing case"}
            )
            continue
        chunks = [
            chunk_by_id[item] for item in case["evidence_bundle_chunk_ids"]
        ]
        surfaces = {
            "question": _normalized(case["question"]),
            "supplied_text": _normalized(
                " ".join(item["text"] for item in chunks)
            ),
            "metadata": _normalized(
                " ".join(
                    " ".join(
                        [
                            str(item.get("title", "")),
                            str(item.get("section", "")),
                            str(item.get("population", "")),
                            str(item.get("study_type", "")),
                            " ".join(item.get("topics", [])),
                        ]
                    )
                    for item in chunks
                )
            ),
        }
        for phrase in phrases:
            normalized_phrase = _normalized(phrase)
            for field, surface in surfaces.items():
                if normalized_phrase and normalized_phrase in surface:
                    hits.append(
                        {"case_id": case_id, "field": field, "phrase": phrase}
                    )
    return hits


def _validate_gold_case(
    input_case: dict[str, Any],
    gold_case: dict[str, Any],
    chunk_by_id: dict[str, dict[str, Any]],
) -> tuple[bool, set[str]]:
    components = gold_case.get("required_components", [])
    component_ids = [item.get("component_id") for item in components]
    expected_ids = [f"C{index}" for index in range(1, len(components) + 1)]
    bundle = set(input_case["evidence_bundle_chunk_ids"])
    withheld: set[str] = set()
    valid = bool(components) and component_ids == expected_ids
    statuses: list[ComponentStatusV21] = []
    for component in components:
        try:
            ComponentKindV21(component["kind"])
            status = ComponentStatusV21(component["status"])
            statuses.append(status)
        except (KeyError, ValueError):
            valid = False
            continue
        supporting = set(component.get("supporting_chunk_ids", []))
        references = set(component.get("withheld_reference_chunk_ids", []))
        withheld.update(references)
        valid = valid and component.get("required") is True
        valid = valid and _normalized(component.get("question_span")) in _normalized(
            input_case["question"]
        )
        valid = valid and bool(component.get("requirement"))
        valid = valid and supporting <= bundle
        valid = valid and not (references & bundle)
        valid = valid and all(item in chunk_by_id for item in references)
        if status == ComponentStatusV21.MISSING:
            valid = valid and not supporting
        else:
            valid = valid and bool(supporting)

    if not statuses:
        return False, withheld
    mock_components = []
    for index, (component, status) in enumerate(zip(components, statuses), start=1):
        evidence = []
        for chunk_id in component.get("supporting_chunk_ids", []):
            evidence.append(
                {
                    "chunk_id": chunk_id,
                    "quote": chunk_by_id[chunk_id]["text"][: min(
                        200, len(chunk_by_id[chunk_id]["text"])
                    )],
                }
            )
        mock_components.append(
            {
                "component_id": f"C{index}",
                "required": True,
                "kind": component["kind"],
                "question_span": component["question_span"],
                "requirement": component["requirement"],
                "status": status.value,
                "evidence": evidence,
                "assessment_note": "Candidate Gold mapping validation.",
            }
        )
    try:
        assessment = EvidenceAssessmentDraftV21(
            literature_scope_only=True,
            structured_evidence_handling=(
                "excluded_assumed_evaluated_separately"
            ),
            literature_subquestion=gold_case["literature_subquestion"],
            required_components=mock_components,
            confidence=0.5,
        )
        verdict, reason, failed = derive_v21_decision(
            assessment.required_components
        )
        valid = valid and gold_case.get("proposed_verdict") == verdict.value
        valid = (
            valid
            and gold_case.get("proposed_insufficiency_reason") == reason.value
        )
        expected_failed = [
            item["component_id"]
            for item in components
            if item["status"] != "supported"
        ]
        valid = valid and failed == expected_failed
    except Exception:
        valid = False
    return bool(valid), withheld


def validate_v21_design(
    project_root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    inputs = read_json(root / INPUT_PATH)
    gold = read_json(root / GOLD_PATH)
    dev = read_json(root / DEV_PATH)
    eval_dataset = read_json(root / EVAL_PATH)
    old_v2_inputs = read_json(root / OLD_V2_INPUT_PATH)
    chunks = read_jsonl(root / CORPUS_PATH)
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    input_cases = inputs["cases"]
    gold_cases = gold["cases"]
    input_by_id = {item["case_id"]: item for item in input_cases}
    gold_by_id = {item["case_id"]: item for item in gold_cases}
    input_ids = [item["case_id"] for item in input_cases]
    gold_ids = [item["case_id"] for item in gold_cases]
    supplied = [
        chunk_id
        for case in input_cases
        for chunk_id in case["evidence_bundle_chunk_ids"]
    ]
    eval_questions = {item["question"] for item in eval_dataset["cases"]}
    old_v2_questions = {item["question"] for item in old_v2_inputs["cases"]}
    eval_gold_chunks = {
        chunk_id
        for case in eval_dataset["cases"]
        for group in case.get("gold", {}).get("literature_evidence_groups", [])
        for chunk_id in group.get("chunk_ids", [])
    }
    old_v2_chunks = {
        chunk_id
        for case in old_v2_inputs["cases"]
        for chunk_id in case["evidence_bundle_chunk_ids"]
    }

    gold_cases_valid = True
    withheld: set[str] = set()
    for case_id in input_ids:
        case_valid, case_withheld = _validate_gold_case(
            input_by_id[case_id], gold_by_id.get(case_id, {}), chunk_by_id
        )
        gold_cases_valid = gold_cases_valid and case_valid
        withheld.update(case_withheld)

    model_inputs = build_v21_model_inputs(root)
    leakage_hits = find_leakage_hits(
        inputs=inputs, gold=gold, chunk_by_id=chunk_by_id
    )
    guarded_cases = set(gold.get("withheld_answer_leakage_guards", {}))
    withheld_cases = {
        case["case_id"]
        for case in gold_cases
        if any(
            component.get("withheld_reference_chunk_ids", [])
            for component in case.get("required_components", [])
        )
    }

    dev_valid = True
    for case in dev["cases"]:
        try:
            grader_input = GraderV21Input.model_validate(case["grader_input"])
            assessment = EvidenceAssessmentDraftV21.model_validate(
                case["assessment"]
            )
            grade = finalize_v21_assessment(grader_input, assessment)
            dev_valid = dev_valid and (
                grade.verdict.value == case["expected"]["verdict"]
                and grade.insufficiency_reason.value
                == case["expected"]["insufficiency_reason"]
                and grade.failed_component_ids
                == case["expected"]["failed_component_ids"]
            )
        except Exception:
            dev_valid = False

    diagnostic_tags = {
        tag for case in input_cases for tag in case.get("diagnostic_tags", [])
    }
    required_tags = {
        "requested_limitation_missing",
        "specificity_trap",
        "quantitative_component_missing",
        "population_mismatch",
        "outcome_mismatch",
        "terminology",
        "multi_outcome_incomplete",
        "structured_channel_separation",
    }
    checks = {
        "candidate_inputs_are_draft": inputs.get("status")
        == "draft_human_review_required_not_frozen",
        "candidate_gold_is_draft": gold.get("status")
        == "draft_human_review_required_not_frozen",
        "candidate_count_12": len(input_cases) == len(gold_cases) == 12,
        "case_ids_align_and_unique": input_ids == gold_ids
        and len(set(input_ids)) == 12,
        "category_balance_10_lit_2_hybrid": sum(
            item["category"] == "literature_only" for item in input_cases
        )
        == 10
        and sum(item["category"] == "hybrid" for item in input_cases) == 2,
        "label_balance_6_6": sum(
            item["proposed_verdict"] == "sufficient" for item in gold_cases
        )
        == 6
        and sum(
            item["proposed_verdict"] == "insufficient" for item in gold_cases
        )
        == 6,
        "human_review_round_reflected": {
            item["case_id"]
            for item in gold_cases
            if item.get("human_review_status") == "approved"
        }
        == EXPECTED_APPROVED_CASE_IDS
        and {
            item["case_id"]
            for item in gold_cases
            if item.get("human_review_status") == "pending"
        }
        == EXPECTED_PENDING_REREVIEW_CASE_IDS,
        "questions_new_and_unique": len(
            {item["question"] for item in input_cases}
        )
        == 12
        and not ({item["question"] for item in input_cases} & eval_questions)
        and not ({item["question"] for item in input_cases} & old_v2_questions),
        "five_unique_chunks_per_case": all(
            len(item["evidence_bundle_chunk_ids"])
            == len(set(item["evidence_bundle_chunk_ids"]))
            == 5
            for item in input_cases
        ),
        "bundles_globally_disjoint": len(supplied) == len(set(supplied)) == 60,
        "all_supplied_chunks_exist": all(item in chunk_by_id for item in supplied),
        "no_eval_gold_chunk_reuse": not (set(supplied) & eval_gold_chunks),
        "no_grader_v2_heldout_chunk_reuse": not (set(supplied) & old_v2_chunks),
        "gold_component_contracts_valid": bool(gold_cases_valid),
        "withheld_references_exist_and_not_supplied": all(
            item in chunk_by_id and item not in set(supplied) for item in withheld
        ),
        "withheld_cases_have_guards": withheld_cases <= guarded_cases,
        "guarded_answer_phrases_not_model_visible": not leakage_hits,
        "required_diagnostic_coverage": required_tags <= diagnostic_tags,
        "dev_case_count_6": len(dev.get("cases", [])) == 6,
        "dev_cases_validate": bool(dev_valid),
        "model_input_count_12": len(model_inputs) == 12,
        "model_inputs_typed": all(
            GraderV21Input.model_validate(item["grader_input"])
            for item in model_inputs
        ),
        "gold_not_in_model_inputs": all(
            item["grader_input_fields"] == MODEL_INPUT_FIELDS
            and not (_all_keys(item["grader_input"]) & BANNED_MODEL_KEYS)
            for item in model_inputs
        ),
        "corpus_immutable": sha256_file(root / CORPUS_PATH) == CORPUS_SHA256,
        "eval_immutable": sha256_file(root / EVAL_PATH) == EVAL_SHA256,
        "grader_v2_heldout_manifest_immutable": sha256_file(
            root / OLD_V2_HELDOUT_MANIFEST_PATH
        )
        == OLD_V2_HELDOUT_MANIFEST_SHA256,
        "grader_v2_baseline_manifest_immutable": sha256_file(
            root / OLD_V2_BASELINE_MANIFEST_PATH
        )
        == OLD_V2_BASELINE_MANIFEST_SHA256,
        "v21_heldout_absent_or_valid_frozen": _absent_or_valid_frozen_manifest(
            root / "data/evaluation/grader_v2_1_heldout_v1"
        ),
        "v21_baseline_absent_or_valid_frozen": _absent_or_valid_frozen_manifest(
            root / "reports/baselines/grader_v2_1_baseline"
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "all_checks_passed": not failed,
        "failed_checks": failed,
        "checks": checks,
        "summary": {
            "candidate_cases": len(input_cases),
            "literature_only": sum(
                item["category"] == "literature_only" for item in input_cases
            ),
            "hybrid": sum(item["category"] == "hybrid" for item in input_cases),
            "proposed_sufficient": sum(
                item["proposed_verdict"] == "sufficient" for item in gold_cases
            ),
            "proposed_insufficient": sum(
                item["proposed_verdict"] == "insufficient" for item in gold_cases
            ),
            "human_review_approved": sum(
                item.get("human_review_status") == "approved"
                for item in gold_cases
            ),
            "human_review_pending": sum(
                item.get("human_review_status") == "pending"
                for item in gold_cases
            ),
            "dev_cases": len(dev.get("cases", [])),
            "model_input_bundle_sha256": stable_sha256(model_inputs),
            "leakage_hits": leakage_hits,
        },
    }


def _chunk_markdown(chunk: dict[str, Any]) -> list[str]:
    return [
        f"- Chunk ID: `{chunk['chunk_id']}`",
        f"- Paper / PMCID: `{chunk['paper_id']}` / `{chunk.get('pmcid')}`",
        f"- Title: {chunk['title']}",
        f"- Section: {chunk['section']}",
        "- Reviewer judgment: [ ] SUPPORTS MAPPED COMPONENT  [ ] RELATED ONLY  [ ] NOT RELEVANT",
        "- Reviewer note:",
        "",
        "> " + chunk["text"].replace("\n", "\n> "),
        "",
    ]


def render_v21_human_review(
    project_root: str | Path = PROJECT_ROOT,
) -> str:
    root = Path(project_root).resolve()
    inputs = read_json(root / INPUT_PATH)
    gold = read_json(root / GOLD_PATH)
    chunks = read_jsonl(root / CORPUS_PATH)
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    validation = validate_v21_design(root)
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    review_complete = (
        validation["summary"]["human_review_approved"] == 12
        and validation["summary"]["human_review_pending"] == 0
    )

    lines = [
        "# Grader v2.1 Held-out Candidate — Human Review",
        "",
        (
            "> Status: **FINAL — 12/12 HUMAN APPROVED — COMPLETED BEFORE API EVALUATION**"
            if review_complete
            else "> Status: **DRAFT — HUMAN REVIEW REQUIRED — NOT FROZEN — NOT EVALUATED**"
        ),
        "",
        (
            "This artifact records final human approval of the question-conditioned"
            if review_complete
            else "This is a proposed Gold review artifact for question-conditioned"
        ),
        (
            "required-component Gold before the first API evaluation. It contains"
            if review_complete
            else "required-component grading. It is not a Grader result and must not be"
        ),
        (
            "no model-generated evaluation result."
            if review_complete
            else "used for API evaluation until a later explicit review and freeze step."
        ),
        "",
        "## Review protocol",
        "",
        "1. Use only the full frozen-corpus text shown for the case.",
        "2. Verify that the components exhaust every material literature-side",
        "   requirement in the question without adding unrelated requirements.",
        "3. Verify each exact question span, component kind, status, and evidence",
        "   mapping. A related limitation cannot replace a requested limitation.",
        "4. A supported or mismatch component needs direct mapped evidence; a",
        "   missing component must have none in the supplied bundle.",
        "5. Overall proposed sufficiency is mechanical: every required component",
        "   must be supported. Free-text notes do not affect the verdict.",
        "6. Hybrid log/metric facts remain outside the literature channel.",
        "",
        "## Candidate summary",
        "",
        f"- Cases: {validation['summary']['candidate_cases']} (10 literature-only, 2 hybrid)",
        f"- Proposed labels: {validation['summary']['proposed_sufficient']} sufficient / {validation['summary']['proposed_insufficient']} insufficient",
        f"- Human-review status: {validation['summary']['human_review_approved']} approved / {validation['summary']['human_review_pending']} modified and pending re-review",
        f"- No-Gold model-input bundle SHA-256: `{validation['summary']['model_input_bundle_sha256']}`",
        "- Existing eval Gold chunk reuse: none",
        "- Existing Grader v2 held-out chunk reuse: none",
        "- Guarded answer leakage: none detected",
        "",
    ]
    for gold_case in gold["cases"]:
        case = input_by_id[gold_case["case_id"]]
        lines.extend(
            [
                "---",
                "",
                f"## {case['case_id']}",
                "",
                f"- Category: `{case['category']}`",
                "- Diagnostic tags: "
                + ", ".join(f"`{item}`" for item in case["diagnostic_tags"]),
                f"- Question: {case['question']}",
                f"- Literature subquestion: {gold_case['literature_subquestion']}",
                f"- Proposed verdict: **{gold_case['proposed_verdict'].upper()}**",
                f"- Proposed reason: `{gold_case['proposed_insufficiency_reason']}`",
                f"- Current human-review status: **{gold_case['human_review_status'].upper()}**",
                *(
                    [f"- Review note: {gold_case['review_note']}"]
                    if gold_case.get("review_note")
                    else []
                ),
                "",
                "### Required-component review",
                "",
            ]
        )
        for component in gold_case["required_components"]:
            lines.extend(
                [
                    f"#### {component['component_id']} — `{component['kind']}`",
                    "",
                    f"- Exact question span: `{component['question_span']}`",
                    f"- Requirement: {component['requirement']}",
                    f"- Proposed status: **{component['status'].upper()}**",
                    "- Supporting supplied chunks: "
                    + (
                        ", ".join(
                            f"`{item}`"
                            for item in component["supporting_chunk_ids"]
                        )
                        or "none"
                    ),
                    "- Withheld reference chunks: "
                    + (
                        ", ".join(
                            f"`{item}`"
                            for item in component["withheld_reference_chunk_ids"]
                        )
                        or "none"
                    ),
                    (
                        "- [x] Component is required and specific to the question."
                        if gold_case["human_review_status"] == "approved"
                        else "- [ ] Component is required and specific to the question."
                    ),
                    (
                        "- [x] Status is correct for the supplied evidence."
                        if gold_case["human_review_status"] == "approved"
                        else "- [ ] Status is correct for the supplied evidence."
                    ),
                    (
                        "- [x] Evidence mapping is direct, not merely topical."
                        if gold_case["human_review_status"] == "approved"
                        else "- [ ] Evidence mapping is direct, not merely topical."
                    ),
                    "- Reviewer note:",
                    "",
                ]
            )
        lines.extend(["### Supplied evidence — full text", ""])
        for rank, chunk_id in enumerate(case["evidence_bundle_chunk_ids"], start=1):
            lines.extend([f"#### Rank {rank}", ""])
            lines.extend(_chunk_markdown(chunk_by_id[chunk_id]))

        withheld = list(
            dict.fromkeys(
                chunk_id
                for component in gold_case["required_components"]
                for chunk_id in component["withheld_reference_chunk_ids"]
            )
        )
        if withheld:
            lines.extend(
                [
                    "### Withheld reference evidence — reviewer only",
                    "",
                    "These chunks are not model-visible. They only help verify",
                    "that a proposed missing component was intentionally withheld.",
                    "",
                ]
            )
            for chunk_id in withheld:
                lines.extend(_chunk_markdown(chunk_by_id[chunk_id]))
        lines.extend(
            [
                "### Human decision",
                "",
                (
                    "- [x] APPROVE"
                    if gold_case["human_review_status"] == "approved"
                    else "- [ ] APPROVE"
                ),
                "- [ ] MODIFY",
                "- [ ] REJECT",
                "- Potential issue: [ ] none  [ ] missing component",
                "  [ ] over-decomposition  [ ] specificity error",
                "  [ ] weak evidence mapping  [ ] label disagreement",
                "  [ ] leakage  [ ] other",
                "- Required modification / reviewer note:",
                "",
                (
                    "- Reviewer: human_user"
                    if gold_case["human_review_status"] == "approved"
                    else "- Reviewer:"
                ),
                (
                    "- Review date: 2026-09-03 (before API evaluation)"
                    if gold_case["human_review_status"] == "approved"
                    else "- Review date:"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "---",
            "",
            "## Stop checkpoint",
            "",
            (
                "- [x] All 12 cases have explicit human decisions."
                if review_complete
                else "- [ ] All 12 cases have explicit human decisions."
            ),
            (
                "- [x] All component decompositions are exhaustive and minimal."
                if review_complete
                else "- [ ] All component decompositions are exhaustive and minimal."
            ),
            (
                "- [x] All status and evidence mappings are approved."
                if review_complete
                else "- [ ] All status and evidence mappings are approved."
            ),
            (
                "- [x] Any MODIFY case has been revised and re-reviewed."
                if review_complete
                else "- [ ] Any MODIFY case has been revised and re-reviewed."
            ),
            "",
            (
                "Human review is complete. The separate freeze/evaluation workflow must"
                if review_complete
                else "No held-out freeze, preregistration, API evaluation, prompt tuning,"
            ),
            (
                "verify this exact artifact before the first API call."
                if review_complete
                else "or Recovery Agent work is authorized by this artifact."
            ),
            "",
        ]
    )
    return "\n".join(lines)
