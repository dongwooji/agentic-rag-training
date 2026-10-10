"""Node adapters that call existing Router, Tool, Grader, and Recovery parts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from pydantic import BaseModel

from src.agent.contracts import PlannerDraft
from src.agent.executor import DeterministicToolExecutor
from src.grading.runtime_contracts import RuntimeEvidenceGraderInput
from src.recovery.contracts import EvidenceRecoveryInput, RecoveryErrorCode
from src.recovery.evidence_fusion import (
    LiteratureRetrievalHit,
    RecoveryEvidenceFusionInput,
    fuse_recovery_evidence,
)
from src.routing.contracts import RouterInput
from src.tools.literature import LiteratureInput

from .literature_scope import derive_literature_subquestion
from .initial_literature_tool import InitialLiteratureTool
from src.retrieval.literature_query import LiteratureQueryGenerator
from .state import AgenticRAGState, GraphError
from .tool_input_resolver import ToolInputResolverRequest


FAILURE_TOOL_STATUSES = {
    "failure",
    "configuration_error",
    "dependency_error",
    "execution_error",
}


class RouterPort(Protocol):
    def route(self, request: RouterInput) -> Any: ...


class RuntimeGraderPort(Protocol):
    def grade(self, grader_input: RuntimeEvidenceGraderInput) -> Any: ...


class RecoveryAgentPort(Protocol):
    def generate(self, recovery_input: EvidenceRecoveryInput) -> Any: ...


class LiteratureToolPort(Protocol):
    def execute(self, request: LiteratureInput) -> Any: ...


class ToolInputResolverPort(Protocol):
    def resolve(self, request: ToolInputResolverRequest) -> Any: ...


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
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
    return value


def _error(stage: str, code: str, message: str) -> GraphError:
    return {"stage": stage, "code": code, "message": message}


def _append_error(
    state: AgenticRAGState,
    stage: str,
    code: str,
    message: str,
) -> list[GraphError]:
    return [*state.get("errors", []), _error(stage, code, message)]


def _build_tool_plan(state: AgenticRAGState) -> PlannerDraft:
    steps: list[dict[str, Any]] = []
    configured_inputs = state.get("tool_inputs", {})
    for tool_id in state.get("execution_order", []):
        if tool_id == "search_literature":
            inputs = {
                "operation": "search",
                "query": state["initial_query"],
                "top_k": 10,
            }
        else:
            inputs = configured_inputs.get(tool_id)
            if not isinstance(inputs, Mapping):
                raise ValueError(
                    f"typed tool_inputs are required for routed Tool {tool_id}"
                )
            inputs = dict(inputs)
        steps.append(
            {
                "tool": tool_id,
                "reason": "Execute the deterministic Router selection.",
                "subtask": f"Run {tool_id} with validated typed inputs.",
                "inputs": inputs,
            }
        )
    return PlannerDraft.model_validate(
        {
            "task_type": state["task_type"],
            "unsupported": False,
            "unsupported_reason": None,
            "planner_reasoning_summary": (
                "Typed execution adapter for the deterministic Router plan."
            ),
            "steps": steps,
        }
    )


def _runtime_chunk(hit: Mapping[str, Any], fallback_rank: int) -> dict[str, Any]:
    rank = hit.get("final_rank", hit.get("rank", fallback_rank))
    score = hit.get("fusion_score", hit.get("rrf_score"))
    dense_rank = hit.get("dense_rank")
    bm25_rank = hit.get("bm25_rank")
    if "final_rank" in hit:
        initial = hit.get("initial_retrieval")
        recovery = hit.get("recovery_retrieval")
        source = initial if isinstance(initial, Mapping) else recovery
        if isinstance(source, Mapping):
            dense_rank = source.get("dense_rank")
            bm25_rank = source.get("bm25_rank")
    return {
        "chunk_id": hit["chunk_id"],
        "paper_id": hit["paper_id"],
        "pmcid": hit.get("pmcid"),
        "title": hit["title"],
        "section": hit["section"],
        "text": hit["text"],
        "retrieval": {
            "rank": rank,
            "dense_rank": dense_rank,
            "bm25_rank": bm25_rank,
            "rrf_score": score,
        },
        "corpus_version": hit["corpus_version"],
    }


def _fusion_hit(hit: Mapping[str, Any]) -> LiteratureRetrievalHit:
    if "final_rank" not in hit:
        payload = {
            field: hit.get(field)
            for field in LiteratureRetrievalHit.model_fields
        }
        return LiteratureRetrievalHit.model_validate(payload)

    initial = hit.get("initial_retrieval")
    recovery = hit.get("recovery_retrieval")
    source = initial if isinstance(initial, Mapping) else recovery
    if not isinstance(source, Mapping):
        raise ValueError("fused evidence has no source retrieval provenance")
    return LiteratureRetrievalHit.model_validate(
        {
            "chunk_id": hit["chunk_id"],
            "paper_id": hit["paper_id"],
            "pmcid": hit.get("pmcid"),
            "pmid": hit.get("pmid"),
            "doi": hit.get("doi"),
            "title": hit["title"],
            "section": hit["section"],
            "text": hit["text"],
            "rank": hit["final_rank"],
            "rrf_score": hit["fusion_score"],
            "dense_rank": source.get("dense_rank"),
            "dense_score": source.get("dense_score"),
            "bm25_rank": source.get("bm25_rank"),
            "bm25_score": source.get("bm25_score"),
            "dense_rrf_contribution": source.get(
                "dense_rrf_contribution", 0.0
            ),
            "bm25_rrf_contribution": source.get(
                "bm25_rrf_contribution", 0.0
            ),
            "text_sha256": hit["text_sha256"],
            "source_sha256": hit["source_sha256"],
            "corpus_version": hit["corpus_version"],
        }
    )


class WorkflowNodes:
    """State transitions only; domain logic stays in injected components."""

    def __init__(
        self,
        *,
        router: RouterPort,
        tool_executor: DeterministicToolExecutor,
        literature_tool: LiteratureToolPort,
        runtime_grader: RuntimeGraderPort,
        recovery_agent: RecoveryAgentPort,
        tool_input_resolver: ToolInputResolverPort | None = None,
        query_mode: str = 'original_question',
        query_generator: LiteratureQueryGenerator | None = None,
    ) -> None:
        self.router = router
        self.tool_executor = tool_executor
        self.literature_tool = literature_tool
        self.runtime_grader = runtime_grader
        self.recovery_agent = recovery_agent
        self.tool_input_resolver = tool_input_resolver
        self.query_mode = query_mode
        self.query_generator = query_generator
        if query_mode == 'literature_subquestion':
            raise ValueError('Historical Phase A query mode is audit-only')
        if query_mode != 'original_question' and query_generator is None:
            raise ValueError('Generated query mode requires a query generator')

    def route_question(self, state: AgenticRAGState) -> AgenticRAGState:
        try:
            plan = self.router.route(RouterInput(state["original_question"]))
            route = plan.to_dict()
        except Exception as exc:
            return {
                "errors": _append_error(
                    state,
                    "router",
                    "router_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }

        update: AgenticRAGState = {
            "route": route,
            "task_type": str(route["task_type"]),
            "selected_tools": list(route.get("selected_tools", [])),
            "execution_order": list(route.get("execution_order", [])),
        }
        if "search_literature" in update["selected_tools"]:
            supplied_subquestion = str(
                state.get("literature_subquestion") or ""
            ).strip()
            original = str(state.get("original_question") or "").strip()
            if update["task_type"] == "hybrid" and (
                not supplied_subquestion
                or supplied_subquestion.casefold() == original.casefold()
            ):
                update["literature_subquestion"] = derive_literature_subquestion(
                    original,
                    route,
                )
            elif not supplied_subquestion:
                update["literature_subquestion"] = original
        status = str(route.get("status"))
        if status in {"unsupported", "ambiguous"}:
            update["final_status"] = "abstain_ready"
        elif status != "planned":
            update["errors"] = _append_error(
                state,
                "router",
                "unexecutable_route",
                f"Router returned terminal status {status}",
            )
            update["final_status"] = "execution_failure"
        return update

    def resolve_tool_inputs(self, state: AgenticRAGState) -> AgenticRAGState:
        structured = [
            tool
            for tool in state.get("selected_tools", [])
            if tool in {"query_training_log", "compute_metrics"}
        ]
        if not structured:
            return {
                "tool_input_resolution": {
                    "execution_status": "not_required",
                    "requested_tools": [],
                }
            }

        configured = state.get("tool_inputs", {})
        present = [
            tool
            for tool in structured
            if isinstance(configured.get(tool), Mapping)
        ]
        if len(present) == len(structured):
            return {
                "tool_input_resolution": {
                    "execution_status": "ready",
                    "requested_tools": structured,
                    "method": "prebound",
                }
            }
        if present:
            return {
                "tool_input_resolution": {
                    "execution_status": "failure",
                    "requested_tools": structured,
                    "error": {
                        "code": "missing_required_argument",
                        "message": (
                            "Partially prebound structured Tool inputs are not allowed"
                        ),
                    },
                },
                "errors": _append_error(
                    state,
                    "tool_input_resolver",
                    "missing_required_argument",
                    "Partially prebound structured Tool inputs are not allowed",
                ),
                "final_status": "execution_failure",
            }
        if self.tool_input_resolver is None:
            # Preserve pre-Resolver behavior for deliberately unconfigured graphs.
            return {"tool_input_resolution": None}

        try:
            result = self.tool_input_resolver.resolve(
                ToolInputResolverRequest(
                    question=state["original_question"],
                    task_type=state["task_type"],
                    selected_tools=list(state.get("selected_tools", [])),
                    execution_order=list(state.get("execution_order", [])),
                    route=dict(state.get("route", {})),
                )
            )
            payload = _json_safe(result)
        except Exception as exc:
            return {
                "tool_input_resolution": {
                    "execution_status": "failure",
                    "requested_tools": structured,
                    "error": {
                        "code": "resolver_failure",
                        "message": "Tool input resolution failed",
                    },
                },
                "errors": _append_error(
                    state,
                    "tool_input_resolver",
                    "resolver_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }

        if not isinstance(payload, Mapping):
            return {
                "errors": _append_error(
                    state,
                    "tool_input_resolver",
                    "invalid_resolver_result",
                    "Resolver returned a non-mapping result",
                ),
                "final_status": "execution_failure",
            }
        if str(payload.get("execution_status")) != "ready":
            error = payload.get("error")
            error_mapping = error if isinstance(error, Mapping) else {}
            code = str(error_mapping.get("code") or "resolver_failure")
            message = str(
                error_mapping.get("message")
                or "Structured Tool inputs could not be resolved"
            )
            return {
                "tool_input_resolution": dict(payload),
                "errors": _append_error(
                    state,
                    "tool_input_resolver",
                    code,
                    message,
                ),
                "final_status": "execution_failure",
            }
        resolved = payload.get("tool_inputs")
        if not isinstance(resolved, Mapping) or set(resolved) != set(structured):
            return {
                "tool_input_resolution": dict(payload),
                "errors": _append_error(
                    state,
                    "tool_input_resolver",
                    "invalid_resolver_result",
                    "Resolved inputs differ from Router-selected structured Tools",
                ),
                "final_status": "execution_failure",
            }
        return {
            "tool_input_resolution": dict(payload),
            "tool_inputs": {
                str(tool): dict(value)
                for tool, value in resolved.items()
                if isinstance(value, Mapping)
            },
        }

    def execute_initial_tools(self, state: AgenticRAGState) -> AgenticRAGState:
        initial_tool = None
        try:
            plan = _build_tool_plan(state)
            executor = self.tool_executor
            if self.query_generator is not None and self.query_mode != 'original_question':
                ports = self.tool_executor._tools
                initial_tool = InitialLiteratureTool(ports['search_literature'], self.query_generator,
                                                     state['original_question'], self.query_mode)
                executor = DeterministicToolExecutor(training_log_tool=ports['query_training_log'],
                                                     metric_tool=ports['compute_metrics'], literature_tool=initial_tool)
            results = executor.execute(plan, enabled=True)
        except Exception as exc:
            return {
                "errors": _append_error(
                    state,
                    "initial_tools",
                    "tool_execution_setup_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }

        errors = list(state.get("errors", []))
        for result in results:
            if str(result.get("status")) in FAILURE_TOOL_STATUSES:
                errors.append(
                    _error(
                        "initial_tools",
                        "tool_execution_failure",
                        f"{result.get('tool')}: {result.get('error')}",
                    )
                )
        update: AgenticRAGState = {
            "tool_results": results,
            "errors": errors,
        }
        actual_query = state['initial_query']
        if initial_tool is not None and initial_tool.selection is not None:
            selection = initial_tool.selection
            actual_query = selection['dense_query']
            update.update(first_query_selection=selection, initial_query=actual_query,
                          initial_bm25_query=selection['bm25_query'])
        if len(errors) > len(state.get("errors", [])):
            update["final_status"] = "execution_failure"
            return update

        if "search_literature" not in state.get("selected_tools", []):
            update["final_status"] = "answer_ready"
            return update

        literature_records = [
            item for item in results if item.get("tool") == "search_literature"
        ]
        if len(literature_records) != 1:
            update["errors"] = [
                *errors,
                _error(
                    "initial_tools",
                    "literature_result_missing",
                    "Exactly one initial Literature Tool result is required",
                ),
            ]
            update["final_status"] = "execution_failure"
            return update
        result_payload = literature_records[0].get("result")
        hits = (
            result_payload.get("hits", [])
            if isinstance(result_payload, Mapping)
            else []
        )
        if not isinstance(hits, list):
            update["errors"] = [
                *errors,
                _error(
                    "initial_tools",
                    "invalid_literature_result",
                    "Literature Tool hits must be a list",
                ),
            ]
            update["final_status"] = "execution_failure"
            return update
        update.update(
            {
                "literature_evidence": list(hits),
                "fused_evidence": list(hits),
                "current_query": actual_query,
                "query_history": [
                    *state.get("query_history", []),
                    actual_query,
                ],
            }
        )
        if not hits:
            update["errors"] = [
                *errors,
                _error(
                    "initial_tools",
                    "empty_initial_literature",
                    "Initial Literature Tool search returned no evidence",
                ),
            ]
            update["final_status"] = "abstain_ready"
        return update

    def grade_evidence(self, state: AgenticRAGState) -> AgenticRAGState:
        evidence = state.get("fused_evidence", [])
        if not evidence:
            return {
                "errors": _append_error(
                    state,
                    "grader",
                    "empty_literature_evidence",
                    "Runtime Evidence Grader requires literature evidence",
                ),
                "final_status": "abstain_ready",
            }
        try:
            grader_input = RuntimeEvidenceGraderInput.model_validate(
                {
                    "question": state["original_question"],
                    "literature_subquestion": state["literature_subquestion"],
                    "supplied_chunks": [
                        _runtime_chunk(item, rank)
                        for rank, item in enumerate(evidence, 1)
                    ],
                }
            )
            result = self.runtime_grader.grade(grader_input)
            payload = _json_safe(result)
        except Exception as exc:
            return {
                "errors": _append_error(
                    state,
                    "grader",
                    "grader_execution_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }

        update: AgenticRAGState = {"grader_result": payload}
        if payload.get("execution_status") != "completed":
            update["errors"] = _append_error(
                state,
                "grader",
                str(payload.get("error_code") or "grader_failure"),
                str(payload.get("error_detail") or "Runtime Grader failed"),
            )
            update["final_status"] = "execution_failure"
            return update
        if payload.get("evidence_sufficient") is True:
            update["missing_components"] = []
            update["final_status"] = "answer_ready"
            return update

        missing_components = [
            {
                "component_id": str(item["component_id"]),
                "requirement": str(item["requirement"]),
            }
            for item in payload.get("components", [])
            if item.get("status") == "missing"
        ]
        update["missing_components"] = missing_components
        if not missing_components:
            update["errors"] = _append_error(
                state,
                "grader",
                "missing_component_contract_failure",
                "Insufficient Grader result contained no missing components",
            )
            update["final_status"] = "execution_failure"
        elif state.get("retry_count", 0) >= state.get("max_retry", 2):
            update["final_status"] = "abstain_ready"
        return update

    def generate_recovery_query(self, state: AgenticRAGState) -> AgenticRAGState:
        try:
            recovery_input = EvidenceRecoveryInput.model_validate(
                {
                    "original_question": state["original_question"],
                    "literature_subquestion": state["literature_subquestion"],
                    "missing_components": state["missing_components"],
                    "previous_query": state["current_query"],
                    "query_history": state.get("query_history", []),
                    "retry_count": state.get("retry_count", 0),
                    "max_retry": state.get("max_retry", 2),
                }
            )
            result = self.recovery_agent.generate(recovery_input)
            payload = _json_safe(result)
        except Exception as exc:
            return {
                "errors": _append_error(
                    state,
                    "recovery_agent",
                    "recovery_agent_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }

        update: AgenticRAGState = {"recovery_result": payload}
        status = payload.get("execution_status")
        if status == "query_ready":
            update["current_query"] = str(payload["recovery_query"])
            return update

        error_payload = payload.get("error") or {}
        code = str(error_payload.get("code") or status or "recovery_failure")
        message = str(error_payload.get("detail") or "Recovery Agent failed")
        update["errors"] = _append_error(
            state,
            "recovery_agent",
            code,
            message,
        )
        if status == "budget_exhausted" or code == RecoveryErrorCode.DUPLICATE_QUERY.value:
            update["final_status"] = "abstain_ready"
        else:
            update["final_status"] = "execution_failure"
        return update

    def search_recovery_literature(
        self,
        state: AgenticRAGState,
    ) -> AgenticRAGState:
        try:
            response = self.literature_tool.execute(
                LiteratureInput(
                    operation="search",
                    query=state["current_query"],
                    top_k=10,
                )
            )
            payload = _json_safe(response)
        except Exception as exc:
            return {
                "errors": _append_error(
                    state,
                    "recovery_search",
                    "literature_tool_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }

        if not payload.get("success"):
            return {
                "tool_results": [
                    *state.get("tool_results", []),
                    {
                        "tool": "search_literature",
                        "phase": "recovery",
                        "retry_count": state.get("retry_count", 0),
                        **payload,
                    },
                ],
                "errors": _append_error(
                    state,
                    "recovery_search",
                    "literature_tool_failure",
                    str(payload.get("error") or "Literature Tool failed"),
                ),
                "final_status": "execution_failure",
            }
        result = payload.get("result")
        hits = result.get("hits", []) if isinstance(result, Mapping) else []
        if not isinstance(hits, list):
            return {
                "errors": _append_error(
                    state,
                    "recovery_search",
                    "invalid_literature_result",
                    "Recovery Literature Tool hits must be a list",
                ),
                "final_status": "execution_failure",
            }

        next_retry = state.get("retry_count", 0) + 1
        record = {
            "tool": "search_literature",
            "phase": "recovery",
            "retry_count": next_retry,
            **payload,
        }
        return {
            "recovery_evidence": list(hits),
            "retry_count": next_retry,
            "query_history": [
                *state.get("query_history", []),
                state["current_query"],
            ],
            "tool_results": [*state.get("tool_results", []), record],
        }

    def fuse_evidence(self, state: AgenticRAGState) -> AgenticRAGState:
        try:
            base = state.get("fused_evidence") or state.get(
                "literature_evidence", []
            )
            fusion_input = RecoveryEvidenceFusionInput(
                initial_results=[_fusion_hit(item) for item in base],
                recovery_results=[
                    _fusion_hit(item)
                    for item in state.get("recovery_evidence", [])
                ],
                initial_query=state["initial_query"],
                recovery_query=state["current_query"],
                query_history=list(state.get("query_history", [])),
            )
            fused = fuse_recovery_evidence(fusion_input)
            payload = fused.model_dump(mode="json")
        except Exception as exc:
            return {
                "errors": _append_error(
                    state,
                    "evidence_fusion",
                    "fusion_failure",
                    f"{type(exc).__name__}: {exc}",
                ),
                "final_status": "execution_failure",
            }
        return {
            "fused_evidence": payload["evidence"],
            "fusion_history": [
                *state.get("fusion_history", []),
                payload,
            ],
        }
