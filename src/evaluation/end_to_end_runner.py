"""One-pass real-component runner for the frozen End-to-End baseline.

Importing this module or running its verification path never calls PostgreSQL,
Hugging Face, or OpenAI.  External components are constructed only by
``run_end_to_end_baseline`` after secrets are supplied explicitly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from src.agent.executor import DeterministicToolExecutor
from src.answer.generator import FinalResponseLayer, build_final_answer_input
from src.answer.provider import OpenAIFinalAnswerProvider
from src.database.config import DatabaseConfig
from src.evaluation.end_to_end_metrics import (
    aggregate_end_to_end_metrics,
    evaluate_end_to_end_case,
)
from src.grading.runtime_grader import RuntimeEvidenceGrader
from src.grading.runtime_provider import OpenAIRuntimeEvidenceProvider
from src.graph.workflow import AgenticRAGWorkflow
from src.recovery.agent import EvidenceRecoveryAgent
from src.recovery.provider import OpenAIEvidenceRecoveryProvider
from src.smoke.e2e import (
    SharedProviderPacer,
    TracedGrader,
    TracedProvider,
    TracedRecoveryAgent,
    TracedTool,
)
from src.tools.literature import LiteratureTool
from src.tools.metric import MetricTool
from src.tools.training_log import PsycopgTrainingRepository, TrainingLogTool


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_VERSION = "end_to_end_baseline_v1"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/end_to_end_baseline_v1.json"
DEFAULT_PREREGISTRATION_MANIFEST = (
    PROJECT_ROOT
    / "reports/baselines/end_to_end_baseline_v1_preregistration/manifest.json"
)
DEFAULT_PREREGISTRATION_LOCK = (
    PROJECT_ROOT
    / "reports/baselines/end_to_end_baseline_v1_preregistration/manifest.sha256"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports/baselines/end_to_end_baseline_v1"
DEFAULT_CHECKPOINT_DIR = (
    PROJECT_ROOT / "reports/baselines/.end_to_end_baseline_v1_checkpoint"
)
EXPECTED_CATEGORY_COUNTS = {
    "literature_only": 10,
    "log_metric": 6,
    "hybrid": 8,
    "unanswerable": 6,
}


@dataclass(frozen=True)
class EndToEndBaselineProtocol:
    baseline_version: str
    config_path: Path
    preregistration_manifest: Path
    preregistration_lock: Path
    output_dir: Path
    checkpoint_dir: Path


V1_PROTOCOL = EndToEndBaselineProtocol(
    baseline_version=BASELINE_VERSION,
    config_path=DEFAULT_CONFIG_PATH,
    preregistration_manifest=DEFAULT_PREREGISTRATION_MANIFEST,
    preregistration_lock=DEFAULT_PREREGISTRATION_LOCK,
    output_dir=DEFAULT_OUTPUT_DIR,
    checkpoint_dir=DEFAULT_CHECKPOINT_DIR,
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if not isinstance(value, str):
        return value
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", value)
    return re.sub(r"(?i)(password\s*[=:]\s*)\S+", r"\1[REDACTED]", text)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def load_protocol_config(
    path: Path = DEFAULT_CONFIG_PATH,
    *,
    expected_baseline_version: str = BASELINE_VERSION,
) -> dict[str, Any]:
    config = _read_json(path)
    if config.get("baseline_version") != expected_baseline_version:
        raise RuntimeError("Unexpected End-to-End baseline configuration")
    execution = config.get("execution") or {}
    fixed = {
        "literature_top_k": 10,
        "max_retry": 2,
        "fusion_policy": "retry_evidence_fusion_v1",
        "fusion_rrf_k": 60,
        "provider_automatic_retries": 0,
        "result_driven_tuning": False,
    }
    mismatches = {
        key: (execution.get(key), expected)
        for key, expected in fixed.items()
        if execution.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"End-to-End fixed configuration changed: {mismatches}")
    return config


def load_frozen_cases(
    root: Path = PROJECT_ROOT,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = dict(config or load_protocol_config(root / "config/end_to_end_baseline_v1.json"))
    payload = _read_json(root / str(config["evaluation_dataset"]))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("Frozen evaluation dataset has no case list")
    counts: dict[str, int] = {}
    for case in cases:
        category = str(case.get("category"))
        counts[category] = counts.get(category, 0) + 1
    if len(cases) != 30 or counts != EXPECTED_CATEGORY_COUNTS:
        raise RuntimeError(
            f"Frozen evaluation case composition changed: count={len(cases)}, categories={counts}"
        )
    ids = [str(case.get("id")) for case in cases]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Frozen evaluation dataset contains duplicate case IDs")
    return [dict(case) for case in cases]


def verify_preregistration(
    root: Path = PROJECT_ROOT,
    *,
    manifest_path: Path | None = None,
    lock_path: Path | None = None,
    expected_baseline_version: str = BASELINE_VERSION,
) -> dict[str, Any]:
    """Verify the preregistration manifest and every frozen source dependency."""

    manifest_path = manifest_path or (
        root / "reports/baselines/end_to_end_baseline_v1_preregistration/manifest.json"
    )
    lock_path = lock_path or (
        root / "reports/baselines/end_to_end_baseline_v1_preregistration/manifest.sha256"
    )
    expected_manifest_hash = lock_path.read_text(encoding="utf-8").strip()
    actual_manifest_hash = sha256_file(manifest_path)
    if actual_manifest_hash != expected_manifest_hash:
        raise RuntimeError("End-to-End preregistration manifest hash mismatch")
    manifest = _read_json(manifest_path)
    if manifest.get("baseline_version") != expected_baseline_version:
        raise RuntimeError("Unexpected preregistration baseline version")
    if manifest.get("status") != "PREREGISTERED_NOT_EXECUTED":
        raise RuntimeError("End-to-End preregistration status is not frozen")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("Preregistration manifest has no frozen artifacts")
    for item in artifacts:
        path = root / str(item["path"])
        actual = sha256_file(path)
        if actual != str(item["sha256"]):
            raise RuntimeError(f"Preregistered artifact changed: {item['path']}")
    manifest["manifest_sha256"] = actual_manifest_hash
    return manifest


def _date_only(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


def _primary_structured_spec(case: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    structured = (case.get("gold") or {}).get("structured_evidence", [])
    if not structured:
        return "", {}
    item = structured[0]
    return str(item.get("operation") or ""), dict(item.get("parameters") or {})


def build_tool_inputs_from_frozen_case(case: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Bind frozen operation metadata to Phase 8 typed inputs.

    Only the operation name and its non-answer parameters are consulted.  The
    ``expected`` field and all literature Gold labels are intentionally ignored.
    Defaults exist so a Router false-positive Tool call remains an evaluated
    routing/system behavior instead of becoming an absent-harness-input error.
    """

    operation, parameters = _primary_structured_spec(case)
    exercise = parameters.get("exercise")
    training: dict[str, Any] = {
        "operation": "list_sessions",
        "limit": 5000,
        "include_lineage": False,
    }
    if exercise:
        training = {
            "operation": "exercise_records",
            "canonical_exercise_name": str(exercise),
            "limit": 5000,
            "include_lineage": False,
        }
        start = _date_only(parameters.get("start"))
        end = _date_only(parameters.get("end"))
        if start:
            training["start_date"] = start
        if end:
            training["end_date"] = end

    metric: dict[str, Any] = {
        "operation": "training_gap",
        "records_source": "query_training_log",
    }
    if exercise:
        metric["canonical_exercise_name"] = str(exercise)
    if operation in {"e1rm_window_summary", "e1rm_interval_summary"}:
        metric["operation"] = "first_last_n_session_median_e1rm"
        metric["n_sessions"] = int(
            parameters.get("window_sessions", 3 if operation == "e1rm_interval_summary" else 5)
        )
    elif operation == "e1rm_peak_and_latest":
        metric["operation"] = "estimated_1rm"
    elif operation == "weekly_load_comparison":
        metric["operation"] = "weekly_volume"
    elif operation in {"longest_training_gap", "gap_e1rm_comparison"}:
        metric["operation"] = "training_gap"

    start = _date_only(parameters.get("start"))
    end = _date_only(parameters.get("end"))
    if start:
        metric["start_date"] = start
    if end:
        metric["end_date"] = end
    return {
        "query_training_log": training,
        "compute_metrics": metric,
    }


