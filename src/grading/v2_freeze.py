"""Human-review finalization and immutable held-out freeze for Grader v2."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .v2_contracts import GraderV2Input
from .v2_review import (
    AGENT_MANIFEST_SHA256,
    CORPUS_CHUNKS_SHA256,
    DENSE_MANIFEST_SHA256,
    EVAL_DATASET_SHA256,
    GOLD_PATH,
    GRADER_V1_MANIFEST_SHA256,
    GRADER_V2_CONFIG_SHA256,
    GRADER_V2_PROMPT_SHA256,
    HYBRID_MANIFEST_SHA256,
    INPUT_PATH,
    MODEL_INPUT_FIELDS,
    PROJECT_ROOT,
    REVIEW_REPORT_PATH,
    ROUTER_MANIFEST_SHA256,
    all_keys,
    read_json,
    read_jsonl,
    render_human_review_report,
    sha256_file,
    stable_sha256,
    validate_heldout_drafts,
)


FROZEN_HELDOUT_VERSION = "grader_v2_heldout_v1"
DEFAULT_FROZEN_DIR = Path("data/evaluation/grader_v2_heldout_v1")
DRAFT_INPUT_SHA256 = (
    "4e13fc0e96e7a4756be105b2c3f640a14a6ef97aa12940e7d062ba2c7a33c1c3"
)
DRAFT_GOLD_SHA256 = (
    "c3d5ce2c10942051cbb750dc130c9de204d7eb9ae3fa99b6d5fd8ad38ee92193"
)
DRAFT_REVIEW_SHA256 = (
    "c76b681da96ff666ef5ba5376904f17bd06e6b667c43e24968748a8a6a73fc6f"
)
EVAL_MANIFEST_SHA256 = (
    "4abd207041c4dd69df73e85e6d50b9288fe9f0d9c19f1ce33d06ae62e008d972"
)
CORPUS_MANIFEST_SHA256 = (
    "84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66"
)
HYBRID_RESULTS_SHA256 = (
    "d97c2c8c2ff46d07dc2730586eec444fa4c62f98231710c891cbda4d586f6fbb"
)
MANUAL_LEAKAGE_CHECKS = {
    "paper_title_or_metadata_does_not_reveal_withheld_answer": True,
    "question_wording_does_not_reveal_missing_answer": True,
    "withheld_chunks_absent_from_supplied_bundles": True,
    "gold_fields_absent_from_model_inputs": True,
    "eval_dataset_v1_questions_not_reused": True,
    "eval_dataset_v1_gold_chunks_not_reused": True,
}
APPROVED_CASE_IDS = {
    "G2H-LIT-001",
    "G2H-LIT-002",
    "G2H-LIT-003",
    "G2H-LIT-004",
    "G2H-LIT-005",
    "G2H-LIT-006",
    "G2H-LIT-007",
    "G2H-LIT-008",
    "G2H-LIT-009",
    "G2H-LIT-010",
    "G2H-HYB-001",
    "G2H-HYB-002",
    "G2H-HYB-003",
    "G2H-HYB-004",
}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _validate_manifest_artifacts(directory: Path, manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "frozen":
        raise RuntimeError(f"Dependency manifest is not frozen: {directory}")
    for artifact in manifest.get("artifacts", []):
        path = directory / str(artifact["path"])
        if sha256_file(path) != artifact["sha256"]:
            raise RuntimeError(f"Frozen dependency artifact changed: {path}")


def validate_phase6_to_11a_dependencies(
    project_root: str | Path = PROJECT_ROOT,
) -> dict[str, str]:
    """Validate every named frozen dependency before and after v2 evaluation."""

    root = Path(project_root).resolve()
    fixed_files = {
        "literature_corpus_chunks": (
            root / "data/literature/processed/chunks.jsonl",
            CORPUS_CHUNKS_SHA256,
        ),
        "literature_corpus_manifest": (
            root / "data/literature/manifests/corpus_v1.json",
            CORPUS_MANIFEST_SHA256,
        ),
        "eval_dataset": (
            root / "data/evaluation/eval_dataset_v1.json",
            EVAL_DATASET_SHA256,
        ),
        "eval_manifest": (
            root / "data/evaluation/eval_dataset_v1.manifest.json",
            EVAL_MANIFEST_SHA256,
        ),
        "hybrid_results": (
            root / "reports/baselines/hybrid_baseline_v1/hybrid_results.jsonl",
            HYBRID_RESULTS_SHA256,
        ),
        "hybrid_manifest": (
            root / "reports/baselines/hybrid_baseline_v1/manifest.json",
            HYBRID_MANIFEST_SHA256,
        ),
        "router_manifest": (
            root / "reports/baselines/router_baseline_v1/manifest.json",
            ROUTER_MANIFEST_SHA256,
        ),
        "grader_v1_manifest": (
            root / "reports/baselines/grader_baseline_v1/manifest.json",
            GRADER_V1_MANIFEST_SHA256,
        ),
    }
    hashes: dict[str, str] = {}
    for name, (path, expected) in fixed_files.items():
        actual = sha256_file(path)
        if actual != expected:
            raise RuntimeError(f"Frozen dependency changed: {name} ({path})")
        hashes[f"{name}_sha256"] = actual

    grader_v1_reproducibility = read_json(
        root / "reports/baselines/grader_baseline_v1/reproducibility.json"
    )
    anchored_references = {
        "dense_manifest_sha256": DENSE_MANIFEST_SHA256,
        "agent_manifest_sha256": AGENT_MANIFEST_SHA256,
    }
    for name, expected in anchored_references.items():
        if grader_v1_reproducibility.get(name) != expected:
            raise RuntimeError(f"Frozen Grader v1 reference changed: {name}")
        hashes[name] = expected

    for relative in (
        "reports/baselines/hybrid_baseline_v1",
        "reports/baselines/router_baseline_v1",
        "reports/baselines/grader_baseline_v1",
    ):
        directory = root / relative
        _validate_manifest_artifacts(directory, read_json(directory / "manifest.json"))
    return hashes


def _build_model_inputs(
    inputs: Mapping[str, Any], chunk_by_id: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for case in inputs["cases"]:
        evidence = []
        for rank, chunk_id in enumerate(case["evidence_bundle_chunk_ids"], start=1):
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
        grader_input = GraderV2Input.model_validate(
            {
                "question": case["question"],
                "grading_procedure": {},
                "retrieved_evidence": evidence,
            }
        ).model_dump(mode="json")
        records.append(
            {
                "case_id": case["case_id"],
                "input_sha256": stable_sha256(grader_input),
                "grader_input_fields": MODEL_INPUT_FIELDS,
                "grader_input": grader_input,
            }
        )
    return records


def build_human_reviewed_final(
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str, dict[str, Any]]:
    """Derive final data without mutating the preserved draft artifacts."""

    root = Path(project_root).resolve()
    validation = validate_heldout_drafts(root)
    if not validation["all_checks_passed"]:
        raise RuntimeError(
            "Pre-finalization draft validation failed: "
            + ", ".join(validation["failed_checks"])
        )
    if sha256_file(root / INPUT_PATH) != DRAFT_INPUT_SHA256:
        raise RuntimeError("Held-out input draft changed before finalization")
    if sha256_file(root / GOLD_PATH) != DRAFT_GOLD_SHA256:
        raise RuntimeError("Held-out Gold draft changed before finalization")
    if sha256_file(root / REVIEW_REPORT_PATH) != DRAFT_REVIEW_SHA256:
        raise RuntimeError("Held-out human-review draft changed before finalization")

    completed_at = datetime.now(timezone.utc).isoformat()
    inputs = deepcopy(read_json(root / INPUT_PATH))
    gold = deepcopy(read_json(root / GOLD_PATH))
    inputs.update(
        {
            "dataset_version": "grader_v2_heldout_inputs_v1",
            "status": "frozen_human_reviewed",
            "human_review_completed_at_utc": completed_at,
            "source_draft_path": INPUT_PATH.as_posix(),
            "source_draft_sha256": DRAFT_INPUT_SHA256,
        }
    )
    gold.update(
        {
            "label_version": "grader_v2_heldout_gold_v1",
            "status": "frozen_human_reviewed",
            "input_dataset_version": "grader_v2_heldout_inputs_v1",
            "human_review_completed_at_utc": completed_at,
            "source_draft_path": GOLD_PATH.as_posix(),
            "source_draft_sha256": DRAFT_GOLD_SHA256,
            "reviewer_provenance": {
                "reviewer": "human_user",
                "completed_before_first_grader_v2_api_evaluation": True,
                "approved_case_count": 14,
                "modify_count": 0,
                "reject_count": 0,
                "pending_count": 0,
                "manual_leakage_checks": MANUAL_LEAKAGE_CHECKS,
            },
        }
    )
    gold_by_id = {case["case_id"]: case for case in gold["cases"]}
    for case in gold["cases"]:
        case["human_review_status"] = "approved"

    lit010 = gold_by_id["G2H-LIT-010"]
    contract_by_id = {
        item["component_id"]: item for item in lit010["evidence_contract"]
    }
    lit010_c1 = contract_by_id["G2H-LIT-010-C1"]
    if lit010_c1["bundle_status"] != "mismatch":
        raise RuntimeError("Unexpected pre-finalization LIT-010 C1 bundle status")
    lit010_c1["bundle_status"] = "supported"
    lit010["component_mapping_consistency_decision"] = {
        "decision": "A_component_support_state",
        "before": "mismatch",
        "after": "supported",
        "reason": (
            "bundle_status records support for the component claim; the separate "
            "population_scope field retains the population mismatch"
        ),
        "schema_prompt_or_finalizer_changed": False,
    }

    if set(gold_by_id) != APPROVED_CASE_IDS:
        raise RuntimeError("Final held-out case set changed")
    if any(case["human_review_status"] != "approved" for case in gold["cases"]):
        raise RuntimeError("Every final held-out case must be explicitly approved")
    expected = lit010["expected_components"]
    if not (
        expected["surface_proposition_resolution"] == "mixed_or_qualified"
        and expected["answer_to_literature_question_supported"] is True
        and expected["population_scope"] == "mismatch"
        and lit010["proposed_verdict"] == "sufficient"
        and lit010["proposed_insufficiency_reason"] == "none"
    ):
        raise RuntimeError("LIT-010 substantive semantics changed during finalization")

    chunks = read_jsonl(root / "data/literature/processed/chunks.jsonl")
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    model_inputs = _build_model_inputs(inputs, chunk_by_id)
    banned_keys = {
        "category",
        "diagnostic_tags",
        "gold",
        "proposed_verdict",
        "proposed_insufficiency_reason",
        "expected_components",
        "evidence_contract",
        "supporting_chunk_ids",
        "withheld_reference_chunk_ids",
        "human_review_status",
    }
    if any(all_keys(item["grader_input"]) & banned_keys for item in model_inputs):
        raise RuntimeError("Gold or diagnostic fields leaked into frozen model inputs")

    review = _render_final_review(render_human_review_report(root))
    finalization = {
        "heldout_version": FROZEN_HELDOUT_VERSION,
        "status": "human_review_complete_before_api_evaluation",
        "completed_at_utc": completed_at,
        "human_decisions": {
            "approved": 14,
            "modify": 0,
            "reject": 0,
            "pending": 0,
            "approved_case_ids": sorted(APPROVED_CASE_IDS),
        },
        "lit010_c1_consistency": lit010["component_mapping_consistency_decision"],
        "manual_leakage_checks": MANUAL_LEAKAGE_CHECKS,
        "model_input_bundle_sha256": stable_sha256(model_inputs),
        "api_evaluation_run_at_finalization": False,
    }
    return inputs, gold, model_inputs, review, finalization


def _replace_case_block(text: str, case_id: str, replacements: Sequence[tuple[str, str]]) -> str:
    marker = f"## {case_id}"
    start = text.index(marker)
    next_case = text.find("\n---\n", start)
    if next_case < 0:
        next_case = len(text)
    block = text[start:next_case]
    for old, new in replacements:
        if old not in block:
            raise RuntimeError(f"Final review replacement missing for {case_id}: {old}")
        block = block.replace(old, new, 1)
    return text[:start] + block + text[next_case:]


def _render_final_review(draft_review: str) -> str:
    text = draft_review
    text = text.replace(
        "# Grader v2 Held-out Candidate — Human Review",
        "# Grader v2 Held-out v1 — Final Human Review",
        1,
    )
    text = text.replace(
        "> Status: **DRAFT — HUMAN REVIEW REQUIRED — NOT FROZEN**",
        "> Status: **FINAL — 14/14 HUMAN APPROVED — FROZEN BEFORE API EVALUATION**",
        1,
    )
    text = text.replace(
        "This artifact reviews proposed Gold labels for a new controlled held-out set.\n"
        "It is not a Grader result. No Grader v2 API evaluation has been run.\n"
        "The previous human-review decisions are retained for 12 unchanged cases;\n"
        "the two modified cases, G2H-LIT-007 and G2H-LIT-010, are pending re-review.",
        "This artifact records final human approval of the controlled held-out set.\n"
        "All decisions and manual leakage checks were completed before the first\n"
        "Grader v2 API evaluation. It contains no model-generated evaluation result.",
        1,
    )
    text = text.replace("- Approved without semantic changes: 12", "- Approved: 14", 1)
    text = text.replace("- Pending human review: 2", "- Pending human review: 0", 1)
    for case_id in ("G2H-LIT-007", "G2H-LIT-010"):
        text = _replace_case_block(
            text,
            case_id,
            (
                (
                    "- Current human-review status: **PENDING**",
                    "- Current human-review status: **APPROVED**",
                ),
                ("### Human decision\n\n- [ ] APPROVE", "### Human decision\n\n- [x] APPROVE"),
            ),
        )
    text = _replace_case_block(
        text,
        "G2H-LIT-010",
        (("- Proposed bundle status: `mismatch`", "- Proposed bundle status: `supported`"),),
    )
    text = text.replace(
        "- Mapping judgment: [ ] APPROVE  [ ] MODIFY  [ ] REJECT",
        "- Mapping judgment: [x] APPROVE  [ ] MODIFY  [ ] REJECT",
    )
    text = text.replace(
        "- [ ] Paper title/metadata does not reveal a withheld answer.",
        "- [x] Paper title/metadata does not reveal a withheld answer.",
    )
    text = text.replace(
        "- [ ] Question wording does not reveal the missing answer.",
        "- [x] Question wording does not reveal the missing answer.",
    )
    text = text.replace(
        "- Leakage reviewer note:",
        "- Leakage reviewer note: Human-confirmed before the first API evaluation.",
    )
    text = text.replace(
        "- Reviewer:\n- Review date:",
        "- Reviewer: human_user\n- Review date: 2026-09-02 (before API evaluation)",
    )
    text = text.replace("- [ ] All 14 cases have an explicit human decision.", "- [x] All 14 cases have an explicit human decision.")
    text = text.replace("- [ ] All MODIFY decisions have been incorporated and re-reviewed.", "- [x] All MODIFY decisions have been incorporated and re-reviewed.")
    text = text.replace("- [ ] No supplied evidence bundle reuses an eval_dataset_v1 Gold chunk.", "- [x] No supplied evidence bundle reuses an eval_dataset_v1 Gold chunk.")
    text = text.replace("- [ ] Candidate input and Gold files have final reviewer approval.", "- [x] Candidate input and Gold files have final reviewer approval.")
    text = text.replace("- [ ] Grader v2 final pre-evaluation prompt/config hashes are verified.", "- [x] Grader v2 final pre-evaluation prompt/config hashes are verified.")
    text = text.replace("- [ ] A separate frozen manifest has been generated.", "- [x] A separate frozen manifest has been generated.")
    text = text.replace(
        "Until every box is complete, `grader_v2_baseline` must not be run.",
        "All boxes were completed before `grader_v2_baseline` was first run.",
    )
    return text


def freeze_grader_v2_heldout(
    project_root: str | Path = PROJECT_ROOT,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    target = Path(output_dir).resolve() if output_dir else root / DEFAULT_FROZEN_DIR
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite frozen held-out set: {target}")
    dependencies = validate_phase6_to_11a_dependencies(root)
    if sha256_file(root / "config/grader_v2.json") != GRADER_V2_CONFIG_SHA256:
        raise RuntimeError("Grader v2 config changed before held-out freeze")
    if sha256_file(root / "config/grader_v2_prompt.md") != GRADER_V2_PROMPT_SHA256:
        raise RuntimeError("Grader v2 prompt changed before held-out freeze")

    inputs, gold, model_inputs, review, finalization = build_human_reviewed_final(root)
    evaluation_policy = {
        "evaluation_version": "grader_v2_evaluation_v1",
        "primary_verdict_metrics": [
            "accuracy",
            "true_sufficient",
            "false_sufficient",
            "true_insufficient",
            "false_insufficient",
            "sufficient_precision",
            "sufficient_recall",
            "insufficient_precision",
            "insufficient_recall",
            "false_sufficient_rate",
            "false_insufficient_rate",
        ],
        "category_slices": ["literature_only", "hybrid"],
        "diagnostic_slices": [
            "polarity_sensitive",
            "limitation_sensitive",
            "population_mismatch",
            "bounded_negative",
            "multi_evidence_incomplete",
            "missing_main_claim",
            "outcome_mismatch",
            "no_relevant_evidence",
            "hybrid_literature_sufficient",
            "hybrid_literature_insufficient",
        ],
        "component_fields": [
            "question_polarity",
            "surface_proposition_resolution",
            "relevant_evidence_present",
            "answer_to_literature_question_supported",
            "limitation_or_context_supported",
            "population_scope",
            "outcome_scope",
            "terminology_scope",
            "multi_evidence_status",
        ],
        "confidence_policy": "diagnostic_only_no_threshold",
        "gold_applied_after_all_predictions": True,
        "automatic_provider_retry": False,
        "post_result_tuning": False,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}-staging-{uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        (staging / "heldout_inputs.json").write_text(_json_text(inputs), encoding="utf-8")
        (staging / "heldout_gold.json").write_text(_json_text(gold), encoding="utf-8")
        with (staging / "model_inputs.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for item in model_inputs:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        (staging / "human_review.md").write_text(review, encoding="utf-8")
        (staging / "human_review_finalization.json").write_text(
            _json_text(finalization), encoding="utf-8"
        )
        (staging / "evaluation_policy.json").write_text(
            _json_text(evaluation_policy), encoding="utf-8"
        )
        snapshots = {
            "prompt.md": root / "config/grader_v2_prompt.md",
            "config.json": root / "config/grader_v2.json",
            "v2_contracts.py": root / "src/grading/v2_contracts.py",
            "provider_v2.py": root / "src/grading/provider_v2.py",
            "v2_freeze.py": root / "src/grading/v2_freeze.py",
            "v2_baseline.py": root / "src/grading/v2_baseline.py",
            "run_grader_v2_baseline.py": root / "scripts/run_grader_v2_baseline.py",
        }
        for name, source in snapshots.items():
            shutil.copyfile(source, staging / name)
        artifact_names = (
            "heldout_inputs.json",
            "heldout_gold.json",
            "model_inputs.jsonl",
            "human_review.md",
            "human_review_finalization.json",
            "evaluation_policy.json",
            *snapshots.keys(),
        )
        manifest = {
            "heldout_version": FROZEN_HELDOUT_VERSION,
            "status": "frozen",
            "created_at_utc": finalization["completed_at_utc"],
            "human_review": finalization["human_decisions"],
            "lineage": {
                "draft_input_sha256": DRAFT_INPUT_SHA256,
                "draft_gold_sha256": DRAFT_GOLD_SHA256,
                "draft_human_review_sha256": DRAFT_REVIEW_SHA256,
                "transition": "draft -> human-reviewed final -> frozen v1",
            },
            "frozen_dependency_hashes": dependencies,
            "model_input_bundle_sha256": finalization["model_input_bundle_sha256"],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(staging / name),
                    "bytes": (staging / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite grader_v2_heldout_v1; create a new version for "
                "any later dataset, prompt, config, contract, finalizer, or evaluator."
            ),
        }
        (staging / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        staging.replace(target)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return manifest


def validate_frozen_heldout(
    project_root: str | Path = PROJECT_ROOT,
    *,
    frozen_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    directory = Path(frozen_dir).resolve() if frozen_dir else root / DEFAULT_FROZEN_DIR
    manifest = read_json(directory / "manifest.json")
    if manifest.get("heldout_version") != FROZEN_HELDOUT_VERSION:
        raise RuntimeError("Unexpected frozen held-out version")
    _validate_manifest_artifacts(directory, manifest)
    current_dependencies = validate_phase6_to_11a_dependencies(root)
    if manifest.get("frozen_dependency_hashes") != current_dependencies:
        raise RuntimeError("Frozen dependency hashes differ from held-out manifest")
    gold = read_json(directory / "heldout_gold.json")
    if gold.get("status") != "frozen_human_reviewed":
        raise RuntimeError("Frozen Gold is not human-reviewed")
    if any(case.get("human_review_status") != "approved" for case in gold["cases"]):
        raise RuntimeError("Frozen Gold contains a non-approved case")
    finalization = read_json(directory / "human_review_finalization.json")
    if not all(finalization["manual_leakage_checks"].values()):
        raise RuntimeError("Manual leakage review is incomplete")
    return {
        "directory": directory,
        "manifest": manifest,
        "manifest_sha256": sha256_file(directory / "manifest.json"),
        "dependencies": current_dependencies,
    }
