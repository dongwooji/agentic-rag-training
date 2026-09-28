"""Production-facing frozen Phase 7 Dense+BM25+RRF retrieval runtime."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Protocol, Sequence

from .baseline import CORPUS_VERSION, EMBEDDING_RUN_ID, sha256_file
from .bm25 import BM25Index, DEFAULT_B, DEFAULT_K1, TOKENIZER_VERSION
from .rrf import DEFAULT_RRF_K, reciprocal_rank_fusion


HYBRID_VERSION = "hybrid_baseline_v1"
SOURCE_DEPTH = 10
FROZEN_CORPUS_CHUNKS_SHA256 = (
    "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177"
)
FROZEN_CORPUS_MANIFEST_SHA256 = (
    "84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66"
)
FROZEN_PHASE7_MANIFEST_SHA256 = (
    "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
)


class DenseEncoder(Protocol):
    def encode(self, texts: Sequence[str], *, show_progress: bool = False) -> Any: ...


class DenseVectorStore(Protocol):
    def search_exact_cosine(
        self, query_embedding: Any, *, embedding_run_id: str, top_k: int
    ) -> Any: ...


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
) -> FrozenLiteratureAssets:
    """Load corpus/config only; evaluation questions and Gold are never opened."""

    root = Path(project_root).resolve()
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
    if chunks_hash != FROZEN_CORPUS_CHUNKS_SHA256:
        raise RuntimeError("Frozen literature_corpus_v1 chunks hash changed")
    if corpus_manifest_hash != FROZEN_CORPUS_MANIFEST_SHA256:
        raise RuntimeError("Frozen literature_corpus_v1 manifest hash changed")
    if phase7_manifest_hash != FROZEN_PHASE7_MANIFEST_SHA256:
        raise RuntimeError("Frozen hybrid_baseline_v1 manifest hash changed")

    corpus_manifest = _read_json(corpus_manifest_path)
    phase7_manifest = _read_json(phase7_manifest_path)
    reproduction = _read_json(reproduction_path)
    bm25_metadata = _read_json(bm25_metadata_path)
    chunks = _read_jsonl(chunks_path)
    artifact_hashes: dict[str, str] = {}
    for artifact in phase7_manifest.get("artifacts", []):
        artifact_path = phase7_dir / str(artifact["path"])
        actual = sha256_file(artifact_path)
        if actual != artifact.get("sha256"):
            raise RuntimeError(
                f"Frozen hybrid_baseline_v1 artifact hash changed: {artifact_path.name}"
            )
        artifact_hashes[artifact_path.name] = actual

    checks = {
        "phase7_status": phase7_manifest.get("status") == "frozen",
        "phase7_version": phase7_manifest.get("phase7_version") == HYBRID_VERSION,
        "configuration_status": phase7_manifest.get("configuration_status")
        == "untuned_first_configuration",
        "reproduction_version": reproduction.get("phase7_version") == HYBRID_VERSION,
        "reproduction_corpus": reproduction.get("inputs", {}).get(
            "corpus_chunks_sha256"
        )
        == chunks_hash,
        "corpus_version": corpus_manifest.get("corpus_version") == CORPUS_VERSION,
        "corpus_count": corpus_manifest.get("chunk_count") == len(chunks) == 488,
        "chunk_versions": all(
            chunk.get("corpus_version") == CORPUS_VERSION for chunk in chunks
        ),
        "chunk_ids": len({chunk.get("chunk_id") for chunk in chunks}) == len(chunks),
        "bm25_k1": reproduction.get("bm25", {}).get("k1") == DEFAULT_K1,
        "bm25_b": reproduction.get("bm25", {}).get("b") == DEFAULT_B,
        "tokenizer": reproduction.get("bm25", {}).get("tokenizer_version")
        == TOKENIZER_VERSION,
        "rrf_k": reproduction.get("rrf", {}).get("rrf_k") == DEFAULT_RRF_K,
        "rrf_weights": reproduction.get("rrf", {}).get("weights")
        == {"dense": 1.0, "bm25": 1.0},
        "source_depth": reproduction.get("rrf", {}).get("source_depth")
        == {"dense": SOURCE_DEPTH, "bm25": SOURCE_DEPTH},
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError("Frozen Phase 7 retrieval contract changed: " + ", ".join(failed))

    by_id = {str(chunk["chunk_id"]): chunk for chunk in chunks}
    return FrozenLiteratureAssets(
        project_root=root,
        chunks=chunks,
        chunks_by_id=by_id,
        corpus_version=CORPUS_VERSION,
        corpus_chunks_sha256=chunks_hash,
        corpus_manifest_sha256=corpus_manifest_hash,
        phase7_manifest_sha256=phase7_manifest_hash,
        phase7_reproducibility_sha256=sha256_file(reproduction_path),
        phase7_artifact_hashes=artifact_hashes,
        bm25_metadata=bm25_metadata,
        phase7_reproducibility=reproduction,
    )


class FrozenHybridRetriever:
    """Exactly the Phase 7 first configuration, without evaluation/Gold access."""

    def __init__(
        self,
        *,
        assets: FrozenLiteratureAssets,
        encoder: DenseEncoder,
        vector_store: DenseVectorStore,
    ) -> None:
        self.assets = assets
        self._encoder = encoder
        self._vector_store = vector_store
        self._bm25 = BM25Index(assets.chunks, k1=DEFAULT_K1, b=DEFAULT_B)
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

    def search(self, query: str, *, top_k: int = SOURCE_DEPTH) -> HybridRuntimeResponse:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be non-empty text")
        if not isinstance(top_k, int) or not 1 <= top_k <= SOURCE_DEPTH:
            raise ValueError(f"top_k must be between 1 and {SOURCE_DEPTH}")
        total_started = perf_counter()
        embedding_started = perf_counter()
        query_vector = self._encoder.encode([query], show_progress=False)[0]
        embedding_ms = (perf_counter() - embedding_started) * 1000.0
        dense = self._vector_store.search_exact_cosine(
            query_vector,
            embedding_run_id=EMBEDDING_RUN_ID,
            top_k=SOURCE_DEPTH,
        )
        bm25 = self._bm25.search(query, top_k=SOURCE_DEPTH)
        dense_ids = [str(hit.chunk_id) for hit in dense.hits]
        bm25_ids = [str(hit.chunk_id) for hit in bm25.hits]
        fused = reciprocal_rank_fusion(
            dense_ids,
            bm25_ids,
            rrf_k=DEFAULT_RRF_K,
            top_k=SOURCE_DEPTH,
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