def prepare_graph_invocation(case: Mapping[str, Any]) -> dict[str, Any]:
    """Create graph input without literature Gold exposure.

    The initial retrieval query remains the exact frozen question.  Literature
    grading/recovery scope is now derived by the graph from deterministic Router
    provenance unless a caller supplies an explicit separated subquestion.
    """

    question = str(case["question"])
    return {
        "question": question,
        "literature_subquestion": None,
        "initial_query": question,
        "tool_inputs": build_tool_inputs_from_frozen_case(case),
    }


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_redact(_json_safe(payload)), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _case_checkpoint_path(checkpoint_dir: Path, case_id: str) -> Path:
    return checkpoint_dir / "cases" / f"{case_id}.json"


def _started_marker_path(checkpoint_dir: Path, case_id: str) -> Path:
    return checkpoint_dir / "started" / f"{case_id}.json"


def _load_completed_checkpoints(
    cases: Sequence[Mapping[str, Any]], checkpoint_dir: Path
) -> dict[str, dict[str, Any]]:
    completed: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_id = str(case["id"])
        path = _case_checkpoint_path(checkpoint_dir, case_id)
        if path.exists():
            completed[case_id] = _read_json(path)
            continue
        if _started_marker_path(checkpoint_dir, case_id).exists():
            raise RuntimeError(
                f"Case {case_id} has an interrupted external attempt and will not be "
                "automatically reissued. Preserve the checkpoint and close out the run."
            )
    return completed


