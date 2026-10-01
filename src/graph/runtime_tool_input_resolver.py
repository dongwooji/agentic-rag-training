"""Normalize API input expressions before the existing typed argument resolver."""

from __future__ import annotations

import re
from time import perf_counter
from typing import Any

from src.routing.runtime_language import (
    InputLanguageError,
    LANGUAGE_VERSION,
    RuntimeQuestionNormalizer,
)

from .tool_input_resolver import ToolInputResolver, ToolInputResolverRequest


class RuntimeToolInputResolver:
    def __init__(
        self,
        resolver: ToolInputResolver,
        normalizer: RuntimeQuestionNormalizer | None = None,
    ) -> None:
        self.resolver = resolver
        self.normalizer = normalizer or RuntimeQuestionNormalizer()

    def resolve(self, request: ToolInputResolverRequest) -> dict[str, Any]:
        started = perf_counter()
        if not request.structured_tools:
            return self.resolver.resolve(request).model_dump(mode="json")
        try:
            normalized = self.normalizer.normalize(request.question)
            text = normalized.question.casefold()
            if "compute_metrics" in request.structured_tools:
                explicit_operations = sum(
                    term in text
                    for term in ("weekly volume", "weekly frequency", "training gap", "plateau candidate", "e1rm")
                )
                if explicit_operations > 1:
                    raise InputLanguageError("현재는 한 요청에서 하나의 지표만 계산합니다. 지표별로 질문해주세요.")
                if "e1rm" in text and re.search(r"처음|첫|최초", text) and re.search(r"최근|마지막", text):
                    if not all(re.search(prefix + r"\s*\d+-session", text) for prefix in ("처음", "최근")):
                        raise InputLanguageError("비교할 세션 수를 지정해주세요. 예: 처음 3세션과 최근 3세션.")
            adapted = request.model_copy(update={"question": normalized.question})
            payload = self.resolver.resolve(adapted).model_dump(mode="json")
            payload["provenance"].update({
                "input_language_version": LANGUAGE_VERSION,
                "input_language_config_sha256": self.normalizer.config_sha256,
                "original_question": request.question,
                "normalized_question": normalized.question,
                "language_replacements": list(normalized.replacements),
            })
            return payload
        except InputLanguageError as exc:
            return {
                "execution_status": "failure",
                "requested_tools": request.structured_tools,
                "tool_inputs": {},
                "provenance": {
                    "method": "deterministic",
                    "input_language_version": LANGUAGE_VERSION,
                    "input_language_config_sha256": self.normalizer.config_sha256,
                    "selected_tools": request.selected_tools,
                    "original_question": request.question,
                    "latency_ms": (perf_counter() - started) * 1000.0,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0},
                },
                "error": {"code": "ambiguous_tool_arguments", "message": str(exc)},
            }
