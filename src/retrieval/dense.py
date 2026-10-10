"""Pinned dense encoder used by the immutable Phase 6 baseline."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
import platform
from typing import Sequence

import numpy as np


DEFAULT_MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
EXPECTED_EMBEDDING_DIMENSION = 384
DEFAULT_BATCH_SIZE = 32
MODEL_RUNTIME_FILES = (
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "unigram.json",
)


@dataclass(frozen=True)
class EncoderMetadata:
    model_id: str
    model_revision: str
    embedding_dimension: int
    normalize_embeddings: bool
    precision: str
    batch_size: int
    max_sequence_length: int
    device: str
    sentence_transformers_version: str
    transformers_version: str
    torch_version: str
    numpy_version: str
    python_version: str
    platform: str


class MiniLMEncoder:
    """Load an exact model revision and emit normalized float32 vectors."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        model_revision: str = DEFAULT_MODEL_REVISION,
        batch_size: int = DEFAULT_BATCH_SIZE,
        cache_dir: str | Path | None = None,
        device: str = "cpu",
        max_seq_length: int | None = None,
    ) -> None:
        try:
            from huggingface_hub import snapshot_download
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Phase 6 requires sentence-transformers. Install requirements.txt first."
            ) from exc

        snapshot_path = snapshot_download(
            repo_id=model_id,
            revision=model_revision,
            cache_dir=str(Path(cache_dir).resolve()) if cache_dir else None,
            allow_patterns=MODEL_RUNTIME_FILES,
        )
        self._model = SentenceTransformer(snapshot_path, device=device)
        if max_seq_length is not None:
            if type(max_seq_length) is not int or max_seq_length not in (128, 256, 512):
                raise ValueError('Only declared input lengths 128, 256, 512 are supported')
            position_limit = self._model[0].auto_model.config.max_position_embeddings
            if max_seq_length > position_limit:
                raise ValueError('Input length exceeds model position capacity')
            self._model.max_seq_length = max_seq_length
        self._batch_size = batch_size
        self._model_id = model_id
        self._model_revision = model_revision
        dimension = self._model.get_embedding_dimension()
        if dimension != EXPECTED_EMBEDDING_DIMENSION:
            raise ValueError(
                f"Unexpected embedding dimension {dimension}; "
                f"expected {EXPECTED_EMBEDDING_DIMENSION}"
            )
        self.metadata = EncoderMetadata(
            model_id=model_id,
            model_revision=model_revision,
            embedding_dimension=dimension,
            normalize_embeddings=True,
            precision="float32",
            batch_size=batch_size,
            max_sequence_length=int(self._model.max_seq_length),
            device=device,
            sentence_transformers_version=metadata.version("sentence-transformers"),
            transformers_version=metadata.version("transformers"),
            torch_version=metadata.version("torch"),
            numpy_version=np.__version__,
            python_version=platform.python_version(),
            platform=platform.platform(),
        )

    @property
    def tokenizer(self):
        """The exact loaded model tokenizer, shared by representation building."""
        return self._model.tokenizer

    def encode(self, texts: Sequence[str], *, show_progress: bool = False) -> np.ndarray:
        if not texts:
            return np.empty((0, EXPECTED_EMBEDDING_DIMENSION), dtype=np.float32)
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty strings")
        vectors = self._model.encode(
            list(texts),
            batch_size=self._batch_size,
            show_progress_bar=show_progress,
            output_value="sentence_embedding",
            precision="float32",
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        result = np.asarray(vectors, dtype=np.float32)
        expected_shape = (len(texts), EXPECTED_EMBEDDING_DIMENSION)
        if result.shape != expected_shape:
            raise ValueError(f"Encoder returned {result.shape}; expected {expected_shape}")
        if not np.isfinite(result).all():
            raise ValueError("Encoder returned non-finite values")
        norms = np.linalg.norm(result, axis=1)
        if not np.allclose(norms, 1.0, atol=1e-5):
            raise ValueError("Encoder did not return normalized embeddings")
        return result
