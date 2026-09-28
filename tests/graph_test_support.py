from __future__ import annotations

import hashlib
import json
from typing import Any

from src.agent.executor import DeterministicToolExecutor
from src.grading.runtime_contracts import RuntimeEvidenceAssessmentDraft
from src.grading.runtime_finalizer import finalize_runtime_assessment
from src.graph.workflow import AgenticRAGWorkflow
from src.recovery.agent import EvidenceRecoveryAgent
from src.recovery.contracts import RecoveryErrorCode, RecoveryTokenUsage
from src.recovery.provider import RecoveryProviderResult
from src.tools.contracts import (
    ToolError,
    ToolErrorCode,
    failure_response,
    success_response,
)


LITERATURE_QUESTION = (
    "저항운동 연구에서 VBT가 최대 근력 향상에 미치는 효과는?"
)
LITERATURE_SUBQUESTION = "훈련 경험자에서 VBT가 최대근력을 향상시키는가?"
INITIAL_QUERY = "VBT maximal strength trained athletes"
HYBRID_QUESTION = (
    "Bench Press (Barbell)의 e1RM 변화와 periodization 문헌을 함께 설명해줘"
)


def literature_hit(chunk_id: str, rank: int) -> dict[str, Any]:
    text = f"Frozen literature text for {chunk_id}."
    return {
        "chunk_id": chunk_id,
        "paper_id": f"paper-{chunk_id}",
        "pmcid": f"PMC-{chunk_id}",
        "pmid": None,
        "doi": None,
        "title": f"Title {chunk_id}",
        "section": "Results",
        "text": text,
        "rank": rank,
        "rrf_score": 0.03 / rank,
        "dense_rank": rank,
        "dense_score": 0.9 - rank / 100,
        "bm25_rank": rank,
        "bm25_score": 10.0 - rank / 10,
        "dense_rrf_contribution": 0.015 / rank,
        "bm25_rrf_contribution": 0.015 / rank,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(chunk_id.encode()).hexdigest(),
        "corpus_version": "literature_corpus_v1",
    }


class FakeTrainingLogTool:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute(self, request: Any) -> Any:
        self.calls.append(request)
        return success_response(
            operation=str(request.operation),
            result={
                "records": [
                    {
                        "set_id": "set-1",
                        "exercise": "Bench Press (Barbell)",
                        "weight": 100.0,
                        "reps": 5,
                    }
                ]
            },
            provenance={"tool": "training_log_tool_v1"},
        )


class FakeMetricTool:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def execute(self, request: Any) -> Any:
        self.calls.append(request)
        return success_response(
            operation=str(request.operation),
            result={"e1rm": 116.6667},
            provenance={"tool": "metric_tool_v1"},
        )


class SequenceLiteratureTool:
    def __init__(self, responses: list[list[dict[str, Any]] | None | Exception]) -> None:
        self.responses = responses
        self.calls: list[Any] = []

    def execute(self, request: Any) -> Any:
        index = len(self.calls)
        self.calls.append(request)
        value = self.responses[index]
        if isinstance(value, Exception):
            raise value
        if value is None:
            return failure_response(
                operation="search",
                error=ToolError(
                    ToolErrorCode.RETRIEVAL_ERROR,
                    "simulated retrieval failure",
                ),
                provenance={"tool": "literature_tool_v1"},
            )
        return success_response(
            operation="search",
            result={
                "query": request.query,
                "top_k": request.top_k,
                "candidate_count": len(value),
                "hits": value,
                "latency_ms": {"end_to_end": 1.0},
            },
            provenance={
                "tool": "literature_tool_v1",
                "retrieval_version": "hybrid_baseline_v1",
            },
            empty=not value,
        )


