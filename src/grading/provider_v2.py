"""Pinned one-shot OpenAI backend for the Grader v2 candidate."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any

from src.agent.planner import estimate_cost_usd

from .contracts import GraderCallResult, GraderUsage
from .provider import _extract_final_json_text
from .v2_contracts import (
    EvidenceAssessmentDraftV2,
    GraderV2Input,
    finalize_v2_assessment,
)
from .v2_review import sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config/grader_v2.json"
EXPECTED_GRADER_VERSION = "grader_v2_candidate_v1"
EXPECTED_CONFIG_SHA256 = (
    "c2533dd9fb95c38d41d91700bcc9c3f7167fbf278e06b3b0e4109aff424c7e20"
)
EXPECTED_PROMPT_SHA256 = (
    "ee9372e3a23fb52ec2c4843169da6445e39deb40cffd5cd48b4d4ab1fd38b702"
)


def load_grader_v2_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, Any], str, Path]:
    config_path = Path(path).resolve()
    if sha256_file(config_path) != EXPECTED_CONFIG_SHA256:
        raise RuntimeError("Preregistered Grader v2 configuration hash changed")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("grader_version") != EXPECTED_GRADER_VERSION:
        raise RuntimeError("Unexpected Grader v2 version")
    if config.get("design_revision") != "pre_evaluation_final_r2":
        raise RuntimeError("Unexpected Grader v2 design revision")
    if config.get("status") != "preregistered_before_heldout_human_review_and_evaluation":
        raise RuntimeError("Grader v2 is not in preregistered state")
    root = config_path.parents[1]
    prompt_path = (root / str(config["prompt_path"])).resolve()
    if sha256_file(prompt_path) != EXPECTED_PROMPT_SHA256:
        raise RuntimeError("Preregistered Grader v2 prompt hash changed")
    return config, prompt_path.read_text(encoding="utf-8"), prompt_path


class OpenAIGraderV2Backend:
    """Exactly one provider call; no recovery, retry, or threshold decision."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        client: Any | None = None,
    ) -> None:
        self.config, self.prompt, self.prompt_path = load_grader_v2_config(
            config_path
        )
        supplied_key = api_key or os.environ.get("OPENAI_API_KEY")
        if client is None:
            if not supplied_key:
                raise RuntimeError("OPENAI_API_KEY is required for Grader v2")
            from openai import OpenAI

            client = OpenAI(
                api_key=supplied_key,
                timeout=float(self.config["timeout_seconds"]),
                max_retries=int(self.config["max_retries"]),
            )
        self.client = client

    def invoke(self, grader_input: GraderV2Input) -> GraderCallResult:
        started = perf_counter()
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
                        "name": "EvidenceAssessmentDraftV2",
                        "schema": to_strict_json_schema(EvidenceAssessmentDraftV2),
                        "strict": True,
                    },
                },
                max_output_tokens=int(self.config["max_output_tokens"]),
                store=bool(self.config["store"]),
                service_tier=self.config["service_tier"],
            )
            draft = EvidenceAssessmentDraftV2.model_validate(
                json.loads(_extract_final_json_text(response))
            )
            grade = finalize_v2_assessment(draft)
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
            grader_usage = GraderUsage(
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
                model=str(getattr(response, "model", self.config["model"])),
                response_id=getattr(response, "id", None),
                latency_ms=(perf_counter() - started) * 1000,
                usage=grader_usage,
            )
        except Exception as exc:
            return GraderCallResult(
                model=str(self.config["model"]),
                latency_ms=(perf_counter() - started) * 1000,
                usage=GraderUsage(),
                error=f"{type(exc).__name__}: {exc}",
            )
