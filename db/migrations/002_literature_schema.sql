BEGIN;

CREATE SCHEMA IF NOT EXISTS literature;

CREATE TABLE IF NOT EXISTS literature.corpus_runs (
    corpus_version text PRIMARY KEY,
    selection_date date NOT NULL,
    paper_count integer NOT NULL CHECK (paper_count > 0),
    chunk_count integer NOT NULL CHECK (chunk_count > 0),
    papers_file_sha256 char(64) NOT NULL,
    chunks_file_sha256 char(64) NOT NULL,
    manifest_file_sha256 char(64) NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT literature_papers_hash_format
        CHECK (papers_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT literature_chunks_hash_format
        CHECK (chunks_file_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT literature_manifest_hash_format
        CHECK (manifest_file_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS literature.papers (
    paper_id text PRIMARY KEY,
    pmid text NOT NULL UNIQUE,
    pmcid text NOT NULL UNIQUE,
    doi text NOT NULL UNIQUE,
    title text NOT NULL CHECK (btrim(title) <> ''),
    authors jsonb NOT NULL CHECK (jsonb_typeof(authors) = 'array'),
    publication_year integer NOT NULL CHECK (publication_year BETWEEN 1900 AND 2100),
    journal text NOT NULL CHECK (btrim(journal) <> ''),
    study_type text NOT NULL CHECK (btrim(study_type) <> ''),
    population text NOT NULL CHECK (btrim(population) <> ''),
    topics text[] NOT NULL CHECK (cardinality(topics) > 0),
    selection_reason text NOT NULL CHECK (btrim(selection_reason) <> ''),
    source_url text NOT NULL,
    source_format text NOT NULL CHECK (source_format = 'jats_xml'),
    license text NOT NULL CHECK (btrim(license) <> ''),
    license_url text,
    source_sha256 char(64) NOT NULL,
    retrieved_at date NOT NULL,
    corpus_version text NOT NULL
        REFERENCES literature.corpus_runs(corpus_version),
    CONSTRAINT literature_paper_hash_format
        CHECK (source_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS literature.chunks (
    chunk_id text PRIMARY KEY,
    paper_id text NOT NULL
        REFERENCES literature.papers(paper_id) ON DELETE CASCADE,
    section text NOT NULL CHECK (btrim(section) <> ''),
    paragraph_start integer NOT NULL CHECK (paragraph_start > 0),
    paragraph_end integer NOT NULL CHECK (paragraph_end >= paragraph_start),
    word_count integer NOT NULL CHECK (word_count > 0),
    text text NOT NULL CHECK (btrim(text) <> ''),
    text_sha256 char(64) NOT NULL,
    source_sha256 char(64) NOT NULL,
    corpus_version text NOT NULL
        REFERENCES literature.corpus_runs(corpus_version),
    CONSTRAINT literature_chunk_text_hash_format
        CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT literature_chunk_source_hash_format
        CHECK (source_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_literature_papers_year
    ON literature.papers (publication_year);
CREATE INDEX IF NOT EXISTS idx_literature_papers_study_type
    ON literature.papers (study_type);
CREATE INDEX IF NOT EXISTS idx_literature_papers_topics
    ON literature.papers USING gin (topics);
CREATE INDEX IF NOT EXISTS idx_literature_chunks_paper
    ON literature.chunks (paper_id);
CREATE INDEX IF NOT EXISTS idx_literature_chunks_section
    ON literature.chunks (section);

CREATE OR REPLACE VIEW literature.chunk_details AS
SELECT
    c.chunk_id,
    c.paper_id,
    p.pmid,
    p.pmcid,
    p.doi,
    p.title,
    p.authors,
    p.publication_year,
    p.journal,
    p.study_type,
    p.population,
    p.topics,
    c.section,
    c.paragraph_start,
    c.paragraph_end,
    c.word_count,
    c.text,
    p.source_url,
    p.license,
    c.corpus_version
FROM literature.chunks AS c
JOIN literature.papers AS p USING (paper_id);

COMMENT ON TABLE literature.papers IS
    'Frozen paper-level provenance for literature_corpus_v1.';
COMMENT ON TABLE literature.chunks IS
    'JATS section-aware text chunks; embeddings are intentionally deferred to Phase 6.';

INSERT INTO training.schema_migrations (migration_id)
VALUES ('002_literature_schema')
ON CONFLICT (migration_id) DO NOTHING;

COMMIT;
