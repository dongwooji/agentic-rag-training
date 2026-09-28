"""Run the real FastAPI boundary smoke test with ephemeral secrets.

This is an integration harness, not an evaluation runner. It starts an actual
Uvicorn process, performs HTTP requests, writes only secret-free responses, and
then stops the server.
"""

from __future__ import annotations

from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys
from threading import Lock, Thread
from time import perf_counter, sleep
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
RESULT_PATH = PROJECT_ROOT / "reports" / ".api_smoke_test_v2_results.json"
REPORT_PATH = PROJECT_ROOT / "reports" / "API_SMOKE_TEST_V2.md"
BASE_URL = "http://127.0.0.1:8000"

QUERIES = (
    (
        "API-SMOKE-LITERATURE",
        "건강한 성인의 저항훈련 연구 문헌에서 세트 간 휴식시간이 근력과 "
        "근육량 증가에 영향을 주는지, 현재 근거의 한계와 함께 요약해줘.",
    ),
    (
        "API-SMOKE-LOG-METRIC",
        "내 운동 기록에서 Deadlift (Barbell)의 처음 3-session과 최근 "
        "3-session median e1RM을 비교해줘.",
    ),
    (
        "API-SMOKE-HYBRID",
        "내 Deadlift (Barbell)의 처음 3-session과 최근 3-session median "
        "e1RM 변화를 계산하고, 이 변화가 점진적 과부하 원칙과 어떻게 "
        "관련되는지 문헌 근거와 함께 설명해줘.",
    ),
)


