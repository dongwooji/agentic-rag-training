"""Grounded answer assembly and terminal graph-state dispatch."""

from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import ValidationError

from .abstention import (
    build_abstention_response,
    build_execution_failure_response,
)
from .contracts import (
    AnswerTokenUsage,
    ChannelMode,
    FinalAnswerDraft,
    FinalAnswerErrorCode,
    FinalAnswerInput,
    FinalResponse,
    FinalResponseProvenance,
    FinalResponseStatus,
    LiteratureEvidence,
    StructuredEvidence,
    UsedToolResult,
)
from .provider import FinalAnswerProvider


STRUCTURED_TOOLS = {"query_training_log", "compute_metrics"}
SUCCESS_TOOL_STATUSES = {"success"}
TRAINING_LOG_SEQUENCE_LIMIT = 20
TRAINING_LOG_SEQUENCE_EDGE = TRAINING_LOG_SEQUENCE_LIMIT // 2
TRAINING_LOG_PAYLOAD_POLICY = "bounded_training_log_payload_v1"


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            output.append(cleaned)
    return output


def _bounded_training_value(
    value: Any,
    *,
    path: str,
    omitted: dict[str, int],
) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_training_value(
                item,
                path=f"{path}.{key}" if path else str(key),
                omitted=omitted,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        values = list(value)
        if len(values) > TRAINING_LOG_SEQUENCE_LIMIT:
            omitted[path] = len(values) - TRAINING_LOG_SEQUENCE_LIMIT
            values = [
                *values[:TRAINING_LOG_SEQUENCE_EDGE],
                *values[-TRAINING_LOG_SEQUENCE_EDGE:],
            ]
        return [
            _bounded_training_value(
                item,
                path=f"{path}[{index}]",
                omitted=omitted,
            )
            for index, item in enumerate(values)
        ]
    return value


def _bounded_training_result(result: Any) -> Any:
    """Bound only the provider-facing Training Log copy.

    The original Tool result remains unchanged in graph state.  Every list in
    the copy is capped deterministically by retaining its first ten and last
    ten entries.  Metric Tool outputs are not passed through this function.
    """

    omitted: dict[str, int] = {}
    bounded = _bounded_training_value(result, path="result", omitted=omitted)
    if omitted and isinstance(bounded, Mapping):
        bounded = dict(bounded)
        bounded["_answer_payload"] = {
            "policy": TRAINING_LOG_PAYLOAD_POLICY,
            "max_items_per_sequence": TRAINING_LOG_SEQUENCE_LIMIT,
            "selection": "first_10_and_last_10",
            "omitted_items_by_path": omitted,
        }
    return bounded


def _structured_evidence(state: Mapping[str, Any]) -> list[StructuredEvidence]:
    output: list[StructuredEvidence] = []
    for index, record in enumerate(state.get("tool_results", []) or [], 1):
        if not isinstance(record, Mapping):
            continue
        tool = str(record.get("tool") or "")
        if tool not in STRUCTURED_TOOLS:
            continue
        if str(record.get("status") or "") not in SUCCESS_TOOL_STATUSES:
            continue
        if record.get("result") is None:
            continue
        provider_result = (
            _bounded_training_result(record["result"])
            if tool == "query_training_log"
            else record["result"]
        )
        output.append(
            StructuredEvidence(
                result_id=f"tool-result-{index:02d}-{tool}",
                tool=tool,
                requested_operation=str(record.get("requested_operation") or tool),
                result=provider_result,
                provenance=dict(record.get("provenance") or {}),
                limitations=[str(item) for item in record.get("limitations", [])],
            )
        )
    return output


def _literature_evidence(state: Mapping[str, Any]) -> list[LiteratureEvidence]:
    candidates = state.get("fused_evidence") or state.get("literature_evidence") or []
    output: list[LiteratureEvidence] = []
    seen: set[str] = set()
    for item in candidates:
        if len(output) >= 10 or not isinstance(item, Mapping):
            break
        chunk_id = str(item.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        retrieval = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "chunk_id",
                "paper_id",
                "pmcid",
                "pmid",
                "doi",
                "title",
                "section",
                "text",
                "corpus_version",
            }
        }
        output.append(
            LiteratureEvidence(
                chunk_id=chunk_id,
                paper_id=str(item.get("paper_id") or "unknown-paper"),
                pmcid=item.get("pmcid"),
                pmid=item.get("pmid"),
                doi=item.get("doi"),
                title=str(item.get("title") or "Untitled"),
                section=str(item.get("section") or "Unknown section"),
                text=str(item.get("text") or ""),
                rank=len(output) + 1,
                corpus_version=str(item.get("corpus_version") or "unknown"),
                retrieval_provenance=retrieval,
            )
        )
    return output


