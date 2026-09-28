"""Preregistered, question-text-only deterministic routing rules."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata
from typing import Any, Mapping, Sequence

from .contracts import (
    RouterInput,
    RoutingError,
    RoutingPlan,
    RoutingStatus,
    RuleMatch,
    TaskType,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/router_baseline_v1.json"
EXPECTED_ROUTER_VERSION = "router_baseline_v1"
EXPECTED_TOOL_IDS = {
    "training_log": "query_training_log",
    "metric": "compute_metrics",
    "literature": "search_literature",
}


def load_router_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("router_version") != EXPECTED_ROUTER_VERSION:
        raise ValueError("Unsupported deterministic Router version")
    if config.get("status") != "preregistered_before_first_evaluation":
        raise ValueError("Router configuration is not preregistered")
    if config.get("tool_ids") != EXPECTED_TOOL_IDS:
        raise ValueError("Router Tool IDs differ from the frozen evaluation contract")
    if config.get("controls", {}).get("llm_used") is not False:
        raise ValueError("Deterministic Router cannot enable an LLM")
    if config.get("controls", {}).get("tool_execution") is not False:
        raise ValueError("Phase 9 Router cannot execute Tools")
    return config


def _normalize(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _literal_matches(text: str, patterns: Sequence[str]) -> tuple[str, ...]:
    return tuple(pattern for pattern in patterns if _normalize(pattern) in text)


def _regex_matches(text: str, patterns: Sequence[str]) -> tuple[str, ...]:
    return tuple(pattern for pattern in patterns if re.search(pattern, text, re.I))


class DeterministicRouter:
    """Select a Tool plan; never execute Tools or generate an answer."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_router_config())
        if self.config.get("router_version") != EXPECTED_ROUTER_VERSION:
            raise ValueError("Router configuration version mismatch")
        tool_ids = self.config["tool_ids"]
        self._training = str(tool_ids["training_log"])
        self._metric = str(tool_ids["metric"])
        self._literature = str(tool_ids["literature"])
        self._global_order = tuple(str(item) for item in self.config["execution_order"])

    def _plan(
        self,
        *,
        task_type: TaskType,
        status: RoutingStatus,
        tools: Sequence[str],
        reason: str,
        confidence: float,
        rule_id: str,
        normalized_question: str,
        signals: Mapping[str, bool],
        matches: Mapping[str, tuple[str, ...]],
        unsupported_reason: str | None = None,
        error: RoutingError | None = None,
    ) -> RoutingPlan:
        selected = tuple(tools)
        order = tuple(item for item in self._global_order if item in selected)
        if set(order) != set(selected):
            raise RuntimeError("Selected Tool is absent from configured execution order")
        return RoutingPlan(
            router_version=EXPECTED_ROUTER_VERSION,
            status=status,
            route=task_type.value,
            task_type=task_type,
            selected_tools=selected,
            execution_order=order,
            routing_reason=reason,
            confidence=confidence,
            rule_match=RuleMatch(
                rule_id=rule_id,
                normalized_question=normalized_question,
                signals=dict(signals),
                matched_patterns=dict(matches),
            ),
            unsupported_reason=unsupported_reason,
            error=error,
        )

    def route(self, request: RouterInput) -> RoutingPlan:
        question = getattr(request, "question", None)
        empty_signals = {
            "unsupported": False,
            "structured_log": False,
            "metric": False,
            "literature": False,
        }
        empty_matches = {
            "unsupported_missing_data": (),
            "unsupported_prescription": (),
            "structured_log_literals": (),
            "structured_log_regex": (),
            "metric_literals": (),
            "metric_regex": (),
            "literature_explicit": (),
            "literature_concepts": (),
            "literature_regex": (),
        }
        if not isinstance(question, str):
            return self._plan(
                task_type=TaskType.INVALID,
                status=RoutingStatus.INVALID,
                tools=(),
                reason="Router input question must be text.",
                confidence=0.0,
                rule_id="R-INVALID-NON-TEXT",
                normalized_question="",
                signals=empty_signals,
                matches=empty_matches,
                error=RoutingError("invalid_input", "question must be a string"),
            )
        text = _normalize(question)
        if not text:
            return self._plan(
                task_type=TaskType.INVALID,
                status=RoutingStatus.INVALID,
                tools=(),
                reason="Blank questions cannot be routed.",
                confidence=0.0,
                rule_id="R-INVALID-BLANK",
                normalized_question=text,
                signals=empty_signals,
                matches=empty_matches,
                error=RoutingError("invalid_input", "question cannot be blank"),
            )

        unsupported = self.config["unsupported_signals"]
        log = self.config["structured_log_signals"]
        metric = self.config["metric_signals"]
        literature = self.config["literature_signals"]
        matches = {
            "unsupported_missing_data": _literal_matches(
                text, unsupported["missing_data_literals"]
            ),
            "unsupported_prescription": _literal_matches(
                text, unsupported["personalized_prescription_literals"]
            ),
            "structured_log_literals": _literal_matches(text, log["literals"]),
            "structured_log_regex": _regex_matches(text, log["regex"]),
            "metric_literals": _literal_matches(text, metric["literals"]),
            "metric_regex": _regex_matches(text, metric["regex"]),
            "literature_explicit": _literal_matches(
                text, literature["explicit_literals"]
            ),
            "literature_concepts": _literal_matches(
                text, literature["concept_literals"]
            ),
            "literature_regex": _regex_matches(text, literature["regex"]),
        }
        has_unsupported = bool(
            matches["unsupported_missing_data"]
            or matches["unsupported_prescription"]
        )
        has_log = bool(
            matches["structured_log_literals"] or matches["structured_log_regex"]
        )
        has_metric = bool(matches["metric_literals"] or matches["metric_regex"])
        has_literature = bool(
            matches["literature_explicit"]
            or matches["literature_concepts"]
            or matches["literature_regex"]
        )
        signals = {
            "unsupported": has_unsupported,
            "structured_log": has_log,
            "metric": has_metric,
            "literature": has_literature,
        }
        confidence = self.config["confidence_policy"]

        if has_unsupported and not has_literature:
            missing = (
                matches["unsupported_missing_data"]
                + matches["unsupported_prescription"]
            )
            unsupported_reason = (
                "The question requires unsupported or unrecorded inputs: "
                + ", ".join(missing)
            )
            return self._plan(
                task_type=TaskType.UNSUPPORTED,
                status=RoutingStatus.UNSUPPORTED,
                tools=(),
                reason=(
                    "Dataset-limitation rule matched before Tool selection; no Tool "
                    "can supply the requested missing personal data."
                ),
                confidence=float(confidence["unsupported"]),
                rule_id="R-UNSUPPORTED-MISSING-DATA",
                normalized_question=text,
                signals=signals,
                matches=matches,
                unsupported_reason=unsupported_reason,
            )

        if has_log and has_literature:
            if has_metric:
                return self._plan(
                    task_type=TaskType.HYBRID,
                    status=RoutingStatus.PLANNED,
                    tools=(self._training, self._metric, self._literature),
                    reason=(
                        "Structured-log, deterministic-metric and literature signals "
                        "all matched."
                    ),
                    confidence=float(confidence["hybrid"]),
                    rule_id="R-HYBRID-WITH-METRIC",
                    normalized_question=text,
                    signals=signals,
                    matches=matches,
                )
            return self._plan(
                task_type=TaskType.HYBRID,
                status=RoutingStatus.PLANNED,
                tools=(self._training, self._literature),
                reason=(
                    "Structured-log and literature signals matched without a "
                    "deterministic metric request."
                ),
                confidence=float(confidence["hybrid"]),
                rule_id="R-HYBRID-LOOKUP-LITERATURE",
                normalized_question=text,
                signals=signals,
                matches=matches,
            )

        if has_log:
            if has_metric:
                return self._plan(
                    task_type=TaskType.LOG_METRIC,
                    status=RoutingStatus.PLANNED,
                    tools=(self._training, self._metric),
                    reason="Structured-log and deterministic-metric signals matched.",
                    confidence=float(confidence["log_metric"]),
                    rule_id="R-LOG-METRIC",
                    normalized_question=text,
                    signals=signals,
                    matches=matches,
                )
            return self._plan(
                task_type=TaskType.LOG_LOOKUP,
                status=RoutingStatus.PLANNED,
                tools=(self._training,),
                reason="Structured-log lookup matched without metric or literature scope.",
                confidence=float(confidence["log_lookup"]),
                rule_id="R-LOG-LOOKUP",
                normalized_question=text,
                signals=signals,
                matches=matches,
            )

        if has_literature:
            explicit = bool(matches["literature_explicit"])
            return self._plan(
                task_type=TaskType.LITERATURE_ONLY,
                status=RoutingStatus.PLANNED,
                tools=(self._literature,),
                reason="Literature evidence scope matched without structured-log scope.",
                confidence=float(
                    confidence[
                        "literature_explicit" if explicit else "literature_concept_only"
                    ]
                ),
                rule_id="R-LITERATURE-ONLY",
                normalized_question=text,
                signals=signals,
                matches=matches,
            )

        return self._plan(
            task_type=TaskType.AMBIGUOUS,
            status=RoutingStatus.AMBIGUOUS,
            tools=(),
            reason="No preregistered routing signal combination matched.",
            confidence=float(confidence["ambiguous"]),
            rule_id="R-AMBIGUOUS",
            normalized_question=text,
            signals=signals,
            matches=matches,
            unsupported_reason="Clarification is required before selecting a Tool.",
        )