def _top_level_list_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): len(item)
        for key, item in value.items()
        if isinstance(item, list)
    }


def _remediation_diagnostics(state: Mapping[str, Any]) -> dict[str, Any]:
    """Record bounded, non-Gold diagnostics for the three v2 targets."""

    tool_results = [
        item
        for item in state.get("tool_results", []) or []
        if isinstance(item, Mapping)
    ]
    training = next(
        (item for item in tool_results if item.get("tool") == "query_training_log"),
        None,
    )
    metric = next(
        (item for item in tool_results if item.get("tool") == "compute_metrics"),
        None,
    )
    diagnostic: dict[str, Any] = {
        "list_sessions_metric_adapter": {
            "training_operation": (
                training.get("requested_operation") if training else None
            ),
            "training_status": training.get("status") if training else None,
            "metric_operation": metric.get("requested_operation") if metric else None,
            "metric_status": metric.get("status") if metric else None,
            "metric_error_code": (
                (metric.get("error") or {}).get("code")
                if metric and isinstance(metric.get("error"), Mapping)
                else None
            ),
        },
        "literature_channel": {
            "original_question": str(state.get("original_question") or ""),
            "literature_subquestion": str(
                state.get("literature_subquestion") or ""
            ),
            "separated": bool(
                state.get("literature_subquestion")
                and str(state.get("literature_subquestion")).casefold()
                != str(state.get("original_question") or "").casefold()
            ),
        },
    }
    try:
        answer_input = build_final_answer_input(state)
        provider_training = next(
            (
                item
                for item in answer_input.structured_evidence
                if item.tool == "query_training_log"
            ),
            None,
        )
        provider_metric = next(
            (
                item
                for item in answer_input.structured_evidence
                if item.tool == "compute_metrics"
            ),
            None,
        )
        provider_training_result = (
            provider_training.result if provider_training is not None else None
        )
        payload_metadata = (
            provider_training_result.get("_answer_payload")
            if isinstance(provider_training_result, Mapping)
            else None
        )
        diagnostic["final_answer_payload"] = {
            "serialized_input_characters": len(answer_input.model_dump_json()),
            "training_log_source_list_counts": _top_level_list_counts(
                training.get("result") if training else None
            ),
            "training_log_provider_list_counts": _top_level_list_counts(
                provider_training_result
            ),
            "training_log_payload_policy": payload_metadata,
            "metric_result_present": provider_metric is not None,
            "metric_result_preserved": bool(
                metric
                and provider_metric is not None
                and provider_metric.result == metric.get("result")
            ),
        }
    except Exception as exc:
        diagnostic["final_answer_payload"] = {
            "diagnostic_error": f"{type(exc).__name__}: {exc}"
        }
    return diagnostic


