"""Single-call OpenAI provider for grounded final-answer drafting."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from src.llm_support import estimate_cost_usd, extract_final_json_text

from .contracts import AnswerTokenUsage, FinalAnswerDraft, FinalAnswerInput


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/final_answer.json"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_final_answer_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, str, str]:
    config_path = Path(path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("component") != "final_answer_layer":
        raise RuntimeError("Unexpected Final Answer configuration")
    if config.get("max_retries") != 0:
        raise RuntimeError("Final Answer provider retries must be zero")
    if config.get("evidence_budget") != 10:
        raise RuntimeError("Final Answer evidence budget must remain Top-10")
    root = config_path.parents[1]
    prompt_path = (root / str(config["prompt_path"])).resolve()
    if root not in prompt_path.parents:
        raise RuntimeError("Final Answer prompt must be inside the project")
    return (
        config,
        prompt_path.read_text(encoding="utf-8"),
        sha256_file(config_path),
        sha256_file(prompt_path),
    )


@dataclass(frozen=True)
class FinalAnswerProviderResult:
    raw_output_text: str | None
    model: str
    prompt_sha256: str
    config_sha256: str
    response_id: str | None
    latency_ms: float
    token_usage: AnswerTokenUsage
    response_metadata: dict[str, Any]
    error_detail: str | None = None


class FinalAnswerProvider(Protocol):
    def invoke(self, answer_input: FinalAnswerInput) -> FinalAnswerProviderResult: ...


def _read_attr(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_from_response(
    response: Any,
    config: dict[str, Any],
) -> AnswerTokenUsage:
    raw_usage = _read_attr(response, "usage")
    input_tokens = int(_read_attr(raw_usage, "input_tokens", 0) or 0)
    output_tokens = int(_read_attr(raw_usage, "output_tokens", 0) or 0)
    total_tokens = int(
        _read_attr(raw_usage, "total_tokens", input_tokens + output_tokens)
        or input_tokens + output_tokens
    )
    input_details = _read_attr(raw_usage, "input_tokens_details")
    output_details = _read_attr(raw_usage, "output_tokens_details")
    cached_tokens = int(_read_attr(input_details, "cached_tokens", 0) or 0)
    reasoning_tokens = int(_read_attr(output_details, "reasoning_tokens", 0) or 0)
    return AnswerTokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimate_cost_usd(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            pricing=config["pricing_usd_per_million_tokens"],
        ),
    )


class OpenAIFinalAnswerProvider:
    """Generate one typed draft; never retrieve, call Tools, or retry."""

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
        ) = load_final_answer_config(config_path)
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

    def invoke(self, answer_input: FinalAnswerInput) -> FinalAnswerProviderResult:
        started = perf_counter()
        if self.client is None:
            return FinalAnswerProviderResult(
                raw_output_text=None,
                model=self.model,
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=None,
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=AnswerTokenUsage(),
                response_metadata={"response_received": False},
                error_detail=self.initialization_error or "provider unavailable",
            )

        response: Any | None = None
        try:
            from openai.lib._pydantic import to_strict_json_schema

            response = self.client.responses.create(
                model=self.model,
                instructions=self.prompt,
                input=answer_input.model_dump_json(),
                temperature=float(self.config["temperature"]),
                reasoning={"effort": self.config["reasoning_effort"]},
                text={
                    "verbosity": self.config["verbosity"],
                    "format": {
                        "type": "json_schema",
                        "name": "FinalAnswerDraft",
                        "schema": to_strict_json_schema(FinalAnswerDraft),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=bool(self.config["store"]),
                service_tier=self.config["service_tier"],
            )
            response_id = _read_attr(response, "id")
            return FinalAnswerProviderResult(
                raw_output_text=extract_final_json_text(response),
                model=str(_read_attr(response, "model", self.model)),
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=response_id,
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=_usage_from_response(response, self.config),
                response_metadata={
                    "response_received": True,
                    "id": response_id,
                    "status": _read_attr(response, "status"),
                    "service_tier": _read_attr(response, "service_tier"),
                },
            )
        except Exception as exc:
            response_id = _read_attr(response, "id")
            return FinalAnswerProviderResult(
                raw_output_text=None,
                model=str(_read_attr(response, "model", self.model)),
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=response_id,
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=(
                    _usage_from_response(response, self.config)
                    if response is not None
                    else AnswerTokenUsage()
                ),
                response_metadata={
                    "response_received": response is not None,
                    "id": response_id,
                },
                error_detail=f"{type(exc).__name__}: {exc}",
            )
