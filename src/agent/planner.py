"""Pinned OpenAI Responses API backend for the preregistered first plan."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from .contracts import PlannerCallResult, PlannerDraft, PlannerUsage


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/agent_planner_v1.json"
EXPECTED_AGENT_VERSION = "agent_baseline_v1"
EXPECTED_CONFIG_SHA256 = (
    "53b9744e30015c8fa7f18c636ba41253009edcc3b27ee4e34c0f1350e17184b6"
)
EXPECTED_PROMPT_SHA256 = (
    "f8ce2695de99b2111e0253545c5658d80b4832d7339daaedd074e80290bd3e8e"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_agent_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, Path]:
    config_path = Path(path).resolve()
    if sha256_file(config_path) != EXPECTED_CONFIG_SHA256:
        raise RuntimeError("Preregistered Agent configuration hash changed")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("agent_version") != EXPECTED_AGENT_VERSION:
        raise RuntimeError("Agent version differs from the preregistered baseline")
    if config.get("status") != "preregistered_before_first_evaluation":
        raise RuntimeError("Agent configuration is not preregistered")
    root = config_path.parents[1]
    prompt_path = (root / str(config["prompt_path"])).resolve()
    if sha256_file(prompt_path) != EXPECTED_PROMPT_SHA256:
        raise RuntimeError("Preregistered Agent prompt hash changed")
    prompt = prompt_path.read_text(encoding="utf-8")
    return config, prompt, prompt_path


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


class PlannerBackend(Protocol):
    def invoke(self, question: str) -> PlannerCallResult: ...


def _read_attr(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _extract_planning_json_text(response: Any) -> str:
    """Select the final structured message without aggregating commentary JSON."""

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


class OpenAIPlannerBackend:
    """One model request per question; SDK and transport retries are disabled."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        client: Any | None = None,
    ) -> None:
        self.config, self.prompt, self.prompt_path = load_agent_config(config_path)
        supplied_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None:
            if not supplied_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is required for the first Phase 10 evaluation"
                )
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Phase 10 requires the pinned openai SDK") from exc
            client = OpenAI(
                api_key=supplied_key,
                timeout=float(self.config["timeout_seconds"]),
                max_retries=int(self.config["max_retries"]),
            )
        self.client = client

    def invoke(self, question: str) -> PlannerCallResult:
        started = perf_counter()
        empty_usage = PlannerUsage()
        if not isinstance(question, str) or not question.strip():
            return PlannerCallResult(
                model=str(self.config["model"]),
                latency_ms=(perf_counter() - started) * 1000,
                usage=empty_usage,
                error="question must be a non-empty string",
            )
        try:
            from openai.lib._pydantic import to_strict_json_schema

            response = self.client.responses.create(
                model=self.config["model"],
                instructions=self.prompt,
                input=question.strip(),
                temperature=float(self.config["temperature"]),
                reasoning={"effort": self.config["reasoning_effort"]},
                text={
                    "verbosity": self.config["verbosity"],
                    "format": {
                        "type": "json_schema",
                        "name": "PlannerDraft",
                        "schema": to_strict_json_schema(PlannerDraft),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=bool(self.config["store"]),
                service_tier=self.config["service_tier"],
            )
            output_text = _extract_planning_json_text(response)
            parsed = json.loads(output_text)
            if not isinstance(parsed, dict):
                raise RuntimeError("Planner output must be a JSON object")

            raw_usage = _read_attr(response, "usage")
            input_tokens = int(_read_attr(raw_usage, "input_tokens", 0) or 0)
            output_tokens = int(_read_attr(raw_usage, "output_tokens", 0) or 0)
            total_tokens = int(
                _read_attr(raw_usage, "total_tokens", input_tokens + output_tokens)
                or input_tokens + output_tokens
            )
            input_details = _read_attr(raw_usage, "input_tokens_details")
            cached_tokens = int(
                _read_attr(input_details, "cached_tokens", 0) or 0
            )
            usage = PlannerUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimate_cost_usd(
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    pricing=self.config["pricing_usd_per_million_tokens"],
                ),
            )
            return PlannerCallResult(
                raw_plan=parsed,
                model=str(_read_attr(response, "model", self.config["model"])),
                response_id=_read_attr(response, "id"),
                latency_ms=(perf_counter() - started) * 1000,
                usage=usage,
            )
        except Exception as exc:
            return PlannerCallResult(
                model=str(self.config["model"]),
                latency_ms=(perf_counter() - started) * 1000,
                usage=empty_usage,
                error=f"{type(exc).__name__}: {exc}",
            )
