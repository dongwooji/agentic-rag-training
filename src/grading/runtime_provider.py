"""Single-call OpenAI provider for the Runtime Evidence Grader."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from src.agent.planner import estimate_cost_usd

from .provider import _extract_final_json_text
from .runtime_contracts import (
    RuntimeErrorCode,
    RuntimeEvidenceAssessmentDraft,
    RuntimeEvidenceGraderInput,
    RuntimeTokenUsage,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/runtime_evidence_grader.json"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_runtime_grader_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, str, str]:
    config_path = Path(path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("component") != "runtime_evidence_grader":
        raise RuntimeError("Unexpected Runtime Evidence Grader configuration")
    if config.get("max_retries") != 0:
        raise RuntimeError("Runtime Evidence Grader provider retries must be zero")
    root = config_path.parents[1]
    prompt_path = (root / str(config["prompt_path"])).resolve()
    if root not in prompt_path.parents:
        raise RuntimeError("Runtime Evidence Grader prompt must be inside project")
    prompt = prompt_path.read_text(encoding="utf-8")
    return (
        config,
        prompt,
        sha256_file(config_path),
        sha256_file(prompt_path),
    )


@dataclass(frozen=True)
class RuntimeProviderResult:
    raw_output_text: str | None
    model: str
    prompt_sha256: str
    config_sha256: str
    response_id: str | None
    latency_ms: float
    token_usage: RuntimeTokenUsage
    response_metadata: dict[str, Any]
    error_code: RuntimeErrorCode | None = None
    error_detail: str | None = None


def _usage_from_response(
    response: Any, config: dict[str, Any]
) -> RuntimeTokenUsage:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(
        getattr(usage, "total_tokens", input_tokens + output_tokens)
        or input_tokens + output_tokens
    )
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    cached_tokens = int(getattr(input_details, "cached_tokens", 0) or 0)
    reasoning_tokens = int(
        getattr(output_details, "reasoning_tokens", 0) or 0
    )
    return RuntimeTokenUsage(
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


class OpenAIRuntimeEvidenceProvider:
    """Make one typed provider request and never retry or finalize it."""

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
        ) = load_runtime_grader_config(config_path)
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
                    self.initialization_error = (
                        f"{type(exc).__name__}: {exc}"
                    )

    def invoke(
        self, grader_input: RuntimeEvidenceGraderInput
    ) -> RuntimeProviderResult:
        started = perf_counter()
        if self.client is None:
            return RuntimeProviderResult(
                raw_output_text=None,
                model=self.model,
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=None,
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=RuntimeTokenUsage(),
                response_metadata={"response_received": False},
                error_code=RuntimeErrorCode.PROVIDER_FAILURE,
                error_detail=self.initialization_error or "provider unavailable",
            )

        response: Any | None = None
        try:
            from openai.lib._pydantic import to_strict_json_schema

            response = self.client.responses.create(
                model=self.model,
                instructions=self.prompt,
                input=grader_input.model_dump_json(),
                temperature=float(self.config["temperature"]),
                reasoning={"effort": self.config["reasoning_effort"]},
                text={
                    "verbosity": self.config["verbosity"],
                    "format": {
                        "type": "json_schema",
                        "name": "RuntimeEvidenceAssessmentDraft",
                        "schema": to_strict_json_schema(
                            RuntimeEvidenceAssessmentDraft
                        ),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=bool(self.config["store"]),
                service_tier=self.config["service_tier"],
            )
            token_usage = _usage_from_response(response, self.config)
            response_id = getattr(response, "id", None)
            return RuntimeProviderResult(
                raw_output_text=_extract_final_json_text(response),
                model=str(getattr(response, "model", self.model)),
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=response_id,
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=token_usage,
                response_metadata={
                    "response_received": True,
                    "id": response_id,
                    "status": getattr(response, "status", None),
                    "service_tier": getattr(response, "service_tier", None),
                },
            )
        except Exception as exc:
            response_id = (
                getattr(response, "id", None)
                if response is not None
                else None
            )
            return RuntimeProviderResult(
                raw_output_text=None,
                model=str(
                    getattr(response, "model", self.model)
                    if response is not None
                    else self.model
                ),
                prompt_sha256=self.prompt_sha256,
                config_sha256=self.config_sha256,
                response_id=response_id,
                latency_ms=(perf_counter() - started) * 1000,
                token_usage=(
                    _usage_from_response(response, self.config)
                    if response is not None
                    else RuntimeTokenUsage()
                ),
                response_metadata={
                    "response_received": response is not None,
                    "id": response_id,
                },
                error_code=RuntimeErrorCode.PROVIDER_FAILURE,
                error_detail=f"{type(exc).__name__}: {exc}",
            )

