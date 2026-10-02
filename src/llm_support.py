"""Shared OpenAI Responses helpers for runtime providers.

Behavior-identical copies of helpers that originated in the historical LLM
Planner and Grader v1 provider modules, which are preserved in the
``legacy-pre-retrieval-v2`` Git tag. Error messages are preserved verbatim.
"""

from __future__ import annotations

from typing import Any


def estimate_cost_usd(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    pricing: dict[str, Any],
) -> float:
    uncached = max(0, input_tokens - cached_input_tokens)
    cost = (
        uncached * float(pricing["input"])
        + cached_input_tokens * float(pricing["cached_input"])
        + output_tokens * float(pricing["output"])
    ) / 1_000_000
    return round(cost, 12)


def _read_attr(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def extract_final_json_text(response: Any) -> str:
    """Copy of the historical Grader v1 ``_extract_final_json_text``."""

    outputs = _read_attr(response, "output")
    if isinstance(outputs, (list, tuple)):
        final_messages: list[str] = []
        unphased_messages: list[str] = []
        for output in outputs:
            if _read_attr(output, "type") != "message":
                continue
            texts = [
                _read_attr(content, "text")
                for content in (_read_attr(output, "content", []) or [])
                if _read_attr(content, "type") == "output_text"
                and isinstance(_read_attr(content, "text"), str)
                and _read_attr(content, "text").strip()
            ]
            if not texts:
                continue
            value = "".join(texts)
            if _read_attr(output, "phase") == "final_answer":
                final_messages.append(value)
            elif _read_attr(output, "phase") != "commentary":
                unphased_messages.append(value)
        candidates = final_messages or unphased_messages
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise RuntimeError("Responses API returned multiple final grades")
    output_text = _read_attr(response, "output_text")
    if not isinstance(output_text, str) or not output_text.strip():
        raise RuntimeError("Responses API returned no grading JSON")
    return output_text


def extract_planning_json_text(response: Any) -> str:
    """Copy of the historical LLM Planner ``_extract_planning_json_text``.

    Select the final structured message without aggregating commentary JSON.
    """

    outputs = _read_attr(response, "output")
    if isinstance(outputs, (list, tuple)):
        final_messages: list[str] = []
        unphased_messages: list[str] = []
        commentary_messages: list[str] = []
        for output in outputs:
            if _read_attr(output, "type") != "message":
                continue
            content_texts: list[str] = []
            for content in _read_attr(output, "content", []) or []:
                if _read_attr(content, "type") != "output_text":
                    continue
                text = _read_attr(content, "text")
                if isinstance(text, str) and text.strip():
                    content_texts.append(text)
            if not content_texts:
                continue
            message_text = "".join(content_texts)
            phase = _read_attr(output, "phase")
            if phase == "final_answer":
                final_messages.append(message_text)
            elif phase == "commentary":
                commentary_messages.append(message_text)
            else:
                unphased_messages.append(message_text)

        candidates = final_messages or unphased_messages
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise RuntimeError(
                "Responses API returned multiple final planning messages"
            )
        if commentary_messages:
            raise RuntimeError(
                "Responses API returned commentary but no final planning message"
            )

    output_text = _read_attr(response, "output_text")
    if not isinstance(output_text, str) or not output_text.strip():
        raise RuntimeError("Responses API returned no planning JSON")
    return output_text
