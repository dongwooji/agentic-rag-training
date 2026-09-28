"""Real-component End-to-End smoke test with sanitized observability."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter, sleep
from typing import Any, Mapping
import unicodedata

from pydantic import BaseModel

from src.agent.executor import DeterministicToolExecutor
from src.answer.generator import FinalResponseLayer
from src.answer.provider import OpenAIFinalAnswerProvider
from src.database.config import DatabaseConfig
from src.grading.runtime_grader import RuntimeEvidenceGrader
from src.grading.runtime_provider import OpenAIRuntimeEvidenceProvider
from src.graph.workflow import AgenticRAGWorkflow
from src.recovery.agent import EvidenceRecoveryAgent
from src.recovery.provider import OpenAIEvidenceRecoveryProvider
from src.tools.literature import LiteratureTool
from src.tools.metric import MetricTool
from src.tools.training_log import (
    PsycopgTrainingRepository,
    TrainingLogTool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "reports/END_TO_END_SMOKE_TEST.md"
SMOKE_VERSION = "end_to_end_smoke_v1"

FROZEN_HASHES = {
    "data/evaluation/eval_dataset_v1.manifest.json": (
        "4abd207041c4dd69df73e85e6d50b9288fe9f0d9c19f1ce33d06ae62e008d972"
    ),
    "data/literature/manifests/corpus_v1.json": (
        "84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66"
    ),
    "reports/baselines/hybrid_baseline_v1/manifest.json": (
        "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
    ),
    "reports/baselines/router_baseline_v1/manifest.json": (
        "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
    ),
    "reports/baselines/grader_baseline_v1/manifest.json": (
        "e32ab0476ba7ac82b4f16768e08a4233ff3695012426a0e036587c864bf093ba"
    ),
    "reports/baselines/grader_v2_baseline/manifest.json": (
        "870764d296dd469050d4699140cf5ef0508eb8d31585214082c0d8df3b963a5d"
    ),
    "reports/baselines/grader_v2_1_baseline/manifest.json": (
        "b96fc923d4ce2fe82f6d10e2dfe26e4b8cb257aa7752bd39e2743b8b49264556"
    ),
}

PROTECTED_FROZEN_REFERENCES = {
    "reports/baselines/dense_baseline_v1/manifest.json": (
        "8edea5983d63e588515a5330c2107679357c73e977502f444eb9ee5d25b2170a"
    ),
    "reports/baselines/agent_baseline_v1/manifest.json": (
        "7e6a584b83134374e315af218660f7113481ec772c746810a8bc07fa86997cce"
    ),
}


@dataclass(frozen=True)
class SmokeCase:
    case_id: str
    question: str
    literature_subquestion: str | None = None
    initial_query: str | None = None
    tool_inputs: dict[str, dict[str, Any]] | None = None


SMOKE_CASES = (
    SmokeCase(
        case_id="SMOKE-LIT-001",
        question=(
            "건강한 성인의 저항훈련 연구 문헌에서 세트 간 휴식시간이 근력과 "
            "근육량 증가에 영향을 주는지, 현재 근거의 한계와 함께 요약해줘."
        ),
        literature_subquestion=(
            "건강한 성인에서 저항훈련 세트 간 휴식시간이 근력과 근육량 증가에 "
            "미치는 영향과 근거의 한계"
        ),
        initial_query=(
            "resistance training inter-set rest interval muscle strength hypertrophy "
            "healthy adults evidence limitations"
        ),
    ),
    SmokeCase(
        case_id="SMOKE-METRIC-001",
        question=(
            "내 운동 기록에서 Deadlift (Barbell)의 처음 3-session과 최근 "
            "3-session median e1RM을 비교해줘."
        ),
        tool_inputs={
            "query_training_log": {
                "operation": "exercise_records",
                "canonical_exercise_name": "Deadlift (Barbell)",
                "limit": 500,
                "include_lineage": False,
            },
            "compute_metrics": {
                "operation": "first_last_n_session_median_e1rm",
                "records_source": "query_training_log",
                "canonical_exercise_name": "Deadlift (Barbell)",
                "n_sessions": 3,
            },
        },
    ),
    SmokeCase(
        case_id="SMOKE-RECOVERY-001",
        question=(
            "저항훈련 경험이 있는 70세 이상 남성에서 30초와 3분의 세트 간 "
            "휴식이 종아리 근비대에 만드는 직접 차이를 연구 문헌 근거로 정량화해줘."
        ),
        literature_subquestion=(
            "저항훈련 경험이 있는 70세 이상 남성에서 30초 대 3분 세트 간 "
            "휴식의 종아리 근비대 직접 정량 비교"
        ),
        initial_query=(
            "resistance-trained men age 70 inter-set rest 30 seconds versus 3 minutes "
            "calf hypertrophy direct quantitative comparison"
        ),
    ),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_frozen_integrity(root: Path = PROJECT_ROOT) -> dict[str, str]:
    actual: dict[str, str] = {}
    for relative, expected in FROZEN_HASHES.items():
        path = root / relative
        digest = _sha256_file(path)
        if digest != expected:
            raise RuntimeError(f"Frozen artifact changed: {relative}")
        actual[relative] = digest
    return actual


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _collect_questions(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "question" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_collect_questions(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_questions(item))
    return found


def validate_smoke_case_isolation(root: Path = PROJECT_ROOT) -> None:
    sources = [
        root / "data/evaluation/eval_dataset_v1.json",
        root / "data/evaluation/grader_v2_heldout_v1/heldout_inputs.json",
        root / "data/evaluation/grader_v2_1_heldout_v1/heldout_inputs.json",
    ]
    frozen_questions: set[str] = set()
    for path in sources:
        payload = json.loads(path.read_text(encoding="utf-8"))
        frozen_questions.update(_normalize(item) for item in _collect_questions(payload))
    smoke_questions = [_normalize(item.question) for item in SMOKE_CASES]
    if len(smoke_questions) != len(set(smoke_questions)):
        raise RuntimeError("Smoke questions must be unique")
    overlap = sorted(set(smoke_questions) & frozen_questions)
    if overlap:
        raise RuntimeError(f"Smoke question exactly reuses frozen evaluation input: {overlap}")


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
    if isinstance(value, (datetime,)):
        return value.isoformat()
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if not isinstance(value, str):
        return value
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_API_KEY]", value)
    text = re.sub(
        r"(?i)(password\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        text,
    )
    return text


class SharedProviderPacer:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self._last_start: float | None = None

    def wait(self) -> float:
        waited = 0.0
        now = perf_counter()
        if self._last_start is not None:
            remaining = self.min_interval_seconds - (now - self._last_start)
            if remaining > 0:
                sleep(remaining)
                waited = remaining
        self._last_start = perf_counter()
        return waited * 1000.0


class TracedProvider:
    """Record metadata/usage but never the secret or raw model output."""

    def __init__(
        self,
        component: str,
        delegate: Any,
        pacer: SharedProviderPacer,
        event_sink: list[dict[str, Any]] | None = None,
    ) -> None:
        self.component = component
        self.delegate = delegate
        self.pacer = pacer
        self.calls: list[dict[str, Any]] = []
        self.event_sink = event_sink
        for field in ("model", "prompt_sha256", "config_sha256"):
            if hasattr(delegate, field):
                setattr(self, field, getattr(delegate, field))

    def invoke(self, value: Any) -> Any:
        pacing_ms = self.pacer.wait()
        result = self.delegate.invoke(value)
        usage = _json_safe(getattr(result, "token_usage", {}))
        error_code = getattr(result, "error_code", None)
        record = {
                "component": self.component,
                "request_number": len(self.calls) + 1,
                "pacing_ms": round(pacing_ms, 3),
                "model": getattr(result, "model", None),
                "response_id": getattr(result, "response_id", None),
                "latency_ms": round(float(getattr(result, "latency_ms", 0.0)), 3),
                "token_usage": usage,
                "response_received": bool(
                    getattr(result, "response_metadata", {}).get("response_received")
                ),
                "error_code": (
                    error_code.value if isinstance(error_code, Enum) else error_code
                ),
                "has_error": bool(getattr(result, "error_detail", None)),
            }
        self.calls.append(record)
        if self.event_sink is not None:
            self.event_sink.append(
                {"sequence": len(self.event_sink) + 1, **record}
            )
        return result


class TracedTool:
    def __init__(self, name: str, delegate: Any) -> None:
        self.name = name
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []

    def execute(self, request: Any) -> Any:
        started = perf_counter()
        response: Any | None = None
        try:
            response = self.delegate.execute(request)
            return response
        finally:
            payload = _json_safe(response) if response is not None else {}
            result = payload.get("result") if isinstance(payload, Mapping) else None
            hits = result.get("hits", []) if isinstance(result, Mapping) else []
            self.calls.append(
                {
                    "tool": self.name,
                    "operation": str(getattr(request, "operation", "")),
                    "query": getattr(request, "query", None),
                    "status": payload.get("status") if isinstance(payload, Mapping) else "raised",
                    "result_count": len(hits) if isinstance(hits, list) else None,
                    "latency_ms": round((perf_counter() - started) * 1000.0, 3),
                    "retrieval_latency_ms": (
                        result.get("latency_ms") if isinstance(result, Mapping) else None
                    ),
                }
            )


class TracedGrader:
    def __init__(self, delegate: RuntimeEvidenceGrader) -> None:
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []

    def grade(self, grader_input: Any) -> Any:
        result = self.delegate.grade(grader_input)
        payload = _json_safe(result)
        self.calls.append(
            {
                "execution_status": payload.get("execution_status"),
                "evidence_sufficient": payload.get("evidence_sufficient"),
                "missing_component_ids": payload.get("missing_component_ids", []),
                "components": [
                    {
                        "component_id": item.get("component_id"),
                        "requirement": item.get("requirement"),
                        "status": item.get("status"),
                        "supporting_chunk_ids": item.get("supporting_chunk_ids", []),
                    }
                    for item in payload.get("components", [])
                ],
                "error_code": payload.get("error_code"),
            }
        )
        return result


class TracedRecoveryAgent:
    def __init__(self, delegate: EvidenceRecoveryAgent) -> None:
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []

    def generate(self, recovery_input: Any) -> Any:
        result = self.delegate.generate(recovery_input)
        payload = _json_safe(result)
        self.calls.append(
            {
                "execution_status": payload.get("execution_status"),
                "target_component_ids": payload.get("target_component_ids", []),
                "recovery_query": payload.get("recovery_query"),
                "preserved_terms": payload.get("preserved_terms", []),
                "error_code": (payload.get("error") or {}).get("code"),
            }
        )
        return result


def _slice_lengths(*collections: list[Any]) -> tuple[int, ...]:
    return tuple(len(item) for item in collections)


def _summarize_case(
    case: SmokeCase,
    state: Mapping[str, Any],
    *,
    elapsed_ms: float,
    tool_calls: list[dict[str, Any]],
    grader_calls: list[dict[str, Any]],
    recovery_calls: list[dict[str, Any]],
    provider_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    final_response = state.get("final_response") or {}
    route = state.get("route") or {}
    literature_calls = [item for item in tool_calls if item["tool"] == "search_literature"]
    initial_count = len(state.get("literature_evidence", []) or [])
    fused_count = len(state.get("fused_evidence", []) or [])
    return _redact(
        {
            "case_id": case.case_id,
            "question": case.question,
            "route": {
                "status": route.get("status"),
                "task_type": route.get("task_type"),
                "selected_tools": route.get("selected_tools", []),
                "execution_order": route.get("execution_order", []),
                "rule_id": (route.get("rule_match") or {}).get("rule_id"),
            },
            "initial_query": state.get("initial_query"),
            "initial_retrieval_count": initial_count,
            "tool_calls": tool_calls,
            "grader_calls": grader_calls,
            "initial_grader_result": grader_calls[0] if grader_calls else None,
            "final_grader_result": grader_calls[-1] if grader_calls else None,
            "recovery_executed": bool(recovery_calls),
            "recovery_calls": recovery_calls,
            "recovery_queries": [
                item.get("recovery_query")
                for item in recovery_calls
                if item.get("recovery_query")
            ],
            "retry_count": state.get("retry_count", 0),
            "query_history": state.get("query_history", []),
            "fusion_rounds": len(state.get("fusion_history", []) or []),
            "fusion_result_counts": [
                len(item.get("evidence", []))
                for item in state.get("fusion_history", []) or []
            ],
            "final_evidence_count": fused_count,
            "final_status": state.get("final_status"),
            "answer_text": final_response.get("answer_text"),
            "used_tool_result_ids": (
                (final_response.get("provenance") or {}).get("used_tool_result_ids", [])
            ),
            "used_literature_chunk_ids": final_response.get(
                "used_literature_chunk_ids", []
            ),
            "case_latency_ms": round(elapsed_ms, 3),
            "api_request_count": len(provider_calls),
            "api_calls": provider_calls,
            "tool_call_count": len(tool_calls),
            "literature_call_count": len(literature_calls),
            "structured_errors": state.get("errors", []),
        }
    )


def _md_json(value: Any) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```"


def render_report(
    cases: list[dict[str, Any]],
    *,
    hashes: Mapping[str, str],
    min_interval_seconds: float,
    started_at: str,
) -> str:
    lines = [
        "# End-to-End Integration Smoke Test",
        "",
        f"**Status:** {'COMPLETED' if cases else 'NOT RUN'}  ",
        "**Purpose:** actual-component connectivity smoke test; not a performance evaluation  ",
        f"**Smoke version:** `{SMOKE_VERSION}`  ",
        f"**Started at:** `{started_at}`  ",
        f"**Shared provider pacing:** `{min_interval_seconds:.2f}s` minimum between API requests  ",
        "**Secrets:** API key and PostgreSQL password were never persisted or printed",
        "",
        "## Scope and controls",
        "",
        "The run used the deterministic Router, PostgreSQL Training Log/Metric Tool path, frozen Phase 7 Hybrid Literature Tool, Runtime Evidence Grader, Evidence Recovery Agent, deterministic recovery fusion, LangGraph workflow, and Final Answer/Abstain layer. The three questions are development-only smoke inputs and exact-match isolation was checked against the frozen eval and Grader held-out inputs.",
        "",
        "No ranking, Router, Grader, Recovery, fusion, prompt semantics, `MAX_RETRY=2`, Top-10 budget, Gold, evaluation input, or frozen baseline artifact was tuned from these results.",
        "",
        "## Summary",
        "",
        "| Case | Route | Path | Recovery | API requests | Final status |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in cases:
        path = " → ".join(item["route"]["execution_order"] or ["no_tool"])
        if item["grader_calls"]:
            path += " → grader"
        if item["recovery_executed"]:
            path += " → recovery → literature → fusion → grader"
        path += " → final_response"
        lines.append(
            f"| {item['case_id']} | {item['route']['task_type']} | {path} | "
            f"{item['retry_count']} | {item['api_request_count']} | {item['final_status']} |"
        )

    for item in cases:
        lines.extend(
            [
                "",
                f"## {item['case_id']}",
                "",
                f"**Question:** {item['question']}",
                "",
                f"- Router: `{item['route']['task_type']}` via `{item['route']['rule_id']}`",
                f"- Tools/order: `{item['route']['execution_order']}`",
                f"- Initial query: `{item['initial_query']}`",
                f"- Initial retrieval count: `{item['initial_retrieval_count']}`",
                f"- Recovery executed: `{item['recovery_executed']}`",
                f"- Recovery queries: `{item['recovery_queries']}`",
                f"- Retry count: `{item['retry_count']}`",
                f"- Fusion rounds/result counts: `{item['fusion_rounds']}` / `{item['fusion_result_counts']}`",
                f"- Final evidence count: `{item['final_evidence_count']}`",
                f"- Final status: `{item['final_status']}`",
                f"- Used Tool result IDs: `{item['used_tool_result_ids']}`",
                f"- Used literature chunk IDs: `{item['used_literature_chunk_ids']}`",
                f"- Case latency: `{item['case_latency_ms']:.3f} ms`",
                f"- API / Tool calls: `{item['api_request_count']}` / `{item['tool_call_count']}`",
                "",
                "### Grader state transitions",
                "",
                _md_json(item["grader_calls"]),
                "",
                "### Tool/API trace",
                "",
                _md_json({"tools": item["tool_calls"], "api": item["api_calls"]}),
                "",
                "### Final user response",
                "",
                item["answer_text"] or "(no answer text)",
                "",
                "### Structured errors",
                "",
                _md_json(item["structured_errors"]),
                "",
                "**Connection issue / harness fix:** none recorded by the harness.",
            ]
        )

    lines.extend(
        [
            "",
            "## Frozen integrity",
            "",
            "Readable frozen manifests were hash-checked before and after the run:",
            "",
        ]
    )
    for path, digest in hashes.items():
        lines.append(f"- `{path}`: `{digest}`")
    lines.extend(
        [
            "",
            "The access-controlled Dense and Agent baseline directories were not written. Their recorded reference hashes are:",
            "",
        ]
    )
    for path, digest in PROTECTED_FROZEN_REFERENCES.items():
        lines.append(f"- `{path}`: `{digest}` (reference; OS access-controlled)")
    lines.extend(
        [
            "",
            "## Integration fixes",
            "",
            "No result-driven policy or prompt changes are permitted. Any actual harness wiring fixes discovered during execution must be listed here before close-out.",
            "",
            "## Regression suite",
            "",
            "Pending execution after the real smoke run.",
            "",
            "## Baseline readiness",
            "",
            "Pending smoke-result review. This document is not a frozen End-to-End baseline artifact.",
            "",
        ]
    )
    return "\n".join(lines)


def run_end_to_end_smoke(
    *,
    database_config: DatabaseConfig,
    postgres_password: str,
    openai_api_key: str,
    model_cache: Path,
    output_path: Path = DEFAULT_OUTPUT,
    min_interval_seconds: float = 6.2,
) -> list[dict[str, Any]]:
    if not postgres_password:
        raise ValueError("PostgreSQL password must be non-empty")
    if not openai_api_key:
        raise ValueError("OpenAI API key must be non-empty")
    started_at = datetime.now(timezone.utc).isoformat()
    before_hashes = verify_frozen_integrity()
    validate_smoke_case_isolation()

    pacer = SharedProviderPacer(min_interval_seconds)
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
            PsycopgTrainingRepository(
                config=database_config,
                password=postgres_password,
            )
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

    cases: list[dict[str, Any]] = []
    try:
        for case in SMOKE_CASES:
            print(f"Running {case.case_id}: {case.question}", flush=True)
            offsets = _slice_lengths(
                training_tool.calls,
                metric_tool.calls,
                literature_tool.calls,
                grader.calls,
                recovery.calls,
                grader_provider.calls,
                recovery_provider.calls,
                answer_provider.calls,
                api_events,
            )
            started = perf_counter()
            state = workflow.invoke(
                case.question,
                literature_subquestion=case.literature_subquestion,
                initial_query=case.initial_query,
                tool_inputs=case.tool_inputs,
            )
            elapsed_ms = (perf_counter() - started) * 1000.0
            tool_calls = [
                *training_tool.calls[offsets[0] :],
                *metric_tool.calls[offsets[1] :],
                *literature_tool.calls[offsets[2] :],
            ]
            provider_calls = api_events[offsets[8] :]
            cases.append(
                _summarize_case(
                    case,
                    state,
                    elapsed_ms=elapsed_ms,
                    tool_calls=tool_calls,
                    grader_calls=grader.calls[offsets[3] :],
                    recovery_calls=recovery.calls[offsets[4] :],
                    provider_calls=provider_calls,
                )
            )
            print(
                f"Completed {case.case_id}: {state.get('final_status')} "
                f"(retry_count={state.get('retry_count', 0)})",
                flush=True,
            )
    finally:
        literature_inner.close()

    after_hashes = verify_frozen_integrity()
    if before_hashes != after_hashes:
        raise RuntimeError("Frozen artifact hashes changed during smoke run")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_report(
            cases,
            hashes=after_hashes,
            min_interval_seconds=min_interval_seconds,
            started_at=started_at,
        ),
        encoding="utf-8",
    )
    return cases
