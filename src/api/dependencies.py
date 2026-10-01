"""Lazy construction and lifecycle management for the real workflow."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Mapping, Protocol

from src.agent.executor import DeterministicToolExecutor
from src.answer.generator import FinalResponseLayer
from src.answer.provider import OpenAIFinalAnswerProvider
from src.database.config import DatabaseConfig
from src.grading.runtime_grader import RuntimeEvidenceGrader
from src.grading.runtime_provider import OpenAIRuntimeEvidenceProvider
from src.graph.tool_input_provider import OpenAIToolInputProvider
from src.graph.tool_input_resolver import ToolInputResolver
from src.graph.runtime_tool_input_resolver import RuntimeToolInputResolver
from src.graph.workflow import AgenticRAGWorkflow
from src.graph.interpreted_workflow import InterpretedWorkflow
from src.interpretation.interpreter import QuestionInterpreter
from src.interpretation.provider import OpenAIQuestionInterpreterProvider
from src.recovery.agent import EvidenceRecoveryAgent
from src.recovery.provider import OpenAIEvidenceRecoveryProvider
from src.routing.runtime import RuntimeRouter
from src.routing.runtime_language import RuntimeQuestionNormalizer
from src.tools.literature import LiteratureTool
from src.tools.metric import MetricTool
from src.tools.training_log import PsycopgTrainingRepository, TrainingLogTool


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CACHE = PROJECT_ROOT / "data" / "models" / "huggingface"


class WorkflowPort(Protocol):
    def invoke(self, question: str) -> Mapping[str, Any]: ...
    def clarify(self, clarification_id: str, selected_option: str) -> Mapping[str, Any]: ...


class WorkflowConfigurationError(RuntimeError):
    """Raised without including secret values when runtime secrets are absent."""


class WorkflowInitializationError(RuntimeError):
    """Raised when external runtime dependencies cannot be initialized."""


class WorkflowClarificationUnavailable(RuntimeError):
    """The underlying legacy workflow does not support selection resume."""


class RuntimeWorkflow:
    """Own one real graph plus its closeable database-backed Literature Tool."""

    def __init__(
        self,
        *,
        workflow: AgenticRAGWorkflow | InterpretedWorkflow,
        literature_tool: LiteratureTool,
    ) -> None:
        self._workflow = workflow
        self._literature_tool = literature_tool
        self._invoke_lock = RLock()
        self._closed = False

    def invoke(self, question: str) -> Mapping[str, Any]:
        with self._invoke_lock:
            if self._closed:
                raise RuntimeError("workflow runtime is closed")
            return self._workflow.invoke(question)

    def clarify(self, clarification_id: str, selected_option: str) -> Mapping[str, Any]:
        """Forward selection to the same live graph under its existing lock."""
        with self._invoke_lock:
            if self._closed:
                raise RuntimeError("workflow runtime is closed")
            resume = getattr(self._workflow, "clarify", None)
            if not callable(resume):
                raise WorkflowClarificationUnavailable()
            return resume(clarification_id, selected_option)

    def close(self) -> None:
        with self._invoke_lock:
            if self._closed:
                return
            self._literature_tool.close()
            self._closed = True


def _required_secret(name: str) -> str:
    value = os.environ.get(name, "")
    if not value.strip():
        raise WorkflowConfigurationError(
            f"Required server environment variable is not configured: {name}"
        )
    return value


def _build_runtime_workflow() -> RuntimeWorkflow:
    """Build the existing graph without changing any domain configuration."""

    postgres_password = _required_secret("PGPASSWORD")
    openai_api_key = _required_secret("OPENAI_API_KEY")
    cache_dir = Path(
        os.environ.get("AGENTIC_RAG_MODEL_CACHE", str(DEFAULT_MODEL_CACHE))
    )

    literature_tool: LiteratureTool | None = None
    try:
        database_config = DatabaseConfig.from_environment()
        training_repository = PsycopgTrainingRepository(
            config=database_config,
            password=postgres_password,
        )
        training_tool = TrainingLogTool(training_repository)
        metric_tool = MetricTool()
        literature_tool = LiteratureTool.from_postgres(
            password=postgres_password,
            config=database_config,
            cache_dir=cache_dir,
        )
        grader = RuntimeEvidenceGrader(
            OpenAIRuntimeEvidenceProvider(api_key=openai_api_key)
        )
        recovery = EvidenceRecoveryAgent(
            OpenAIEvidenceRecoveryProvider(api_key=openai_api_key)
        )
        executor = DeterministicToolExecutor(
            training_log_tool=training_tool,
            metric_tool=metric_tool,
            literature_tool=literature_tool,
        )
        shared = dict(
            tool_executor=executor, literature_tool=literature_tool,
            runtime_grader=grader, recovery_agent=recovery,
            final_response_layer=FinalResponseLayer(OpenAIFinalAnswerProvider(api_key=openai_api_key)),
        )
        mode = os.environ.get("AGENTIC_RAG_QUESTION_INTERPRETATION", "legacy")
        if mode == "phase_a":
            # Do not even construct the legacy LLM argument provider here.
            workflow = InterpretedWorkflow(
                interpreter=QuestionInterpreter(exercise_repository=training_repository,
                                                provider=OpenAIQuestionInterpreterProvider(api_key=openai_api_key)),
                **shared,
            )
        elif mode == "legacy":
            normalizer = RuntimeQuestionNormalizer()
            tool_input_resolver = RuntimeToolInputResolver(
                ToolInputResolver(exercise_repository=training_repository,
                                  provider=OpenAIToolInputProvider(api_key=openai_api_key)), normalizer,
            )
            workflow = AgenticRAGWorkflow(router=RuntimeRouter(normalizer), tool_input_resolver=tool_input_resolver, **shared)
        else:
            raise WorkflowConfigurationError("Unsupported question interpretation mode")
        return RuntimeWorkflow(
            workflow=workflow,
            literature_tool=literature_tool,
        )
    except WorkflowConfigurationError:
        raise
    except Exception as exc:
        if literature_tool is not None:
            literature_tool.close()
        raise WorkflowInitializationError(
            "Agentic RAG runtime initialization failed"
        ) from exc


_runtime_guard = Lock()
_runtime_workflow: RuntimeWorkflow | None = None


def get_workflow() -> WorkflowPort:
    """FastAPI dependency: lazily return one process-local workflow runtime."""

    global _runtime_workflow
    if _runtime_workflow is None:
        with _runtime_guard:
            if _runtime_workflow is None:
                _runtime_workflow = _build_runtime_workflow()
    return _runtime_workflow


def close_workflow() -> None:
    """Close an initialized runtime without causing lazy initialization."""

    global _runtime_workflow
    with _runtime_guard:
        runtime, _runtime_workflow = _runtime_workflow, None
    if runtime is not None:
        runtime.close()