def build_final_answer_input(state: Mapping[str, Any]) -> FinalAnswerInput:
    structured = _structured_evidence(state)
    literature = _literature_evidence(state)
    channel = (
        ChannelMode.HYBRID
        if structured and literature
        else ChannelMode.STRUCTURED_ONLY
        if structured
        else ChannelMode.LITERATURE_ONLY
        if literature
        else ChannelMode.NO_EVIDENCE
    )
    question = str(state.get("original_question") or "").strip()
    if not question:
        question = "처리할 수 없는 빈 질문"
    return FinalAnswerInput(
        original_question=question,
        task_type=str(state.get("task_type") or "unknown"),
        literature_subquestion=(
            str(state.get("literature_subquestion") or "").strip() or None
        ),
        channel_mode=channel,
        structured_evidence=structured,
        literature_evidence=literature,
    )


def _validate_draft(
    draft: FinalAnswerDraft,
    input_data: FinalAnswerInput,
) -> tuple[FinalAnswerDraft, list[UsedToolResult], list[str]]:
    tool_ids = _dedupe(draft.used_tool_result_ids)
    chunk_ids = _dedupe(draft.used_literature_chunk_ids)
    available_tools = {item.result_id: item for item in input_data.structured_evidence}
    available_chunks = {item.chunk_id for item in input_data.literature_evidence}
    unknown_tools = [item for item in tool_ids if item not in available_tools]
    unknown_chunks = [item for item in chunk_ids if item not in available_chunks]
    if unknown_tools or unknown_chunks:
        raise ValueError(
            f"draft cited unavailable evidence IDs: tools={unknown_tools}, chunks={unknown_chunks}"
        )

    has_structured = bool(input_data.structured_evidence)
    has_literature = bool(input_data.literature_evidence)
    if has_structured != bool(draft.record_summary):
        raise ValueError("record_summary must exist exactly when structured evidence exists")
    if has_literature != bool(draft.literature_summary):
        raise ValueError("literature_summary must exist exactly when literature evidence exists")
    if (has_structured and has_literature) != bool(draft.integrated_summary):
        raise ValueError("integrated_summary is required only for Hybrid evidence")
    if has_structured and not tool_ids:
        raise ValueError("structured answer must cite at least one supplied Tool result")
    if has_literature and not chunk_ids:
        raise ValueError("literature answer must cite at least one supplied chunk")
    if not has_structured and tool_ids:
        raise ValueError("literature-only answer cannot cite Tool results")
    if not has_literature and chunk_ids:
        raise ValueError("structured-only answer cannot cite literature chunks")

    normalized = draft.model_copy(
        update={
            "used_tool_result_ids": tool_ids,
            "used_literature_chunk_ids": chunk_ids,
        }
    )
    used_tools = [
        UsedToolResult(
            result_id=available_tools[result_id].result_id,
            tool=available_tools[result_id].tool,
            phase=available_tools[result_id].phase,
            requested_operation=available_tools[result_id].requested_operation,
            result=available_tools[result_id].result,
            provenance=available_tools[result_id].provenance,
        )
        for result_id in tool_ids
    ]
    return normalized, used_tools, chunk_ids


def _answer_text(draft: FinalAnswerDraft) -> str:
    sections: list[str] = []
    if draft.record_summary:
        sections.append(f"당신의 기록에서는 {draft.record_summary}")
    if draft.literature_summary:
        sections.append(f"문헌에서는 {draft.literature_summary}")
    if draft.integrated_summary:
        sections.append(f"두 정보를 함께 보면 {draft.integrated_summary}")
    if draft.limitations:
        sections.append("제한사항: " + "; ".join(_dedupe(draft.limitations)))
    return "\n\n".join(sections)


def _standard_limitations(input_data: FinalAnswerInput) -> list[str]:
    limitations: list[str] = []
    if input_data.structured_evidence:
        limitations.append("개인 기록의 관찰된 변화만으로 인과관계를 확정할 수 없습니다.")
    if input_data.literature_evidence:
        limitations.append("일반 문헌 결과가 개인에게 동일하게 적용된다고 확정할 수 없습니다.")
    limitations.append("이 응답은 의료적 진단이나 개인 처방이 아닙니다.")
    return limitations


