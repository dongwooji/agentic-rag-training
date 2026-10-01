"""Rule-first orchestration. There is exactly one optional LLM interpretation."""

from time import perf_counter
from typing import Literal, Protocol

from pydantic import Field
from src.agent.contracts import StrictModel
from src.graph.tool_input_resolver import ExerciseCanonicalizer
from .contracts import QuestionInterpretation
from .provider import InterpreterProviderResult
from .rule_parser import RuleQuestionParser
from .validator import GroundingError, validate_interpretation
from .exercise_catalog import RepositoryExerciseCatalog, catalog_sha256


class InterpreterProvider(Protocol):
    def invoke(self, question: str, *, canonical_exercises: tuple[str, ...]) -> InterpreterProviderResult: ...


class InterpretationResult(StrictModel):
    execution_status: Literal["ready", "clarification_required", "failure"]
    interpretation: QuestionInterpretation | None = None
    provider: InterpreterProviderResult | None = None
    error_code: str | None = None
    latency_ms: float = Field(default=0, ge=0)


class QuestionInterpreter:
    def __init__(self, *, exercise_repository, provider: InterpreterProvider | None = None):
        self.canonicalizer = ExerciseCanonicalizer(exercise_repository)
        self.provider = provider
        self.rule_parser = RuleQuestionParser()
        self.exercise_catalog = RepositoryExerciseCatalog(exercise_repository)

    def interpret(self, question: str) -> InterpretationResult:
        started = perf_counter()
        provider_result = None
        try:
            if not isinstance(question, str) or not question.strip():
                raise ValueError("invalid_question")
            raw = self.rule_parser.parse(question)
            candidates = None
            source = "rule"
            if raw is None:
                source = "llm"
                if self.provider is None:
                    return InterpretationResult(execution_status="failure", error_code="interpreter_provider_unavailable")
                candidates = self.exercise_catalog.names()
                provider_result = self.provider.invoke(question, canonical_exercises=candidates)
                provider_result.response_metadata.update(canonical_catalog_count=len(candidates),
                                                         canonical_catalog_sha256=catalog_sha256(candidates))
                if provider_result.error or provider_result.raw_interpretation is None:
                    return InterpretationResult(execution_status="failure", provider=provider_result, error_code="interpreter_provider_failure",
                                                latency_ms=(perf_counter() - started) * 1000)
                raw = provider_result.raw_interpretation
            interpretation = validate_interpretation(question, raw, source=source, canonicalizer=self.canonicalizer,
                                                       normalizer=self.rule_parser.normalizer,
                                                       canonical_exercises=candidates)
            return InterpretationResult(execution_status="clarification_required" if interpretation.clarification_required else "ready",
                                        interpretation=interpretation, provider=provider_result,
                                        latency_ms=(perf_counter() - started) * 1000)
        except Exception as exc:
            return InterpretationResult(execution_status="failure", provider=provider_result,
                                        error_code="ungrounded_interpretation" if isinstance(exc, GroundingError) else "interpretation_validation_failure",
                                        latency_ms=(perf_counter() - started) * 1000)
