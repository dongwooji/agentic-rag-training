"""One-shot Phase 11A evaluation and immutable grader_baseline_v1 artifacts."""

from __future__ import annotations

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

from src.evaluation.retrieval_metrics import complete_evidence_at_k

from .contracts import EvidenceGrade, GraderInput
from .evaluation import evaluate_grader
from .provider import (
    EXPECTED_CONFIG_SHA256,
    EXPECTED_GRADER_VERSION,
    EXPECTED_PROMPT_SHA256,
    GraderBackend,
    OpenAIGraderBackend,
    load_grader_config,
    sha256_file,
)


EVAL_DATASET_SHA256 = (
    "1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509"
)
EVAL_MANIFEST_SHA256 = (
    "4abd207041c4dd69df73e85e6d50b9288fe9f0d9c19f1ce33d06ae62e008d972"
)
HYBRID_MANIFEST_SHA256 = (
    "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
)
HYBRID_RESULTS_SHA256 = (
    "d97c2c8c2ff46d07dc2730586eec444fa4c62f98231710c891cbda4d586f6fbb"
)
CORPUS_CHUNKS_SHA256 = (
    "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177"
)
CORPUS_MANIFEST_SHA256 = (
    "84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66"
)
DENSE_MANIFEST_SHA256 = (
    "8edea5983d63e588515a5330c2107679357c73e977502f444eb9ee5d25b2170a"
)
ROUTER_MANIFEST_SHA256 = (
    "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
)
AGENT_MANIFEST_SHA256 = (
    "7e6a584b83134374e315af218660f7113481ec772c746810a8bc07fa86997cce"
)
CHECKPOINT_VERSION = "grader_baseline_v1_checkpoint_v1"
GRADER_INPUT_FIELDS = ["question", "grading_context", "retrieved_evidence"]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


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
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "mean": mean(ordered),
        "median": median(ordered),
        "p95": ordered[p95_index],
        "min": ordered[0],
        "max": ordered[-1],
    }


def _validate_manifest_artifacts(directory: Path, manifest: dict[str, Any]) -> None:
    if manifest.get("status") != "frozen":
        raise RuntimeError(f"Frozen manifest status changed: {directory}")
    for artifact in manifest.get("artifacts", []):
        path = directory / str(artifact["path"])
        if sha256_file(path) != artifact["sha256"]:
            raise RuntimeError(f"Frozen artifact changed: {path}")


def _validate_frozen_dependencies(root: Path) -> dict[str, Any]:
    eval_path = root / "data/evaluation/eval_dataset_v1.json"
    eval_manifest_path = root / "data/evaluation/eval_dataset_v1.manifest.json"
    hybrid_dir = root / "reports/baselines/hybrid_baseline_v1"
    hybrid_manifest_path = hybrid_dir / "manifest.json"
    hybrid_results_path = hybrid_dir / "hybrid_results.jsonl"
    chunks_path = root / "data/literature/processed/chunks.jsonl"
    corpus_manifest_path = root / "data/literature/manifests/corpus_v1.json"
    actual = {
        "eval_dataset_sha256": sha256_file(eval_path),
        "eval_manifest_sha256": sha256_file(eval_manifest_path),
        "hybrid_manifest_sha256": sha256_file(hybrid_manifest_path),
        "hybrid_results_sha256": sha256_file(hybrid_results_path),
        "corpus_chunks_sha256": sha256_file(chunks_path),
        "corpus_manifest_sha256": sha256_file(corpus_manifest_path),
    }
    expected = {
        "eval_dataset_sha256": EVAL_DATASET_SHA256,
        "eval_manifest_sha256": EVAL_MANIFEST_SHA256,
        "hybrid_manifest_sha256": HYBRID_MANIFEST_SHA256,
        "hybrid_results_sha256": HYBRID_RESULTS_SHA256,
        "corpus_chunks_sha256": CORPUS_CHUNKS_SHA256,
        "corpus_manifest_sha256": CORPUS_MANIFEST_SHA256,
    }
    if actual != expected:
        raise RuntimeError("Frozen Phase 11A input hash changed")
    hybrid_manifest = _read_json(hybrid_manifest_path)
    _validate_manifest_artifacts(hybrid_dir, hybrid_manifest)
    return {
        "eval_path": eval_path,
        "eval_manifest_path": eval_manifest_path,
        "hybrid_dir": hybrid_dir,
        "hybrid_manifest_path": hybrid_manifest_path,
        "hybrid_results_path": hybrid_results_path,
        "chunks_path": chunks_path,
        "corpus_manifest_path": corpus_manifest_path,
        **actual,
    }


