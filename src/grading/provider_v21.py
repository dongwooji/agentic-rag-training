"""Pinned one-request-per-case OpenAI backend for Grader v2.1."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from src.agent.planner import estimate_cost_usd

from .contracts import GraderUsage
from .provider import _extract_final_json_text
from .v21_contracts import EvidenceAssessmentDraftV21, GraderV21Input
from .v21_review import sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/grader_v2_1.json"
EXPECTED_GRADER_VERSION = "grader_v2_1_candidate_v1"
EXPECTED_CONFIG_SHA256 = (
    "6fd4db93e3e71650b6349794345d0e0ee5427bf1b302cdc7823d8b05798568c1"
)
EXPECTED_PROMPT_SHA256 = (
    "05d27548cc6abf76b403b655bb50afa28293ef8d429c1307db5374e9735ab117"
)


@dataclass(frozen=True)
class GraderV21ProviderResult:
    """Provider response before typed validation and deterministic finalization."""

    raw_output_text: str | None
    raw_response_output: Any | None
    response_metadata: dict[str, Any]
    model: str
    response_id: str | None
    latency_ms: float
    usage: GraderUsage
    error: str | None = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_safe(model_dump(mode="json"))
    return str(value)


def _usage_from_response(response: Any, config: dict[str, Any]) -> GraderUsage:
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
    return GraderUsage(
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


def _response_metadata(response: Any) -> dict[str, Any]:
    return {
        "response_received": True,
        "id": getattr(response, "id", None),
        "model": getattr(response, "model", None),
        "status": getattr(response, "status", None),
        "created_at": getattr(response, "created_at", None),
        "service_tier": getattr(response, "service_tier", None),
        "incomplete_details": _json_safe(
            getattr(response, "incomplete_details", None)
        ),
        "error": _json_safe(getattr(response, "error", None)),
    }


def load_grader_v21_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, Path]:
    config_path = Path(path).resolve()
    if sha256_file(config_path) != EXPECTED_CONFIG_SHA256:
        raise RuntimeError("Preregistered Grader v2.1 configuration hash changed")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("grader_version") != EXPECTED_GRADER_VERSION:
        raise RuntimeError("Unexpected Grader v2.1 version")
    if config.get("design_revision") != (
        "question_conditioned_components_pre_evaluation_final_r1"
    ):
        raise RuntimeError("Unexpected Grader v2.1 design revision")
    if config.get("status") != "preregistered_before_first_api_evaluation":
        raise RuntimeError("Grader v2.1 is not preregistered")
    root = config_path.parents[1]
    prompt_path = (root / str(config["prompt_path"])).resolve()
    if sha256_file(prompt_path) != EXPECTED_PROMPT_SHA256:
        raise RuntimeError("Preregistered Grader v2.1 prompt hash changed")
    return config, prompt_path.read_text(encoding="utf-8"), prompt_path


class OpenAIGraderV21Backend:
    """Exactly one provider request per case, with no SDK retry."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        client: Any | None = None,
    ) -> None:
        self.config, self.prompt, self.prompt_path = load_grader_v21_config(
            config_path
        )
        supplied_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None:
            if not supplied_key:
                raise RuntimeError("OPENAI_API_KEY is required for Grader v2.1")
            from openai import OpenAI

            client = OpenAI(
                api_key=supplied_key,
                timeout=float(self.config["timeout_seconds"]),
                max_retries=int(self.config["max_retries"]),
            )
        self.client = client

    def invoke(self, grader_input: GraderV21Input) -> GraderV21ProviderResult:
        started = perf_counter()
        response: Any | None = None
        try:
            from openai.lib._pydantic import to_strict_json_schema

            response = self.client.responses.create(
                model=self.config["model"],
                instructions=self.prompt,
                input=grader_input.model_dump_json(),
                temperature=float(self.config["temperature"]),
                reasoning={"effort": self.config["reasoning_effort"]},
                text={
                    "verbosity": self.config["verbosity"],
                    "format": {
                        "type": "json_schema",
                        "name": "EvidenceAssessmentDraftV21",
                        "schema": to_strict_json_schema(
                            EvidenceAssessmentDraftV21
                        ),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=bool(self.config["store"]),
                service_tier=self.config["service_tier"],
            )
            usage = _usage_from_response(response, self.config)
            metadata = _response_metadata(response)
            raw_response_output = _json_safe(getattr(response, "output", None))
            try:
                raw_output_text = _extract_final_json_text(response)
            except Exception as exc:
                return GraderV21ProviderResult(
                    raw_output_text=None,
                    raw_response_output=raw_response_output,
                    response_metadata=metadata,
                    model=str(getattr(response, "model", self.config["model"])),
                    response_id=getattr(response, "id", None),
                    latency_ms=(perf_counter() - started) * 1000,
                    usage=usage,
                    error=f"{type(exc).__name__}: {exc}",
                )
            return GraderV21ProviderResult(
                raw_output_text=raw_output_text,
                raw_response_output=raw_response_output,
                response_metadata=metadata,
                model=str(getattr(response, "model", self.config["model"])),
                response_id=getattr(response, "id", None),
                latency_ms=(perf_counter() - started) * 1000,
                usage=usage,
            )
        except Exception as exc:
            return GraderV21ProviderResult(
                raw_output_text=None,
                raw_response_output=(
                    _json_safe(getattr(response, "output", None))
                    if response is not None
                    else None
                ),
                response_metadata=(
                    _response_metadata(response)
                    if response is not None
                    else {"response_received": False}
                ),
                model=str(self.config["model"]),
                response_id=(
                    getattr(response, "id", None)
                    if response is not None
                    else None
                ),
                latency_ms=(perf_counter() - started) * 1000,
                usage=(
                    _usage_from_response(response, self.config)
                    if response is not None
                    else GraderUsage()
                ),
                error=f"{type(exc).__name__}: {exc}",
            )
