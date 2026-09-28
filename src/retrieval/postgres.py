"""PostgreSQL/pgvector persistence and exact cosine search for Phase 6."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from time import perf_counter
from typing import Any, Iterable, Sequence

import numpy as np

from src.database.config import DatabaseConfig

from .dense import EncoderMetadata, EXPECTED_EMBEDDING_DIMENSION


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float


@dataclass(frozen=True)
class SearchResponse:
    hits: list[SearchHit]
    database_search_ms: float


def vector_literal(vector: Sequence[float] | np.ndarray) -> str:
    """Serialize a validated vector for an explicit PostgreSQL ``::vector`` cast."""

    values = np.asarray(vector, dtype=np.float32)
    if values.shape != (EXPECTED_EMBEDDING_DIMENSION,):
        raise ValueError(
            f"Expected a {EXPECTED_EMBEDDING_DIMENSION}-dimensional vector, "
            f"got {values.shape}"
        )
    if not np.isfinite(values).all():
        raise ValueError("Vector contains non-finite values")
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


class PgVectorStore:
    """Small psycopg wrapper whose ranking path has no access to gold labels."""

    def __init__(self, *, config: DatabaseConfig, password: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Phase 6 requires psycopg. Install requirements.txt first."
            ) from exc

        self._psycopg = psycopg
        self._connection = psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=password,
            autocommit=False,
        )

    def __enter__(self) -> "PgVectorStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is not None:
            self._connection.rollback()
        self.close()

    def close(self) -> None:
        """Close the connection; safe to call more than once."""

        if not self._connection.closed:
            self._connection.close()

    def validate_prerequisites(
        self,
        *,
        corpus_version: str,
        corpus_chunks_sha256: str,
        expected_chunk_count: int,
    ) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            if cursor.fetchone() is None:
                raise RuntimeError(
                    "The pgvector extension is not enabled in the project database. "
                    "Run scripts/setup_dense_retrieval.py first."
                )
            cursor.execute(
                """
                SELECT chunks_file_sha256, chunk_count
                FROM literature.corpus_runs
                WHERE corpus_version = %s
                """,
                (corpus_version,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(
                    f"Frozen corpus {corpus_version!r} is not loaded in PostgreSQL"
                )
            if str(row[0]) != corpus_chunks_sha256 or int(row[1]) != expected_chunk_count:
                raise RuntimeError(
                    "PostgreSQL corpus provenance does not match the frozen filesystem corpus"
                )
            cursor.execute(
                "SELECT count(*) FROM literature.chunks WHERE corpus_version = %s",
                (corpus_version,),
            )
            actual_count = int(cursor.fetchone()[0])
            if actual_count != expected_chunk_count:
                raise RuntimeError(
                    f"PostgreSQL has {actual_count} chunks; expected {expected_chunk_count}"
                )
        self._connection.rollback()

    def ensure_embedding_run(
        self,
        *,
        embedding_run_id: str,
        baseline_version: str,
        corpus_version: str,
        corpus_chunks_sha256: str,
        expected_chunk_count: int,
        encoder: EncoderMetadata,
    ) -> None:
        expected = {
            "embedding_run_id": embedding_run_id,
            "baseline_version": baseline_version,
            "corpus_version": corpus_version,
            "corpus_chunks_sha256": corpus_chunks_sha256,
            "model_id": encoder.model_id,
            "model_revision": encoder.model_revision,
            "embedding_dimension": encoder.embedding_dimension,
            "normalize_embeddings": encoder.normalize_embeddings,
            "precision": encoder.precision,
            "similarity_metric": "cosine",
            "search_mode": "exact",
            "expected_chunk_count": expected_chunk_count,
        }
        columns = tuple(expected)
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {', '.join(columns)} FROM retrieval.embedding_runs "
                    "WHERE embedding_run_id = %s",
                    (embedding_run_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    placeholders = ", ".join(["%s"] * len(columns))
                    cursor.execute(
                        f"INSERT INTO retrieval.embedding_runs "
                        f"({', '.join(columns)}) VALUES ({placeholders})",
                        tuple(expected[column] for column in columns),
                    )
                else:
                    actual = dict(zip(columns, row, strict=True))
                    actual["corpus_chunks_sha256"] = str(
                        actual["corpus_chunks_sha256"]
                    )
                    if actual != expected:
                        differences = sorted(
                            key for key in expected if actual.get(key) != expected[key]
                        )
                        raise RuntimeError(
                            "Existing embedding run metadata differs for: "
                            + ", ".join(differences)
                        )

    def validate_embedding_run(
        self,
        *,
        embedding_run_id: str,
        corpus_version: str,
        corpus_chunks_sha256: str,
        expected_chunk_count: int,
        encoder: EncoderMetadata,
    ) -> None:
        """Require the existing Phase 6 embedding run without mutating it."""

        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT corpus_version, corpus_chunks_sha256, model_id,
                       model_revision, embedding_dimension, normalize_embeddings,
                       precision, similarity_metric, search_mode,
                       expected_chunk_count
                FROM retrieval.embedding_runs
                WHERE embedding_run_id = %s
                """,
                (embedding_run_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"Embedding run {embedding_run_id!r} is not loaded")
            expected = (
                corpus_version,
                corpus_chunks_sha256,
                encoder.model_id,
                encoder.model_revision,
                encoder.embedding_dimension,
                True,
                "float32",
                "cosine",
                "exact",
                expected_chunk_count,
            )
            actual = tuple(row)
            if actual != expected:
                raise RuntimeError("Stored embedding run differs from frozen Dense configuration")
            cursor.execute(
                """
                SELECT count(*)
                FROM retrieval.chunk_embeddings
                WHERE embedding_run_id = %s
                """,
                (embedding_run_id,),
            )
            count = int(cursor.fetchone()[0])
            if count != expected_chunk_count:
                raise RuntimeError(
                    f"Embedding run has {count} chunks; expected {expected_chunk_count}"
                )
        self._connection.rollback()

    def store_embeddings(
        self,
        *,
        embedding_run_id: str,
        chunks: Sequence[dict[str, Any]],
        embeddings: np.ndarray,
    ) -> int:
        if embeddings.shape != (len(chunks), EXPECTED_EMBEDDING_DIMENSION):
            raise ValueError("Chunk count and embedding matrix shape do not match")
        if len({str(chunk["chunk_id"]) for chunk in chunks}) != len(chunks):
            raise ValueError("Chunk IDs must be unique")

        rows = [
            (
                embedding_run_id,
                str(chunk["chunk_id"]),
                str(chunk["text_sha256"]),
                vector_literal(embeddings[index]),
            )
            for index, chunk in enumerate(chunks)
        ]
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO retrieval.chunk_embeddings (
                        embedding_run_id, chunk_id, text_sha256, embedding
                    )
                    VALUES (%s, %s, %s, %s::vector)
                    ON CONFLICT (embedding_run_id, chunk_id) DO NOTHING
                    """,
                    rows,
                )
                cursor.execute(
                    """
                    SELECT ce.chunk_id, ce.text_sha256
                    FROM retrieval.chunk_embeddings AS ce
                    WHERE ce.embedding_run_id = %s
                    ORDER BY ce.chunk_id
                    """,
                    (embedding_run_id,),
                )
                stored = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
                expected = {
                    str(chunk["chunk_id"]): str(chunk["text_sha256"])
                    for chunk in chunks
                }
                if stored != expected:
                    raise RuntimeError(
                        "Stored embeddings do not exactly match the frozen chunk IDs/hashes"
                    )
        return len(stored)

    def search_exact_cosine(
        self,
        query_embedding: Sequence[float] | np.ndarray,
        *,
        embedding_run_id: str,
        top_k: int,
    ) -> SearchResponse:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        serialized = vector_literal(query_embedding)
        started = perf_counter()
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                WITH query_vector AS (
                    SELECT %s::vector AS embedding
                )
                SELECT
                    stored.chunk_id,
                    1.0 - (stored.embedding <=> query_vector.embedding) AS score
                FROM retrieval.chunk_embeddings AS stored
                CROSS JOIN query_vector
                WHERE stored.embedding_run_id = %s
                ORDER BY
                    stored.embedding <=> query_vector.embedding,
                    stored.chunk_id
                LIMIT %s
                """,
                (serialized, embedding_run_id, top_k),
            )
            rows = cursor.fetchall()
        elapsed_ms = (perf_counter() - started) * 1000.0
        if len(rows) != top_k:
            raise RuntimeError(
                f"Exact search returned {len(rows)} rows; expected {top_k}"
            )
        hits = [SearchHit(chunk_id=str(row[0]), score=float(row[1])) for row in rows]
        if any(not math.isfinite(hit.score) for hit in hits):
            raise RuntimeError("Database returned a non-finite similarity score")
        return SearchResponse(hits=hits, database_search_ms=elapsed_ms)

    def server_metadata(self) -> dict[str, str]:
        with self._connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            postgresql_version = str(cursor.fetchone()[0])
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cursor.fetchone()
        self._connection.rollback()
        return {
            "postgresql_version": postgresql_version,
            "pgvector_version": str(row[0]) if row else "not-installed",
            "driver": "psycopg",
            "driver_version": self._psycopg.__version__,
        }