def load_frozen_grader_inputs(project_root: str | Path) -> dict[str, Any]:
    """Build the exact no-Gold model inputs from frozen rankings and corpus."""

    root = Path(project_root).resolve()
    dependencies = _validate_frozen_dependencies(root)
    dataset = _read_json(dependencies["eval_path"])
    cases = [
        case
        for case in dataset["cases"]
        if case.get("gold", {}).get("literature_evidence_groups")
    ]
    if len(cases) != 18:
        raise RuntimeError("Phase 11A requires exactly 18 literature-bearing cases")

    rankings = _read_jsonl(dependencies["hybrid_results_path"])
    ranking_by_case = {item["case_id"]: item for item in rankings}
    if list(ranking_by_case) != [case["id"] for case in cases]:
        raise RuntimeError("Frozen Hybrid result order differs from eval case order")

    chunks = _read_jsonl(dependencies["chunks_path"])
    chunk_by_id = {item["chunk_id"]: item for item in chunks}
    if len(chunk_by_id) != 488:
        raise RuntimeError("Frozen literature corpus must contain 488 unique chunks")

    records: list[dict[str, Any]] = []
    for case in cases:
        ranking = ranking_by_case[case["id"]]
        retrieved = sorted(ranking["retrieved"], key=lambda item: item["rank"])
        if [item["rank"] for item in retrieved] != list(range(1, 11)):
            raise RuntimeError(f"Hybrid Top-10 ranks are invalid for {case['id']}")
        if len({item["chunk_id"] for item in retrieved}) != 10:
            raise RuntimeError(f"Hybrid Top-10 contains duplicates for {case['id']}")

        evidence: list[dict[str, Any]] = []
        for result in retrieved:
            chunk_id = str(result["chunk_id"])
            if chunk_id not in chunk_by_id:
                raise RuntimeError(f"Missing frozen corpus chunk: {chunk_id}")
            chunk = chunk_by_id[chunk_id]
            evidence.append(
                {
                    "chunk_id": chunk_id,
                    "paper_id": chunk["paper_id"],
                    "pmcid": chunk.get("pmcid"),
                    "title": chunk["title"],
                    "section": chunk["section"],
                    "year": chunk.get("year"),
                    "population": chunk.get("population"),
                    "study_type": chunk.get("study_type"),
                    "topics": list(chunk.get("topics", [])),
                    "text": chunk["text"],
                    "retrieval": {
                        "hybrid_rank": result["rank"],
                        "rrf_score": result["score"],
                        "dense_rank": result.get("dense_rank"),
                        "bm25_rank": result.get("bm25_rank"),
                    },
                }
            )
        grader_input = GraderInput.model_validate(
            {
                "question": case["question"],
                "grading_context": {},
                "retrieved_evidence": evidence,
            }
        )
        input_value = grader_input.model_dump(mode="json")
        records.append(
            {
                "case_id": case["id"],
                "input_sha256": _stable_sha256(input_value),
                "grader_input_fields": GRADER_INPUT_FIELDS,
                "grader_input": input_value,
            }
        )
    return {
        "root": root,
        "dataset": dataset,
        "cases": cases,
        "grader_inputs": records,
        "input_bundle_sha256": _stable_sha256(records),
        "dependencies": dependencies,
    }


