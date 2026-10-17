"""One provider call per assessment; existing provider configuration is preserved."""
import json
from pathlib import Path
from time import perf_counter
from src.grading.runtime_provider import (
    OpenAIRuntimeEvidenceProvider, RuntimeProviderResult, _usage_from_response,
)
from src.grading.runtime_contracts import RuntimeErrorCode, RuntimeTokenUsage
from src.llm_support import extract_final_json_text
from src.grading.fixed_requirements_v1.contracts import Reassessment
from .contracts import ReferencedAssessment, source_catalog

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config/runtime_evidence_grader_fixed_requirements_v1_1.json"


class FixedRequirementsProvider(OpenAIRuntimeEvidenceProvider):
    def __init__(self, **kwargs):
        super().__init__(config_path=kwargs.pop("config_path", DEFAULT_CONFIG), **kwargs)

    def invoke(self, data):
        started = perf_counter()
        if self.client is None:
            return RuntimeProviderResult(None, self.model, self.prompt_sha256, self.config_sha256,
                                         response_id=None, latency_ms=0.0, token_usage=RuntimeTokenUsage(), response_metadata={},
                                         error_code=RuntimeErrorCode.PROVIDER_FAILURE,
                                         error_detail="provider unavailable")
        try:
            from openai.lib._pydantic import to_strict_json_schema
            schema = ReferencedAssessment if data.requirements is None else Reassessment
            payload = data.model_dump(mode="json")
            if data.requirements is None:
                payload["literature_source_catalog"] = source_catalog(data.literature_subquestion, "L")
                payload["original_source_catalog"] = source_catalog(data.question, "Q")
            response = self.client.responses.create(
                model=self.model, instructions=self.prompt, input=json.dumps(payload, ensure_ascii=False),
                temperature=float(self.config["temperature"]), reasoning={"effort":self.config["reasoning_effort"]},
                text={"verbosity":self.config["verbosity"], "format":{"type":"json_schema", "name":schema.__name__,
                    "schema":to_strict_json_schema(schema), "strict":True}},
                max_output_tokens=self.config["max_output_tokens"], store=False, service_tier=self.config["service_tier"])
            return RuntimeProviderResult(
                extract_final_json_text(response), str(getattr(response,"model",self.model)),
                self.prompt_sha256, self.config_sha256, response_id=getattr(response,"id",None),
                latency_ms=(perf_counter()-started)*1000, token_usage=_usage_from_response(response,self.config),
                response_metadata={"response_received":True})
        except Exception as exc:
            return RuntimeProviderResult(None,self.model,self.prompt_sha256,self.config_sha256,
                latency_ms=(perf_counter()-started)*1000, error_code=RuntimeErrorCode.PROVIDER_FAILURE,
                response_id=None, token_usage=RuntimeTokenUsage(), response_metadata={},
                error_detail=f"{type(exc).__name__}: {exc}")
