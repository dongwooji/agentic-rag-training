"""Pinned OpenAI Responses backend for Evidence Grader v1."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from src.agent.planner import estimate_cost_usd

from .contracts import EvidenceGrade, GraderCallResult, GraderInput, GraderUsage


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/grader_v1.json"
EXPECTED_GRADER_VERSION = "grader_baseline_v1"
EXPECTED_CONFIG_SHA256 = (
    "f6f490481a635e02d22f48bddda8f3313d7275631d0460141f500243925c4f37"
)
EXPECTED_PROMPT_SHA256 = (
    "7eefe2f4c4b83ddfed5b9d98db315b9c2fa151b73627c9c561f419116fe20b70"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_grader_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, Path]:
    config_path = Path(path).resolve()
    if sha256_file(config_path) != EXPECTED_CONFIG_SHA256:
        raise RuntimeError("Preregistered Grader configuration hash changed")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("grader_version") != EXPECTED_GRADER_VERSION:
        raise RuntimeError("Grader version differs from the preregistered baseline")
    if config.get("status") != "preregistered_before_first_evaluation":
        raise RuntimeError("Grader configuration is not preregistered")
    root = config_path.parents[1]
    prompt_path = (root / str(config["prompt_path"])).resolve()
    if sha256_file(prompt_path) != EXPECTED_PROMPT_SHA256:
        raise RuntimeError("Preregistered Grader prompt hash changed")
    return config, prompt_path.read_text(encoding="utf-8"), prompt_path


def _read_attr(value: Any, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _extract_final_json_text(response: Any) -> str:
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


class GraderBackend(Protocol):
    def invoke(self, grader_input: GraderInput) -> GraderCallResult: ...


class OpenAIGraderBackend:
    """Exactly one provider call per invocation; transport retries are disabled."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        client: Any | None = None,
    ) -> None:
        self.config, self.prompt, self.prompt_path = load_grader_config(config_path)
        supplied_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None:
            if not supplied_key:
                raise RuntimeError(
                    "OPENAI_API_KEY is required for the first Phase 11A evaluation"
                )
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError("Phase 11A requires the pinned openai SDK") from exc
            client = OpenAI(
                api_key=supplied_key,
                timeout=float(self.config["timeout_seconds"]),
                max_retries=int(self.config["max_retries"]),
            )
        self.client = client

    def invoke(self, grader_input: GraderInput) -> GraderCallResult:
        started = perf_counter()
        empty_usage = GraderUsage()
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
                        "name": "EvidenceGrade",
                        "schema": to_strict_json_schema(EvidenceGrade),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=bool(self.config["store"]),
                service_tier=self.config["service_tier"],
            )
            parsed = json.loads(_extract_final_json_text(response))
            grade = EvidenceGrade.model_validate(parsed)

            raw_usage = _read_attr(response, "usage")
            input_tokens = int(_read_attr(raw_usage, "input_tokens", 0) or 0)
            output_tokens = int(_read_attr(raw_usage, "output_tokens", 0) or 0)
            total_tokens = int(
                _read_attr(raw_usage, "total_tokens", input_tokens + output_tokens)
                or input_tokens + output_tokens
            )
            input_details = _read_attr(raw_usage, "input_tokens_details")
            output_details = _read_attr(raw_usage, "output_tokens_details")
            cached_tokens = int(
                _read_attr(input_details, "cached_tokens", 0) or 0
            )
            reasoning_tokens = int(
                _read_attr(output_details, "reasoning_tokens", 0) or 0
            )
            usage = GraderUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=estimate_cost_usd(
                    input_tokens=input_tokens,
                    cached_input_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    pricing=self.config["pricing_usd_per_million_tokens"],
                ),
            )
            return GraderCallResult(
                raw_grade=grade.model_dump(mode="json"),
                model=str(_read_attr(response, "model", self.config["model"])),
                response_id=_read_attr(response, "id"),
                latency_ms=(perf_counter() - started) * 1000,
                usage=usage,
            )
        except Exception as exc:
            return GraderCallResult(
                model=str(self.config["model"]),
                latency_ms=(perf_counter() - started) * 1000,
                usage=empty_usage,
                error=f"{type(exc).__name__}: {exc}",
            )

