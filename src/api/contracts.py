"""Public, secret-safe request and response contracts for the REST API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.answer.contracts import FinalResponseStatus


class PublicModel(BaseModel):
    """Strict base model used only for public HTTP payloads."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class HealthResponse(PublicModel):
    status: Literal["ok"] = "ok"


class VersionResponse(PublicModel):
    app_version: str
    graph_version: str
    recovery_max_retry: int = Field(ge=0)
    literature_top_k: int = Field(ge=1)
    evidence_fusion_policy: str
    fusion_rrf_k: int = Field(ge=1)


class QueryRequest(PublicModel):
    question: str = Field(
        min_length=1,
        max_length=4000,
        description="사용자가 Agentic RAG 시스템에 전달할 질문",
        examples=[
            "저항훈련의 세트 간 휴식시간이 근력 향상에 미치는 영향을 문헌 근거로 요약해줘."
        ],
    )

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must contain non-whitespace text")
        return value


class PublicQueryError(PublicModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class QueryResponse(PublicModel):
    final_status: FinalResponseStatus
    answer_text: str = Field(min_length=1)
    route: str = Field(min_length=1)
    tools_used: list[str] = Field(default_factory=list)
    retry_count: int = Field(ge=0)
    recovery_used: bool
    used_literature_chunk_ids: list[str] = Field(
        default_factory=list,
        max_length=10,
    )
    limitations: list[str] = Field(default_factory=list)
    latency_ms: float = Field(ge=0.0)
    error: PublicQueryError | None = None

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        protected_namespaces=(),
        json_schema_extra={
            "examples": [
                {
                    "final_status": "answer_ready",
                    "answer_text": "현재 제공된 문헌 근거에서는 …",
                    "route": "literature_only",
                    "tools_used": ["search_literature"],
                    "retry_count": 0,
                    "recovery_used": False,
                    "used_literature_chunk_ids": ["PAPER-001::chunk-003"],
                    "limitations": ["검색된 문헌 범위 안에서만 요약했습니다."],
                    "latency_ms": 842.7,
                    "error": None,
                },
                {
                    "final_status": "abstain_ready",
                    "answer_text": "현재 검색된 근거만으로는 해당 비교를 확정할 수 없습니다.",
                    "route": "literature_only",
                    "tools_used": ["search_literature"],
                    "retry_count": 1,
                    "recovery_used": True,
                    "used_literature_chunk_ids": [],
                    "limitations": ["요구된 집단과 결과에 대한 직접 근거가 부족합니다."],
                    "latency_ms": 1331.4,
                    "error": None,
                },
            ]
        },
    )
