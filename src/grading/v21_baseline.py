"""Immutable first API evaluation for frozen Grader v2.1 held-out data."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import shutil
from statistics import mean, median
from time import perf_counter, sleep
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .evaluation import evaluate_grader
from .provider_v21 import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    GraderV21ProviderResult,
    OpenAIGraderV21Backend,
    load_grader_v21_config,
)
from .v21_contracts import (
    EvidenceAssessmentDraftV21,
    EvidenceGradeV21,
    GraderV21Input,
    finalize_v21_assessment,
)
from .v21_freeze import DEFAULT_FROZEN_DIR, validate_frozen_v21_heldout
from .v21_review import MODEL_INPUT_FIELDS, PROJECT_ROOT, read_json, read_jsonl, sha256_file, stable_sha256


BASELINE_VERSION = "grader_v2_1_baseline"
CHECKPOINT_VERSION = "grader_v2_1_baseline_checkpoint_v1"
MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE = (
    "MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE"
)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _distribution(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    ordered = sorted(float(item) for item in values)
    return {
        "mean": mean(ordered),
        "median": median(ordered),
        "p95": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        "min": ordered[0],
        "max": ordered[-1],
    }


def _append_jsonl(path: Path, item: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _checkpoint_paths(directory: Path) -> dict[str, Path]:
    return {
        "metadata": directory / "metadata.json",
        "attempts": directory / "request_attempts.jsonl",
        "raw_responses": directory / "raw_provider_outputs.jsonl",
        "outputs": directory / "grader_outputs.jsonl",
        "validation_failures": directory / "validation_failures.jsonl",
        "errors": directory / "operational_failures.jsonl",
    }


def load_frozen_v21_model_inputs(
    project_root: str | Path = PROJECT_ROOT,
    *,
    frozen_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    frozen = validate_frozen_v21_heldout(root, frozen_dir=frozen_dir)
    records = read_jsonl(frozen["directory"] / "model_inputs.jsonl")
    if len(records) != 12:
        raise RuntimeError("Frozen Grader v2.1 held-out must contain 12 cases")
    for record in records:
        if record.get("grader_input_fields") != MODEL_INPUT_FIELDS:
            raise RuntimeError("Frozen Grader v2.1 input field contract changed")
        GraderV21Input.model_validate(record["grader_input"])
        if stable_sha256(record["grader_input"]) != record["input_sha256"]:
            raise RuntimeError(f"Frozen model input changed: {record['case_id']}")
    if stable_sha256(records) != frozen["manifest"]["model_input_bundle_sha256"]:
        raise RuntimeError("Frozen Grader v2.1 input bundle changed")
    return {"root": root, "frozen": frozen, "grader_inputs": records}


def _load_or_create_checkpoint(
    *,
    checkpoint_dir: Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    expected_metadata: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    paths = _checkpoint_paths(checkpoint_dir)
    if checkpoint_dir.exists():
        metadata = read_json(paths["metadata"])
        for key, expected in expected_metadata.items():
            if metadata.get(key) != expected:
                raise RuntimeError(f"Grader v2.1 checkpoint changed: {key}")
        if metadata.get("status") not in {"in_progress", "finalized"}:
            raise RuntimeError("Grader v2.1 checkpoint status is invalid")
    else:
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        paths["metadata"].write_text(
            _json_text(
                {
                    **dict(expected_metadata),
                    "status": "in_progress",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "controls": {
                        "gold_loaded_before_all_predictions": False,
                        "one_provider_request_per_case": True,
                        "automatic_provider_retry": False,
                        "post_result_tuning": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        for key in (
            "attempts",
            "raw_responses",
            "outputs",
            "validation_failures",
            "errors",
        ):
            paths[key].touch()
    for key in ("raw_responses", "validation_failures"):
        if not paths[key].exists():
            paths[key].touch()
    attempts = read_jsonl(paths["attempts"])
    raw_responses = read_jsonl(paths["raw_responses"])
    outputs = read_jsonl(paths["outputs"])
    validation_failures = read_jsonl(paths["validation_failures"])
    legacy_or_operational_errors = read_jsonl(paths["errors"])
    operational_errors: list[dict[str, Any]] = []
    for record in legacy_or_operational_errors:
        if record.get("error_classification") == "provider_operational":
            operational_errors.append(record)
            continue
        validation_failures.append(
            {
                **record,
                "failure_type": MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE,
                "validation_stage": (
                    "grounding_finalizer"
                    if "quote is not in the cited chunk"
                    in str(record.get("error", ""))
                    else "typed_or_grounding_validation"
                ),
                "raw_provider_output_checkpointed": False,
                "legacy_checkpoint_record": True,
            }
        )
    attempt_ids = [item["case_id"] for item in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise RuntimeError("Duplicate provider request attempt detected")
    expected_attempt_prefix = [
        item["case_id"] for item in grader_inputs[: len(attempts)]
    ]
    if attempt_ids != expected_attempt_prefix:
        raise RuntimeError("Checkpoint request attempts are not a held-out prefix")
    expected_by_id = {item["case_id"]: item for item in grader_inputs}
    for attempt in attempts:
        expected = expected_by_id[attempt["case_id"]]
        if attempt.get("input_sha256") != expected["input_sha256"]:
            raise RuntimeError("Checkpoint request model-input hash changed")
    output_ids = [item["case_id"] for item in outputs]
    validation_failure_ids = [
        item["case_id"] for item in validation_failures
    ]
    operational_error_ids = [item["case_id"] for item in operational_errors]
    outcome_ids = output_ids + validation_failure_ids + operational_error_ids
    if len(outcome_ids) != len(set(outcome_ids)):
        raise RuntimeError("Duplicate checkpoint case outcome detected")
    expected_output_ids = [
        case_id for case_id in attempt_ids if case_id in set(output_ids)
    ]
    if output_ids != expected_output_ids:
        raise RuntimeError("Checkpoint outputs changed held-out order")
    for output in outputs:
        expected = expected_by_id[output["case_id"]]
        if output.get("input_sha256") != expected["input_sha256"]:
            raise RuntimeError("Checkpoint model-input hash changed")
        EvidenceGradeV21.model_validate(output["validated_grade"])
    for failure in validation_failures:
        expected = expected_by_id[failure["case_id"]]
        if failure.get("input_sha256") != expected["input_sha256"]:
            raise RuntimeError("Checkpoint failure model-input hash changed")
        if failure.get("failure_type") != (
            MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE
        ):
            raise RuntimeError("Unexpected Grader v2.1 validation failure type")
    raw_response_ids = [item["case_id"] for item in raw_responses]
    if len(raw_response_ids) != len(set(raw_response_ids)):
        raise RuntimeError("Duplicate raw provider output detected")
    if not set(raw_response_ids).issubset(set(attempt_ids)):
        raise RuntimeError("Raw provider output has no matching request attempt")
    completed_ids = set(output_ids)
    failed_ids = set(validation_failure_ids) | set(operational_error_ids)
    unresolved = set(attempt_ids) - completed_ids - failed_ids
    if operational_errors:
        raise RuntimeError(
            "A Grader v2.1 case already consumed its one allowed provider request; "
            "the baseline cannot retry or complete."
        )
    if unresolved:
        raise RuntimeError(
            "An indeterminate Grader v2.1 provider attempt exists; refusing a "
            "possibly duplicate request."
        )
    return (
        attempts,
        raw_responses,
        outputs,
        validation_failures,
        operational_errors,
    )


def _is_operational_error(error: str) -> bool:
    return any(
        item in error
        for item in (
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
            "ServiceUnavailableError",
        )
    )


def produce_v21_grades(
    grader_inputs: Sequence[Mapping[str, Any]],
    backend: Any,
    *,
    checkpoint_dir: Path,
    initial_attempts: Sequence[Mapping[str, Any]] = (),
    initial_outputs: Sequence[Mapping[str, Any]] = (),
    initial_validation_failures: Sequence[Mapping[str, Any]] = (),
    min_request_interval_seconds: float = 0.0,
    clock: Callable[[], float] = perf_counter,
    sleep_fn: Callable[[float], None] = sleep,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outputs = [dict(item) for item in initial_outputs]
    validation_failures = [
        dict(item) for item in initial_validation_failures
    ]
    paths = _checkpoint_paths(checkpoint_dir)
    checkpoint_attempts = read_jsonl(paths["attempts"])
    checkpoint_attempt_ids = [
        str(item["case_id"]) for item in checkpoint_attempts
    ]
    if len(checkpoint_attempt_ids) != len(set(checkpoint_attempt_ids)):
        raise RuntimeError("Duplicate provider request attempt detected")
    supplied_attempt_ids = [
        str(item["case_id"]) for item in initial_attempts
    ]
    if supplied_attempt_ids and supplied_attempt_ids != checkpoint_attempt_ids:
        raise RuntimeError("Checkpoint attempts changed before resume")
    attempted_ids = set(checkpoint_attempt_ids)
    if attempted_ids:
        print(
            "Continuing preserved first-pass outcomes: "
            f"{len(attempted_ids)}/{len(grader_inputs)}",
            flush=True,
        )
    last_request_started_at: float | None = None
    for index, item in enumerate(grader_inputs):
        case_id = str(item["case_id"])
        if case_id in attempted_ids:
            continue
        wait_seconds = 0.0
        if last_request_started_at is not None and min_request_interval_seconds > 0:
            wait_seconds = max(
                0.0,
                min_request_interval_seconds - (clock() - last_request_started_at),
            )
            if wait_seconds:
                print(f"Rate-limit pacing before {case_id}: {wait_seconds:.2f}s", flush=True)
                sleep_fn(wait_seconds)
        attempt = {
            "case_id": case_id,
            "input_sha256": item["input_sha256"],
            "request_ordinal": index + 1,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_retry_count": 0,
        }
        _append_jsonl(paths["attempts"], attempt)
        last_request_started_at = clock()
        print(f"Grading {index + 1:02d}/12: {case_id}", flush=True)
        call: GraderV21ProviderResult = backend.invoke(
            GraderV21Input.model_validate(item["grader_input"])
        )
        attempted_ids.add(case_id)
        raw_typed_output: Any | None = None
        typed_validation_error: str | None = call.error
        validation_stage = "response_extraction" if call.error else None
        draft: EvidenceAssessmentDraftV21 | None = None
        if typed_validation_error is None:
            try:
                raw_typed_output = json.loads(call.raw_output_text or "")
                draft = EvidenceAssessmentDraftV21.model_validate(
                    raw_typed_output
                )
            except Exception as exc:
                typed_validation_error = f"{type(exc).__name__}: {exc}"
                validation_stage = "typed_schema_validation"

        raw_record = {
            "case_id": case_id,
            "input_sha256": item["input_sha256"],
            "request_ordinal": index + 1,
            "raw_output_text": call.raw_output_text,
            "raw_typed_output": raw_typed_output,
            "raw_response_output": call.raw_response_output,
            "response_metadata": call.response_metadata,
            "grader_metrics": {
                "model": call.model,
                "response_id": call.response_id,
                "latency_ms": call.latency_ms,
                "usage": call.usage.model_dump(mode="json"),
                "provider_retry_count": 0,
            },
            "typed_validation_error": typed_validation_error,
            "checkpointed_before_finalizer": True,
        }
        _append_jsonl(paths["raw_responses"], raw_record)

        if typed_validation_error is not None:
            if (
                call.response_metadata.get("response_received") is False
                or (
                    call.response_id is None
                    and _is_operational_error(typed_validation_error)
                )
            ):
                _append_jsonl(
                    paths["errors"],
                    {
                        **attempt,
                        "error_classification": "provider_operational",
                        "error": typed_validation_error,
                        "latency_ms": call.latency_ms,
                    },
                )
                raise RuntimeError(
                    f"Grader v2.1 provider operation stopped at {case_id}; "
                    f"this case will not be retried: {typed_validation_error}"
                )
            failure = {
                **attempt,
                "failure_type": MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE,
                "validation_stage": validation_stage,
                "error": typed_validation_error,
                "latency_ms": call.latency_ms,
                "response_id": call.response_id,
                "usage": call.usage.model_dump(mode="json"),
                "request_pacing_wait_ms": wait_seconds * 1000,
                "raw_provider_output_checkpointed": True,
            }
            validation_failures.append(failure)
            _append_jsonl(
                paths["validation_failures"], failure
            )
            continue

        try:
            assert draft is not None
            grader_input = GraderV21Input.model_validate(item["grader_input"])
            grade = finalize_v21_assessment(grader_input, draft)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failure = {
                **attempt,
                "failure_type": MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE,
                "validation_stage": "grounding_finalizer",
                "error": error,
                "latency_ms": call.latency_ms,
                "response_id": call.response_id,
                "usage": call.usage.model_dump(mode="json"),
                "request_pacing_wait_ms": wait_seconds * 1000,
                "raw_provider_output_checkpointed": True,
            }
            validation_failures.append(failure)
            _append_jsonl(paths["validation_failures"], failure)
            continue

        output = {
            "case_id": case_id,
            "input_sha256": item["input_sha256"],
            "grader_input_fields": MODEL_INPUT_FIELDS,
            "raw_typed_output": raw_typed_output,
            "raw_grade": grade.model_dump(mode="json"),
            "validated_grade": grade.model_dump(mode="json"),
            "grader_metrics": {
                "model": call.model,
                "response_id": call.response_id,
                "latency_ms": call.latency_ms,
                "usage": call.usage.model_dump(mode="json"),
                "provider_retry_count": 0,
            },
            "request_pacing_wait_ms": wait_seconds * 1000,
        }
        outputs.append(output)
        _append_jsonl(paths["outputs"], output)
    return outputs, validation_failures


def build_v21_case_results(
    *,
    frozen_dir: Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    grader_outputs: Sequence[Mapping[str, Any]],
    validation_failures: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Open Gold only after all 12 model outputs are fixed."""

    inputs = read_json(frozen_dir / "heldout_inputs.json")
    gold = read_json(frozen_dir / "heldout_gold.json")
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    gold_by_id = {item["case_id"]: item for item in gold["cases"]}
    if not (
        len(grader_inputs) == len(input_by_id) == len(gold_by_id) == 12
        and len(grader_outputs) + len(validation_failures) == 12
    ):
        raise RuntimeError("Grader v2.1 evaluation must align exactly 12 cases")
    output_by_id = {item["case_id"]: item for item in grader_outputs}
    failure_by_id = {item["case_id"]: item for item in validation_failures}
    if set(output_by_id) & set(failure_by_id):
        raise RuntimeError("A case cannot be both valid and invalid")
    results: list[dict[str, Any]] = []
    for input_record in grader_inputs:
        case_id = str(input_record["case_id"])
        case = input_by_id[case_id]
        gold_case = gold_by_id[case_id]
        if case_id in failure_by_id:
            failure = failure_by_id[case_id]
            results.append(
                {
                    "case_id": case_id,
                    "category": case["category"],
                    "diagnostic_tags": case["diagnostic_tags"],
                    "question": case["question"],
                    "gold_verdict": gold_case["proposed_verdict"],
                    "predicted_verdict": None,
                    "prediction_status": "invalid",
                    "correct": False,
                    "gold_insufficiency_reason": gold_case[
                        "proposed_insufficiency_reason"
                    ],
                    "predicted_insufficiency_reason": None,
                    "reason_code": "invalid",
                    "failed_component_ids": [],
                    "confidence": None,
                    "failure_type": MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE,
                    "validation_stage": failure.get("validation_stage"),
                    "validation_error": failure.get("error"),
                    "gold_component_count": len(
                        gold_case["required_components"]
                    ),
                    "predicted_component_count": 0,
                    "component_count_exact_match": False,
                    "component_comparisons": [],
                    "extra_predicted_component_ids": [],
                    "retrieved_evidence_ids": [
                        item["chunk_id"]
                        for item in input_record["grader_input"][
                            "retrieved_evidence"
                        ]
                    ],
                }
            )
            continue
        output = output_by_id.get(case_id)
        if output is None:
            raise RuntimeError(f"Missing Grader v2.1 outcome for {case_id}")
        prediction = EvidenceGradeV21.model_validate(output["validated_grade"])
        predicted_by_id = {
            item.component_id: item for item in prediction.required_components
        }
        component_comparisons: list[dict[str, Any]] = []
        for expected in gold_case["required_components"]:
            predicted = predicted_by_id.get(expected["component_id"])
            comparison = {
                "component_id": expected["component_id"],
                "gold_kind": expected["kind"],
                "predicted_kind": predicted.kind.value if predicted else None,
                "gold_question_span": expected["question_span"],
                "predicted_question_span": predicted.question_span if predicted else None,
                "gold_status": expected["status"],
                "predicted_status": predicted.status.value if predicted else None,
                "component_present": predicted is not None,
                "kind_correct": predicted is not None and predicted.kind.value == expected["kind"],
                "question_span_exact": predicted is not None and predicted.question_span == expected["question_span"],
                "status_correct": predicted is not None and predicted.status.value == expected["status"],
            }
            comparison["fully_correct"] = all(
                comparison[key]
                for key in (
                    "component_present",
                    "kind_correct",
                    "question_span_exact",
                    "status_correct",
                )
            )
            component_comparisons.append(comparison)
        expected_ids = {item["component_id"] for item in gold_case["required_components"]}
        predicted_ids = set(predicted_by_id)
        gold_verdict = gold_case["proposed_verdict"]
        predicted_verdict = prediction.verdict.value
        failure_type = None
        if gold_verdict != predicted_verdict:
            failure_type = (
                "FALSE_SUFFICIENT_COMPONENT_OVER_CREDIT"
                if predicted_verdict == "sufficient"
                else "FALSE_INSUFFICIENT_COMPONENT_UNDER_CREDIT"
            )
        results.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "diagnostic_tags": case["diagnostic_tags"],
                "question": case["question"],
                "gold_verdict": gold_verdict,
                "predicted_verdict": predicted_verdict,
                "prediction_status": "valid",
                "correct": gold_verdict == predicted_verdict,
                "gold_insufficiency_reason": gold_case["proposed_insufficiency_reason"],
                "predicted_insufficiency_reason": prediction.insufficiency_reason.value,
                "reason_code": prediction.insufficiency_reason.value,
                "failed_component_ids": prediction.failed_component_ids,
                "confidence": prediction.confidence,
                "failure_type": failure_type,
                "gold_component_count": len(gold_case["required_components"]),
                "predicted_component_count": len(prediction.required_components),
                "component_count_exact_match": len(gold_case["required_components"])
                == len(prediction.required_components),
                "component_comparisons": component_comparisons,
                "extra_predicted_component_ids": sorted(predicted_ids - expected_ids),
                "retrieved_evidence_ids": [
                    item["chunk_id"]
                    for item in input_record["grader_input"]["retrieved_evidence"]
                ],
            }
        )
    return results


