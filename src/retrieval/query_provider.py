"""Typed Responses calls with sanitized, stage-specific usage accounting."""

import hashlib
import json
from pathlib import Path
from time import perf_counter

from src.llm_support import estimate_cost_usd
from .literature_query import GenerationDraft, TranslationDraft, QueryProviderResult

ROOT = Path(__file__).resolve().parents[2]


def _attr(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


class OpenAILiteratureQueryProvider:
    def __init__(self, *, stage: str, api_key=None, client=None, config_path=None):
        if stage not in ("generation", "translation"):
            raise ValueError("Invalid query stage")
        self.stage = stage
        path = Path(config_path) if config_path else ROOT / "config/literature_query_model_v1.json"
        self.config = json.loads(path.read_text(encoding="utf-8"))
        if self.config["max_retries"] != 0:
            raise ValueError("Query providers must not retry")
        prompt_path = ROOT / self.config[stage]["prompt_path"]
        self.prompt = prompt_path.read_text(encoding="utf-8")
        self.config_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        self.prompt_hash = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        self.client = client
        if self.client is None and api_key:
            from openai import OpenAI
            self.client = OpenAI(api_key=api_key, max_retries=0, timeout=self.config["timeout_seconds"])

    def invoke(self, text: str) -> QueryProviderResult:
        start = perf_counter()
        meta = dict(stage=self.stage, configured_model=self.config["model"], model=self.config["model"],
                    prompt_version=self.config[self.stage]["prompt_version"], prompt_sha256=self.prompt_hash,
                    config_sha256=self.config_hash, api_requests=0, failed_calls=0, response_received=False,
                    decoding_config={key: self.config[key] for key in ("temperature", "reasoning_effort", "verbosity", "max_output_tokens", "max_retries", "timeout_seconds")},
                    pricing_snapshot=self.config["pricing_usd_per_million_tokens"])
        if self.client is None:
            meta.update(latency_ms=0.0, input_tokens=0, cached_input_tokens=0, output_tokens=0,
                        total_tokens=0, estimated_cost_usd=0.0)
            return QueryProviderResult(None, meta, "provider_not_configured")
        response, payload, error = None, None, None
        schema = GenerationDraft if self.stage == "generation" else TranslationDraft
        input_key = "original_question" if self.stage == "generation" else "step2_query"
        meta["api_requests"] = 1
        try:
            response = self.client.responses.parse(
                model=self.config["model"], instructions=self.prompt,
                input=json.dumps({input_key: text}, ensure_ascii=False), text_format=schema,
                temperature=self.config["temperature"], reasoning={"effort": self.config["reasoning_effort"]},
                text={"verbosity": self.config["verbosity"]},
                max_output_tokens=self.config["max_output_tokens"], store=False,
            )
            parsed = _attr(response, "output_parsed")
            if isinstance(parsed, schema):
                parsed = parsed.model_dump(mode="json")
            payload = schema.model_validate(parsed).model_dump(mode="json")
        except Exception:
            error = "provider_failure"
            meta["failed_calls"] = 1
        usage = _attr(response, "usage")
        input_tokens = int(_attr(usage, "input_tokens", 0) or 0)
        cached = int(_attr(_attr(usage, "input_tokens_details"), "cached_tokens", 0) or 0)
        output_tokens = int(_attr(usage, "output_tokens", 0) or 0)
        meta.update(model=_attr(response, "model", self.config["model"]), response_id=_attr(response, "id"),
                    response_received=response is not None, response_status=_attr(response, "status"),
                    latency_ms=(perf_counter()-start)*1000, input_tokens=input_tokens,
                    cached_input_tokens=cached, output_tokens=output_tokens,
                    total_tokens=int(_attr(usage, "total_tokens", input_tokens+output_tokens) or 0),
                    estimated_cost_usd=estimate_cost_usd(input_tokens=input_tokens, cached_input_tokens=cached,
                                                        output_tokens=output_tokens, pricing=self.config["pricing_usd_per_million_tokens"]),
                    usage_available=usage is not None)
        return QueryProviderResult(payload, meta, error)
