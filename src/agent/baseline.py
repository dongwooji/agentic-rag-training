"""One-shot Phase 10 evaluation and immutable agent_baseline_v1 artifacts."""

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
import tempfile
from time import perf_counter, sleep
from typing import Any, Callable, Mapping, Sequence

from src.evaluation.routing_metrics import evaluate_routing
from src.routing.baseline import load_frozen_routing_evaluation, sha256_file
from src.routing.contracts import RouterInput
from src.routing.deterministic import DeterministicRouter

from .executor import DeterministicToolExecutor
from .graph import PlanningAgentWorkflow
from .planner import (
    EXPECTED_AGENT_VERSION,
    EXPECTED_CONFIG_SHA256,
    EXPECTED_PROMPT_SHA256,
    OpenAIPlannerBackend,
    load_agent_config,
)


ROUTER_MANIFEST_SHA256 = (
    "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
)
HYBRID_MANIFEST_SHA256 = (
    "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
)
CORPUS_CHUNKS_SHA256 = (
    "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177"
)
CORPUS_MANIFEST_SHA256 = (
    "84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66"
)
ACCOUNT_RPM_LIMIT = 10
MIN_REQUEST_START_INTERVAL_SECONDS = 7.0
CHECKPOINT_VERSION = "agent_baseline_v1_checkpoint_v1"


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


def _validate_frozen_dependencies(root: Path) -> dict[str, Any]:
    router_dir = root / "reports/baselines/router_baseline_v1"
    router_manifest_path = router_dir / "manifest.json"
    if sha256_file(router_manifest_path) != ROUTER_MANIFEST_SHA256:
        raise RuntimeError("Frozen router_baseline_v1 manifest hash changed")
    router_manifest = _read_json(router_manifest_path)
    if router_manifest.get("status") != "frozen":
        raise RuntimeError("router_baseline_v1 is not frozen")
    for item in router_manifest["artifacts"]:
        if sha256_file(router_dir / item["path"]) != item["sha256"]:
            raise RuntimeError(
                f"Frozen Router artifact changed: {item['path']}"
            )

    hybrid_manifest_path = root / "reports/baselines/hybrid_baseline_v1/manifest.json"
    chunks_path = root / "data/literature/processed/chunks.jsonl"
    corpus_manifest_path = root / "data/literature/manifests/corpus_v1.json"
    checks = {
        "hybrid_manifest_sha256": sha256_file(hybrid_manifest_path),
        "corpus_chunks_sha256": sha256_file(chunks_path),
        "corpus_manifest_sha256": sha256_file(corpus_manifest_path),
    }
    expected = {
        "hybrid_manifest_sha256": HYBRID_MANIFEST_SHA256,
        "corpus_chunks_sha256": CORPUS_CHUNKS_SHA256,
        "corpus_manifest_sha256": CORPUS_MANIFEST_SHA256,
    }
    if checks != expected:
        raise RuntimeError("Frozen Hybrid or literature corpus dependency changed")
    return {
        "router_dir": router_dir,
        "router_manifest_path": router_manifest_path,
        "hybrid_manifest_path": hybrid_manifest_path,
        "chunks_path": chunks_path,
        "corpus_manifest_path": corpus_manifest_path,
        **checks,
    }


