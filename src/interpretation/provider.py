"""One typed interpretation request, never a Tool call or a retry loop."""

import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import Field
from src.agent.contracts import PlannerUsage, StrictModel
from src.llm_support import estimate_cost_usd
from .contracts import InterpretationDraft
from .exercise_catalog import candidate_schema


ROOT = Path(__file__).resolve().parents[2]


class InterpreterProviderResult(StrictModel):
    raw_interpretation: dict[str, Any] | None = None
    model: str | None = None
    response_id: str | None = None
    prompt_sha256: str | None = None
    config_sha256: str | None = None
    latency_ms: float = Field(default=0, ge=0)
    api_requests: int = Field(default=0, ge=0, le=1)
    usage: PlannerUsage = Field(default_factory=PlannerUsage)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


def _attr(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


class OpenAIQuestionInterpreterProvider:
    def __init__(self, *, api_key=None, client=None, config_path=None):
        path = Path(config_path) if config_path else ROOT / "config/question_interpreter.json"
        self.config = json.loads(path.read_text(encoding="utf-8"))
        if self.config["max_retries"] != 0 or self.config["version"] != "question_interpretation_phase_a_v1":
            raise ValueError("Unexpected interpreter configuration")
        prompt_path = ROOT / self.config["prompt_path"]
        self.prompt = prompt_path.read_text(encoding="utf-8")
        self.config_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        self.prompt_sha256 = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        self.client = client
        if self.client is None:
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if key:
                from openai import OpenAI
                self.client = OpenAI(api_key=key, timeout=self.config["timeout_seconds"], max_retries=0)

    def invoke(self, question: str, *, canonical_exercises: tuple[str, ...]) -> InterpreterProviderResult:
        start = perf_counter()
        metadata = dict(model=self.config["model"], prompt_sha256=self.prompt_sha256, config_sha256=self.config_sha256)
        if self.client is None:
            return InterpreterProviderResult(**metadata, error="provider_not_configured")
        response = None
        try:
            schema = candidate_schema(canonical_exercises)
            response = self.client.responses.parse(
                model=self.config["model"], instructions=self.prompt,
                input=json.dumps({"question": question, "allowed_canonical_exercises": canonical_exercises}, ensure_ascii=False),
                text_format=schema,
                text={"verbosity": self.config["verbosity"]},
                reasoning={"effort": self.config["reasoning_effort"]},
                temperature=self.config["temperature"],
                max_output_tokens=self.config["max_output_tokens"], store=False,
            )
            parsed = _attr(response, "output_parsed")
            if parsed is None:
                raise ValueError("missing_typed_output")
            parsed_payload = parsed.model_dump() if isinstance(parsed, InterpretationDraft) else parsed
            payload = schema.model_validate(parsed_payload).model_dump(mode="json")
            error = None
        except Exception as exc:
            payload = None
            # Deliberately do not log raw exception messages, headers or credentials.
            error = type(exc).__name__
        usage = _attr(response, "usage")
        input_tokens = int(_attr(usage, "input_tokens", 0) or 0)
        output_tokens = int(_attr(usage, "output_tokens", 0) or 0)
        cached = int(_attr(_attr(usage, "input_tokens_details"), "cached_tokens", 0) or 0)
        metadata["model"] = _attr(response, "model", self.config["model"])
        return InterpreterProviderResult(
            **metadata, raw_interpretation=payload, error=error, api_requests=1,
            response_id=_attr(response, "id"), latency_ms=(perf_counter() - start) * 1000,
            usage=PlannerUsage(input_tokens=input_tokens, cached_input_tokens=cached, output_tokens=output_tokens,
                               total_tokens=int(_attr(usage, "total_tokens", input_tokens + output_tokens) or 0),
                               estimated_cost_usd=estimate_cost_usd(input_tokens=input_tokens, cached_input_tokens=cached,
                                                                   output_tokens=output_tokens, pricing=self.config["pricing_usd_per_million_tokens"])),
            response_metadata={"response_received": response is not None, "status": _attr(response, "status"),
                               "reasoning_tokens": int(_attr(_attr(usage, "output_tokens_details"), "reasoning_tokens", 0) or 0)},
        )
