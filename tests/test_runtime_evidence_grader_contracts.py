import json
from pathlib import Path

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from src.grading.runtime_contracts import (
    RuntimeEvidenceAssessmentDraft,
    RuntimeEvidenceGraderInput,
)
from src.grading.runtime_provider import load_runtime_grader_config


ROOT = Path(__file__).resolve().parents[1]


def _chunk(chunk_id: str, rank: int, *, corpus: str = "literature_corpus_v1") -> dict:
    return {
        "chunk_id": chunk_id,
        "paper_id": f"paper-{rank}",
        "pmcid": f"PMC{rank}",
        "title": f"Paper {rank}",
        "section": "Results",
        "text": f"Frozen source text {rank}.",
        "retrieval": {
            "rank": rank,
            "dense_rank": rank + 1,
            "bm25_rank": rank,
            "rrf_score": 0.02,
        },
        "corpus_version": corpus,
    }


def _input_payload() -> dict:
    return {
        "question": "내 기록과 관련 문헌을 바탕으로 무엇을 말할 수 있나?",
        "literature_subquestion": "관련 개입이 최대근력 향상을 지원하는가?",
        "supplied_chunks": [_chunk("chunk-1", 1), _chunk("chunk-2", 2)],
    }


def test_model_output_schema_is_minimal_and_strict() -> None:
    schema = to_strict_json_schema(RuntimeEvidenceAssessmentDraft)
    component_ref = schema["properties"]["components"]["items"]["$ref"]
    component_name = component_ref.rsplit("/", 1)[-1]
    component_properties = set(schema["$defs"][component_name]["properties"])

    assert set(schema["properties"]) == {"components"}
    assert component_properties == {
        "component_id",
        "requirement",
        "status",
        "supporting_chunk_ids",
    }
    forbidden = {"quote", "verdict", "evidence_sufficient", "confidence", "rationale"}
    assert component_properties.isdisjoint(forbidden)
    assert schema["additionalProperties"] is False
    assert schema["$defs"][component_name]["additionalProperties"] is False


def test_component_order_is_contiguous_but_duplicate_anchors_are_allowed() -> None:
    draft = RuntimeEvidenceAssessmentDraft.model_validate(
        {
            "components": [
                {
                    "component_id": "C1",
                    "requirement": "효과 근거",
                    "status": "supported",
                    "supporting_chunk_ids": ["chunk-1", "chunk-1"],
                }
            ]
        }
    )
    assert draft.components[0].supporting_chunk_ids == ["chunk-1", "chunk-1"]

    with pytest.raises(ValidationError, match="contiguous C1..Cn"):
        RuntimeEvidenceAssessmentDraft.model_validate(
            {
                "components": [
                    {
                        "component_id": "C2",
                        "requirement": "효과 근거",
                        "status": "missing",
                        "supporting_chunk_ids": [],
                    }
                ]
            }
        )


def test_input_contract_excludes_hybrid_structured_evidence() -> None:
    payload = _input_payload()
    parsed = RuntimeEvidenceGraderInput.model_validate(payload)
    assert parsed.evidence_channel == "literature_only"
    assert parsed.structured_channel_policy == (
        "log_and_metric_evidence_excluded_and_evaluated_separately"
    )

    payload["log_evidence"] = [{"exercise": "Bench Press"}]
    with pytest.raises(ValidationError, match="log_evidence"):
        RuntimeEvidenceGraderInput.model_validate(payload)


@pytest.mark.parametrize("mutation", ["duplicate", "rank", "corpus"])
def test_input_enforces_chunk_identity_rank_and_corpus(mutation: str) -> None:
    payload = _input_payload()
    chunks = [json.loads(json.dumps(item)) for item in payload["supplied_chunks"]]
    payload["supplied_chunks"] = chunks
    if mutation == "duplicate":
        chunks[1]["chunk_id"] = chunks[0]["chunk_id"]
        match = "unique"
    elif mutation == "rank":
        chunks[1]["retrieval"]["rank"] = 3
        match = "contiguous"
    else:
        chunks[1]["corpus_version"] = "other_corpus"
        match = "one corpus"

    with pytest.raises(ValidationError, match=match):
        RuntimeEvidenceGraderInput.model_validate(payload)


def test_runtime_config_disables_retry_and_recovery_behaviors() -> None:
    config, prompt, config_hash, prompt_hash = load_runtime_grader_config()
    assert config["max_retries"] == 0
    assert config["controls"] == {
        "provider_retry": False,
        "query_rewrite": False,
        "re_retrieval": False,
        "recovery_agent": False,
        "answer_generation": False,
        "model_global_verdict": False,
        "model_exact_quote": False,
        "model_confidence": False,
    }
    assert len(config_hash) == len(prompt_hash) == 64
    assert "do not output quotes" in prompt.lower()