def load_or_create_plan_checkpoint(
    *,
    checkpoint_dir: Path,
    cases: Sequence[Mapping[str, Any]],
    expected_metadata: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Load a contiguous question-only prefix or create an empty journal."""

    metadata_path = checkpoint_dir / "metadata.json"
    plans_path = checkpoint_dir / "plans.jsonl"
    if checkpoint_dir.exists():
        if not checkpoint_dir.is_dir() or not metadata_path.is_file():
            raise RuntimeError("Agent checkpoint is incomplete or not a directory")
        metadata = _read_json(metadata_path)
        for key, expected in expected_metadata.items():
            if metadata.get(key) != expected:
                raise RuntimeError(f"Agent checkpoint metadata changed: {key}")
        if metadata.get("status") not in {"in_progress", "finalized"}:
            raise RuntimeError("Agent checkpoint status is invalid")
    else:
        checkpoint_dir.mkdir(parents=True, exist_ok=False)
        metadata = {
            **dict(expected_metadata),
            "status": "in_progress",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "controls": {
                "question_only": True,
                "gold_exposed_to_planner": False,
                "automatic_provider_retry": False,
            },
        }
        metadata_path.write_text(_json_text(metadata), encoding="utf-8")
        plans_path.touch()

    plans = _read_jsonl(plans_path) if plans_path.exists() else []
    if len(plans) > len(cases):
        raise RuntimeError("Agent checkpoint contains too many cases")
    for index, item in enumerate(plans):
        case = cases[index]
        if item.get("case_id") != str(case["id"]):
            raise RuntimeError("Agent checkpoint is not a contiguous case prefix")
        if item.get("question") != case["question"]:
            raise RuntimeError("Agent checkpoint question changed")
        if item.get("planner_input_fields") != ["question"]:
            raise RuntimeError("Agent checkpoint exposed non-question inputs")
        if "prediction" not in item or "planner_metrics" not in item:
            raise RuntimeError("Agent checkpoint plan record is incomplete")
    return plans


def append_plan_checkpoint(path: Path, item: Mapping[str, Any]) -> None:
    """Durably append one completed first-pass provider result."""

    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def finalize_plan_checkpoint(
    *, checkpoint_dir: Path, final_manifest_sha256: str
) -> None:
    metadata_path = checkpoint_dir / "metadata.json"
    metadata = _read_json(metadata_path)
    metadata["status"] = "finalized"
    metadata["finalized_at_utc"] = datetime.now(timezone.utc).isoformat()
    metadata["final_manifest_sha256"] = final_manifest_sha256
    metadata_path.write_text(_json_text(metadata), encoding="utf-8")


def produce_agent_plans(
    cases: Sequence[Mapping[str, Any]],
    workflow: PlanningAgentWorkflow,
    *,
    min_request_interval_seconds: float = 0.0,
    clock: Callable[[], float] = perf_counter,
    sleep_fn: Callable[[float], None] = sleep,
    initial_plans: Sequence[Mapping[str, Any]] = (),
    checkpoint_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Generate every prediction from question text before applying any Gold."""

    plans: list[dict[str, Any]] = [dict(item) for item in initial_plans]
    if len(plans) > len(cases):
        raise ValueError("initial_plans cannot exceed the case count")
    if plans:
        print(
            f"Resuming from checkpoint: {len(plans)}/{len(cases)} cases preserved",
            flush=True,
        )
    last_request_started_at: float | None = None
    for zero_index in range(len(plans), len(cases)):
        case = cases[zero_index]
        index = zero_index + 1
        case_id = str(case["id"])
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
        print(f"Planning {index:02d}/{len(cases)}: {case_id}", flush=True)
        state = workflow.invoke(str(case["question"]))
        if state.get("final_status") == "planner_error":
            detail = "; ".join(
                item.get("message", "unknown error")
                for item in state.get("errors", [])
            )
            raise RuntimeError(
                "Agent baseline aborted before freeze because the provider "
                f"failed at {case_id}: {detail}"
            )
        plan = state.get("plan")
        prediction = {
            "case_id": case_id,
            "task_type": plan["task_type"] if plan else "invalid",
            "selected_tools": list(state.get("selected_tools", [])),
            "execution_order": list(state.get("execution_order", [])),
            "routing_reason": (
                plan["planner_reasoning_summary"]
                if plan
                else "Planner or typed validation failed."
            ),
            "confidence": 1.0 if plan else 0.0,
            "unsupported_reason": plan.get("unsupported_reason") if plan else None,
            "rule_match": {
                "rule_id": "LLM_PLANNER_TYPED_V1" if plan else "LLM_PLANNER_ERROR",
                "normalized_question": "",
                "signals": {},
                "matched_patterns": {},
            },
        }
        item = {
            "case_id": case_id,
            "question": case["question"],
            "planner_input_fields": ["question"],
            "raw_plan": state.get("raw_plan"),
            "validated_plan": plan,
            "selected_tools": prediction["selected_tools"],
            "execution_order": prediction["execution_order"],
            "tool_inputs": state.get("tool_inputs", {}),
            "tool_results": state.get("tool_results", []),
            "errors": state.get("errors", []),
            "final_status": state.get("final_status"),
            "planner_metrics": state.get("planner_metrics", {}),
            "request_pacing_wait_ms": pacing_wait_seconds * 1000,
            "prediction": prediction,
        }
        plans.append(item)
        if checkpoint_path is not None:
            append_plan_checkpoint(checkpoint_path, item)
    return plans


def measure_router_reference(
    *, cases: Sequence[Mapping[str, Any]], router_dir: Path
) -> dict[str, Any]:
    config = _read_json(router_dir.parents[2] / "config/router_baseline_v1.json")
    router = DeterministicRouter(config)
    routes = []
    latencies = []
    for case in cases:
        started = perf_counter()
        plan = router.route(RouterInput(question=str(case["question"]))).to_dict()
        latencies.append((perf_counter() - started) * 1000)
        routes.append({"case_id": str(case["id"]), "question": case["question"], **plan})
    stored_routes = _read_jsonl(router_dir / "routes.jsonl")
    if routes != stored_routes:
        raise RuntimeError("Deterministic Router no longer reproduces frozen routes")
    return {
        "metrics": _read_json(router_dir / "metrics.json"),
        "latency_ms": _distribution(latencies),
        "token_usage": 0,
        "estimated_cost_usd": 0.0,
    }


def build_comparison(
    *,
    agent_metrics: Mapping[str, Any],
    router_reference: Mapping[str, Any],
) -> dict[str, Any]:
    router_metrics = router_reference["metrics"]
    router_cases = {item["case_id"]: item for item in router_metrics["per_case"]}
    agent_cases = {item["case_id"]: item for item in agent_metrics["per_case"]}
    quadrants = {
        "router_correct_agent_correct": [],
        "router_wrong_agent_correct": [],
        "router_correct_agent_wrong": [],
        "both_wrong": [],
    }
    case_comparison = []
    for case_id in router_cases:
        router_case = router_cases[case_id]
        agent_case = agent_cases[case_id]
        router_correct = bool(router_case["correct"])
        agent_correct = bool(agent_case["correct"])
        if router_correct and agent_correct:
            quadrant = "router_correct_agent_correct"
        elif not router_correct and agent_correct:
            quadrant = "router_wrong_agent_correct"
        elif router_correct and not agent_correct:
            quadrant = "router_correct_agent_wrong"
        else:
            quadrant = "both_wrong"
        quadrants[quadrant].append(case_id)
        case_comparison.append(
            {
                "case_id": case_id,
                "question": agent_case["question"],
                "expected_tools": agent_case["expected_tools"],
                "router_task_type": router_case["predicted_task_type"],
                "router_tools": router_case["predicted_tools"],
                "router_correct": router_correct,
                "agent_task_type": agent_case["predicted_task_type"],
                "agent_tools": agent_case["predicted_tools"],
                "agent_correct": agent_correct,
                "quadrant": quadrant,
            }
        )
    metric_keys = (
        "tool_selection_accuracy",
        "exact_tool_set_match_accuracy",
        "task_type_accuracy",
        "unnecessary_tool_call_rate",
        "missing_required_tool_rate",
        "unanswerable_no_tool_routing_accuracy",
    )
    router_aggregate = router_metrics["aggregate"]
    agent_aggregate = agent_metrics["aggregate"]
    return {
        "metric_differences_agent_minus_router": {
            key: agent_aggregate[key] - router_aggregate[key] for key in metric_keys
        },
        "quadrants": quadrants,
        "quadrant_counts": {key: len(value) for key, value in quadrants.items()},
        "hyb_008": next(
            item for item in case_comparison if item["case_id"] == "HYB-008"
        ),
        "case_comparison": case_comparison,
    }


def _build_report(
    metrics: Mapping[str, Any],
    comparison: Mapping[str, Any],
    reproduction: Mapping[str, Any],
) -> str:
    aggregate = metrics["aggregate"]
    router = reproduction["router_reference"]["metrics"]["aggregate"]
    performance = reproduction["performance"]
    lines = [
        "# Phase 10 LLM Planner / LangGraph Agent Baseline",
        "",
        "> Immutable first evaluation of the preregistered prompt and model configuration.",
        "",
        "## Architecture and controls",
        "",
        "- Flow: Question → OpenAI typed Planner → Pydantic validation → deterministic Tool executor → final status",
        "- LangGraph: `planner → validate_plan → execute_plan → finalize`; no conditional edge or loop",
        "- Evaluation mode: plan-only; Phase 8 Tools were not called",
        "- No answer generation, Grader, retry, query rewrite, abstention workflow, or post-result tuning",
        f"- Model: `{reproduction['model']['name']}`; temperature {reproduction['model']['temperature']}; reasoning `{reproduction['model']['reasoning_effort']}`",
        f"- Provider pacing: minimum {reproduction['model']['request_pacing']['minimum_request_start_interval_seconds']:.1f}s between request starts; automatic retry disabled",
        f"- Provider checkpoint: manual resume enabled; {reproduction['checkpoint']['resumed_case_count']} completed cases reused in this run",
        f"- Prompt SHA-256: `{reproduction['prompt_sha256']}`",
        f"- Config SHA-256: `{reproduction['config_sha256']}`",
        "",
        "## Routing metrics — Agent vs deterministic Router",
        "",
        "| Metric | Router | Agent | Agent − Router |",
        "|---|---:|---:|---:|",
    ]
    keys = (
        ("Tool Selection Accuracy", "tool_selection_accuracy"),
        ("Exact Tool Set Match Accuracy", "exact_tool_set_match_accuracy"),
        ("Task Type Accuracy", "task_type_accuracy"),
        ("Unnecessary Tool Call Rate", "unnecessary_tool_call_rate"),
        ("Missing Required Tool Rate", "missing_required_tool_rate"),
        ("Unanswerable / no-tool Accuracy", "unanswerable_no_tool_routing_accuracy"),
    )
    differences = comparison["metric_differences_agent_minus_router"]
    for label, key in keys:
        lines.append(
            f"| {label} | {router[key]:.6f} | {aggregate[key]:.6f} | {differences[key]:+.6f} |"
        )
    lines.extend(
        [
            "",
            "## Per-tool metrics",
            "",
            "| Tool | Precision | Recall | F1 | TP | FP | FN |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for tool, value in metrics["per_tool"].items():
        lines.append(
            f"| `{tool}` | {value['precision']:.6f} | {value['recall']:.6f} | {value['f1']:.6f} | {value['tp']} | {value['fp']} | {value['fn']} |"
        )
    lines.extend(
        [
            "",
            "## By question type",
            "",
            "| Type | Cases | Exact set | Tool selection | Task type | Unnecessary | Missing |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for category, value in metrics["by_question_type"].items():
        lines.append(
            f"| `{category}` | {value['case_count']} | {value['exact_tool_set_match_accuracy']:.6f} | {value['tool_selection_accuracy']:.6f} | {value['task_type_accuracy']:.6f} | {value['unnecessary_tool_call_rate']:.6f} | {value['missing_required_tool_rate']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Router/Agent outcome matrix",
            "",
        ]
    )
    for key, values in comparison["quadrants"].items():
        lines.append(f"- `{key}`: {len(values)} — {', '.join(values) or 'none'}")
    hyb = comparison["hyb_008"]
    lines.extend(
        [
            "",
            "## HYB-008",
            "",
            f"- Expected: `{hyb['expected_tools']}`",
            f"- Router: `{hyb['router_tools']}` ({'correct' if hyb['router_correct'] else 'wrong'})",
            f"- Agent: `{hyb['agent_tools']}` ({'correct' if hyb['agent_correct'] else 'wrong'})",
            "- The generic preregistered prompt was not tuned to this case after evaluation.",
            "",
            "## Latency, token usage, and estimated cost",
            "",
            "| System | Mean latency / case | Total input | Cached input | Total output | Estimated cost | Avg Tool calls |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| Router | {performance['router_latency_ms']['mean']:.3f} ms | 0 | 0 | 0 | $0.000000 | {performance['router_average_tool_call_count']:.3f} |",
            f"| Agent Planner | {performance['agent_planning_latency_ms']['mean']:.3f} ms | {performance['tokens']['input']} | {performance['tokens']['cached_input']} | {performance['tokens']['output']} | ${performance['estimated_cost_usd']:.6f} | {performance['agent_average_tool_call_count']:.3f} |",
            "",
            "Planning latency excludes Tool execution because the frozen Phase 10 evaluation is plan-only.",
            "",
            "## Case-level results",
            "",
            "| Case | Expected | Agent | Exact | Task type | Failure |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in metrics["per_case"]:
        expected = ", ".join(item["expected_tools"]) or "no tools"
        predicted = ", ".join(item["predicted_tools"]) or "no tools"
        lines.append(
            f"| `{item['case_id']}` | {expected} | {predicted} | {'yes' if item['correct'] else 'no'} | `{item['predicted_task_type']}` | {item['failure_reason'] or '—'} |"
        )
    lines.extend(
        [
            "",
            "The full typed plan, per-Tool reason/input, token usage, latency, validation errors, and plan-only Tool result are in `plans.jsonl`.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_failure_analysis(
    metrics: Mapping[str, Any], comparison: Mapping[str, Any]
) -> str:
    failures = [item for item in metrics["per_case"] if not item["correct"]]
    lines = [
        "# Agent Baseline v1 — Planning Failure Analysis",
        "",
        "> Generated only after all 30 first-pass plans were fixed. No prompt tuning followed.",
        "",
        f"- Exact Tool-set failures: {len(failures)} / {metrics['aggregate']['case_count']}",
        f"- Unnecessary Tool calls: {metrics['aggregate']['unnecessary_tool_call_count']}",
        f"- Missing required Tools: {metrics['aggregate']['missing_required_tool_count']}",
        "",
        "## Comparison quadrants",
        "",
    ]
    for key, values in comparison["quadrants"].items():
        lines.append(f"- `{key}`: {', '.join(values) or 'none'}")
    lines.extend(["", "## Agent failures", ""])
    if not failures:
        lines.extend(
            [
                "No exact Tool-set failure occurred on this small frozen evaluation set.",
                "This does not establish out-of-distribution planning reliability.",
                "",
            ]
        )
    for item in failures:
        lines.extend(
            [
                f"### {item['case_id']}",
                "",
                f"- Question: {item['question']}",
                f"- Expected: `{item['expected_tools']}`",
                f"- Predicted: `{item['predicted_tools']}`",
                f"- Failure: {item['failure_reason']}",
                f"- Planner summary: {item['routing_reason']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Boundaries",
            "",
            "- Tool correctness and answer quality were not evaluated because Tools were not executed.",
            "- Structured-output compliance is necessary but does not prove semantic correctness.",
            "- No failure was used to alter the prompt, model, schema, corpus, Gold, or deterministic Router.",
            "",
        ]
    )
    return "\n".join(lines)


def write_immutable_agent_artifacts(
    *,
    output_dir: str | Path,
    plans: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    comparison: Mapping[str, Any],
    reproduction: Mapping[str, Any],
    config_path: Path,
    prompt_path: Path,
) -> dict[str, Any]:
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Agent baseline: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        with (temp / "plans.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for item in plans:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        with (temp / "case_results.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as handle:
            for item in metrics["per_case"]:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        (temp / "metrics.json").write_text(_json_text(metrics), encoding="utf-8")
        (temp / "comparison.json").write_text(
            _json_text(comparison), encoding="utf-8"
        )
        (temp / "reproducibility.json").write_text(
            _json_text(reproduction), encoding="utf-8"
        )
        shutil.copyfile(config_path, temp / "config_snapshot.json")
        shutil.copyfile(prompt_path, temp / "prompt.md")
        (temp / "REPORT.md").write_text(
            _build_report(metrics, comparison, reproduction), encoding="utf-8"
        )
        (temp / "FAILURE_ANALYSIS.md").write_text(
            _build_failure_analysis(metrics, comparison), encoding="utf-8"
        )
        artifact_names = (
            "plans.jsonl",
            "case_results.jsonl",
            "metrics.json",
            "comparison.json",
            "reproducibility.json",
            "config_snapshot.json",
            "prompt.md",
            "REPORT.md",
            "FAILURE_ANALYSIS.md",
        )
        manifest = {
            "agent_version": EXPECTED_AGENT_VERSION,
            "status": "frozen",
            "configuration_status": "preregistered_first_evaluation",
            "created_at_utc": reproduction["created_at_utc"],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(temp / name),
                    "bytes": (temp / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite agent_baseline_v1; use a new version for any later prompt or model."
            ),
        }
        (temp / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        temp.replace(target)
        return manifest
    except BaseException:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def run_agent_baseline(
    project_root: str | Path,
    *,
    output_dir: str | Path,
    backend: OpenAIPlannerBackend | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = Path(project_root).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Agent baseline: {output}")
    inputs = load_frozen_routing_evaluation(root)
    dependencies = _validate_frozen_dependencies(root)
    config, _, prompt_path = load_agent_config(root / "config/agent_planner_v1.json")
    checkpoint_dir = output.parent / f".{output.name}_checkpoint"
    checkpoint_metadata = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "agent_version": EXPECTED_AGENT_VERSION,
        "model": config["model"],
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "eval_dataset_sha256": inputs["eval_sha256"],
        "eval_manifest_sha256": inputs["eval_manifest_sha256"],
        "case_count": len(inputs["cases"]),
    }
    checkpoint_plans = load_or_create_plan_checkpoint(
        checkpoint_dir=checkpoint_dir,
        cases=inputs["cases"],
        expected_metadata=checkpoint_metadata,
    )
    resumed_case_count = len(checkpoint_plans)
    planner = backend or OpenAIPlannerBackend(config_path=inputs["root"] / "config/agent_planner_v1.json")
    workflow = PlanningAgentWorkflow(
        backend=planner,
        executor=DeterministicToolExecutor(),
        execute_tools=False,
    )
    request_pacing_seconds = (
        MIN_REQUEST_START_INTERVAL_SECONDS
        if isinstance(planner, OpenAIPlannerBackend)
        else 0.0
    )
    plans = produce_agent_plans(
        inputs["cases"],
        workflow,
        min_request_interval_seconds=request_pacing_seconds,
        initial_plans=checkpoint_plans,
        checkpoint_path=checkpoint_dir / "plans.jsonl",
    )

    # Gold and Router results enter only after all first-pass predictions exist.
    predictions = [dict(item["prediction"]) for item in plans]
    metrics = evaluate_routing(inputs["cases"], predictions)
    router_reference = measure_router_reference(
        cases=inputs["cases"], router_dir=dependencies["router_dir"]
    )
    comparison = build_comparison(
        agent_metrics=metrics, router_reference=router_reference
    )

    planner_metrics = [item["planner_metrics"] for item in plans]
    latencies = [float(item.get("latency_ms", 0.0)) for item in planner_metrics]
    usages = [item.get("usage", {}) for item in planner_metrics]
    performance = {
        "agent_planning_latency_ms": _distribution(latencies),
        "router_latency_ms": router_reference["latency_ms"],
        "tokens": {
            "input": sum(int(item.get("input_tokens", 0)) for item in usages),
            "cached_input": sum(
                int(item.get("cached_input_tokens", 0)) for item in usages
            ),
            "output": sum(int(item.get("output_tokens", 0)) for item in usages),
            "total": sum(int(item.get("total_tokens", 0)) for item in usages),
        },
        "estimated_cost_usd": sum(
            float(item.get("estimated_cost_usd", 0.0)) for item in usages
        ),
        "agent_average_tool_call_count": (
            metrics["aggregate"]["predicted_tool_call_count"] / len(inputs["cases"])
        ),
        "router_average_tool_call_count": (
            router_reference["metrics"]["aggregate"]["predicted_tool_call_count"]
            / len(inputs["cases"])
        ),
        "total_request_pacing_wait_ms": sum(
            float(item.get("request_pacing_wait_ms", 0.0)) for item in plans
        ),
    }
    reproduction = {
        "agent_version": EXPECTED_AGENT_VERSION,
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
            "request_pacing": {
                "account_rpm_limit": ACCOUNT_RPM_LIMIT,
                "minimum_request_start_interval_seconds": request_pacing_seconds,
                "automatic_retry": False,
            },
        },
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "prompt_sha256": EXPECTED_PROMPT_SHA256,
        "eval_dataset_version": inputs["dataset"]["dataset_version"],
        "eval_dataset_sha256": inputs["eval_sha256"],
        "eval_manifest_sha256": inputs["eval_manifest_sha256"],
        "router_manifest_sha256": ROUTER_MANIFEST_SHA256,
        "hybrid_manifest_sha256": dependencies["hybrid_manifest_sha256"],
        "corpus_chunks_sha256": dependencies["corpus_chunks_sha256"],
        "corpus_manifest_sha256": dependencies["corpus_manifest_sha256"],
        "case_count": len(inputs["cases"]),
        "input_fields_exposed_to_planner": ["question"],
        "graph": config["graph"],
        "checkpoint": {
            "version": CHECKPOINT_VERSION,
            "resumed_case_count": resumed_case_count,
            "new_case_count": len(plans) - resumed_case_count,
            "manual_resume_only": True,
            "automatic_provider_retry": False,
        },
        "performance": performance,
        "router_reference": router_reference,
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "pydantic_version": importlib.metadata.version("pydantic"),
            "openai_version": importlib.metadata.version("openai"),
            "httpx2_version": importlib.metadata.version("httpx2"),
            "brotli_version": importlib.metadata.version("Brotli"),
            "langgraph_version": importlib.metadata.version("langgraph"),
        },
        "controls": {
            "llm_used": True,
            "typed_plan_validated": True,
            "langgraph_used": True,
            "tool_execution_performed": False,
            "answer_generation_performed": False,
            "case_id_exposed_to_planner": False,
            "category_exposed_to_planner": False,
            "required_tools_exposed_to_planner": False,
            "gold_exposed_to_planner": False,
            "gold_applied_after_all_predictions": True,
            "direct_model_tool_calls": False,
            "post_result_tuning_performed": False,
            "grader_retry_or_rewrite_used": False,
            "manual_provider_checkpoint_resume_supported": True,
            "automatic_provider_retry_used": False,
            "abstention_workflow_used": False,
            "phase11_started": False,
        },
    }
    manifest = write_immutable_agent_artifacts(
        output_dir=output,
        plans=plans,
        metrics=metrics,
        comparison=comparison,
        reproduction=reproduction,
        config_path=inputs["root"] / "config/agent_planner_v1.json",
        prompt_path=prompt_path,
    )
    finalize_plan_checkpoint(
        checkpoint_dir=checkpoint_dir,
        final_manifest_sha256=sha256_file(output / "manifest.json"),
    )
    frozen_checks = {
        inputs["eval_path"]: inputs["eval_sha256"],
        inputs["manifest_path"]: inputs["eval_manifest_sha256"],
        dependencies["router_manifest_path"]: ROUTER_MANIFEST_SHA256,
        dependencies["hybrid_manifest_path"]: HYBRID_MANIFEST_SHA256,
        dependencies["chunks_path"]: CORPUS_CHUNKS_SHA256,
        dependencies["corpus_manifest_path"]: CORPUS_MANIFEST_SHA256,
    }
    for path, expected_hash in frozen_checks.items():
        if sha256_file(path) != expected_hash:
            raise RuntimeError(f"Frozen dependency changed during evaluation: {path}")
    return manifest, metrics, comparison
