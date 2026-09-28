"""FastAPI application exposing the existing Agentic RAG workflow."""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any, Mapping

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from src.answer.contracts import FinalResponse, FinalResponseStatus
from src.recovery.contracts import MAX_RETRY
from src.recovery.evidence_fusion import EVIDENCE_BUDGET, FUSION_POLICY
from src.retrieval.rrf import DEFAULT_RRF_K

from .contracts import (
    HealthResponse,
    PublicQueryError,
    QueryRequest,
    QueryResponse,
    VersionResponse,
)
from .dependencies import (
    WorkflowConfigurationError,
    WorkflowInitializationError,
    WorkflowPort,
    close_workflow,
    get_workflow,
)


APP_VERSION = "1.0.0"
GRAPH_VERSION = "agentic_rag_workflow_v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _unique_strings(values: Any) -> list[str]:
    output: list[str] = []
    if not isinstance(values, list):
        return output
    for value in values:
        text = str(value).strip()
        if text and text not in output:
            output.append(text)
    return output


def _route_from_state(state: Mapping[str, Any]) -> str:
    route = state.get("route")
    if isinstance(route, Mapping):
        value = route.get("route") or route.get("task_type")
        if value:
            return str(value)
    if isinstance(route, str) and route.strip():
        return route.strip()
    task_type = str(state.get("task_type") or "").strip()
    return task_type or "unknown"


def _tools_used(state: Mapping[str, Any]) -> list[str]:
    tools: list[str] = []
    records = state.get("tool_results")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, Mapping):
                continue
            tool = str(record.get("tool") or "").strip()
            if tool and tool not in tools:
                tools.append(tool)
    return tools


def _public_error(response: FinalResponse) -> PublicQueryError | None:
    if response.error is None:
        return None
    return PublicQueryError(
        code=response.error.code.value,
        message=response.error.user_message,
    )


def query_response_from_state(
    state: Mapping[str, Any],
    *,
    latency_ms: float,
) -> QueryResponse:
    """Project an internal graph state into a bounded public response."""

    final_payload = state.get("final_response")
    response = FinalResponse.model_validate(final_payload)
    retry_count = int(state.get("retry_count") or 0)
    recovery_used = bool(
        retry_count
        or state.get("recovery_result")
        or state.get("fusion_history")
    )
    return QueryResponse(
        final_status=response.final_status,
        answer_text=response.answer_text,
        route=_route_from_state(state),
        tools_used=_tools_used(state),
        retry_count=retry_count,
        recovery_used=recovery_used,
        used_literature_chunk_ids=_unique_strings(
            response.used_literature_chunk_ids
        ),
        limitations=list(response.limitations),
        latency_ms=latency_ms,
        error=_public_error(response),
    )


@asynccontextmanager
async def _lifespan(_: FastAPI):
    yield
    close_workflow()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Agentic RAG Training API",
        summary="운동 기록과 동결된 문헌 검색을 연결하는 Agentic RAG REST API",
        description=(
            "기존 deterministic Router, Tool Layer, Runtime Evidence Grader, "
            "Recovery/Fusion 및 Final Answer workflow를 그대로 호출합니다. "
            "API 키와 데이터베이스 비밀번호는 OpenAPI 문서나 응답에 포함되지 않습니다."
        ),
        version=APP_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    @application.exception_handler(WorkflowConfigurationError)
    @application.exception_handler(WorkflowInitializationError)
    async def runtime_unavailable_handler(_, __) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Agentic RAG runtime is not available."},
        )

    @application.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="서버 상태 확인",
        description=(
            "HTTP 애플리케이션이 요청을 받을 수 있는지 확인합니다. "
            "외부 DB/OpenAI 연결을 수행하지 않는 liveness 응답입니다."
        ),
    )
    def health() -> HealthResponse:
        return HealthResponse()

    @application.get(
        "/version",
        response_model=VersionResponse,
        tags=["system"],
        summary="공개 런타임 버전 확인",
        description=(
            "재현성에 필요한 비민감 구성만 반환합니다. 비밀값과 연결 문자열은 "
            "반환하지 않습니다."
        ),
    )
    def version() -> VersionResponse:
        return VersionResponse(
            app_version=APP_VERSION,
            graph_version=GRAPH_VERSION,
            recovery_max_retry=MAX_RETRY,
            literature_top_k=EVIDENCE_BUDGET,
            evidence_fusion_policy=FUSION_POLICY,
            fusion_rrf_k=DEFAULT_RRF_K,
        )

    @application.post(
        "/query",
        response_model=QueryResponse,
        response_model_exclude_none=True,
        tags=["agentic-rag"],
        summary="Agentic RAG 질문 실행",
        description=(
            "질문을 기존 LangGraph workflow에 전달합니다. answer_ready, "
            "abstain_ready, execution_failure는 모두 정상적인 graph 종료 상태이므로 "
            "HTTP 200으로 반환합니다. 요청 검증 실패 또는 API 서버 자체의 예기치 "
            "못한 실패만 HTTP 오류가 됩니다."
        ),
        responses={
            status.HTTP_500_INTERNAL_SERVER_ERROR: {
                "description": "내부 API 실행 오류(민감한 상세 내용은 숨김)"
            },
            status.HTTP_503_SERVICE_UNAVAILABLE: {
                "description": "필수 서버 설정 또는 외부 runtime 초기화 실패"
            },
        },
    )
    def query(
        request: QueryRequest,
        workflow: WorkflowPort = Depends(get_workflow),
    ) -> QueryResponse:
        started = perf_counter()
        try:
            state = workflow.invoke(request.question)
            if not isinstance(state, Mapping):
                raise TypeError("workflow returned a non-mapping state")
            return query_response_from_state(
                state,
                latency_ms=(perf_counter() - started) * 1000.0,
            )
        except (WorkflowConfigurationError, WorkflowInitializationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agentic RAG runtime is not available.",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The request could not be processed safely.",
            ) from exc

    return application


app = create_app()
