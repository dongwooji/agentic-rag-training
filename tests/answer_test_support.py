from __future__ import annotations

import json
from typing import Any

from src.answer.contracts import AnswerTokenUsage
from src.answer.provider import FinalAnswerProviderResult

from graph_test_support import literature_hit


class FakeFinalAnswerProvider:
    def __init__(self, payload: dict[str, Any] | None = None, *, error: str | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[Any] = []

    def invoke(self, answer_input: Any) -> FinalAnswerProviderResult:
        self.calls.append(answer_input)
        return FinalAnswerProviderResult(
            raw_output_text=(
                json.dumps(self.payload, ensure_ascii=False)
                if self.payload is not None and self.error is None
                else None
            ),
            model="mock-final-answer",
            prompt_sha256="a" * 64,
            config_sha256="b" * 64,
            response_id="response-1",
            latency_ms=12.5,
            token_usage=AnswerTokenUsage(
                input_tokens=100,
                output_tokens=30,
                total_tokens=130,
                estimated_cost_usd=0.00021,
            ),
            response_metadata={"response_received": self.error is None},
            error_detail=self.error,
        )


def structured_tool_results() -> list[dict[str, Any]]:
    return [
        {
            "tool": "query_training_log",
            "status": "success",
            "requested_operation": "exercise_records",
            "result": {
                "records": [
                    {
                        "set_id": "set-1",
                        "exercise": "Bench Press (Barbell)",
                        "weight": 100.0,
                        "reps": 5,
                    }
                ]
            },
            "provenance": {"tool": "training_log_tool_v1"},
            "limitations": [],
            "error": None,
        },
        {
            "tool": "compute_metrics",
            "status": "success",
            "requested_operation": "estimated_1rm",
            "result": {"e1rm": 116.6667},
            "provenance": {"tool": "metric_tool_v1", "formula": "Epley"},
            "limitations": [],
            "error": None,
        },
    ]


def answer_state(
    *,
    structured: bool = False,
    literature: bool = False,
    literature_count: int = 2,
) -> dict[str, Any]:
    hits = (
        [literature_hit(f"chunk-{rank}", rank) for rank in range(1, literature_count + 1)]
        if literature
        else []
    )
    selected = []
    if structured:
        selected.extend(["query_training_log", "compute_metrics"])
    if literature:
        selected.append("search_literature")
    return {
        "original_question": "내 Bench Press e1RM 변화와 VBT 문헌을 함께 설명해줘",
        "route": {"status": "planned"},
        "task_type": "hybrid" if structured and literature else "literature" if literature else "metric",
        "selected_tools": selected,
        "execution_order": selected,
        "tool_results": structured_tool_results() if structured else [],
        "literature_subquestion": "VBT가 최대근력에 미치는 효과는?" if literature else "",
        "literature_evidence": hits,
        "fused_evidence": hits,
        "missing_components": [],
        "retry_count": 0,
        "errors": [],
        "final_status": "answer_ready",
    }


def draft_payload(
    *,
    record: str = "",
    literature: str = "",
    integrated: str = "",
    tool_ids: list[str] | None = None,
    chunk_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "record_summary": record,
        "literature_summary": literature,
        "integrated_summary": integrated,
        "limitations": ["근거 범위 안에서만 해석했습니다."],
        "used_tool_result_ids": tool_ids or [],
        "used_literature_chunk_ids": chunk_ids or [],
    }
