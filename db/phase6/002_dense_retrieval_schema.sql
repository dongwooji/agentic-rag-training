BEGIN;

CREATE SCHEMA IF NOT EXISTS retrieval;

CREATE TABLE IF NOT EXISTS retrieval.embedding_runs (
    embedding_run_id text PRIMARY KEY,
    baseline_version text NOT NULL,
    corpus_version text NOT NULL
        REFERENCES literature.corpus_runs(corpus_version),
    corpus_chunks_sha256 char(64) NOT NULL,
    model_id text NOT NULL,
    model_revision text NOT NULL,
    embedding_dimension integer NOT NULL CHECK (embedding_dimension = 384),
    normalize_embeddings boolean NOT NULL CHECK (normalize_embeddings),
    precision text NOT NULL CHECK (precision = 'float32'),
    similarity_metric text NOT NULL CHECK (similarity_metric = 'cosine'),
    search_mode text NOT NULL CHECK (search_mode = 'exact'),
    expected_chunk_count integer NOT NULL CHECK (expected_chunk_count > 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT retrieval_corpus_hash_format
        CHECK (corpus_chunks_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS retrieval.chunk_embeddings (
    embedding_run_id text NOT NULL
        REFERENCES retrieval.embedding_runs(embedding_run_id) ON DELETE RESTRICT,
    chunk_id text NOT NULL
        REFERENCES literature.chunks(chunk_id) ON DELETE RESTRICT,
    text_sha256 char(64) NOT NULL,
    embedding vector(384) NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (embedding_run_id, chunk_id),
    CONSTRAINT retrieval_text_hash_format
        CHECK (text_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_run
    ON retrieval.chunk_embeddings (embedding_run_id);

COMMENT ON TABLE retrieval.embedding_runs IS
    'Immutable metadata for a corpus embedding run used by a retrieval baseline.';
COMMENT ON TABLE retrieval.chunk_embeddings IS
    'Phase 6 normalized 384-dimensional chunk vectors. Exact cosine search only; no ANN index.';

INSERT INTO training.schema_migrations (migration_id)
VALUES ('phase6_002_dense_retrieval_schema')
ON CONFLICT (migration_id) DO NOTHING;

COMMIT;