class SequenceRuntimeGrader:
    def __init__(self, sufficient: list[bool]) -> None:
        self.sufficient = sufficient
        self.calls: list[Any] = []

    def grade(self, grader_input: Any) -> Any:
        index = len(self.calls)
        self.calls.append(grader_input)
        is_sufficient = self.sufficient[index]
        first_chunk = grader_input.supplied_chunks[0].chunk_id
        draft = RuntimeEvidenceAssessmentDraft.model_validate(
            {
                "components": [
                    {
                        "component_id": "C1",
                        "requirement": "훈련 경험자에서 VBT 최대근력 효과",
                        "status": "supported" if is_sufficient else "missing",
                        "supporting_chunk_ids": [first_chunk] if is_sufficient else [],
                    }
                ]
            }
        )
        return finalize_runtime_assessment(
            grader_input,
            draft,
            model="fixture-grader",
            prompt_sha256="a" * 64,
            config_sha256="b" * 64,
        )


class SequenceRecoveryProvider:
    model = "fixture-recovery-model"
    prompt_sha256 = "c" * 64
    config_sha256 = "d" * 64

    def __init__(
        self,
        payloads: list[dict[str, Any] | RecoveryErrorCode],
    ) -> None:
        self.payloads = payloads
        self.calls: list[Any] = []

    def invoke(self, recovery_input: Any) -> RecoveryProviderResult:
        index = len(self.calls)
        self.calls.append(recovery_input)
        value = self.payloads[index]
        error_code = value if isinstance(value, RecoveryErrorCode) else None
        return RecoveryProviderResult(
            raw_output_text=(
                None if error_code else json.dumps(value, ensure_ascii=False)
            ),
            model=self.model,
            prompt_sha256=self.prompt_sha256,
            config_sha256=self.config_sha256,
            response_id=f"recovery-{index + 1}",
            latency_ms=5.0,
            token_usage=RecoveryTokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
            response_metadata={"response_received": True},
            error_code=error_code,
            error_detail="simulated recovery provider failure" if error_code else None,
        )


def recovery_payload(query: str) -> dict[str, Any]:
    return {
        "target_component_ids": ["C1"],
        "recovery_query": query,
        "preserved_terms": ["VBT"],
    }


def build_workflow(
    *,
    literature_responses: list[list[dict[str, Any]] | None | Exception],
    grader_results: list[bool],
    recovery_payloads: list[dict[str, Any] | RecoveryErrorCode],
    final_response_layer: Any | None = None,
) -> tuple[
    AgenticRAGWorkflow,
    FakeTrainingLogTool,
    FakeMetricTool,
    SequenceLiteratureTool,
    SequenceRuntimeGrader,
    SequenceRecoveryProvider,
]:
    training_tool = FakeTrainingLogTool()
    metric_tool = FakeMetricTool()
    literature_tool = SequenceLiteratureTool(literature_responses)
    grader = SequenceRuntimeGrader(grader_results)
    recovery_provider = SequenceRecoveryProvider(recovery_payloads)
    recovery_agent = EvidenceRecoveryAgent(recovery_provider)
    executor = DeterministicToolExecutor(
        training_log_tool=training_tool,
        metric_tool=metric_tool,
        literature_tool=literature_tool,
    )
    workflow = AgenticRAGWorkflow(
        tool_executor=executor,
        literature_tool=literature_tool,
        runtime_grader=grader,
        recovery_agent=recovery_agent,
        final_response_layer=final_response_layer,
    )
    return (
        workflow,
        training_tool,
        metric_tool,
        literature_tool,
        grader,
        recovery_provider,
    )


def hybrid_tool_inputs() -> dict[str, dict[str, Any]]:
    return {
        "query_training_log": {
            "operation": "exercise_records",
            "canonical_exercise_name": "Bench Press (Barbell)",
            "limit": 500,
            "include_lineage": True,
        },
        "compute_metrics": {
            "operation": "estimated_1rm",
            "records_source": "query_training_log",
            "canonical_exercise_name": "Bench Press (Barbell)",
            "n_sessions": 5,
        },
    }
