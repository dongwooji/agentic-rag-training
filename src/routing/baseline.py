"""One-shot immutable evaluation pipeline for router_baseline_v1."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import tempfile
from typing import Any, Mapping, Sequence

from src.evaluation.routing_metrics import evaluate_routing

from .contracts import RouterInput
from .deterministic import DeterministicRouter, EXPECTED_ROUTER_VERSION


EVAL_DATASET_VERSION = "eval_dataset_v1"
FROZEN_EVAL_SHA256 = (
    "1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509"
)
FROZEN_EVAL_MANIFEST_SHA256 = (
    "4abd207041c4dd69df73e85e6d50b9288fe9f0d9c19f1ce33d06ae62e008d972"
)
EXPECTED_CONFIG_SHA256 = (
    "034f72ea19ca6c364ac96e66402626bfdcc1345be677245202795bbe115acb77"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_frozen_routing_evaluation(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    eval_path = root / "data/evaluation/eval_dataset_v1.json"
    manifest_path = root / "data/evaluation/eval_dataset_v1.manifest.json"
    config_path = root / "config/router_baseline_v1.json"
    eval_hash = sha256_file(eval_path)
    manifest_hash = sha256_file(manifest_path)
    config_hash = sha256_file(config_path)
    if eval_hash != FROZEN_EVAL_SHA256:
        raise RuntimeError("Frozen eval_dataset_v1 hash changed")
    if manifest_hash != FROZEN_EVAL_MANIFEST_SHA256:
        raise RuntimeError("Frozen evaluation manifest hash changed")
    if config_hash != EXPECTED_CONFIG_SHA256:
        raise RuntimeError("Preregistered Router configuration hash changed")
    dataset = json.loads(eval_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if dataset.get("dataset_version") != EVAL_DATASET_VERSION:
        raise RuntimeError("Evaluation dataset version changed")
    if dataset.get("status") != "frozen" or manifest.get("status") != "frozen":
        raise RuntimeError("Evaluation dataset is not frozen")
    if manifest.get("dataset_sha256") != eval_hash:
        raise RuntimeError("Evaluation manifest no longer authenticates dataset")
    cases = dataset.get("cases", [])
    if len(cases) != 30:
        raise RuntimeError(f"Expected 30 routing cases, found {len(cases)}")
    valid_tools = {
        "query_training_log",
        "compute_metrics",
        "search_literature",
    }
    for case in cases:
        if not str(case.get("question", "")).strip():
            raise RuntimeError(f"Empty frozen question: {case.get('id')}")
        if not set(case.get("required_tools", [])).issubset(valid_tools):
            raise RuntimeError(f"Unknown required Tool: {case.get('id')}")
    return {
        "root": root,
        "eval_path": eval_path,
        "manifest_path": manifest_path,
        "config_path": config_path,
        "eval_sha256": eval_hash,
        "eval_manifest_sha256": manifest_hash,
        "router_config_sha256": config_hash,
        "dataset": dataset,
        "cases": cases,
    }


def produce_routes(
    cases: Sequence[Mapping[str, Any]], router: DeterministicRouter
) -> list[dict[str, Any]]:
    """Only question text crosses the Router boundary."""

    routes = []
    for case in cases:
        plan = router.route(RouterInput(question=str(case["question"]))).to_dict()
        routes.append({"case_id": str(case["id"]), "question": case["question"], **plan})
    return routes


def build_failure_analysis(metrics: Mapping[str, Any]) -> str:
    failures = [item for item in metrics["per_case"] if not item["correct"]]
    lines = [
        "# Router Baseline v1 — Failure Analysis",
        "",
        "> Generated from the immutable first routing result. No post-result rule tuning was performed.",
        "",
        "## Summary",
        "",
        f"- Failed exact Tool-set cases: {len(failures)} / {metrics['aggregate']['case_count']}",
        f"- Unnecessary Tool selections: {metrics['aggregate']['unnecessary_tool_call_count']}",
        f"- Missing required Tool selections: {metrics['aggregate']['missing_required_tool_count']}",
        "",
    ]
    if failures:
        lines.extend(["## Case-level failures", ""])
        for item in failures:
            lines.extend(
                [
                    f"### {item['case_id']}",
                    "",
                    f"- Question: {item['question']}",
                    f"- Expected: `{item['expected_tools']}`",
                    f"- Predicted: `{item['predicted_tools']}`",
                    f"- Rule: `{item['matched_rule']}`",
                    f"- Failure: {item['failure_reason']}",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "## Case-level failures",
                "",
                "No exact Tool-set failures occurred in the frozen 30-case set.",
                "This does not establish robustness outside the small, preregistered taxonomy.",
                "",
            ]
        )
    lines.extend(
        [
            "## Diagnostic boundaries for later comparison",
            "",
            "- Hybrid lookup without Metric is a distinct rule; metric words do not add Metric unless log scope also matches.",
            "- Missing personal data overrides log/metric selection only when no literature request is present.",
            "- Ambiguous questions outside registered signal families return no Tool and require clarification.",
            "- Phase 10 should compare generalization, paraphrase robustness and planning—not tune this frozen baseline.",
            "",
        ]
    )
    return "\n".join(lines)


def build_report(metrics: Mapping[str, Any], reproduction: Mapping[str, Any]) -> str:
    aggregate = metrics["aggregate"]
    lines = [
        "# Phase 9 Deterministic Router Baseline",
        "",
        "> Immutable first evaluation of the preregistered question-text-only rule set.",
        "",
        "## Configuration",
        "",
        f"- Router: `{reproduction['router_version']}`",
        f"- Rule config SHA-256: `{reproduction['router_config_sha256']}`",
        f"- Evaluation: `{reproduction['eval_dataset_version']}` / `{reproduction['eval_dataset_sha256']}`",
        "- LLM, Tool execution, answer generation, LangGraph, Grader and rewrite: none",
        "- Post-evaluation tuning: none",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value | Count |",
        "|---|---:|---:|",
        f"| Tool Selection Accuracy | {aggregate['tool_selection_accuracy']:.6f} | {aggregate['tool_decision_count']} decisions |",
        f"| Exact Tool Set Match Accuracy | {aggregate['exact_tool_set_match_accuracy']:.6f} | {aggregate['exact_tool_set_match_count']}/{aggregate['case_count']} |",
        f"| Task Type Accuracy | {aggregate['task_type_accuracy']:.6f} | — |",
        f"| Unnecessary Tool Call Rate | {aggregate['unnecessary_tool_call_rate']:.6f} | {aggregate['unnecessary_tool_call_count']} |",
        f"| Missing Required Tool Rate | {aggregate['missing_required_tool_rate']:.6f} | {aggregate['missing_required_tool_count']} |",
        f"| Unanswerable / no-tool routing | {aggregate['unanswerable_no_tool_routing_accuracy']:.6f} | {aggregate['unanswerable_no_tool_correct_count']}/{aggregate['unanswerable_case_count']} |",
        "",
        "## Per-tool metrics",
        "",
        "| Tool | Precision | Recall | F1 | TP | FP | FN | TN |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tool, value in metrics["per_tool"].items():
        lines.append(
            f"| `{tool}` | {value['precision']:.6f} | {value['recall']:.6f} | "
            f"{value['f1']:.6f} | {value['tp']} | {value['fp']} | {value['fn']} | {value['tn']} |"
        )
    lines.extend(
        [
            "",
            "## By question type",
            "",
            "| Type | Cases | Exact set | Tool selection | Task type | Unnecessary rate | Missing rate |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for category, value in metrics["by_question_type"].items():
        lines.append(
            f"| `{category}` | {value['case_count']} | "
            f"{value['exact_tool_set_match_accuracy']:.6f} | "
            f"{value['tool_selection_accuracy']:.6f} | "
            f"{value['task_type_accuracy']:.6f} | "
            f"{value['unnecessary_tool_call_rate']:.6f} | "
            f"{value['missing_required_tool_rate']:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Case-level results",
            "",
            "| Case | Expected | Predicted | Correct | Rule | Failure |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in metrics["per_case"]:
        expected = ", ".join(item["expected_tools"]) or "no tools"
        predicted = ", ".join(item["predicted_tools"]) or "no tools"
        lines.append(
            f"| `{item['case_id']}` | {expected} | {predicted} | "
            f"{'yes' if item['correct'] else 'no'} | `{item['matched_rule']}` | "
            f"{item['failure_reason'] or '—'} |"
        )
    lines.extend(
        [
            "",
            "Detailed question text, reasons, confidence, matched patterns and failure fields are stored in `case_results.jsonl`.",
            "",
        ]
    )
    return "\n".join(lines)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_immutable_router_artifacts(
    *,
    output_dir: str | Path,
    routes: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    reproduction: Mapping[str, Any],
    rule_config: Mapping[str, Any],
) -> dict[str, Any]:
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite immutable Router baseline: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        with (temp / "routes.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for item in routes:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        with (temp / "case_results.jsonl").open(
            "w", encoding="utf-8", newline="\n"
        ) as handle:
            for item in metrics["per_case"]:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        (temp / "metrics.json").write_text(_json_text(metrics), encoding="utf-8")
        (temp / "reproducibility.json").write_text(
            _json_text(reproduction), encoding="utf-8"
        )
        (temp / "rule_config_snapshot.json").write_text(
            _json_text(rule_config), encoding="utf-8"
        )
        (temp / "FAILURE_ANALYSIS.md").write_text(
            build_failure_analysis(metrics), encoding="utf-8"
        )
        (temp / "REPORT.md").write_text(
            build_report(metrics, reproduction), encoding="utf-8"
        )
        artifact_names = (
            "routes.jsonl",
            "case_results.jsonl",
            "metrics.json",
            "reproducibility.json",
            "rule_config_snapshot.json",
            "FAILURE_ANALYSIS.md",
            "REPORT.md",
        )
        manifest = {
            "router_version": EXPECTED_ROUTER_VERSION,
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
                "Never overwrite router_baseline_v1; create a new version for any later rule set."
            ),
        }
        (temp / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        temp.replace(target)
        return manifest
    except BaseException:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def run_router_baseline(
    project_root: str | Path,
    *,
    output_dir: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = load_frozen_routing_evaluation(project_root)
    rule_config = json.loads(inputs["config_path"].read_text(encoding="utf-8"))
    router = DeterministicRouter(rule_config)
    routes = produce_routes(inputs["cases"], router)
    metrics = evaluate_routing(inputs["cases"], routes)
    reproduction = {
        "router_version": EXPECTED_ROUTER_VERSION,
        "status": "preregistered_first_evaluation",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "eval_dataset_version": EVAL_DATASET_VERSION,
        "eval_dataset_sha256": inputs["eval_sha256"],
        "eval_manifest_sha256": inputs["eval_manifest_sha256"],
        "router_config_sha256": inputs["router_config_sha256"],
        "case_count": len(inputs["cases"]),
        "input_fields_exposed_to_router": ["question"],
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "controls": {
            "llm_used": False,
            "tool_execution_performed": False,
            "answer_generation_performed": False,
            "case_id_exposed_to_router": False,
            "category_exposed_to_router": False,
            "required_tools_exposed_to_router": False,
            "gold_exposed_to_router": False,
            "post_result_tuning_performed": False,
            "langgraph_or_agent_used": False,
            "grader_retry_or_rewrite_used": False,
            "phase10_started": False,
        },
    }
    manifest = write_immutable_router_artifacts(
        output_dir=output_dir,
        routes=routes,
        metrics=metrics,
        reproduction=reproduction,
        rule_config=rule_config,
    )
    if sha256_file(inputs["eval_path"]) != inputs["eval_sha256"]:
        raise RuntimeError("Frozen evaluation dataset changed during Router evaluation")
    if sha256_file(inputs["config_path"]) != inputs["router_config_sha256"]:
        raise RuntimeError("Router configuration changed during evaluation")
    return manifest, metrics