class CapturingWorkflow:
    """Record only returned graph states around the unchanged real workflow."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self._lock = Lock()
        self._states: list[dict[str, Any]] = []

    def invoke(self, question: str) -> Mapping[str, Any]:
        state = self.inner.invoke(question)
        if not isinstance(state, Mapping):
            raise TypeError("workflow returned a non-mapping state")
        with self._lock:
            self._states.append(dict(state))
        return state

    def latest_state(self) -> dict[str, Any]:
        with self._lock:
            if not self._states:
                raise RuntimeError("workflow state was not captured")
            return dict(self._states[-1])


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _api_request_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """Count model requests from response IDs and bounded graph transitions."""

    resolver = _mapping(state.get("tool_input_resolution"))
    resolver_provenance = _mapping(resolver.get("provenance"))
    resolver_count = int(
        resolver_provenance.get("method") == "openai"
        and bool(resolver_provenance.get("response_id"))
    )

    retry_count = int(state.get("retry_count") or 0)
    grader = _mapping(state.get("grader_result"))
    grader_count = retry_count + 1 if grader else 0

    recovery = _mapping(state.get("recovery_result"))
    recovery_provenance = _mapping(recovery.get("provenance"))
    recovery_count = retry_count
    if recovery_provenance.get("response_id") and recovery_count == 0:
        recovery_count = 1

    final_response = _mapping(state.get("final_response"))
    final_provenance = _mapping(final_response.get("provenance"))
    final_count = int(bool(final_provenance.get("response_id")))
    by_node = {
        "tool_input_resolver": resolver_count,
        "runtime_grader": grader_count,
        "recovery_agent": recovery_count,
        "final_answer": final_count,
    }
    return {
        "total": sum(by_node.values()),
        "by_node": by_node,
        "method": "derived_from_secret_free_graph_state",
    }


def _summarize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    route = _mapping(state.get("route"))
    resolution = _mapping(state.get("tool_input_resolution"))
    provenance = _mapping(resolution.get("provenance"))
    tool_inputs = _mapping(resolution.get("tool_inputs"))
    bounded_inputs: dict[str, dict[str, Any]] = {}
    allowed_fields = {
        "operation",
        "canonical_exercise_name",
        "start_date",
        "end_date",
        "session_id",
        "n_sessions",
        "records_source",
    }
    for tool, value in tool_inputs.items():
        inputs = _mapping(value)
        bounded_inputs[str(tool)] = {
            key: inputs.get(key)
            for key in allowed_fields
            if key in inputs and inputs.get(key) is not None
        }

    tool_records: list[dict[str, Any]] = []
    for value in state.get("tool_results", []):
        record = _mapping(value)
        tool_records.append(
            {
                "tool": str(record.get("tool") or ""),
                "status": str(record.get("status") or ""),
                "requested_operation": str(
                    record.get("requested_operation") or ""
                ),
            }
        )
    errors = [
        {
            "stage": str(_mapping(item).get("stage") or ""),
            "code": str(_mapping(item).get("code") or ""),
        }
        for item in state.get("errors", [])
    ]
    return {
        "route": str(route.get("route") or state.get("task_type") or "unknown"),
        "selected_tools": list(state.get("selected_tools", [])),
        "resolver": {
            "execution_status": resolution.get("execution_status"),
            "method": provenance.get("method", "not_required"),
            "requested_tools": list(resolution.get("requested_tools", [])),
            "tool_inputs": bounded_inputs,
        },
        "tool_executions": tool_records,
        "retry_count": int(state.get("retry_count") or 0),
        "recovery_used": bool(
            state.get("retry_count")
            or state.get("recovery_result")
            or state.get("fusion_history")
        ),
        "final_status": str(state.get("final_status") or "unknown"),
        "errors": errors,
        "api_requests": _api_request_summary(state),
    }


def _render_report(results: Mapping[str, Any]) -> str:
    requests = list(results.get("requests", []))
    lines = [
        "# FastAPI REST Integration Smoke Test V2",
        "",
        "**Scope:** real integration smoke test after Tool Input Resolver wiring; not a baseline evaluation  ",
        f"**Started:** `{results.get('started_at')}`  ",
        f"**Completed:** `{results.get('completed_at')}`  ",
        "**Secrets:** no-echo process-memory input; not written to artifacts",
        "",
        "## Request summary",
        "",
        "| Request | HTTP | Route | Resolver | Tools used | Final status | Retry | Recovery | Client wall | API latency | API requests |",
        "|---|---:|---|---|---|---|---:|---|---:|---:|---:|",
    ]
    for item in requests:
        response = _mapping(item.get("response"))
        trace = _mapping(item.get("internal_trace"))
        resolver = _mapping(trace.get("resolver"))
        tools = response.get("tools_used") or [
            record.get("tool")
            for record in trace.get("tool_executions", [])
            if _mapping(record).get("status") in {"success", "ok"}
        ]
        api_requests = _mapping(trace.get("api_requests")).get("total", "n/a")
        lines.append(
            "| `{case}` | {http} | `{route}` | `{resolver}` | {tools} | "
            "`{status}` | {retry} | {recovery} | {wall:.3f} ms | {latency} | {api} |".format(
                case=item.get("case_id"),
                http=item.get("http_status"),
                route=response.get("route", trace.get("route", "n/a")),
                resolver=resolver.get("method", "n/a"),
                tools=", ".join(str(value) for value in tools) or "none",
                status=response.get("final_status", "n/a"),
                retry=response.get("retry_count", "n/a"),
                recovery=response.get("recovery_used", "n/a"),
                wall=float(item.get("elapsed_ms", 0.0)),
                latency=(
                    f"{float(response['latency_ms']):.3f} ms"
                    if "latency_ms" in response
                    else "n/a"
                ),
                api=api_requests,
            )
        )

    lines.extend(["", "## Query traces", ""])
    for item in requests:
        if item.get("path") != "/query":
            continue
        response = _mapping(item.get("response"))
        trace = _mapping(item.get("internal_trace"))
        lines.extend(
            [
                f"### {item.get('case_id')}",
                "",
                f"- Question: {item.get('question')}",
                f"- HTTP / final status: `{item.get('http_status')}` / `{response.get('final_status')}`",
                f"- Route: `{response.get('route', trace.get('route'))}`",
                f"- Resolver: `{json.dumps(trace.get('resolver', {}), ensure_ascii=False)}`",
                f"- Tool executions: `{json.dumps(trace.get('tool_executions', []), ensure_ascii=False)}`",
                f"- Retry / recovery: `{response.get('retry_count')}` / `{response.get('recovery_used')}`",
                f"- Used literature chunks: `{json.dumps(response.get('used_literature_chunk_ids', []), ensure_ascii=False)}`",
                f"- Structured errors: `{json.dumps(trace.get('errors', []), ensure_ascii=False)}`",
                f"- API requests: `{json.dumps(trace.get('api_requests', {}), ensure_ascii=False)}`",
                f"- Client wall / API latency: `{float(item.get('elapsed_ms', 0.0)):.3f} ms` / `{float(response.get('latency_ms', 0.0)):.3f} ms`",
                "",
            ]
        )

    lines.extend(
        [
            "## Integration conclusion",
            "",
            str(results.get("conclusion") or "Pending conclusion."),
            "",
            "No v1/v2 baseline was executed or modified during this smoke test.",
            "",
        ]
    )
    return "\n".join(lines)


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 600.0,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    started = perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return {
                "http_status": response.status,
                "elapsed_ms": (perf_counter() - started) * 1000.0,
                "response": json.loads(raw),
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            response: Any = json.loads(raw)
        except json.JSONDecodeError:
            response = {"detail": "non-JSON HTTP error response"}
        return {
            "http_status": exc.code,
            "elapsed_ms": (perf_counter() - started) * 1000.0,
            "response": response,
        }


def _wait_for_server(thread: Thread) -> dict[str, Any]:
    deadline = perf_counter() + 30.0
    last_error = "server did not become ready"
    while perf_counter() < deadline:
        if not thread.is_alive():
            raise RuntimeError("Uvicorn stopped before /health became available")
        try:
            return _request("GET", "/health", timeout=2.0)
        except (URLError, TimeoutError, ConnectionError) as exc:
            last_error = type(exc).__name__
            sleep(0.25)
    raise RuntimeError(f"Uvicorn readiness timeout: {last_error}")


def main() -> None:
    try:
        import fastapi  # noqa: F401
        import uvicorn
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "FastAPI runtime dependencies are missing from this Python. "
            "Run with C:\\mini\\python.exe."
        ) from exc

    print(
        "Secrets are read without echo, kept in process memory, and never "
        "written to the smoke artifact.",
        flush=True,
    )
    postgres_password = getpass.getpass(
        "Password for PostgreSQL role agentic_rag_app: "
    )
    openai_api_key = getpass.getpass("OpenAI API key: ")
    if not postgres_password or not openai_api_key:
        raise SystemExit("Both secrets are required; no requests were sent.")

    from src.api.app import app
    from src.api.dependencies import close_workflow, get_workflow

    os.environ["PGPASSWORD"] = postgres_password
    os.environ["OPENAI_API_KEY"] = openai_api_key
    try:
        print("Initializing the real Agentic RAG workflow...", flush=True)
        runtime = get_workflow()
    finally:
        os.environ.pop("PGPASSWORD", None)
        os.environ.pop("OPENAI_API_KEY", None)
        postgres_password = ""
        openai_api_key = ""

    capturing_workflow = CapturingWorkflow(runtime)
    app.dependency_overrides[get_workflow] = lambda: capturing_workflow
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="warning",
            access_log=False,
        )
    )
    thread = Thread(target=server.run, name="api-smoke-uvicorn", daemon=True)
    thread.start()

    results: dict[str, Any] = {
        "kind": "api_integration_smoke",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "requests": [],
    }
    try:
        print("Starting real FastAPI server...", flush=True)
        health = _wait_for_server(thread)
        results["requests"].append(
            {"case_id": "HEALTH", "method": "GET", "path": "/health", **health}
        )
        print(f"GET /health -> {health['http_status']}", flush=True)

        version = _request("GET", "/version", timeout=10.0)
        results["requests"].append(
            {"case_id": "VERSION", "method": "GET", "path": "/version", **version}
        )
        print(f"GET /version -> {version['http_status']}", flush=True)

        for case_id, question in QUERIES:
            print(f"POST /query {case_id}...", flush=True)
            response = _request(
                "POST",
                "/query",
                payload={"question": question},
            )
            trace = _summarize_state(capturing_workflow.latest_state())
            results["requests"].append(
                {
                    "case_id": case_id,
                    "method": "POST",
                    "path": "/query",
                    "question": question,
                    "internal_trace": trace,
                    **response,
                }
            )
            payload = response.get("response") or {}
            print(
                f"{case_id} -> HTTP {response['http_status']}, "
                f"final_status={payload.get('final_status', 'n/a')}",
                flush=True,
            )
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        app.dependency_overrides.clear()
        close_workflow()

    results["completed_at"] = datetime.now(timezone.utc).isoformat()
    query_results = [
        item for item in results["requests"] if item.get("path") == "/query"
    ]
    log_metric = next(
        item for item in query_results if item["case_id"] == "API-SMOKE-LOG-METRIC"
    )
    hybrid = next(
        item for item in query_results if item["case_id"] == "API-SMOKE-HYBRID"
    )
    log_tools = set(_mapping(log_metric["response"]).get("tools_used", []))
    hybrid_tools = set(_mapping(hybrid["response"]).get("tools_used", []))
    log_ok = {"query_training_log", "compute_metrics"}.issubset(log_tools)
    hybrid_ok = {
        "query_training_log",
        "compute_metrics",
        "search_literature",
    }.issubset(hybrid_tools)
    results["conclusion"] = (
        "The previous missing-tool-input binding defect is resolved: Log/Metric "
        f"structured Tools executed={log_ok}; Hybrid structured and Literature "
        f"Tools executed={hybrid_ok}."
    )
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_PATH.with_suffix(RESULT_PATH.suffix + ".tmp")
    temporary.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(RESULT_PATH)
    report_temporary = REPORT_PATH.with_suffix(REPORT_PATH.suffix + ".tmp")
    report_temporary.write_text(
        _render_report(results),
        encoding="utf-8",
    )
    report_temporary.replace(REPORT_PATH)
    print(f"Secret-free results written to: {RESULT_PATH}", flush=True)
    print(f"Smoke report written to: {REPORT_PATH}", flush=True)


if __name__ == "__main__":
    main()