def load_or_create_grade_checkpoint(
    *,
    checkpoint_dir: Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    expected_metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    metadata_path = checkpoint_dir / "metadata.json"
    outputs_path = checkpoint_dir / "grader_outputs.jsonl"
    if checkpoint_dir.exists():
        if not checkpoint_dir.is_dir() or not metadata_path.is_file():
            raise RuntimeError("Grader checkpoint is incomplete or not a directory")
        metadata = _read_json(metadata_path)
        for key, expected in expected_metadata.items():
            if metadata.get(key) != expected:
                raise RuntimeError(f"Grader checkpoint metadata changed: {key}")
        if metadata.get("status") not in {"in_progress", "finalized"}:
            raise RuntimeError("Grader checkpoint status is invalid")
    else:
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        metadata = {
            **dict(expected_metadata),
            "status": "in_progress",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "controls": {
                "gold_exposed_to_grader": False,
                "automatic_provider_retry": False,
                "phase11b_started": False,
            },
        }
        metadata_path.write_text(_json_text(metadata), encoding="utf-8")
        outputs_path.touch()

    outputs = _read_jsonl(outputs_path) if outputs_path.exists() else []
    if len(outputs) > len(grader_inputs):
        raise RuntimeError("Grader checkpoint contains too many cases")
    for index, item in enumerate(outputs):
        expected = grader_inputs[index]
        if item.get("case_id") != expected["case_id"]:
            raise RuntimeError("Grader checkpoint is not a contiguous case prefix")
        if item.get("input_sha256") != expected["input_sha256"]:
            raise RuntimeError("Grader checkpoint input hash changed")
        if item.get("grader_input_fields") != GRADER_INPUT_FIELDS:
            raise RuntimeError("Grader checkpoint input fields changed")
        if "validated_grade" not in item or "grader_metrics" not in item:
            raise RuntimeError("Grader checkpoint record is incomplete")
    return outputs


def append_grade_checkpoint(path: Path, item: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def finalize_grade_checkpoint(
    *, checkpoint_dir: Path, final_manifest_sha256: str
) -> None:
    metadata_path = checkpoint_dir / "metadata.json"
    metadata = _read_json(metadata_path)
    metadata["status"] = "finalized"
    metadata["finalized_at_utc"] = datetime.now(timezone.utc).isoformat()
    metadata["final_manifest_sha256"] = final_manifest_sha256
    metadata_path.write_text(_json_text(metadata), encoding="utf-8")


def produce_grades(
    grader_inputs: Sequence[Mapping[str, Any]],
    backend: GraderBackend,
    *,
    min_request_interval_seconds: float = 0.0,
    clock: Callable[[], float] = perf_counter,
    sleep_fn: Callable[[float], None] = sleep,
    initial_outputs: Sequence[Mapping[str, Any]] = (),
    checkpoint_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Generate all first-pass verdicts before any Gold label is applied."""

    outputs = [dict(item) for item in initial_outputs]
    if len(outputs) > len(grader_inputs):
        raise ValueError("initial_outputs cannot exceed the case count")
    if outputs:
        print(
            f"Resuming from checkpoint: {len(outputs)}/{len(grader_inputs)} "
            "cases preserved",
            flush=True,
        )
    last_request_started_at: float | None = None
    for zero_index in range(len(outputs), len(grader_inputs)):
        item = grader_inputs[zero_index]
        case_id = str(item["case_id"])
        pacing_wait_seconds = 0.0
        if last_request_started_at is not None and min_request_interval_seconds > 0:
            elapsed = clock() - last_request_started_at
            pacing_wait_seconds = max(0.0, min_request_interval_seconds - elapsed)
            if pacing_wait_seconds > 0:
                print(
                    f"Rate-limit pacing before {case_id}: "
                    f"{pacing_wait_seconds:.2f}s",
                    flush=True,
                )
                sleep_fn(pacing_wait_seconds)
        last_request_started_at = clock()
        print(
            f"Grading {zero_index + 1:02d}/{len(grader_inputs)}: {case_id}",
            flush=True,
        )
        call = backend.invoke(GraderInput.model_validate(item["grader_input"]))
        if call.error or call.raw_grade is None:
            raise RuntimeError(
                "Grader baseline aborted before freeze because the provider or "
                f"typed output failed at {case_id}: {call.error or 'empty grade'}"
            )
        grade = EvidenceGrade.model_validate(call.raw_grade)
        output = {
            "case_id": case_id,
            "input_sha256": item["input_sha256"],
            "grader_input_fields": GRADER_INPUT_FIELDS,
            "raw_grade": call.raw_grade,
            "validated_grade": grade.model_dump(mode="json"),
            "grader_metrics": {
                "model": call.model,
                "response_id": call.response_id,
                "latency_ms": call.latency_ms,
                "usage": call.usage.model_dump(mode="json"),
            },
            "request_pacing_wait_ms": pacing_wait_seconds * 1000,
        }
        outputs.append(output)
        if checkpoint_path is not None:
            append_grade_checkpoint(checkpoint_path, output)
    return outputs


def build_case_results(
    *,
    cases: Sequence[dict[str, Any]],
    grader_inputs: Sequence[Mapping[str, Any]],
    grader_outputs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply frozen Gold only after every first-pass prediction is fixed."""

    if not (len(cases) == len(grader_inputs) == len(grader_outputs) == 18):
        raise RuntimeError("Phase 11A case alignment must contain 18 records")
    results: list[dict[str, Any]] = []
    for case, input_record, output in zip(
        cases, grader_inputs, grader_outputs, strict=True
    ):
        if not (
            case["id"] == input_record["case_id"] == output["case_id"]
        ):
            raise RuntimeError("Phase 11A case order changed")
        retrieved_ids = [
            item["chunk_id"]
            for item in input_record["grader_input"]["retrieved_evidence"]
        ]
        gold_sufficient = bool(complete_evidence_at_k(case, retrieved_ids, 10))
        grade = EvidenceGrade.model_validate(output["validated_grade"])
        gold_verdict = "sufficient" if gold_sufficient else "insufficient"
        predicted_verdict = grade.verdict.value
        results.append(
            {
                "case_id": case["id"],
                "category": case["category"],
                "question": case["question"],
                "gold_verdict": gold_verdict,
                "predicted_verdict": predicted_verdict,
                "correct": gold_verdict == predicted_verdict,
                "reason_code": grade.reason_code.value,
                "missing_evidence_type": grade.missing_evidence_type.value,
                "grader_reason": grade.reason,
                "confidence": grade.confidence,
                "retrieved_evidence_ids": retrieved_ids,
            }
        )
    return results


def _format_ids(values: Sequence[str]) -> str:
    return ", ".join(f"`{item}`" for item in values) if values else "none"


def _build_report(
    metrics: Mapping[str, Any], reproduction: Mapping[str, Any]
) -> str:
    aggregate = metrics["aggregate"]
    performance = reproduction["performance"]
    model = reproduction["model"]
    lines = [
        "# Phase 11A Evidence Grader Validation — grader_baseline_v1",
        "",
        "> Immutable first evaluation of the preregistered Grader prompt/model/config.",
        "",
        "## Architecture and controls",
        "",
        "- Flow: frozen question + frozen Hybrid Top-10 → typed LLM Grader → offline Gold comparison",
        "- Scope: literature evidence only; structured-log evidence in hybrid questions is evaluated separately",
        "- Gold, case ID, category, answer criteria, limitations and evidence groups were not exposed to the Grader",
        "- No retrieval rerun, query rewrite, retry, replan, recovery, answer generation or abstention workflow",
        f"- Model: `{model['name']}`; temperature {model['temperature']}; reasoning `{model['reasoning_effort']}`",
        f"- Prompt SHA-256: `{reproduction['prompt_sha256']}`",
        f"- Config SHA-256: `{reproduction['config_sha256']}`",
        "",
        "## Confusion matrix",
        "",
        "| Gold \\ Prediction | Sufficient | Insufficient |",
        "|---|---:|---:|",
        f"| Sufficient | {aggregate['true_sufficient']} (True Sufficient) | {aggregate['false_insufficient']} (False Insufficient) |",
        f"| Insufficient | {aggregate['false_sufficient']} (False Sufficient) | {aggregate['true_insufficient']} (True Insufficient) |",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Accuracy | {aggregate['accuracy']:.6f} |",
        f"| Sufficient Precision | {aggregate['sufficient_precision']:.6f} |",
        f"| Sufficient Recall | {aggregate['sufficient_recall']:.6f} |",
        f"| Insufficient Precision | {aggregate['insufficient_precision']:.6f} |",
        f"| Insufficient Recall | {aggregate['insufficient_recall']:.6f} |",
        f"| False Sufficient Rate | {aggregate['false_sufficient_rate']:.6f} |",
        f"| False Insufficient Rate | {aggregate['false_insufficient_rate']:.6f} |",
        "",
        "False Sufficient is the primary safety risk for this baseline.",
        "",
        "## False Sufficient cases",
        "",
        _format_ids(metrics["false_sufficient_cases"]),
        "",
        "## False Insufficient cases",
        "",
        _format_ids(metrics["false_insufficient_cases"]),
        "",
        "## Case-level results",
        "",
        "| Case | Gold | Prediction | Correct | Reason code | Confidence | Retrieved IDs |",
        "|---|---|---|---|---|---:|---|",
    ]
    for item in metrics["per_case"]:
        ids = ", ".join(item["retrieved_evidence_ids"])
        lines.append(
            f"| `{item['case_id']}` | {item['gold_verdict']} | "
            f"{item['predicted_verdict']} | {'yes' if item['correct'] else 'no'} | "
            f"`{item['reason_code']}` | {item['confidence']:.3f} | {ids} |"
        )
    latency = performance["grader_latency_ms"]
    tokens = performance["tokens"]
    lines.extend(
        [
            "",
            "## Latency, token usage and estimated cost",
            "",
            f"- Mean / median / p95 latency: {latency['mean']:.3f} / {latency['median']:.3f} / {latency['p95']:.3f} ms",
            f"- Input / cached input / output / reasoning / total tokens: {tokens['input']} / {tokens['cached_input']} / {tokens['output']} / {tokens['reasoning']} / {tokens['total']}",
            f"- Estimated cost: ${performance['estimated_cost_usd']:.9f}",
            f"- Total request-pacing wait: {performance['total_request_pacing_wait_ms']:.3f} ms",
            "",
            "## Boundaries",
            "",
            "- Gold sufficiency is frozen Hybrid `CompleteEvidence@10` using required evidence-group any/all semantics.",
            "- This evaluates the Grader against the frozen contract; it does not evaluate generated answer quality.",
            "- Results were not used to alter prompt, model, configuration, retrieval, Gold, or corpus.",
            "- Phase 11B Evidence-Recovery Agent was not started.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_failure_analysis(metrics: Mapping[str, Any]) -> str:
    failed = [item for item in metrics["per_case"] if not item["correct"]]
    lines = [
        "# Evidence Grader v1 — Case-level Failure Analysis",
        "",
        "> Generated only after all 18 first-pass verdicts were fixed. No tuning followed.",
        "",
        f"- Incorrect cases: {len(failed)} / {metrics['aggregate']['case_count']}",
        f"- False Sufficient: {metrics['aggregate']['false_sufficient']}",
        f"- False Insufficient: {metrics['aggregate']['false_insufficient']}",
        "",
    ]
    for item in failed:
        lines.extend(
            [
                f"## {item['case_id']}",
                "",
                f"- Question: {item['question']}",
                f"- Gold: `{item['gold_verdict']}`",
                f"- Prediction: `{item['predicted_verdict']}`",
                f"- Reason code: `{item['reason_code']}`",
                f"- Missing evidence type: `{item['missing_evidence_type']}`",
                f"- Confidence: {item['confidence']:.3f}",
                f"- Grader reason: {item['grader_reason']}",
                f"- Retrieved evidence: {_format_ids(item['retrieved_evidence_ids'])}",
                "",
            ]
        )
    if not failed:
        lines.extend(["No incorrect cases.", ""])
    lines.extend(
        [
            "## Controls",
            "",
            "No failure was used to tune the Grader or to start query rewrite, retry, recovery, replan, or abstention.",
            "",
        ]
    )
    return "\n".join(lines)


def write_immutable_grader_artifacts(
    *,
    output_dir: str | Path,
    grader_inputs: Sequence[Mapping[str, Any]],
    grader_outputs: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    reproduction: Mapping[str, Any],
    config_path: Path,
    prompt_path: Path,
) -> dict[str, Any]:
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Grader baseline: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}-staging-{uuid4().hex}"
    staging.mkdir(exist_ok=False)
    try:
        for name, records in (
            ("grader_inputs.jsonl", grader_inputs),
            ("grader_outputs.jsonl", grader_outputs),
            ("case_results.jsonl", metrics["per_case"]),
        ):
            with (staging / name).open("w", encoding="utf-8", newline="\n") as handle:
                for item in records:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        (staging / "metrics.json").write_text(_json_text(metrics), encoding="utf-8")
        (staging / "reproducibility.json").write_text(
            _json_text(reproduction), encoding="utf-8"
        )
        shutil.copyfile(config_path, staging / "config_snapshot.json")
        shutil.copyfile(prompt_path, staging / "prompt.md")
        (staging / "REPORT.md").write_text(
            _build_report(metrics, reproduction), encoding="utf-8"
        )
        (staging / "FAILURE_ANALYSIS.md").write_text(
            _build_failure_analysis(metrics), encoding="utf-8"
        )
        artifact_names = (
            "grader_inputs.jsonl",
            "grader_outputs.jsonl",
            "case_results.jsonl",
            "metrics.json",
            "reproducibility.json",
            "config_snapshot.json",
            "prompt.md",
            "REPORT.md",
            "FAILURE_ANALYSIS.md",
        )
        manifest = {
            "grader_version": EXPECTED_GRADER_VERSION,
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
                "Never overwrite grader_baseline_v1; use a new version for any later prompt or model."
            ),
        }
        (staging / "manifest.json").write_text(
            _json_text(manifest), encoding="utf-8"
        )
        staging.replace(target)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def run_grader_baseline(
    project_root: str | Path,
    *,
    output_dir: str | Path,
    backend: GraderBackend | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Grader baseline: {output}")
    inputs = load_frozen_grader_inputs(root)
    config, _, prompt_path = load_grader_config(root / "config/grader_v1.json")
    checkpoint_dir = output.parent / f".{output.name}_checkpoint"
    checkpoint_metadata = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "grader_version": EXPECTED_GRADER_VERSION,
        "model": config["model"],
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "eval_dataset_sha256": EVAL_DATASET_SHA256,
        "hybrid_results_sha256": HYBRID_RESULTS_SHA256,
        "input_bundle_sha256": inputs["input_bundle_sha256"],
        "case_count": len(inputs["cases"]),
    }
    checkpoint_outputs = load_or_create_grade_checkpoint(
        checkpoint_dir=checkpoint_dir,
        grader_inputs=inputs["grader_inputs"],
        expected_metadata=checkpoint_metadata,
    )
    resumed_case_count = len(checkpoint_outputs)
    grader = backend or OpenAIGraderBackend(
        config_path=root / "config/grader_v1.json"
    )
    pacing_seconds = (
        float(config["minimum_request_start_interval_seconds"])
        if isinstance(grader, OpenAIGraderBackend)
        else 0.0
    )
    grader_outputs = produce_grades(
        inputs["grader_inputs"],
        grader,
        min_request_interval_seconds=pacing_seconds,
        initial_outputs=checkpoint_outputs,
        checkpoint_path=checkpoint_dir / "grader_outputs.jsonl",
    )

    # Gold enters only here, after all first-pass Grader outputs are fixed.
    case_results = build_case_results(
        cases=inputs["cases"],
        grader_inputs=inputs["grader_inputs"],
        grader_outputs=grader_outputs,
    )
    metrics = evaluate_grader(case_results)
    call_metrics = [item["grader_metrics"] for item in grader_outputs]
    usages = [item.get("usage", {}) for item in call_metrics]
    performance = {
        "grader_latency_ms": _distribution(
            [float(item.get("latency_ms", 0.0)) for item in call_metrics]
        ),
        "tokens": {
            "input": sum(int(item.get("input_tokens", 0)) for item in usages),
            "cached_input": sum(
                int(item.get("cached_input_tokens", 0)) for item in usages
            ),
            "output": sum(int(item.get("output_tokens", 0)) for item in usages),
            "reasoning": sum(
                int(item.get("reasoning_tokens", 0)) for item in usages
            ),
            "total": sum(int(item.get("total_tokens", 0)) for item in usages),
        },
        "estimated_cost_usd": sum(
            float(item.get("estimated_cost_usd", 0.0)) for item in usages
        ),
        "total_request_pacing_wait_ms": sum(
            float(item.get("request_pacing_wait_ms", 0.0))
            for item in grader_outputs
        ),
    }
    dependencies = inputs["dependencies"]
    reproduction = {
        "grader_version": EXPECTED_GRADER_VERSION,
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
            "service_tier": config["service_tier"],
            "store": config["store"],
            "max_retries": config["max_retries"],
            "minimum_request_start_interval_seconds": pacing_seconds,
        },
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "eval_dataset_sha256": dependencies["eval_dataset_sha256"],
        "eval_manifest_sha256": dependencies["eval_manifest_sha256"],
        "hybrid_manifest_sha256": dependencies["hybrid_manifest_sha256"],
        "hybrid_results_sha256": dependencies["hybrid_results_sha256"],
        "corpus_chunks_sha256": dependencies["corpus_chunks_sha256"],
        "corpus_manifest_sha256": dependencies["corpus_manifest_sha256"],
        "dense_manifest_sha256": DENSE_MANIFEST_SHA256,
        "router_manifest_sha256": ROUTER_MANIFEST_SHA256,
        "agent_manifest_sha256": AGENT_MANIFEST_SHA256,
        "input_bundle_sha256": inputs["input_bundle_sha256"],
        "case_count": len(inputs["cases"]),
        "grader_input_fields": GRADER_INPUT_FIELDS,
        "gold_contract": (
            "frozen hybrid CompleteEvidence@10 under required evidence-group any/all semantics"
        ),
        "checkpoint": {
            "version": CHECKPOINT_VERSION,
            "resumed_case_count": resumed_case_count,
            "new_case_count": len(grader_outputs) - resumed_case_count,
            "manual_resume_only": True,
            "automatic_provider_retry": False,
        },
        "performance": performance,
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
            "answer_criteria_exposed_to_grader": False,
            "limitations_labels_exposed_to_grader": False,
            "gold_applied_after_all_predictions": True,
            "retrieval_rerun": False,
            "query_rewrite": False,
            "retry_loop": False,
            "re_retrieval": False,
            "replan": False,
            "recovery_agent": False,
            "answer_generation": False,
            "abstention_workflow": False,
            "post_result_tuning": False,
            "phase11b_started": False,
        },
    }
    manifest = write_immutable_grader_artifacts(
        output_dir=output,
        grader_inputs=inputs["grader_inputs"],
        grader_outputs=grader_outputs,
        metrics=metrics,
        reproduction=reproduction,
        config_path=root / "config/grader_v1.json",
        prompt_path=prompt_path,
    )
    finalize_grade_checkpoint(
        checkpoint_dir=checkpoint_dir,
        final_manifest_sha256=sha256_file(output / "manifest.json"),
    )

    for path, expected in (
        (dependencies["eval_path"], EVAL_DATASET_SHA256),
        (dependencies["eval_manifest_path"], EVAL_MANIFEST_SHA256),
        (dependencies["hybrid_manifest_path"], HYBRID_MANIFEST_SHA256),
        (dependencies["hybrid_results_path"], HYBRID_RESULTS_SHA256),
        (dependencies["chunks_path"], CORPUS_CHUNKS_SHA256),
        (dependencies["corpus_manifest_path"], CORPUS_MANIFEST_SHA256),
    ):
        if sha256_file(path) != expected:
            raise RuntimeError(f"Frozen dependency changed during evaluation: {path}")
    return manifest, metrics
