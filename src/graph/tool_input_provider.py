"""Optional OpenAI typed backend for non-deterministic Tool argument extraction."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from src.agent.contracts import PlannerUsage
from src.llm_support import estimate_cost_usd, extract_planning_json_text

from .tool_input_resolver import (
    ResolverProviderResult,
    ToolInputExtractionDraft,
    ToolInputResolverRequest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/tool_input_resolver.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def load_resolver_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, str, str]:
    config_path = Path(path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("resolver_version") != "tool_input_resolver_v1":
        raise ValueError("Unsupported Tool Input Resolver version")
    if config.get("max_retries") != 0:
        raise ValueError("Tool Input Resolver provider retries must remain disabled")
    prompt_path = PROJECT_ROOT / str(config["prompt_path"])
    prompt = prompt_path.read_text(encoding="utf-8")
    return config, prompt, _sha256(config_path), _sha256(prompt_path)


class OpenAIToolInputProvider:
    """Make one typed extraction request; never select or execute Tools."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        client: Any | None = None,
    ) -> None:
        (
            self.config,
            self.prompt,
            self.config_sha256,
            self.prompt_sha256,
        ) = load_resolver_config(config_path)
        self.model = str(self.config["model"])
        self.client = client
        self.initialization_error: str | None = None
        if self.client is None:
            supplied_key = api_key or os.environ.get("OPENAI_API_KEY")
            if not supplied_key:
                self.initialization_error = "OPENAI_API_KEY is not configured"
            else:
                try:
                    from openai import OpenAI

                    self.client = OpenAI(
                        api_key=supplied_key,
                        timeout=float(self.config["timeout_seconds"]),
                        max_retries=0,
                    )
                except Exception as exc:
                    self.initialization_error = f"{type(exc).__name__}: {exc}"

    def invoke(self, request: ToolInputResolverRequest) -> ResolverProviderResult:
        started = perf_counter()
        if self.client is None:
            return ResolverProviderResult(
                model=self.model,
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                latency_ms=(perf_counter() - started) * 1000.0,
                error=self.initialization_error or "provider unavailable",
            )
        try:
            from openai.lib._pydantic import to_strict_json_schema

            response = self.client.responses.create(
                model=self.model,
                instructions=self.prompt,
                input=request.model_dump_json(),
                temperature=float(self.config["temperature"]),
                reasoning={"effort": self.config["reasoning_effort"]},
                text={
                    "verbosity": self.config["verbosity"],
                    "format": {
                        "type": "json_schema",
                        "name": "ToolInputExtractionDraft",
                        "schema": to_strict_json_schema(ToolInputExtractionDraft),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=False,
                service_tier=self.config["service_tier"],
            )
            raw_text = extract_planning_json_text(response)
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                raise TypeError("Tool input output must be a JSON object")
            raw_usage = _read_attr(response, "usage")
            input_tokens = int(_read_attr(raw_usage, "input_tokens", 0) or 0)
            output_tokens = int(_read_attr(raw_usage, "output_tokens", 0) or 0)
            details = _read_attr(raw_usage, "input_tokens_details")
            cached = int(_read_attr(details, "cached_tokens", 0) or 0)
            usage = PlannerUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached,
                output_tokens=output_tokens,
                total_tokens=int(
                    _read_attr(
                        raw_usage,
                        "total_tokens",
                        input_tokens + output_tokens,
                    )
                    or input_tokens + output_tokens
                ),
                estimated_cost_usd=estimate_cost_usd(
                    input_tokens=input_tokens,
                    cached_input_tokens=cached,
                    output_tokens=output_tokens,
                    pricing=self.config["pricing_usd_per_million_tokens"],
                ),
            )
            return ResolverProviderResult(
                raw_inputs=parsed,
                model=str(_read_attr(response, "model", self.model)),
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=_read_attr(response, "id"),
                latency_ms=(perf_counter() - started) * 1000.0,
                usage=usage,
            )
        except Exception as exc:
            return ResolverProviderResult(
                model=self.model,
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                latency_ms=(perf_counter() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}",
            )