class FinalResponseLayer:
    """Create a grounded answer or a deterministic terminal response."""

    def __init__(self, provider: FinalAnswerProvider | None = None) -> None:
        self.provider = provider

    def respond(self, state: Mapping[str, Any]) -> FinalResponse:
        graph_status_raw = str(state.get("final_status") or "execution_failure")
        try:
            graph_status = FinalResponseStatus(graph_status_raw)
        except ValueError:
            graph_status = FinalResponseStatus.EXECUTION_FAILURE
        graph_errors = [
            dict(item) for item in state.get("errors", []) if isinstance(item, Mapping)
        ]
        try:
            input_data = build_final_answer_input(state)
        except Exception as exc:
            fallback = FinalAnswerInput(
                original_question="처리할 수 없는 질문",
                task_type="invalid",
                channel_mode=ChannelMode.NO_EVIDENCE,
            )
            return build_execution_failure_response(
                input_data=fallback,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.INVALID_GRAPH_STATE,
                internal_error_detail=f"{type(exc).__name__}: {exc}",
            )

        if graph_status == FinalResponseStatus.ABSTAIN_READY:
            return build_abstention_response(
                input_data,
                missing_components=[
                    dict(item)
                    for item in state.get("missing_components", [])
                    if isinstance(item, Mapping)
                ],
                retry_count=int(state.get("retry_count", 0) or 0),
                graph_errors=graph_errors,
                route=(state.get("route") if isinstance(state.get("route"), Mapping) else {}),
            )
        if graph_status == FinalResponseStatus.EXECUTION_FAILURE:
            return build_execution_failure_response(
                input_data=input_data,
                graph_errors=graph_errors,
            )
        if self.provider is None:
            return build_execution_failure_response(
                input_data=input_data,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.PROVIDER_FAILURE,
                internal_error_detail="Final Answer provider is not configured",
            )
        if input_data.channel_mode == ChannelMode.NO_EVIDENCE:
            return build_execution_failure_response(
                input_data=input_data,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.INVALID_GRAPH_STATE,
                internal_error_detail="answer_ready contained no usable evidence",
            )

        provider_result = self.provider.invoke(input_data)
        if provider_result.error_detail or not provider_result.raw_output_text:
            return build_execution_failure_response(
                input_data=input_data,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.PROVIDER_FAILURE,
                internal_error_detail=provider_result.error_detail or "empty provider output",
            )
        try:
            parsed = json.loads(provider_result.raw_output_text)
            draft = FinalAnswerDraft.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            return build_execution_failure_response(
                input_data=input_data,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.SCHEMA_FAILURE,
                internal_error_detail=f"{type(exc).__name__}: {exc}",
            )
        try:
            draft, used_tools, used_chunks = _validate_draft(draft, input_data)
        except ValueError as exc:
            return build_execution_failure_response(
                input_data=input_data,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.GROUNDING_VALIDATION_FAILURE,
                internal_error_detail=str(exc),
            )

        limitations = _dedupe([*draft.limitations, *_standard_limitations(input_data)])
        answer_text = _answer_text(draft)
        if not answer_text:
            return build_execution_failure_response(
                input_data=input_data,
                graph_terminal_status=graph_status,
                graph_errors=graph_errors,
                error_code=FinalAnswerErrorCode.GROUNDING_VALIDATION_FAILURE,
                internal_error_detail="grounded draft produced no answer text",
            )
        used_tool_ids = [item.result_id for item in used_tools]
        literature_by_id = {
            item.chunk_id: item for item in input_data.literature_evidence
        }
        used_literature_sources = [
            {
                "chunk_id": literature_by_id[chunk_id].chunk_id,
                "paper_id": literature_by_id[chunk_id].paper_id,
                "pmcid": literature_by_id[chunk_id].pmcid,
                "pmid": literature_by_id[chunk_id].pmid,
                "doi": literature_by_id[chunk_id].doi,
                "title": literature_by_id[chunk_id].title,
                "section": literature_by_id[chunk_id].section,
                "rank": literature_by_id[chunk_id].rank,
                "corpus_version": literature_by_id[chunk_id].corpus_version,
                "retrieval_provenance": literature_by_id[
                    chunk_id
                ].retrieval_provenance,
            }
            for chunk_id in used_chunks
        ]
        return FinalResponse(
            final_status=FinalResponseStatus.ANSWER_READY,
            answer_text=answer_text,
            used_tool_results=used_tools,
            used_literature_chunk_ids=used_chunks,
            limitations=limitations,
            provenance=FinalResponseProvenance(
                graph_terminal_status=graph_status,
                channel_mode=input_data.channel_mode,
                available_tool_result_ids=[
                    item.result_id for item in input_data.structured_evidence
                ],
                used_tool_result_ids=used_tool_ids,
                available_literature_chunk_ids=[
                    item.chunk_id for item in input_data.literature_evidence
                ],
                used_literature_chunk_ids=used_chunks,
                used_literature_sources=used_literature_sources,
                model=provider_result.model,
                prompt_sha256=provider_result.prompt_sha256,
                config_sha256=provider_result.config_sha256,
                response_id=provider_result.response_id,
                latency_ms=provider_result.latency_ms,
                token_usage=provider_result.token_usage,
                response_metadata=provider_result.response_metadata,
                internal_graph_errors=graph_errors,
            ),
        )