def _summarize_runtime_case(
    case: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    elapsed_ms: float,
    api_calls: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]],
    grader_calls: Sequence[Mapping[str, Any]],
    recovery_calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized_api = []
    aliases = {
        "runtime_evidence_grader": "runtime_grader",
        "evidence_recovery_agent": "recovery_agent",
    }
    for item in api_calls:
        record = dict(item)
        component = str(record.get("component") or "unknown")
        record["node"] = aliases.get(component, component)
        normalized_api.append(record)
    evaluated = evaluate_end_to_end_case(
        case,
        state,
        api_calls=normalized_api,
        tool_calls=tool_calls,
        grader_calls=grader_calls,
        recovery_calls=recovery_calls,
        case_latency_ms=elapsed_ms,
    )
    return {
        "case_id": str(case["id"]),
        "runtime_input": prepare_graph_invocation(case),
        "state": _redact(_json_safe(state)),
        "trace": {
            "api_calls": normalized_api,
            "tool_calls": [dict(item) for item in tool_calls],
            "grader_calls": [dict(item) for item in grader_calls],
            "recovery_calls": [dict(item) for item in recovery_calls],
        },
        "remediation_diagnostics": _remediation_diagnostics(state),
        "evaluation": evaluated,
    }


def _slice_from(values: Sequence[Any], offset: int) -> list[Any]:
    return list(values[offset:])


