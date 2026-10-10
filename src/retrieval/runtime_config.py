"""Versioned retrieval settings only; no evaluation imports or Gold paths."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config/retrieval_h0_v1.json'
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class FrozenSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True, protected_namespaces=())


class DenseSettings(FrozenSettings):
    model_id: str = Field(min_length=1)
    model_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    embedding_dimension: int = Field(gt=0)
    max_sequence_length: int = Field(gt=0)
    normalize_embeddings: Literal[True]
    precision: Literal['float32']
    batch_size: int = Field(gt=0)
    source_depth: int = Field(gt=0)
    unit: Literal['parent']
    similarity: Literal['cosine_exact']
    metadata: Literal[False]


class BM25Settings(FrozenSettings):
    k1: float = Field(gt=0)
    b: float = Field(ge=0, le=1)
    tokenizer_version: Literal['script_aware_unicode_alnum_casefold_v1']
    source_depth: int = Field(gt=0)
    score_policy: Literal['retain_zero', 'positive_only']
    metadata: Literal[False]


class RRFSettings(FrozenSettings):
    k: int = Field(gt=0)
    dense_weight: Literal[1.0]
    bm25_weight: Literal[1.0]
    top_k: int = Field(gt=0)
    tie_breaker: Literal['score_desc_best_rank_rank_sum_chunk_id']


class AssetValidation(FrozenSettings):
    corpus_chunks_sha256: Sha256
    corpus_manifest_sha256: Sha256
    hybrid_manifest_sha256: Sha256
    expected_chunk_count: int = Field(gt=0)


class RetrievalConfig(FrozenSettings):
    schema_version: Literal[1]
    config_version: str = Field(min_length=1)
    setting: Literal['H0', 'H1']
    retrieval_version: str = Field(min_length=1)
    corpus_version: str = Field(min_length=1)
    embedding_run_id: str = Field(min_length=1)
    query_mode: Literal['original_question', 'literature_subquestion', 'generated_ko', 'translated_bm25', 'translated_both']
    dense: DenseSettings
    bm25: BM25Settings
    rrf: RRFSettings
    validation: AssetValidation

    @model_validator(mode='after')
    def validate_depth(self):
        expected_policy = 'retain_zero' if self.setting == 'H0' else 'positive_only'
        if self.bm25.score_policy != expected_policy:
            raise ValueError('BM25 score policy differs from declared H0/H1 setting')
        if self.query_mode != 'original_question' and self.setting != 'H1':
            raise ValueError('Question isolation requires the H1 BM25 policy')
        if self.rrf.top_k > min(self.dense.source_depth, self.bm25.source_depth):
            raise ValueError('Final Top-K exceeds source depth')
        if max(self.dense.source_depth, self.bm25.source_depth) > self.validation.expected_chunk_count:
            raise ValueError('Source depth exceeds corpus size')
        return self


def load_retrieval_config(path: str | Path = DEFAULT_CONFIG_PATH) -> RetrievalConfig:
    return RetrievalConfig.model_validate_json(Path(path).read_text(encoding='utf-8'))


DEFAULT_RETRIEVAL_CONFIG = load_retrieval_config()
# Compatibility exports used by the existing pgvector tool and scripts.
EMBEDDING_RUN_ID = DEFAULT_RETRIEVAL_CONFIG.embedding_run_id
CORPUS_VERSION = DEFAULT_RETRIEVAL_CONFIG.corpus_version
