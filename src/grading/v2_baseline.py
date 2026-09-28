"""Immutable first evaluation of the frozen Grader v2 held-out set."""

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

from .contracts import GraderCallResult
from .evaluation import evaluate_grader
from .provider_v2 import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_GRADER_VERSION,
    EXPECTED_PROMPT_SHA256,
    OpenAIGraderV2Backend,
    load_grader_v2_config,
)
from .v2_contracts import EvidenceGradeV2, GraderV2Input
from .v2_freeze import DEFAULT_FROZEN_DIR, validate_frozen_heldout
from .v2_review import MODEL_INPUT_FIELDS, PROJECT_ROOT, read_json, read_jsonl, sha256_file


BASELINE_VERSION = "grader_v2_baseline"
CHECKPOINT_VERSION = "grader_v2_baseline_checkpoint_v1"
COMPONENT_FIELDS = (
    "question_polarity",
    "surface_proposition_resolution",
    "relevant_evidence_present",
    "answer_to_literature_question_supported",
    "limitation_or_context_supported",
    "population_scope",
    "outcome_scope",
    "terminology_scope",
    "multi_evidence_status",
)
SLICE_TAGS = {
    "polarity_sensitive": "polarity_sensitive",
    "limitation_sensitive": "limitation_sensitive",
    "population_mismatch": "population_mismatch",
    "bounded_negative": "bounded_negative",
    "multi_evidence_incomplete": "multi_evidence_incomplete",
    "missing_main_claim": "missing_main_claim",
    "outcome_mismatch": "outcome_mismatch",
    "no_relevant_evidence": "no_relevant_evidence",
}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _stable_sha256(value: Any) -> str:
    import hashlib

    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _distribution(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    ordered = sorted(float(value) for value in values)
    return {
        "mean": mean(ordered),
        "median": median(ordered),
        "p95": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        "min": ordered[0],
        "max": ordered[-1],
    }


def load_frozen_v2_model_inputs(
    project_root: str | Path = PROJECT_ROOT,
    *,
    frozen_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    frozen = validate_frozen_heldout(root, frozen_dir=frozen_dir)
    directory = frozen["directory"]
    records = read_jsonl(directory / "model_inputs.jsonl")
    if len(records) != 14:
        raise RuntimeError("Frozen Grader v2 held-out set must contain 14 cases")
    for item in records:
        if item.get("grader_input_fields") != MODEL_INPUT_FIELDS:
            raise RuntimeError("Frozen Grader v2 input field contract changed")
        GraderV2Input.model_validate(item["grader_input"])
        if _stable_sha256(item["grader_input"]) != item["input_sha256"]:
            raise RuntimeError(f"Frozen model input hash changed: {item['case_id']}")
    if _stable_sha256(records) != frozen["manifest"]["model_input_bundle_sha256"]:
        raise RuntimeError("Frozen model-input bundle hash changed")
    return {"root": root, "frozen": frozen, "grader_inputs": records}


def _checkpoint_paths(checkpoint_dir: Path) -> tuple[Path, Path, Path]:
    return (
        checkpoint_dir / "metadata.json",
        checkpoint_dir / "grader_outputs.jsonl",
        checkpoint_dir / "operational_failures.jsonl",
    )


def load_or_create_checkpoint(
    *,
    checkpoint_dir: Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    expected_metadata: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metadata_path, outputs_path, errors_path = _checkpoint_paths(checkpoint_dir)
    if checkpoint_dir.exists():
        metadata = read_json(metadata_path)
        for key, expected in expected_metadata.items():
            if metadata.get(key) != expected:
                raise RuntimeError(f"Grader v2 checkpoint changed: {key}")
        if metadata.get("status") not in {"in_progress", "finalized"}:
            raise RuntimeError("Grader v2 checkpoint status is invalid")
    else:
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        metadata = {
            **dict(expected_metadata),
            "status": "in_progress",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "controls": {
                "gold_loaded_before_all_predictions": False,
                "automatic_provider_retry": False,
                "post_result_tuning": False,
            },
        }
        metadata_path.write_text(_json_text(metadata), encoding="utf-8")
        outputs_path.touch()
        errors_path.touch()
    outputs = read_jsonl(outputs_path)
    errors = read_jsonl(errors_path)
    if len(outputs) > len(grader_inputs):
        raise RuntimeError("Grader v2 checkpoint has too many outputs")
    for index, output in enumerate(outputs):
        expected = grader_inputs[index]
        if output.get("case_id") != expected["case_id"]:
            raise RuntimeError("Grader v2 checkpoint is not a contiguous prefix")
        if output.get("input_sha256") != expected["input_sha256"]:
            raise RuntimeError("Grader v2 checkpoint input hash changed")
        EvidenceGradeV2.model_validate(output["validated_grade"])
    return outputs, errors


def _append_jsonl(path: Path, item: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _is_operational_error(error: str) -> bool:
    return any(
        name in error
        for name in (
            "APIConnectionError",
            "APITimeoutError",
            "RateLimitError",
            "InternalServerError",
            "ServiceUnavailableError",
        )
    )


def produce_v2_grades(
    grader_inputs: Sequence[Mapping[str, Any]],
    backend: Any,
    *,
    checkpoint_dir: Path,
    initial_outputs: Sequence[Mapping[str, Any]] = (),
    min_request_interval_seconds: float = 0.0,
    clock: Callable[[], float] = perf_counter,
    sleep_fn: Callable[[float], None] = sleep,
) -> list[dict[str, Any]]:
    outputs = [dict(item) for item in initial_outputs]
    _, outputs_path, errors_path = _checkpoint_paths(checkpoint_dir)
    if outputs:
        print(f"Resuming from checkpoint: {len(outputs)}/14 cases preserved", flush=True)
    last_request_started_at: float | None = None
    for index in range(len(outputs), len(grader_inputs)):
        item = grader_inputs[index]
        case_id = str(item["case_id"])
        wait_seconds = 0.0
        if last_request_started_at is not None and min_request_interval_seconds > 0:
            wait_seconds = max(
                0.0,
                min_request_interval_seconds - (clock() - last_request_started_at),
            )
            if wait_seconds:
                print(f"Rate-limit pacing before {case_id}: {wait_seconds:.2f}s", flush=True)
                sleep_fn(wait_seconds)
        last_request_started_at = clock()
        print(f"Grading {index + 1:02d}/14: {case_id}", flush=True)
        call: GraderCallResult = backend.invoke(
            GraderV2Input.model_validate(item["grader_input"])
        )
        if call.error or call.raw_grade is None:
            error = call.error or "empty typed grade"
            _append_jsonl(
                errors_path,
                {
                    "case_id": case_id,
                    "input_sha256": item["input_sha256"],
                    "occurred_at_utc": datetime.now(timezone.utc).isoformat(),
                    "error_classification": (
                        "provider_operational" if _is_operational_error(error) else "typed_output_or_validation"
                    ),
                    "error": error,
                    "latency_ms": call.latency_ms,
                    "provider_retry_count": 0,
                },
            )
            raise RuntimeError(
                f"Grader v2 first evaluation stopped at {case_id}: {error}"
            )
        grade = EvidenceGradeV2.model_validate(call.raw_grade)
        output = {
            "case_id": case_id,
            "input_sha256": item["input_sha256"],
            "grader_input_fields": MODEL_INPUT_FIELDS,
            "raw_grade": call.raw_grade,
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
        _append_jsonl(outputs_path, output)
    return outputs


def build_v2_case_results(
    *,
    frozen_dir: Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    grader_outputs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Load Gold only after every first-pass output is fixed."""

    inputs = read_json(frozen_dir / "heldout_inputs.json")
    gold = read_json(frozen_dir / "heldout_gold.json")
    input_by_id = {item["case_id"]: item for item in inputs["cases"]}
    gold_by_id = {item["case_id"]: item for item in gold["cases"]}
    if not (len(grader_inputs) == len(grader_outputs) == len(input_by_id) == len(gold_by_id) == 14):
        raise RuntimeError("Grader v2 evaluation alignment must contain 14 cases")
    results: list[dict[str, Any]] = []
    for input_record, output in zip(grader_inputs, grader_outputs, strict=True):
        case_id = str(input_record["case_id"])
        if output["case_id"] != case_id or case_id not in gold_by_id:
            raise RuntimeError("Grader v2 case order changed")
        case = input_by_id[case_id]
        gold_case = gold_by_id[case_id]
        prediction = EvidenceGradeV2.model_validate(output["validated_grade"])
        predicted = prediction.model_dump(mode="json")
        expected = dict(gold_case["expected_components"])
        component_comparison = {
            field: {
                "gold": expected[field],
                "predicted": predicted[field],
                "correct": expected[field] == predicted[field],
            }
            for field in COMPONENT_FIELDS
        }
        gold_verdict = gold_case["proposed_verdict"]
        predicted_verdict = prediction.verdict.value
        results.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "diagnostic_tags": case["diagnostic_tags"],
                "question": case["question"],
                "gold_verdict": gold_verdict,
                "predicted_verdict": predicted_verdict,
                "correct": gold_verdict == predicted_verdict,
                "reason_code": prediction.insufficiency_reason.value,
                "gold_insufficiency_reason": gold_case["proposed_insufficiency_reason"],
                "grader_reason": prediction.reason,
                "confidence": prediction.confidence,
                "component_comparison": component_comparison,
                "component_error_count": sum(
                    not value["correct"] for value in component_comparison.values()
                ),
                "retrieved_evidence_ids": [
                    evidence["chunk_id"]
                    for evidence in input_record["grader_input"]["retrieved_evidence"]
                ],
            }
        )
    return results


def _subset_metrics(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    evaluated = evaluate_grader(items)
    aggregate = evaluated["aggregate"]
    return {
        "n": aggregate["case_count"],
        "correct": aggregate["true_sufficient"] + aggregate["true_insufficient"],
        "accuracy": aggregate["accuracy"],
        "true_sufficient": aggregate["true_sufficient"],
        "false_sufficient": aggregate["false_sufficient"],
        "true_insufficient": aggregate["true_insufficient"],
        "false_insufficient": aggregate["false_insufficient"],
    }


def build_component_diagnostics(case_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for field in COMPONENT_FIELDS:
        comparisons = [item["component_comparison"][field] for item in case_results]
        pairs = Counter(
            f"{comparison['gold']} -> {comparison['predicted']}"
            for comparison in comparisons
        )
        correct = sum(comparison["correct"] for comparison in comparisons)
        fields[field] = {
            "n": len(comparisons),
            "correct": correct,
            "accuracy": correct / len(comparisons) if comparisons else 0.0,
            "gold_to_prediction": dict(sorted(pairs.items())),
        }
        for item in case_results:
            comparison = item["component_comparison"][field]
            if not comparison["correct"]:
                errors.append(
                    {
                        "case_id": item["case_id"],
                        "field": field,
                        "gold": comparison["gold"],
                        "predicted": comparison["predicted"],
                    }
                )
    total = len(case_results) * len(COMPONENT_FIELDS)
    return {
        "fields": fields,
        "overall": {
            "n": total,
            "correct": total - len(errors),
            "accuracy": (total - len(errors)) / total if total else 0.0,
        },
        "errors": errors,
    }


def build_v2_metrics(case_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    metrics = evaluate_grader(case_results)
    metrics["category"] = {
        category: _subset_metrics(
            [item for item in case_results if item["category"] == category]
        )
        for category in ("literature_only", "hybrid")
    }
    slices = {
        name: _subset_metrics(
            [item for item in case_results if tag in item["diagnostic_tags"]]
        )
        for name, tag in SLICE_TAGS.items()
    }
    slices["hybrid_literature_sufficient"] = _subset_metrics(
        [
            item
            for item in case_results
            if item["category"] == "hybrid"
            and "literature_sufficient" in item["diagnostic_tags"]
        ]
    )
    slices["hybrid_literature_insufficient"] = _subset_metrics(
        [
            item
            for item in case_results
            if item["category"] == "hybrid"
            and "literature_insufficient" in item["diagnostic_tags"]
        ]
    )
    metrics["diagnostic_slices"] = slices
    metrics["per_case"] = list(case_results)
    return metrics


def _mean_confidence(items: Sequence[dict[str, Any]]) -> float | None:
    return mean(float(item["confidence"]) for item in items) if items else None


def build_confidence_diagnostics(case_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "correct_mean_confidence": _mean_confidence(
            [item for item in case_results if item["correct"]]
        ),
        "incorrect_mean_confidence": _mean_confidence(
            [item for item in case_results if not item["correct"]]
        ),
        "false_sufficient_mean_confidence": _mean_confidence(
            [
                item
                for item in case_results
                if item["gold_verdict"] == "insufficient"
                and item["predicted_verdict"] == "sufficient"
            ]
        ),
        "false_insufficient_mean_confidence": _mean_confidence(
            [
                item
                for item in case_results
                if item["gold_verdict"] == "sufficient"
                and item["predicted_verdict"] == "insufficient"
            ]
        ),
        "decision_threshold_used": False,
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"


def _build_report(
    metrics: Mapping[str, Any],
    components: Mapping[str, Any],
    confidence: Mapping[str, Any],
    performance: Mapping[str, Any],
    reproduction: Mapping[str, Any],
) -> str:
    aggregate = metrics["aggregate"]
    lines = [
        "# Grader v2 Baseline — Immutable First Held-out Evaluation",
        "",
        "> Frozen first result. No result-driven tuning or recovery was performed.",
        "",
        "## Configuration",
        "",
        f"- Model: `{reproduction['model']['name']}`",
        f"- Prompt SHA-256: `{reproduction['prompt_sha256']}`",
        f"- Config SHA-256: `{reproduction['config_sha256']}`",
        f"- Frozen held-out manifest SHA-256: `{reproduction['heldout_manifest_sha256']}`",
        "- Gold was loaded only after all 14 first-pass predictions were fixed.",
        "- Confidence was diagnostic only and never changed a verdict.",
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
        f"| Sufficient Precision | {aggregate['sufficient_precision']:.6f} |",
        f"| Sufficient Recall | {aggregate['sufficient_recall']:.6f} |",
        f"| Insufficient Precision | {aggregate['insufficient_precision']:.6f} |",
        f"| Insufficient Recall | {aggregate['insufficient_recall']:.6f} |",
        f"| False Sufficient Rate | {aggregate['false_sufficient_rate']:.6f} |",
        f"| False Insufficient Rate | {aggregate['false_insufficient_rate']:.6f} |",
        "",
        "## Category accuracy",
        "",
        "| Category | N | Correct | Accuracy |",
        "|---|---:|---:|---:|",
    ]
    for name, value in metrics["category"].items():
        lines.append(f"| {name} | {value['n']} | {value['correct']} | {value['accuracy']:.6f} |")
    lines.extend(["", "## Diagnostic slices", "", "| Slice | N | Correct | Accuracy |", "|---|---:|---:|---:|"])
    for name, value in metrics["diagnostic_slices"].items():
        lines.append(f"| {name} | {value['n']} | {value['correct']} | {value['accuracy']:.6f} |")
    lines.extend(
        [
            "",
            "Small-N slices are diagnostic only and must not be generalized.",
            "",
            "## Component diagnostics",
            "",
            f"- Overall: {components['overall']['correct']}/{components['overall']['n']} ({components['overall']['accuracy']:.6f})",
            "",
            "| Component | Correct / N | Accuracy |",
            "|---|---:|---:|",
        ]
    )
    for name, value in components["fields"].items():
        lines.append(f"| {name} | {value['correct']} / {value['n']} | {value['accuracy']:.6f} |")
    lines.extend(
        [
            "",
            "## Confidence diagnostics",
            "",
            f"- Correct mean: {_fmt(confidence['correct_mean_confidence'])}",
            f"- Incorrect mean: {_fmt(confidence['incorrect_mean_confidence'])}",
            f"- False Sufficient mean: {_fmt(confidence['false_sufficient_mean_confidence'])}",
            f"- False Insufficient mean: {_fmt(confidence['false_insufficient_mean_confidence'])}",
            "",
            "## API usage and latency",
            "",
            f"- Request count: {performance['request_count']}",
            f"- Provider retry count: {performance['provider_retry_count']}",
            f"- Operational failure count: {performance['operational_failure_count']}",
            f"- Input / cached / output / reasoning / total tokens: {performance['tokens']['input']} / {performance['tokens']['cached_input']} / {performance['tokens']['output']} / {performance['tokens']['reasoning']} / {performance['tokens']['total']}",
            f"- Estimated cost: ${performance['estimated_cost_usd']:.9f}",
            f"- Mean / median / p95 / max latency: {performance['latency_ms']['mean']:.3f} / {performance['latency_ms']['median']:.3f} / {performance['latency_ms']['p95']:.3f} / {performance['latency_ms']['max']:.3f} ms",
            f"- Total pacing wait: {performance['total_request_pacing_wait_ms']:.3f} ms",
            "",
            "## Grader v1 qualitative comparison boundary",
            "",
            "Grader v1 used a different 18-case retrieval-derived diagnostic set, while v2 uses this new 14-case controlled held-out set. Therefore no direct accuracy delta is claimed.",
            "",
            "The v1 failure analysis identified over-crediting of topically relevant evidence, polarity/limitation interpretation errors, and false insufficiency caused by treating user-log requirements as missing literature evidence in Hybrid questions. Grader v2 addresses these structurally through an answerability-first component checklist, explicit proposition resolution, explicit limitation/scope fields, and literature-only Hybrid channel separation. The held-out slice results above show whether those mechanisms succeeded on their corresponding controlled cases.",
            "",
            "## Controls",
            "",
            "No prompt, model, config, finalizer, Gold, held-out input, query, retrieval, or threshold changed after results were observed. No rewrite, retry loop, recovery agent, replan, answer generation, or abstention workflow was started.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_failure_analysis(
    metrics: Mapping[str, Any], components: Mapping[str, Any]
) -> str:
    failures = [item for item in metrics["per_case"] if not item["correct"]]
    lines = [
        "# Grader v2 Baseline — Failure Analysis",
        "",
        "> Generated only after all first-pass predictions were fixed.",
        "",
        f"- Incorrect cases: {len(failures)} / 14",
        f"- False Sufficient: {metrics['aggregate']['false_sufficient']}",
        f"- False Insufficient: {metrics['aggregate']['false_insufficient']}",
        f"- Component errors: {len(components['errors'])}",
        "",
    ]
    if not failures:
        lines.extend(["No verdict-level failures.", ""])
    for item in failures:
        case_component_errors = [
            error for error in components["errors"] if error["case_id"] == item["case_id"]
        ]
        lines.extend(
            [
                f"## {item['case_id']}",
                "",
                f"- Question: {item['question']}",
                f"- Gold / prediction: `{item['gold_verdict']}` / `{item['predicted_verdict']}`",
                f"- Gold / predicted reason: `{item['gold_insufficiency_reason']}` / `{item['reason_code']}`",
                f"- Confidence: {item['confidence']:.3f}",
                f"- Grader reason: {item['grader_reason']}",
                "- Component errors: "
                + (
                    "; ".join(
                        f"{error['field']} ({error['gold']} -> {error['predicted']})"
                        for error in case_component_errors
                    )
                    or "none"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Controls",
            "",
            "No failure was used to tune or rerun the baseline.",
            "",
        ]
    )
    return "\n".join(lines)


def write_immutable_v2_baseline(
    *,
    output_dir: Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    grader_outputs: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    components: Mapping[str, Any],
    confidence: Mapping[str, Any],
    performance: Mapping[str, Any],
    reproduction: Mapping[str, Any],
    operational_failures: Sequence[Mapping[str, Any]],
    config_path: Path,
    prompt_path: Path,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Grader v2 baseline: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / f".{output_dir.name}-staging-{uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        for name, records in (
            ("grader_inputs.jsonl", grader_inputs),
            ("grader_outputs.jsonl", grader_outputs),
            ("case_results.jsonl", metrics["per_case"]),
            ("operational_failures.jsonl", operational_failures),
        ):
            with (staging / name).open("w", encoding="utf-8", newline="\n") as handle:
                for item in records:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        for name, value in (
            ("metrics.json", metrics),
            ("component_diagnostics.json", components),
            ("confidence_diagnostics.json", confidence),
            ("performance.json", performance),
            ("reproducibility.json", reproduction),
        ):
            (staging / name).write_text(_json_text(value), encoding="utf-8")
        shutil.copyfile(config_path, staging / "config_snapshot.json")
        shutil.copyfile(prompt_path, staging / "prompt.md")
        (staging / "REPORT.md").write_text(
            _build_report(metrics, components, confidence, performance, reproduction),
            encoding="utf-8",
        )
        (staging / "FAILURE_ANALYSIS.md").write_text(
            _build_failure_analysis(metrics, components), encoding="utf-8"
        )
        artifact_names = (
            "grader_inputs.jsonl",
            "grader_outputs.jsonl",
            "case_results.jsonl",
            "operational_failures.jsonl",
            "metrics.json",
            "component_diagnostics.json",
            "confidence_diagnostics.json",
            "performance.json",
            "reproducibility.json",
            "config_snapshot.json",
            "prompt.md",
            "REPORT.md",
            "FAILURE_ANALYSIS.md",
        )
        manifest = {
            "grader_version": BASELINE_VERSION,
            "status": "frozen",
            "configuration_status": "preregistered_first_evaluation",
            "created_at_utc": reproduction["created_at_utc"],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(staging / name),
                    "bytes": (staging / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite grader_v2_baseline; preserve the first held-out result."
            ),
        }
        (staging / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        staging.replace(output_dir)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def run_grader_v2_baseline(
    project_root: str | Path = PROJECT_ROOT,
    *,
    output_dir: str | Path,
    backend: Any | None = None,
    frozen_dir: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Grader v2 baseline: {output}")
    loaded = load_frozen_v2_model_inputs(root, frozen_dir=frozen_dir)
    frozen = loaded["frozen"]
    grader_inputs = loaded["grader_inputs"]
    config, _, prompt_path = load_grader_v2_config(root / "config/grader_v2.json")
    checkpoint_dir = output.parent / f".{output.name}_checkpoint"
    checkpoint_metadata = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "grader_version": BASELINE_VERSION,
        "model": config["model"],
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "heldout_manifest_sha256": frozen["manifest_sha256"],
        "input_bundle_sha256": frozen["manifest"]["model_input_bundle_sha256"],
        "case_count": 14,
    }
    checkpoint_outputs, prior_errors = load_or_create_checkpoint(
        checkpoint_dir=checkpoint_dir,
        grader_inputs=grader_inputs,
        expected_metadata=checkpoint_metadata,
    )
    grader = backend or OpenAIGraderV2Backend(
        config_path=root / "config/grader_v2.json"
    )
    pacing_seconds = (
        float(config["minimum_request_start_interval_seconds"])
        if isinstance(grader, OpenAIGraderV2Backend)
        else 0.0
    )
    outputs = produce_v2_grades(
        grader_inputs,
        grader,
        checkpoint_dir=checkpoint_dir,
        initial_outputs=checkpoint_outputs,
        min_request_interval_seconds=pacing_seconds,
    )

    case_results = build_v2_case_results(
        frozen_dir=frozen["directory"],
        grader_inputs=grader_inputs,
        grader_outputs=outputs,
    )
    metrics = build_v2_metrics(case_results)
    components = build_component_diagnostics(case_results)
    confidence = build_confidence_diagnostics(case_results)
    _, _, errors_path = _checkpoint_paths(checkpoint_dir)
    operational_failures = read_jsonl(errors_path)
    calls = [item["grader_metrics"] for item in outputs]
    usages = [item["usage"] for item in calls]
    performance = {
        "request_count": len(outputs) + len(operational_failures),
        "successful_request_count": len(outputs),
        "provider_retry_count": sum(int(item["provider_retry_count"]) for item in calls),
        "operational_failure_count": sum(
            item["error_classification"] == "provider_operational"
            for item in operational_failures
        ),
        "typed_output_or_validation_failure_count": sum(
            item["error_classification"] == "typed_output_or_validation"
            for item in operational_failures
        ),
        "latency_ms": _distribution([float(item["latency_ms"]) for item in calls]),
        "tokens": {
            "input": sum(int(item.get("input_tokens", 0)) for item in usages),
            "cached_input": sum(int(item.get("cached_input_tokens", 0)) for item in usages),
            "output": sum(int(item.get("output_tokens", 0)) for item in usages),
            "reasoning": sum(int(item.get("reasoning_tokens", 0)) for item in usages),
            "total": sum(int(item.get("total_tokens", 0)) for item in usages),
        },
        "estimated_cost_usd": sum(float(item.get("estimated_cost_usd", 0.0)) for item in usages),
        "total_request_pacing_wait_ms": sum(float(item["request_pacing_wait_ms"]) for item in outputs),
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
        "contract_and_finalizer_sha256": sha256_file(root / "src/grading/v2_contracts.py"),
        "provider_sha256": sha256_file(root / "src/grading/provider_v2.py"),
        "evaluation_sha256": sha256_file(root / "src/grading/v2_baseline.py"),
        "heldout_manifest_sha256": frozen["manifest_sha256"],
        "heldout_artifact_hashes": {
            item["path"]: item["sha256"] for item in frozen["manifest"]["artifacts"]
        },
        "frozen_dependency_hashes": frozen["dependencies"],
        "input_bundle_sha256": frozen["manifest"]["model_input_bundle_sha256"],
        "case_count": 14,
        "grader_input_fields": MODEL_INPUT_FIELDS,
        "checkpoint": {
            "version": CHECKPOINT_VERSION,
            "resumed_case_count": len(checkpoint_outputs),
            "new_case_count": len(outputs) - len(checkpoint_outputs),
            "manual_resume_only": True,
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
            "category_exposed_to_grader": False,
            "diagnostic_tags_exposed_to_grader": False,
            "gold_loaded_after_all_predictions": True,
            "confidence_threshold_enabled": False,
            "post_result_tuning": False,
            "query_rewrite": False,
            "retry_loop": False,
            "re_retrieval": False,
            "replan": False,
            "recovery_agent": False,
            "abstention_workflow": False,
        },
        "performance": performance,
    }
    manifest = write_immutable_v2_baseline(
        output_dir=output,
        grader_inputs=grader_inputs,
        grader_outputs=outputs,
        metrics=metrics,
        components=components,
        confidence=confidence,
        performance=performance,
        reproduction=reproduction,
        operational_failures=operational_failures,
        config_path=root / "config/grader_v2.json",
        prompt_path=prompt_path,
    )
    metadata_path, _, _ = _checkpoint_paths(checkpoint_dir)
    metadata = read_json(metadata_path)
    metadata.update(
        {
            "status": "finalized",
            "finalized_at_utc": datetime.now(timezone.utc).isoformat(),
            "baseline_manifest_sha256": sha256_file(output / "manifest.json"),
        }
    )
    metadata_path.write_text(_json_text(metadata), encoding="utf-8")
    validate_frozen_heldout(root, frozen_dir=frozen["directory"])
    return manifest, metrics