def render_result_report(
    metrics: Mapping[str, Any],
    *,
    started_at: str,
    completed_at: str,
    preregistration_sha256: str,
    baseline_version: str = BASELINE_VERSION,
    comparison: Mapping[str, Any] | None = None,
) -> str:
    overall = metrics["overall"]
    lines = [
        f"# Frozen End-to-End Baseline {baseline_version}",
        "",
        "**Status:** `FROZEN_COMPLETE`  ",
        "**Interpretation:** integration baseline on the existing frozen eval_dataset_v1; not an independent generalization benchmark  ",
        f"**Started:** `{started_at}`  ",
        f"**Completed:** `{completed_at}`  ",
        f"**Preregistration SHA-256:** `{preregistration_sha256}`",
        "",
        "## Aggregate summary",
        "",
        "```json",
        json.dumps(overall, ensure_ascii=False, indent=2),
        "```",
        "",
        "No post-result prompt, policy, Gold, retrieval, retry, fusion, or metric tuning was performed.",
        "",
    ]
    if comparison is not None:
        lines.extend(
            [
                "## Frozen v1 comparison",
                "",
                "```json",
                json.dumps(comparison.get("summary", {}), ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def run_end_to_end_baseline(
    *,
    database_config: DatabaseConfig,
    postgres_password: str,
    openai_api_key: str,
    model_cache: Path,
    output_dir: Path | None = None,
    checkpoint_dir: Path | None = None,
    min_interval_seconds: float | None = None,
    protocol: EndToEndBaselineProtocol = V1_PROTOCOL,
) -> dict[str, Any]:
    """Execute or resume the single preregistered baseline and freeze its output."""

    if not postgres_password.strip() or not openai_api_key.strip():
        raise ValueError("PostgreSQL password and OpenAI API key must be non-empty")
    output_dir = (output_dir or protocol.output_dir).resolve()
    checkpoint_dir = (checkpoint_dir or protocol.checkpoint_dir).resolve()
    preregistration = verify_preregistration(
        PROJECT_ROOT,
        manifest_path=protocol.preregistration_manifest,
        lock_path=protocol.preregistration_lock,
        expected_baseline_version=protocol.baseline_version,
    )
    config = load_protocol_config(
        protocol.config_path,
        expected_baseline_version=protocol.baseline_version,
    )
    cases = load_frozen_cases(config=config)
    if output_dir.exists():
        raise FileExistsError(
            f"Frozen End-to-End output already exists and cannot be overwritten: {output_dir}"
        )
    interval = (
        float(min_interval_seconds)
        if min_interval_seconds is not None
        else float(config["execution"]["api_min_interval_seconds"])
    )
    if interval < 0:
        raise ValueError("min_interval_seconds must be non-negative")

    checkpoint_meta = checkpoint_dir / "checkpoint_manifest.json"
    if checkpoint_meta.exists():
        checkpoint_payload = _read_json(checkpoint_meta)
        if checkpoint_payload.get("preregistration_sha256") != preregistration["manifest_sha256"]:
            raise RuntimeError("Checkpoint belongs to a different preregistration")
        started_at = str(checkpoint_payload["started_at"])
    else:
        started_at = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(
            checkpoint_meta,
            {
                "baseline_version": protocol.baseline_version,
                "preregistration_sha256": preregistration["manifest_sha256"],
                "started_at": started_at,
            },
        )
    completed = _load_completed_checkpoints(cases, checkpoint_dir)

    pacer = SharedProviderPacer(interval)
    api_events: list[dict[str, Any]] = []
    grader_provider = TracedProvider(
        "runtime_evidence_grader",
        OpenAIRuntimeEvidenceProvider(api_key=openai_api_key),
        pacer,
        api_events,
    )
    recovery_provider = TracedProvider(
        "evidence_recovery_agent",
        OpenAIEvidenceRecoveryProvider(api_key=openai_api_key),
        pacer,
        api_events,
    )
    answer_provider = TracedProvider(
        "final_answer",
        OpenAIFinalAnswerProvider(api_key=openai_api_key),
        pacer,
        api_events,
    )
    training_tool = TracedTool(
        "query_training_log",
        TrainingLogTool(
            PsycopgTrainingRepository(config=database_config, password=postgres_password)
        ),
    )
    metric_tool = TracedTool("compute_metrics", MetricTool())
    literature_inner = LiteratureTool.from_postgres(
        password=postgres_password,
        config=database_config,
        cache_dir=model_cache,
    )
    literature_tool = TracedTool("search_literature", literature_inner)
    grader = TracedGrader(RuntimeEvidenceGrader(grader_provider))
    recovery = TracedRecoveryAgent(EvidenceRecoveryAgent(recovery_provider))
    executor = DeterministicToolExecutor(
        training_log_tool=training_tool,
        metric_tool=metric_tool,
        literature_tool=literature_tool,
    )
    workflow = AgenticRAGWorkflow(
        tool_executor=executor,
        literature_tool=literature_tool,
        runtime_grader=grader,
        recovery_agent=recovery,
        final_response_layer=FinalResponseLayer(answer_provider),
    )

    try:
        for index, case in enumerate(cases, 1):
            case_id = str(case["id"])
            if case_id in completed:
                print(f"Preserved {index:02d}/30: {case_id}", flush=True)
                continue
            marker = _started_marker_path(checkpoint_dir, case_id)
            _write_json_atomic(
                marker,
                {
                    "case_id": case_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "external_requests_must_not_be_reissued_if_interrupted": True,
                },
            )
            print(f"Running {index:02d}/30: {case_id}", flush=True)
            offsets = {
                "training": len(training_tool.calls),
                "metric": len(metric_tool.calls),
                "literature": len(literature_tool.calls),
                "grader": len(grader.calls),
                "recovery": len(recovery.calls),
                "api": len(api_events),
            }
            invocation = prepare_graph_invocation(case)
            started = perf_counter()
            try:
                state = workflow.invoke(**invocation)
            except Exception as exc:
                state = {
                    "original_question": case["question"],
                    "route": {},
                    "selected_tools": [],
                    "tool_results": [],
                    "literature_evidence": [],
                    "fused_evidence": [],
                    "retry_count": 0,
                    "errors": [
                        {
                            "stage": "runner",
                            "code": "unhandled_graph_exception",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    ],
                    "final_status": "execution_failure",
                    "final_response": None,
                }
            elapsed_ms = (perf_counter() - started) * 1000.0
            tool_calls = [
                *_slice_from(training_tool.calls, offsets["training"]),
                *_slice_from(metric_tool.calls, offsets["metric"]),
                *_slice_from(literature_tool.calls, offsets["literature"]),
            ]
            record = _summarize_runtime_case(
                case,
                state,
                elapsed_ms=elapsed_ms,
                api_calls=_slice_from(api_events, offsets["api"]),
                tool_calls=tool_calls,
                grader_calls=_slice_from(grader.calls, offsets["grader"]),
                recovery_calls=_slice_from(recovery.calls, offsets["recovery"]),
            )
            _write_json_atomic(_case_checkpoint_path(checkpoint_dir, case_id), record)
            completed[case_id] = record
            print(
                f"Completed {case_id}: {record['evaluation']['final_status']}",
                flush=True,
            )
    finally:
        literature_inner.close()

    verify_preregistration(
        PROJECT_ROOT,
        manifest_path=protocol.preregistration_manifest,
        lock_path=protocol.preregistration_lock,
        expected_baseline_version=protocol.baseline_version,
    )
    ordered_records = [completed[str(case["id"])] for case in cases]
    case_metrics = [item["evaluation"] for item in ordered_records]
    metrics = aggregate_end_to_end_metrics(case_metrics)
    completed_at = datetime.now(timezone.utc).isoformat()

    temporary_output = output_dir.with_name(output_dir.name + ".tmp")
    if temporary_output.exists():
        raise FileExistsError(f"Stale temporary output requires review: {temporary_output}")
    temporary_output.mkdir(parents=True)
    _write_json_atomic(temporary_output / "case_results.json", ordered_records)
    _write_json_atomic(temporary_output / "case_metrics.json", case_metrics)
    _write_json_atomic(temporary_output / "aggregate_metrics.json", metrics)
    comparison = None
    comparison_config = config.get("comparison")
    if isinstance(comparison_config, Mapping):
        from src.evaluation.end_to_end_v2_metrics import build_v1_v2_comparison

        comparison_directory = PROJECT_ROOT / str(
            comparison_config["baseline_directory"]
        )
        v1_case_results = json.loads(
            (comparison_directory / "case_results.json").read_text(encoding="utf-8")
        )
        v1_case_metrics = json.loads(
            (comparison_directory / "case_metrics.json").read_text(encoding="utf-8")
        )
        comparison = build_v1_v2_comparison(
            v1_case_metrics=v1_case_metrics,
            v2_case_metrics=case_metrics,
            v1_case_results=v1_case_results,
            v2_case_results=ordered_records,
        )
        _write_json_atomic(temporary_output / "v1_comparison.json", comparison)
    (temporary_output / "REPORT.md").write_text(
        render_result_report(
            metrics,
            started_at=started_at,
            completed_at=completed_at,
            preregistration_sha256=preregistration["manifest_sha256"],
            baseline_version=protocol.baseline_version,
            comparison=comparison,
        ),
        encoding="utf-8",
    )
    artifact_hashes = {
        path.name: sha256_file(path)
        for path in sorted(temporary_output.iterdir())
        if path.is_file()
    }
    _write_json_atomic(
        temporary_output / "manifest.json",
        {
            "baseline_version": protocol.baseline_version,
            "status": "FROZEN_COMPLETE",
            "started_at": started_at,
            "completed_at": completed_at,
            "case_count": len(ordered_records),
            "preregistration_sha256": preregistration["manifest_sha256"],
            "artifact_hashes": artifact_hashes,
            "post_result_tuning": False,
        },
    )
    temporary_output.replace(output_dir)
    return {
        "output_dir": str(output_dir),
        "manifest_sha256": sha256_file(output_dir / "manifest.json"),
        "metrics": metrics,
    }


def verify_only(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Offline verification used before the one real baseline run."""

    return verify_protocol_only(root, protocol=V1_PROTOCOL)


def verify_protocol_only(
    root: Path = PROJECT_ROOT,
    *,
    protocol: EndToEndBaselineProtocol,
) -> dict[str, Any]:
    """Offline verification for one explicitly versioned protocol."""

    preregistration = verify_preregistration(
        root,
        manifest_path=protocol.preregistration_manifest,
        lock_path=protocol.preregistration_lock,
        expected_baseline_version=protocol.baseline_version,
    )
    config = load_protocol_config(
        protocol.config_path,
        expected_baseline_version=protocol.baseline_version,
    )
    cases = load_frozen_cases(root, config)
    if (root / str(config["output_directory"])).exists():
        execution_state = "FROZEN_OUTPUT_EXISTS"
    else:
        execution_state = "NOT_EXECUTED"
    return {
        "baseline_version": protocol.baseline_version,
        "case_count": len(cases),
        "category_counts": EXPECTED_CATEGORY_COUNTS,
        "preregistration_sha256": preregistration["manifest_sha256"],
        "execution_state": execution_state,
    }
