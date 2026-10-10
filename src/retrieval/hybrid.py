"""Production-facing frozen Phase 7 Dense+BM25+RRF retrieval runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Protocol, Sequence

from .bm25 import BM25Index
from .rrf import reciprocal_rank_fusion
from .runtime_config import DEFAULT_RETRIEVAL_CONFIG, RetrievalConfig


HYBRID_VERSION = DEFAULT_RETRIEVAL_CONFIG.retrieval_version
SOURCE_DEPTH = DEFAULT_RETRIEVAL_CONFIG.rrf.top_k
# Retained import aliases; hash values are defined only in the versioned config.
FROZEN_CORPUS_CHUNKS_SHA256 = DEFAULT_RETRIEVAL_CONFIG.validation.corpus_chunks_sha256
FROZEN_CORPUS_MANIFEST_SHA256 = DEFAULT_RETRIEVAL_CONFIG.validation.corpus_manifest_sha256
FROZEN_PHASE7_MANIFEST_SHA256 = DEFAULT_RETRIEVAL_CONFIG.validation.hybrid_manifest_sha256


class DenseEncoder(Protocol):
    def encode(self, texts: Sequence[str], *, show_progress: bool = False) -> Any: ...


class DenseVectorStore(Protocol):
    def search_exact_cosine(
        self, query_embedding: Any, *, embedding_run_id: str, top_k: int
    ) -> Any: ...


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenLiteratureAssets:
    project_root: Path
    chunks: list[dict[str, Any]]
    chunks_by_id: Mapping[str, dict[str, Any]]
    corpus_version: str
    corpus_chunks_sha256: str
    corpus_manifest_sha256: str
    phase7_manifest_sha256: str
    phase7_reproducibility_sha256: str
    phase7_artifact_hashes: Mapping[str, str]
    bm25_metadata: Mapping[str, Any]
    phase7_reproducibility: Mapping[str, Any]
    retrieval_config: RetrievalConfig | None = None


@dataclass(frozen=True)
class HybridRuntimeHit:
    chunk_id: str
    rank: int
    rrf_score: float
    dense_rank: int | None
    dense_score: float | None
    bm25_rank: int | None
    bm25_score: float | None
    dense_rrf_contribution: float
    bm25_rrf_contribution: float


@dataclass(frozen=True)
class HybridRuntimeResponse:
    hits: list[HybridRuntimeHit]
    latency_ms: Mapping[str, float]
    candidate_count: int


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def load_frozen_literature_assets(
    project_root: str | Path,
    *,
    config: RetrievalConfig | None = None,
) -> FrozenLiteratureAssets:
    """Load corpus/config only; evaluation questions and Gold are never opened."""

    root = Path(project_root).resolve()
    settings = config or DEFAULT_RETRIEVAL_CONFIG
    chunks_path = root / "data/literature/processed/chunks.jsonl"
    corpus_manifest_path = root / "data/literature/manifests/corpus_v1.json"
    phase7_dir = root / "reports/baselines/hybrid_baseline_v1"
    phase7_manifest_path = phase7_dir / "manifest.json"
    reproduction_path = phase7_dir / "reproducibility.json"
    bm25_metadata_path = phase7_dir / "bm25_index_metadata.json"
    required = (
        chunks_path,
        corpus_manifest_path,
        phase7_manifest_path,
        reproduction_path,
        bm25_metadata_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing frozen literature assets: " + ", ".join(missing))

    chunks_hash = sha256_file(chunks_path)
    corpus_manifest_hash = sha256_file(corpus_manifest_path)
    phase7_manifest_hash = sha256_file(phase7_manifest_path)
    if chunks_hash != settings.validation.corpus_chunks_sha256:
        raise RuntimeError("Frozen literature_corpus_v1 chunks hash changed")
    if corpus_manifest_hash != settings.validation.corpus_manifest_sha256:
        raise RuntimeError("Frozen literature_corpus_v1 manifest hash changed")
    if phase7_manifest_hash != settings.validation.hybrid_manifest_sha256:
        raise RuntimeError("Frozen hybrid_baseline_v1 manifest hash changed")

    corpus_manifest = _read_json(corpus_manifest_path)
    phase7_manifest = _read_json(phase7_manifest_path)
    reproduction = _read_json(reproduction_path)
    bm25_metadata = _read_json(bm25_metadata_path)
    chunks = _read_jsonl(chunks_path)
    artifact_hashes: dict[str, str] = {}
    for artifact in phase7_manifest.get("artifacts", []):
        artifact_path = phase7_dir / str(artifact["path"])
        if artifact_path.resolve().parent != phase7_dir.resolve():
            raise RuntimeError('Frozen artifact path must stay within baseline directory')
        actual = sha256_file(artifact_path)
        if actual != artifact.get("sha256"):
            raise RuntimeError(
                f"Frozen hybrid_baseline_v1 artifact hash changed: {artifact_path.name}"
            )
        artifact_hashes[artifact_path.name] = actual

    checks = {
        "phase7_status": phase7_manifest.get("status") == "frozen",
        "phase7_version": phase7_manifest.get("phase7_version") == settings.retrieval_version,
        "configuration_status": phase7_manifest.get("configuration_status")
        == "untuned_first_configuration",
        "reproduction_version": reproduction.get("phase7_version") == settings.retrieval_version,
        "reproduction_corpus": reproduction.get("inputs", {}).get(
            "corpus_chunks_sha256"
        )
        == chunks_hash,
        "corpus_version": corpus_manifest.get("corpus_version") == settings.corpus_version,
        "corpus_count": corpus_manifest.get("chunk_count") == len(chunks) == settings.validation.expected_chunk_count,
        "chunk_versions": all(
            chunk.get("corpus_version") == settings.corpus_version for chunk in chunks
        ),
        "chunk_ids": len({chunk.get("chunk_id") for chunk in chunks}) == len(chunks),
        "bm25_k1": reproduction.get("bm25", {}).get("k1") == settings.bm25.k1,
        "bm25_b": reproduction.get("bm25", {}).get("b") == settings.bm25.b,
        "tokenizer": reproduction.get("bm25", {}).get("tokenizer_version")
        == settings.bm25.tokenizer_version,
        "rrf_k": reproduction.get("rrf", {}).get("rrf_k") == settings.rrf.k,
        "rrf_weights": reproduction.get("rrf", {}).get("weights")
        == {"dense": settings.rrf.dense_weight, "bm25": settings.rrf.bm25_weight},
        "source_depth": reproduction.get("rrf", {}).get("source_depth")
        == ({"dense": settings.dense.source_depth, "bm25": settings.bm25.source_depth}
            if settings.schema_version == 1 else
            {"dense": DEFAULT_RETRIEVAL_CONFIG.dense.source_depth,
             "bm25": DEFAULT_RETRIEVAL_CONFIG.bm25.source_depth}),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError("Frozen Phase 7 retrieval contract changed: " + ", ".join(failed))

    by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
    return FrozenLiteratureAssets(
        project_root=root,
        chunks=chunks,
        chunks_by_id=by_id,
        corpus_version=settings.corpus_version,
        corpus_chunks_sha256=chunks_hash,
        corpus_manifest_sha256=corpus_manifest_hash,
        phase7_manifest_sha256=phase7_manifest_hash,
        phase7_reproducibility_sha256=sha256_file(reproduction_path),
        phase7_artifact_hashes=artifact_hashes,
        bm25_metadata=bm25_metadata,
        phase7_reproducibility=reproduction,
        retrieval_config=settings,
    )


class FrozenHybridRetriever:
    """Exactly the Phase 7 first configuration, without evaluation/Gold access."""

    def __init__(
        self,
        *,
        assets: FrozenLiteratureAssets,
        encoder: DenseEncoder,
        vector_store: DenseVectorStore,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.assets = assets
        self.config = config or assets.retrieval_config or DEFAULT_RETRIEVAL_CONFIG
        if assets.retrieval_config is not None and self.config != assets.retrieval_config:
            raise RuntimeError('Retriever config differs from validated assets')
        self._encoder = encoder
        self._vector_store = vector_store
        if self.config.dense.unit == 'child' and getattr(vector_store, 'search_unit', None) != 'child':
            raise RuntimeError('Child retrieval requires a validated parent-mapping store')
        encoder_metadata = getattr(encoder, 'metadata', None)
        if encoder_metadata is not None:
            for field in ('model_id', 'model_revision', 'embedding_dimension', 'max_sequence_length', 'normalize_embeddings', 'precision', 'batch_size'):
                if getattr(encoder_metadata, field) != getattr(self.config.dense, field):
                    raise RuntimeError(f'Encoder differs from retrieval config: {field}')
        self._bm25 = BM25Index(assets.chunks, k1=self.config.bm25.k1, b=self.config.bm25.b)
        for field in (
            "implementation",
            "k1",
            "b",
            "tokenizer_version",
            "document_count",
            "document_order_sha256",
            "tokenized_corpus_sha256",
        ):
            if self._bm25.metadata.get(field) != assets.bm25_metadata.get(field):
                raise RuntimeError(f"Runtime BM25 differs from frozen Phase 7: {field}")

    def search(self, query: str, *, top_k: int = SOURCE_DEPTH, bm25_query: str | None = None) -> HybridRuntimeResponse:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty text")
        if bm25_query is not None and (not isinstance(bm25_query, str) or not bm25_query.strip()):
            raise ValueError("bm25_query must be non-empty text")
        if not isinstance(top_k, int) or not 1 <= top_k <= self.config.rrf.top_k:
            raise ValueError(f"top_k must be between 1 and {self.config.rrf.top_k}")
        total_started = perf_counter()
        embedding_started = perf_counter()
        query_vector = self._encoder.encode([query], show_progress=False)[0]
        embedding_ms = (perf_counter() - embedding_started) * 1000.0
        dense = self._vector_store.search_exact_cosine(
            query_vector,
            embedding_run_id=self.config.embedding_run_id,
            top_k=self.config.dense.source_depth,
        )
        bm25 = self._bm25.search(
            bm25_query if bm25_query is not None else query, top_k=self.config.bm25.source_depth,
            score_policy=self.config.bm25.score_policy,
        )
        dense_ids = [str(hit.chunk_id) for hit in dense.hits]
        bm25_ids = [str(hit.chunk_id) for hit in bm25.hits]
        fused = reciprocal_rank_fusion(
            dense_ids,
            bm25_ids,
            rrf_k=self.config.rrf.k,
            top_k=self.config.rrf.top_k,
        )
        dense_scores = {str(hit.chunk_id): float(hit.score) for hit in dense.hits}
        bm25_scores = {str(hit.chunk_id): float(hit.score) for hit in bm25.hits}
        hits = [
            HybridRuntimeHit(
                chunk_id=hit.chunk_id,
                rank=rank,
                rrf_score=hit.score,
                dense_rank=hit.dense_rank,
                dense_score=dense_scores.get(hit.chunk_id),
                bm25_rank=hit.bm25_rank,
                bm25_score=bm25_scores.get(hit.chunk_id),
                dense_rrf_contribution=hit.dense_contribution,
                bm25_rrf_contribution=hit.bm25_contribution,
            )
            for rank, hit in enumerate(fused.hits[:top_k], 1)
        ]
        return HybridRuntimeResponse(
            hits=hits,
            latency_ms={
                "query_embedding": embedding_ms,
                "dense_database_search": float(dense.database_search_ms),
                "bm25_search": bm25.latency_ms,
                "rrf_fusion": fused.latency_ms,
                "end_to_end": (perf_counter() - total_started) * 1000.0,
            },
            candidate_count=fused.candidate_count,
        )