def build_component_diagnostics(
    case_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    invalid_cases = [
        item["case_id"]
        for item in case_results
        if item.get("prediction_status") == "invalid"
    ]
    comparisons = [
        comparison
        for case in case_results
        for comparison in case["component_comparisons"]
    ]
    fields = {}
    for key in (
        "component_present",
        "kind_correct",
        "question_span_exact",
        "status_correct",
        "fully_correct",
    ):
        correct = sum(bool(item[key]) for item in comparisons)
        fields[key] = {
            "n": len(comparisons),
            "correct": correct,
            "accuracy": correct / len(comparisons) if comparisons else 0.0,
        }
    status_pairs = Counter(
        f"{item['gold_status']} -> {item['predicted_status']}"
        for item in comparisons
    )
    exact_count = sum(bool(item["component_count_exact_match"]) for item in case_results)
    errors = [
        {"case_id": case["case_id"], **comparison}
        for case in case_results
        for comparison in case["component_comparisons"]
        if not comparison["fully_correct"]
    ]
    return {
        "invalid_case_count": len(invalid_cases),
        "invalid_case_ids": invalid_cases,
        "gold_component_count": len(comparisons),
        "predicted_component_count": sum(
            int(item["predicted_component_count"]) for item in case_results
        ),
        "fields": fields,
        "status_gold_to_prediction": dict(sorted(status_pairs.items())),
        "component_count_exact_match": {
            "n": len(case_results),
            "correct": exact_count,
            "accuracy": exact_count / len(case_results) if case_results else 0.0,
        },
        "errors": errors,
    }


def _build_metrics(case_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    valid_results = [
        item
        for item in case_results
        if item.get("prediction_status") == "valid"
    ]
    invalid_results = [
        item
        for item in case_results
        if item.get("prediction_status") == "invalid"
    ]
    metrics = evaluate_grader(valid_results)
    aggregate = metrics["aggregate"]
    aggregate["binary_valid_case_count"] = len(valid_results)
    aggregate["invalid_count"] = len(invalid_results)
    aggregate["case_count"] = len(case_results)
    aggregate["overall_gold_sufficient_count"] = sum(
        item["gold_verdict"] == "sufficient" for item in case_results
    )
    aggregate["overall_gold_insufficient_count"] = sum(
        item["gold_verdict"] == "insufficient" for item in case_results
    )
    aggregate["binary_valid_accuracy"] = (
        (
            aggregate["true_sufficient"]
            + aggregate["true_insufficient"]
        )
        / len(valid_results)
        if valid_results
        else 0.0
    )
    aggregate["accuracy"] = (
        (
            aggregate["true_sufficient"]
            + aggregate["true_insufficient"]
        )
        / len(case_results)
        if case_results
        else 0.0
    )
    metrics["definitions"]["invalid"] = (
        "No binary verdict because typed/grounding validation failed; counted "
        "incorrect only in overall accuracy"
    )
    metrics["definitions"]["binary_precision_recall_scope"] = (
        "TS/FS/TI/FI and class precision/recall use valid binary predictions only"
    )
    metrics["invalid_cases"] = [item["case_id"] for item in invalid_results]
    metrics["category_accuracy"] = {}
    for category in ("literature_only", "hybrid"):
        items = [item for item in case_results if item["category"] == category]
        correct = sum(bool(item["correct"]) for item in items)
        metrics["category_accuracy"][category] = {
            "n": len(items),
            "correct": correct,
            "accuracy": correct / len(items) if items else 0.0,
            "invalid": sum(
                item.get("prediction_status") == "invalid" for item in items
            ),
        }
    metrics["per_case"] = list(case_results)
    return metrics


def _build_report(
    metrics: Mapping[str, Any],
    components: Mapping[str, Any],
    performance: Mapping[str, Any],
    reproducibility: Mapping[str, Any],
) -> str:
    aggregate = metrics["aggregate"]
    failures = [item for item in metrics["per_case"] if not item["correct"]]
    lines = [
        "# Grader v2.1 Baseline — Immutable First Held-out Evaluation",
        "",
        "> Frozen first result. No result-driven tuning or rerun was performed.",
        "",
        "## Configuration",
        "",
        f"- Model: `{reproducibility['model']['name']}`",
        f"- Prompt SHA-256: `{reproducibility['prompt_sha256']}`",
        f"- Config SHA-256: `{reproducibility['config_sha256']}`",
        f"- Contract/finalizer SHA-256: `{reproducibility['contract_finalizer_sha256']}`",
        f"- Held-out manifest SHA-256: `{reproducibility['heldout_manifest_sha256']}`",
        "- Gold was opened only after all 12 first-pass predictions were fixed.",
        "- The evaluation harness received an observability/resume-only patch after V21H-LIT-001 consumed its first request.",
        "- Grading semantics and frozen evaluation inputs were unchanged by that patch.",
        "- V21H-LIT-001 was not requested or evaluated again; its original validation failure remains INVALID.",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Accuracy | {aggregate['accuracy']:.6f} |",
        f"| True Sufficient | {aggregate['true_sufficient']} |",
        f"| False Sufficient | {aggregate['false_sufficient']} |",
        f"| True Insufficient | {aggregate['true_insufficient']} |",
        f"| False Insufficient | {aggregate['false_insufficient']} |",
        f"| INVALID | {aggregate['invalid_count']} |",
        f"| Sufficient Precision | {aggregate['sufficient_precision']:.6f} |",
        f"| Sufficient Recall | {aggregate['sufficient_recall']:.6f} |",
        f"| Insufficient Precision | {aggregate['insufficient_precision']:.6f} |",
        f"| Insufficient Recall | {aggregate['insufficient_recall']:.6f} |",
        "",
        "## Component diagnostics",
        "",
        f"- Valid-case Gold / predicted components: {components['gold_component_count']} / {components['predicted_component_count']}",
        f"- Invalid cases excluded from component confusion: {components['invalid_case_count']}",
        f"- Status accuracy: {components['fields']['status_correct']['correct']}/{components['fields']['status_correct']['n']} ({components['fields']['status_correct']['accuracy']:.6f})",
        f"- Full component accuracy: {components['fields']['fully_correct']['correct']}/{components['fields']['fully_correct']['n']} ({components['fields']['fully_correct']['accuracy']:.6f})",
        f"- Exact component-count cases: {components['component_count_exact_match']['correct']}/{components['component_count_exact_match']['n']}",
        "",
        "## Failures",
        "",
    ]
    if not failures:
        lines.extend(["No verdict-level failures.", ""])
    for item in failures:
        lines.extend(
            [
                f"- `{item['case_id']}`: `{item['failure_type']}` "
                f"({item['gold_verdict']} -> {item['predicted_verdict']})",
            ]
        )
    lines.extend(
        [
            "",
            "## API usage and latency",
            "",
            f"- Provider request count: {performance['request_count']}",
            f"- Valid outputs / validation failures: {performance['valid_output_count']} / {performance['validation_failure_count']}",
            f"- Provider retry count: {performance['provider_retry_count']}",
            f"- Tokens (input / cached / output / reasoning / total): {performance['tokens']['input']} / {performance['tokens']['cached_input']} / {performance['tokens']['output']} / {performance['tokens']['reasoning']} / {performance['tokens']['total']}",
            f"- Estimated cost: ${performance['estimated_cost_usd']:.9f}",
            f"- Latency mean / median / p95 / max: {performance['latency_ms']['mean']:.3f} / {performance['latency_ms']['median']:.3f} / {performance['latency_ms']['p95']:.3f} / {performance['latency_ms']['max']:.3f} ms",
            "",
            "## Controls",
            "",
            "No prompt, schema, finalizer, config, Gold, held-out input, or supplied evidence changed after results. No retry, tuning, query rewrite, or Recovery Agent was run.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_failure_analysis(
    metrics: Mapping[str, Any], components: Mapping[str, Any]
) -> str:
    failures = [item for item in metrics["per_case"] if not item["correct"]]
    lines = [
        "# Grader v2.1 Baseline — Failure Analysis",
        "",
        "> Generated after all one-pass predictions were fixed.",
        "",
        f"- Incorrect cases: {len(failures)} / 12",
        f"- False Sufficient: {metrics['aggregate']['false_sufficient']}",
        f"- False Insufficient: {metrics['aggregate']['false_insufficient']}",
        f"- Invalid: {metrics['aggregate']['invalid_count']}",
        f"- Component errors: {len(components['errors'])}",
        "",
    ]
    for item in failures:
        component_errors = [
            error for error in components["errors"] if error["case_id"] == item["case_id"]
        ]
        lines.extend(
            [
                f"## {item['case_id']}",
                "",
                f"- Question: {item['question']}",
                f"- Gold / prediction: `{item['gold_verdict']}` / `{item['predicted_verdict']}`",
                f"- Failure type: `{item['failure_type']}`",
                f"- Gold / predicted reason: `{item['gold_insufficiency_reason']}` / `{item['predicted_insufficiency_reason']}`",
                *(
                    [
                        f"- Validation stage: `{item.get('validation_stage')}`",
                        f"- Validation error: `{item.get('validation_error')}`",
                    ]
                    if item.get("prediction_status") == "invalid"
                    else []
                ),
                "- Component errors: "
                + (
                    "; ".join(
                        f"{error['component_id']} status {error['gold_status']} -> {error['predicted_status']}"
                        for error in component_errors
                    )
                    or "none"
                ),
                "",
            ]
        )
    if not failures:
        lines.extend(["No verdict-level failures.", ""])
    lines.extend(["No failure was used to tune or rerun the baseline.", ""])
    return "\n".join(lines)


def _write_immutable_baseline(
    *,
    output_dir: Path,
    request_attempts: Sequence[Mapping[str, Any]],
    raw_provider_outputs: Sequence[Mapping[str, Any]],
    grader_inputs: Sequence[Mapping[str, Any]],
    grader_outputs: Sequence[Mapping[str, Any]],
    validation_failures: Sequence[Mapping[str, Any]],
    operational_failures: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    components: Mapping[str, Any],
    performance: Mapping[str, Any],
    reproducibility: Mapping[str, Any],
    config_path: Path,
    prompt_path: Path,
    contract_path: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite immutable baseline: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / f".{output_dir.name}-staging-{uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        jsonl_artifacts = {
            "request_attempts.jsonl": request_attempts,
            "raw_provider_outputs.jsonl": raw_provider_outputs,
            "grader_inputs.jsonl": grader_inputs,
            "grader_outputs.jsonl": grader_outputs,
            "case_results.jsonl": metrics["per_case"],
            "validation_failures.jsonl": validation_failures,
            "operational_failures.jsonl": operational_failures,
        }
        for name, records in jsonl_artifacts.items():
            with (staging / name).open("w", encoding="utf-8", newline="\n") as handle:
                for item in records:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        for name, value in (
            ("metrics.json", metrics),
            ("component_diagnostics.json", components),
            ("performance.json", performance),
            ("reproducibility.json", reproducibility),
        ):
            (staging / name).write_text(_json_text(value), encoding="utf-8")
        shutil.copyfile(config_path, staging / "config_snapshot.json")
        shutil.copyfile(prompt_path, staging / "prompt.md")
        shutil.copyfile(contract_path, staging / "v21_contracts.py")
        (staging / "REPORT.md").write_text(
            _build_report(metrics, components, performance, reproducibility),
            encoding="utf-8",
        )
        (staging / "FAILURE_ANALYSIS.md").write_text(
            _build_failure_analysis(metrics, components), encoding="utf-8"
        )
        artifact_names = tuple(jsonl_artifacts) + (
            "metrics.json",
            "component_diagnostics.json",
            "performance.json",
            "reproducibility.json",
            "config_snapshot.json",
            "prompt.md",
            "v21_contracts.py",
            "REPORT.md",
            "FAILURE_ANALYSIS.md",
        )
        manifest = {
            "grader_version": BASELINE_VERSION,
            "status": "frozen",
            "configuration_status": "preregistered_first_evaluation",
            "created_at_utc": reproducibility["created_at_utc"],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(staging / name),
                    "bytes": (staging / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite grader_v2_1_baseline; preserve this first result."
            ),
        }
        (staging / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        shutil.copytree(staging, output_dir)
        shutil.rmtree(staging)
        return manifest
    except BaseException:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if staging.exists():
            shutil.rmtree(staging)
        raise


def validate_frozen_v21_baseline(output_dir: str | Path) -> dict[str, Any]:
    directory = Path(output_dir).resolve()
    manifest = read_json(directory / "manifest.json")
    if manifest.get("grader_version") != BASELINE_VERSION or manifest.get("status") != "frozen":
        raise RuntimeError("Unexpected Grader v2.1 baseline manifest")
    for artifact in manifest["artifacts"]:
        if sha256_file(directory / artifact["path"]) != artifact["sha256"]:
            raise RuntimeError(f"Frozen baseline artifact changed: {artifact['path']}")
    performance = read_json(directory / "performance.json")
    if not (
        performance["request_count"] == 12
        and performance["valid_output_count"]
        + performance["validation_failure_count"]
        == 12
        and performance["provider_retry_count"] == 0
        and performance["operational_failure_count"] == 0
    ):
        raise RuntimeError("Frozen baseline does not represent 12 one-pass calls")
    return {
        "directory": directory,
        "manifest": manifest,
        "manifest_sha256": sha256_file(directory / "manifest.json"),
    }


def run_grader_v21_baseline(
    project_root: str | Path = PROJECT_ROOT,
    *,
    output_dir: str | Path,
    backend: Any | None = None,
    frozen_dir: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite immutable baseline: {output}")
    loaded = load_frozen_v21_model_inputs(root, frozen_dir=frozen_dir)
    frozen = loaded["frozen"]
    grader_inputs = loaded["grader_inputs"]
    config_path = root / "config/grader_v2_1.json"
    config, _, prompt_path = load_grader_v21_config(config_path)
    checkpoint_dir = output.parent / f".{output.name}_checkpoint"
    checkpoint_metadata = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "grader_version": BASELINE_VERSION,
        "model": config["model"],
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "heldout_manifest_sha256": frozen["manifest_sha256"],
        "input_bundle_sha256": frozen["manifest"]["model_input_bundle_sha256"],
        "case_count": 12,
    }
    (
        attempts,
        initial_raw_responses,
        initial_outputs,
        prior_validation_failures,
        prior_operational_errors,
    ) = _load_or_create_checkpoint(
        checkpoint_dir=checkpoint_dir,
        grader_inputs=grader_inputs,
        expected_metadata=checkpoint_metadata,
    )
    grader = backend or OpenAIGraderV21Backend(config_path=config_path)
    pacing_seconds = (
        float(config["minimum_request_start_interval_seconds"])
        if isinstance(grader, OpenAIGraderV21Backend)
        else 0.0
    )
    outputs, validation_failures = produce_v21_grades(
        grader_inputs,
        grader,
        checkpoint_dir=checkpoint_dir,
        initial_attempts=attempts,
        initial_outputs=initial_outputs,
        initial_validation_failures=prior_validation_failures,
        min_request_interval_seconds=pacing_seconds,
    )
    paths = _checkpoint_paths(checkpoint_dir)
    final_attempts = read_jsonl(paths["attempts"])
    raw_provider_outputs = read_jsonl(paths["raw_responses"])
    operational_failures = [
        item
        for item in read_jsonl(paths["errors"])
        if item.get("error_classification") == "provider_operational"
    ]
    if (
        len(final_attempts) != 12
        or len(outputs) + len(validation_failures) != 12
        or operational_failures
        or prior_operational_errors
    ):
        raise RuntimeError(
            "Grader v2.1 did not resolve exactly one first-pass outcome per case"
        )

    case_results = build_v21_case_results(
        frozen_dir=frozen["directory"],
        grader_inputs=grader_inputs,
        grader_outputs=outputs,
        validation_failures=validation_failures,
    )
    metrics = _build_metrics(case_results)
    components = build_component_diagnostics(case_results)
    calls = [item["grader_metrics"] for item in outputs]
    calls.extend(
        {
            "model": config["model"],
            "response_id": item.get("response_id"),
            "latency_ms": item.get("latency_ms", 0.0),
            "usage": item.get("usage", {}),
            "provider_retry_count": item.get("provider_retry_count", 0),
        }
        for item in validation_failures
    )
    usages = [item["usage"] for item in calls]
    performance = {
        "request_count": len(final_attempts),
        "successful_request_count": len(outputs),
        "valid_output_count": len(outputs),
        "validation_failure_count": len(validation_failures),
        "invalid_count": len(validation_failures),
        "provider_retry_count": sum(int(item["provider_retry_count"]) for item in calls),
        "operational_failure_count": len(operational_failures),
        "latency_ms": _distribution([float(item["latency_ms"]) for item in calls]),
        "tokens": {
            "input": sum(int(item.get("input_tokens", 0)) for item in usages),
            "cached_input": sum(int(item.get("cached_input_tokens", 0)) for item in usages),
            "output": sum(int(item.get("output_tokens", 0)) for item in usages),
            "reasoning": sum(int(item.get("reasoning_tokens", 0)) for item in usages),
            "total": sum(int(item.get("total_tokens", 0)) for item in usages),
        },
        "estimated_cost_usd": sum(float(item.get("estimated_cost_usd", 0.0)) for item in usages),
        "total_request_pacing_wait_ms": sum(
            float(item["request_pacing_wait_ms"]) for item in outputs
        )
        + sum(
            float(item.get("request_pacing_wait_ms", 0.0))
            for item in validation_failures
        ),
        "usage_unavailable_case_count": sum(
            not bool(item.get("usage")) for item in validation_failures
        ),
    }
    reproduction = {
        "grader_version": BASELINE_VERSION,
        "status": "preregistered_first_evaluation",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": {
            "provider": config["provider"],
            "api": config["api"],
            "name": config["model"],
            "temperature": config["temperature"],
            "reasoning_effort": config["reasoning_effort"],
            "verbosity": config["verbosity"],
            "max_output_tokens": config["max_output_tokens"],
            "store": config["store"],
            "service_tier": config["service_tier"],
            "sdk_max_retries": config["max_retries"],
            "minimum_request_start_interval_seconds": pacing_seconds,
        },
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "contract_finalizer_sha256": sha256_file(root / "src/grading/v21_contracts.py"),
        "provider_sha256": sha256_file(root / "src/grading/provider_v21.py"),
        "evaluation_sha256": sha256_file(root / "src/grading/v21_baseline.py"),
        "heldout_manifest_sha256": frozen["manifest_sha256"],
        "heldout_artifact_hashes": {
            item["path"]: item["sha256"] for item in frozen["manifest"]["artifacts"]
        },
        "input_bundle_sha256": frozen["manifest"]["model_input_bundle_sha256"],
        "case_count": 12,
        "grader_input_fields": MODEL_INPUT_FIELDS,
        "harness_provenance": {
            "patch_scope": "observability_and_checkpoint_resume_only",
            "patch_applied_after_case_id": "V21H-LIT-001",
            "grading_semantics_changed": False,
            "frozen_evaluation_inputs_changed": False,
            "v21h_lit_001_re_requested": False,
            "v21h_lit_001_outcome": (
                MODEL_OUTPUT_GROUNDING_VALIDATION_FAILURE
            ),
            "v21h_lit_001_prediction_status": "invalid",
        },
        "request_accounting": {
            "one_request_per_case": True,
            "request_attempt_count": len(final_attempts),
            "resumed_attempt_count": len(attempts),
            "resumed_successful_output_count": len(initial_outputs),
            "resumed_validation_failure_count": len(
                prior_validation_failures
            ),
            "new_successful_output_count": len(outputs) - len(initial_outputs),
            "new_validation_failure_count": len(validation_failures)
            - len(prior_validation_failures),
            "raw_provider_output_count": len(raw_provider_outputs),
            "legacy_untraced_response_count": len(final_attempts)
            - len(raw_provider_outputs),
            "automatic_provider_retry": False,
        },
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "pydantic_version": importlib.metadata.version("pydantic"),
            "openai_version": importlib.metadata.version("openai"),
            "httpx2_version": importlib.metadata.version("httpx2"),
            "brotli_version": importlib.metadata.version("Brotli"),
        },
        "controls": {
            "gold_exposed_to_grader": False,
            "case_id_exposed_to_grader": False,
            "gold_loaded_after_all_predictions": True,
            "confidence_threshold_enabled": False,
            "post_result_tuning": False,
            "query_rewrite": False,
            "retry_loop": False,
            "recovery_agent": False,
        },
        "performance": performance,
    }
    manifest = _write_immutable_baseline(
        output_dir=output,
        request_attempts=final_attempts,
        raw_provider_outputs=raw_provider_outputs,
        grader_inputs=grader_inputs,
        grader_outputs=outputs,
        validation_failures=validation_failures,
        operational_failures=operational_failures,
        metrics=metrics,
        components=components,
        performance=performance,
        reproducibility=reproduction,
        config_path=config_path,
        prompt_path=prompt_path,
        contract_path=root / "src/grading/v21_contracts.py",
    )
    metadata = read_json(paths["metadata"])
    metadata.update(
        {
            "status": "finalized",
            "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
            "baseline_manifest_sha256": sha256_file(output / "manifest.json"),
        }
    )
    paths["metadata"].write_text(_json_text(metadata), encoding="utf-8")
    validate_frozen_v21_heldout(root, frozen_dir=frozen["directory"])
    validate_frozen_v21_baseline(output)
    return manifest, metrics
