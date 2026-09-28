"""Deterministic user responses for abstention and execution failure."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contracts import (
    ChannelMode,
    FinalAnswerErrorCode,
    FinalAnswerInput,
    FinalResponse,
    FinalResponseError,
    FinalResponseProvenance,
    FinalResponseStatus,
    UsedToolResult,
)


def _dedupe_text(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
    return output


def _used_structured(input_data: FinalAnswerInput) -> list[UsedToolResult]:
    return [
        UsedToolResult(
            result_id=item.result_id,
            tool=item.tool,
            phase=item.phase,
            requested_operation=item.requested_operation,
            result=item.result,
            provenance=item.provenance,
        )
        for item in input_data.structured_evidence
    ]


def build_abstention_response(
    input_data: FinalAnswerInput,
    *,
    missing_components: list[Mapping[str, Any]],
    retry_count: int,
    graph_errors: list[dict[str, Any]],
    route: Mapping[str, Any] | None = None,
) -> FinalResponse:
    """Explain a bounded abstention without inventing the missing answer."""

    paragraphs: list[str] = []
    if input_data.structured_evidence:
        tools = {item.tool for item in input_data.structured_evidence}
        labels: list[str] = []
        if "query_training_log" in tools:
            labels.append("개인 운동 기록")
        if "compute_metrics" in tools:
            labels.append("계산된 지표")
        paragraphs.append(f"현재 확인 가능한 내용: {', '.join(labels)} 결과는 확보되어 있습니다.")
    if input_data.literature_evidence:
        paragraphs.append(
            "현재 확인 가능한 내용: 검색된 문헌 근거 "
            f"{len(input_data.literature_evidence)}개를 확인했습니다."
        )

    requirements = _dedupe_text(
        str(item.get("requirement", "")) for item in missing_components
    )
    if requirements:
        paragraphs.append("부족한 문헌 근거: " + "; ".join(requirements))
    else:
        unsupported_reason = str((route or {}).get("unsupported_reason") or "").strip()
        paragraphs.append(
            "부족한 근거: "
            + (unsupported_reason or "현재 데이터와 문헌 근거로 답할 수 있는 범위를 확인하지 못했습니다.")
        )

    if retry_count > 0:
        paragraphs.append(
            f"초기 검색 후 문헌 추가 검색을 {retry_count}회 수행했지만 필요한 근거가 충분하지 않았습니다."
        )
    elif input_data.literature_evidence:
        paragraphs.append("현재 문헌 검색 결과만으로는 필요한 근거가 충분하지 않았습니다.")

    boundary = input_data.literature_subquestion or input_data.original_question
    paragraphs.append(
        "따라서 현재 근거만으로는 다음 주장에 답을 확정할 수 없습니다: "
        f"{boundary}"
    )

    used_tools = _used_structured(input_data)
    used_tool_ids = [item.result_id for item in used_tools]
    limitations = _dedupe_text(
        [
            *requirements,
            "누락된 근거를 추측으로 보완하지 않았습니다.",
        ]
    )
    return FinalResponse(
        final_status=FinalResponseStatus.ABSTAIN_READY,
        answer_text="\n\n".join(paragraphs),
        used_tool_results=used_tools,
        used_literature_chunk_ids=[],
        limitations=limitations,
        provenance=FinalResponseProvenance(
            graph_terminal_status=FinalResponseStatus.ABSTAIN_READY,
            channel_mode=input_data.channel_mode,
            available_tool_result_ids=[
                item.result_id for item in input_data.structured_evidence
            ],
            used_tool_result_ids=used_tool_ids,
            available_literature_chunk_ids=[
                item.chunk_id for item in input_data.literature_evidence
            ],
            used_literature_chunk_ids=[],
            response_metadata={
                "response_mode": "deterministic_abstention",
                "retry_count": retry_count,
                "missing_component_ids": [
                    str(item.get("component_id", ""))
                    for item in missing_components
                    if str(item.get("component_id", "")).strip()
                ],
            },
            internal_graph_errors=list(graph_errors),
        ),
    )


def build_execution_failure_response(
    *,
    input_data: FinalAnswerInput,
    graph_terminal_status: FinalResponseStatus = FinalResponseStatus.EXECUTION_FAILURE,
    graph_errors: list[dict[str, Any]],
    error_code: FinalAnswerErrorCode = FinalAnswerErrorCode.GRAPH_EXECUTION_FAILURE,
    internal_error_detail: str | None = None,
) -> FinalResponse:
    """Return a sanitized user message while retaining internal diagnostics."""

    safe_message = (
        "요청을 처리하는 중 내부 실행 오류가 발생했습니다. "
        "근거가 완전하게 준비되지 않아 답변을 생성하지 않았습니다."
    )
    return FinalResponse(
        final_status=FinalResponseStatus.EXECUTION_FAILURE,
        answer_text=safe_message,
        used_tool_results=[],
        used_literature_chunk_ids=[],
        limitations=["불완전한 실행 결과로 답변을 생성하지 않았습니다."],
        provenance=FinalResponseProvenance(
            graph_terminal_status=graph_terminal_status,
            channel_mode=input_data.channel_mode,
            available_tool_result_ids=[
                item.result_id for item in input_data.structured_evidence
            ],
            used_tool_result_ids=[],
            available_literature_chunk_ids=[
                item.chunk_id for item in input_data.literature_evidence
            ],
            used_literature_chunk_ids=[],
            response_metadata={"response_mode": "deterministic_failure"},
            internal_graph_errors=list(graph_errors),
            internal_error_detail=internal_error_detail,
        ),
        error=FinalResponseError(
            code=error_code,
            user_message=safe_message,
        ),
    )
