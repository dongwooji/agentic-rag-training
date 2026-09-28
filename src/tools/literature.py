"""Typed Literature Tool over the frozen Phase 7 hybrid retrieval runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from src.database.config import DatabaseConfig
from src.retrieval.baseline import EMBEDDING_RUN_ID
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.hybrid import (
    FrozenHybridRetriever,
    FrozenLiteratureAssets,
    HYBRID_VERSION,
    SOURCE_DEPTH,
    load_frozen_literature_assets,
)
from src.retrieval.postgres import PgVectorStore

from .contracts import (
    ToolError,
    ToolErrorCode,
    ToolResponse,
    failure_response,
    success_response,
)


TOOL_VERSION = "literature_tool_v1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LiteratureOperation(str, Enum):
    SEARCH = "search"


@dataclass(frozen=True)
class LiteratureInput:
    operation: LiteratureOperation | str
    query: str
    top_k: int = 10


class HybridRetriever(Protocol):
    def search(self, query: str, *, top_k: int) -> Any: ...


class LiteratureTool:
    def __init__(
        self,
        *,
        assets: FrozenLiteratureAssets,
        retriever: HybridRetriever,
        close_callback: Any | None = None,
    ) -> None:
        self._assets = assets
        self._retriever = retriever
        self._close_callback = close_callback

    @classmethod
    def from_postgres(
        cls,
        *,
        password: str,
        config: DatabaseConfig | None = None,
        cache_dir: str | Path | None = None,
        project_root: str | Path = PROJECT_ROOT,
    ) -> "LiteratureTool":
        """Construct the pinned model + existing pgvector embedding runtime."""

        assets = load_frozen_literature_assets(project_root)
        encoder = MiniLMEncoder(cache_dir=cache_dir, device="cpu")
        store = PgVectorStore(
            config=config or DatabaseConfig.from_environment(), password=password
        )
        store.validate_prerequisites(
            corpus_version=assets.corpus_version,
            corpus_chunks_sha256=assets.corpus_chunks_sha256,
            expected_chunk_count=len(assets.chunks),
        )
        store.validate_embedding_run(
            embedding_run_id=EMBEDDING_RUN_ID,
            corpus_version=assets.corpus_version,
            corpus_chunks_sha256=assets.corpus_chunks_sha256,
            expected_chunk_count=len(assets.chunks),
            encoder=encoder.metadata,
        )
        retriever = FrozenHybridRetriever(
            assets=assets, encoder=encoder, vector_store=store
        )
        return cls(assets=assets, retriever=retriever, close_callback=store.close)

    @property
    def provenance(self) -> dict[str, Any]:
        reproduction = self._assets.phase7_reproducibility
        return {
            "tool": TOOL_VERSION,
            "retrieval_version": HYBRID_VERSION,
            "corpus_version": self._assets.corpus_version,
            "corpus_chunks_sha256": self._assets.corpus_chunks_sha256,
            "corpus_manifest_sha256": self._assets.corpus_manifest_sha256,
            "phase7_manifest_sha256": self._assets.phase7_manifest_sha256,
            "phase7_reproducibility_sha256": (
                self._assets.phase7_reproducibility_sha256
            ),
            "dense": {
                "embedding_run_id": EMBEDDING_RUN_ID,
                "similarity": "cosine_exact",
                "source_depth": SOURCE_DEPTH,
            },
            "bm25": {
                "k1": reproduction["bm25"]["k1"],
                "b": reproduction["bm25"]["b"],
                "tokenizer_version": reproduction["bm25"]["tokenizer_version"],
                "source_depth": SOURCE_DEPTH,
            },
            "rrf": {
                "k": reproduction["rrf"]["rrf_k"],
                "weights": reproduction["rrf"]["weights"],
            },
        }

    def close(self) -> None:
        if self._close_callback is not None:
            callback, self._close_callback = self._close_callback, None
            callback()

    def __enter__(self) -> "LiteratureTool":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def execute(self, request: LiteratureInput) -> ToolResponse[Any]:
        raw_operation = (
            request.operation.value
            if isinstance(request.operation, LiteratureOperation)
            else str(request.operation)
        )
        try:
            operation = LiteratureOperation(request.operation)
        except (TypeError, ValueError):
            return failure_response(
                operation=raw_operation,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    "Unsupported Literature operation",
                    {"supported": [LiteratureOperation.SEARCH.value]},
                ),
                provenance=self.provenance,
            )
        if not isinstance(request.query, str) or not request.query.strip():
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT, "query must be non-empty text"
                ),
                provenance=self.provenance,
            )
        if not isinstance(request.top_k, int) or not 1 <= request.top_k <= SOURCE_DEPTH:
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.INVALID_INPUT,
                    f"top_k must be between 1 and {SOURCE_DEPTH}",
                ),
                provenance=self.provenance,
            )
        try:
            response = self._retriever.search(request.query, top_k=request.top_k)
            hits = []
            for hit in response.hits:
                chunk = self._assets.chunks_by_id.get(hit.chunk_id)
                if chunk is None:
                    raise RuntimeError(
                        f"Retrieved chunk is outside frozen corpus: {hit.chunk_id}"
                    )
                hits.append(
                    {
                        "chunk_id": hit.chunk_id,
                        "paper_id": chunk["paper_id"],
                        "pmcid": chunk.get("pmcid"),
                        "pmid": chunk.get("pmid"),
                        "doi": chunk.get("doi"),
                        "title": chunk["title"],
                        "section": chunk["section"],
                        "text": chunk["text"],
                        "rank": hit.rank,
                        "rrf_score": hit.rrf_score,
                        "dense_rank": hit.dense_rank,
                        "dense_score": hit.dense_score,
                        "bm25_rank": hit.bm25_rank,
                        "bm25_score": hit.bm25_score,
                        "dense_rrf_contribution": hit.dense_rrf_contribution,
                        "bm25_rrf_contribution": hit.bm25_rrf_contribution,
                        "text_sha256": chunk["text_sha256"],
                        "source_sha256": chunk["source_sha256"],
                        "corpus_version": chunk["corpus_version"],
                    }
                )
            return success_response(
                operation=operation.value,
                result={
                    "query": request.query,
                    "top_k": request.top_k,
                    "candidate_count": response.candidate_count,
                    "hits": hits,
                    "latency_ms": dict(response.latency_ms),
                },
                provenance=self.provenance,
                limitations=(
                    "Results are retrieval evidence, not a generated answer or evidence-grade judgment.",
                    "The source depth is frozen at Dense Top-10 and BM25 Top-10.",
                ),
                empty=not hits,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return failure_response(
                operation=operation.value,
                error=ToolError(
                    ToolErrorCode.RETRIEVAL_ERROR,
                    "Frozen hybrid retrieval failed",
                    {"exception_type": type(exc).__name__, "message": str(exc)},
                ),
                provenance=self.provenance,
            )
