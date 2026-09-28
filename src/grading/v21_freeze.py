"""Final human-review and frozen held-out artifacts for Grader v2.1."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Mapping
from uuid import uuid4

from .v21_contracts import GraderV21Input
from .v21_review import (
    CORPUS_PATH,
    GOLD_PATH,
    INPUT_PATH,
    MODEL_INPUT_FIELDS,
    PROJECT_ROOT,
    REVIEW_REPORT_PATH,
    _all_keys,
    build_v21_model_inputs,
    read_json,
    read_jsonl,
    sha256_file,
    stable_sha256,
    validate_v21_design,
)


FROZEN_HELDOUT_VERSION = "grader_v2_1_heldout_v1"
DEFAULT_FROZEN_DIR = Path("data/evaluation/grader_v2_1_heldout_v1")
SOURCE_INPUT_SHA256 = (
    "01081d0a62fc03120ceb5c865aa6ff894ef3c21d74b1491360a3abcbbc52321d"
)
SOURCE_GOLD_SHA256 = (
    "559600bf077e2caf1928ff7ffcca60247e6dfe207028204089bfe3ad0d0c336b"
)
SOURCE_REVIEW_SHA256 = (
    "88351100f4f8ce766e866a8aab4dbdd4d9afdc53976bb78963489cc4b1d19000"
)
CONFIG_SHA256 = (
    "6fd4db93e3e71650b6349794345d0e0ee5427bf1b302cdc7823d8b05798568c1"
)
PROMPT_SHA256 = (
    "05d27548cc6abf76b403b655bb50afa28293ef8d429c1307db5374e9735ab117"
)
CONTRACT_FINALIZER_SHA256 = (
    "1ba86ffcffc82b6caf2d933f24bf8127bdb0961a49155b827dd2f11804abdbf8"
)
APPROVED_CASE_IDS = {
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
MANUAL_LEAKAGE_CHECKS = {
    "guarded_answer_phrases_not_model_visible": True,
    "withheld_chunks_absent_from_supplied_bundles": True,
    "gold_fields_absent_from_model_inputs": True,
    "eval_dataset_v1_questions_not_reused": True,
    "eval_dataset_v1_gold_chunks_not_reused": True,
    "grader_v2_heldout_chunks_not_reused": True,
}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _validate_manifest_artifacts(
    directory: Path, manifest: Mapping[str, Any]
) -> None:
    if manifest.get("status") != "frozen":
        raise RuntimeError(f"Dependency manifest is not frozen: {directory}")
    for artifact in manifest.get("artifacts", []):
        path = directory / str(artifact["path"])
        if sha256_file(path) != artifact["sha256"]:
            raise RuntimeError(f"Frozen artifact changed: {path}")


def _validate_source_hashes(root: Path) -> None:
    expected = {
        root / INPUT_PATH: SOURCE_INPUT_SHA256,
        root / GOLD_PATH: SOURCE_GOLD_SHA256,
        root / REVIEW_REPORT_PATH: SOURCE_REVIEW_SHA256,
        root / "config/grader_v2_1.json": CONFIG_SHA256,
        root / "config/grader_v2_1_prompt_draft.md": PROMPT_SHA256,
        root / "src/grading/v21_contracts.py": CONTRACT_FINALIZER_SHA256,
    }
    for path, digest in expected.items():
        if sha256_file(path) != digest:
            raise RuntimeError(f"Pre-evaluation source hash changed: {path}")


def _build_frozen_payloads(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    validation = validate_v21_design(root)
    if not validation["all_checks_passed"]:
        raise RuntimeError(
            "Grader v2.1 pre-freeze validation failed: "
            + ", ".join(validation["failed_checks"])
        )
    if validation["summary"]["human_review_approved"] != 12:
        raise RuntimeError("All 12 held-out cases must be human approved")
    _validate_source_hashes(root)

    completed_at = datetime.now(timezone.utc).isoformat()
    inputs = deepcopy(read_json(root / INPUT_PATH))
    gold = deepcopy(read_json(root / GOLD_PATH))
    inputs.update(
        {
            "dataset_version": "grader_v2_1_heldout_inputs_v1",
            "status": "frozen_human_reviewed",
            "human_review_completed_at_utc": completed_at,
            "source_candidate_sha256": SOURCE_INPUT_SHA256,
        }
    )
    gold.update(
        {
            "label_version": "grader_v2_1_heldout_gold_v1",
            "status": "frozen_human_reviewed",
            "input_dataset_version": "grader_v2_1_heldout_inputs_v1",
            "human_review_completed_at_utc": completed_at,
            "source_candidate_sha256": SOURCE_GOLD_SHA256,
            "reviewer_provenance": {
                "reviewer": "human_user",
                "approved_case_count": 12,
                "modify_count": 0,
                "reject_count": 0,
                "pending_count": 0,
                "completed_before_first_api_evaluation": True,
            },
        }
    )
    if {item["case_id"] for item in gold["cases"]} != APPROVED_CASE_IDS:
        raise RuntimeError("Approved Grader v2.1 case set changed")
    if any(item.get("human_review_status") != "approved" for item in gold["cases"]):
        raise RuntimeError("Frozen Gold contains a non-approved case")

    bare_model_inputs = build_v21_model_inputs(root)
    model_inputs = [
        {"case_id": case["case_id"], **record}
        for case, record in zip(inputs["cases"], bare_model_inputs, strict=True)
    ]
    banned_keys = {
        "case_id",
        "category",
        "diagnostic_tags",
        "required_components",
        "proposed_verdict",
        "proposed_insufficiency_reason",
        "supporting_chunk_ids",
        "withheld_reference_chunk_ids",
        "human_review_status",
    }
    for record in model_inputs:
        if record["grader_input_fields"] != MODEL_INPUT_FIELDS:
            raise RuntimeError("Frozen model-input field contract changed")
        GraderV21Input.model_validate(record["grader_input"])
        if _all_keys(record["grader_input"]) & banned_keys:
            raise RuntimeError("Gold or diagnostics leaked into model input")
        if stable_sha256(record["grader_input"]) != record["input_sha256"]:
            raise RuntimeError("Frozen model-input hash mismatch")

    finalization = {
        "heldout_version": FROZEN_HELDOUT_VERSION,
        "status": "human_review_complete_before_first_api_evaluation",
        "completed_at_utc": completed_at,
        "human_decisions": {
            "approved": 12,
            "modify": 0,
            "reject": 0,
            "pending": 0,
            "approved_case_ids": sorted(APPROVED_CASE_IDS),
        },
        "manual_leakage_checks": MANUAL_LEAKAGE_CHECKS,
        "no_gold_model_input_bundle_sha256": stable_sha256(model_inputs),
        "api_evaluation_run_at_finalization": False,
    }
    return inputs, gold, model_inputs, finalization


def freeze_grader_v21_heldout(
    project_root: str | Path = PROJECT_ROOT,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    target = (
        Path(output_dir).resolve()
        if output_dir
        else root / DEFAULT_FROZEN_DIR
    )
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite frozen held-out set: {target}")
    inputs, gold, model_inputs, finalization = _build_frozen_payloads(root)
    evaluation_policy = {
        "evaluation_version": "grader_v2_1_evaluation_v1",
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
        ],
        "component_metrics": [
            "gold_component_coverage",
            "status_accuracy_by_component_id",
            "question_span_exact_accuracy",
            "fully_correct_component_accuracy",
            "component_count_exact_match",
        ],
        "alignment_policy": "compare ordered C1..Cn identifiers; absent or extra components are errors",
        "gold_loaded_after_all_predictions": True,
        "confidence_policy": "diagnostic_only_no_threshold",
        "automatic_provider_retry": False,
        "post_result_tuning": False,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}-staging-{uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        (staging / "heldout_inputs.json").write_text(
            _json_text(inputs), encoding="utf-8"
        )
        (staging / "heldout_gold.json").write_text(
            _json_text(gold), encoding="utf-8"
        )
        with (staging / "model_inputs.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as handle:
            for item in model_inputs:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        shutil.copyfile(root / REVIEW_REPORT_PATH, staging / "human_review.md")
        (staging / "human_review_finalization.json").write_text(
            _json_text(finalization), encoding="utf-8"
        )
        (staging / "evaluation_policy.json").write_text(
            _json_text(evaluation_policy), encoding="utf-8"
        )
        snapshots = {
            "prompt.md": root / "config/grader_v2_1_prompt_draft.md",
            "config.json": root / "config/grader_v2_1.json",
            "v21_contracts.py": root / "src/grading/v21_contracts.py",
            "provider_v21.py": root / "src/grading/provider_v21.py",
            "v21_freeze.py": root / "src/grading/v21_freeze.py",
            "v21_baseline.py": root / "src/grading/v21_baseline.py",
            "run_grader_v21_baseline.py": root
            / "scripts/run_grader_v21_baseline.py",
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
            "source_hashes": {
                "candidate_inputs_sha256": SOURCE_INPUT_SHA256,
                "candidate_gold_sha256": SOURCE_GOLD_SHA256,
                "human_review_sha256": SOURCE_REVIEW_SHA256,
                "config_sha256": CONFIG_SHA256,
                "prompt_sha256": PROMPT_SHA256,
                "contract_finalizer_sha256": CONTRACT_FINALIZER_SHA256,
            },
            "model_input_bundle_sha256": finalization[
                "no_gold_model_input_bundle_sha256"
            ],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(staging / name),
                    "bytes": (staging / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite grader_v2_1_heldout_v1; create a new version "
                "for any later data, prompt, schema, finalizer, config, or evaluator."
            ),
        }
        (staging / "manifest.json").write_text(
            _json_text(manifest), encoding="utf-8"
        )
        shutil.copytree(staging, target)
        shutil.rmtree(staging)
    except BaseException:
        if target.exists():
            shutil.rmtree(target)
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return manifest


def validate_frozen_v21_heldout(
    project_root: str | Path = PROJECT_ROOT,
    *,
    frozen_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    directory = (
        Path(frozen_dir).resolve()
        if frozen_dir
        else root / DEFAULT_FROZEN_DIR
    )
    manifest = read_json(directory / "manifest.json")
    if manifest.get("heldout_version") != FROZEN_HELDOUT_VERSION:
        raise RuntimeError("Unexpected Grader v2.1 frozen held-out version")
    _validate_manifest_artifacts(directory, manifest)
    gold = read_json(directory / "heldout_gold.json")
    if gold.get("status") != "frozen_human_reviewed":
        raise RuntimeError("Frozen Grader v2.1 Gold is not human reviewed")
    if any(item.get("human_review_status") != "approved" for item in gold["cases"]):
        raise RuntimeError("Frozen Grader v2.1 Gold contains a non-approved case")
    finalization = read_json(directory / "human_review_finalization.json")
    if not all(finalization["manual_leakage_checks"].values()):
        raise RuntimeError("Frozen leakage review is incomplete")
    records = read_jsonl(directory / "model_inputs.jsonl")
    if len(records) != 12:
        raise RuntimeError("Frozen Grader v2.1 held-out must contain 12 cases")
    if stable_sha256(records) != manifest["model_input_bundle_sha256"]:
        raise RuntimeError("Frozen Grader v2.1 model-input bundle changed")
    return {
        "directory": directory,
        "manifest": manifest,
        "manifest_sha256": sha256_file(directory / "manifest.json"),
    }
