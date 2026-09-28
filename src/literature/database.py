"""Transactional PostgreSQL loading for the validated literature corpus."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.database.config import DatabaseConfig
from src.database.loader import apply_migrations
from src.database.psql import run_psql, sql_literal

from .corpus import sha256_file


@dataclass(frozen=True)
class LiteratureLoadInputs:
    project_root: Path
    papers_path: Path
    chunks_path: Path
    manifest_path: Path
    validation: dict[str, Any]
    manifest: dict[str, Any]


def load_literature_inputs(project_root: str | Path) -> LiteratureLoadInputs:
    root = Path(project_root).resolve()
    validation = json.loads(
        (root / "reports" / "literature_corpus_validation.json").read_text(
            encoding="utf-8"
        )
    )
    if not validation.get("all_checks_passed"):
        raise ValueError("Literature corpus validation did not pass")
    files = validation["output_files"]
    papers_path = root / files["papers"]["path"]
    chunks_path = root / files["chunks"]["path"]
    manifest_path = root / files["manifest"]["path"]
    for label, path in (
        ("papers", papers_path),
        ("chunks", chunks_path),
        ("manifest", manifest_path),
    ):
        if sha256_file(path) != files[label]["sha256"]:
            raise ValueError(f"{label} artifact SHA-256 does not match validation metadata")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["paper_count"] != validation["paper_count"]:
        raise ValueError("Paper count differs between manifest and validation")
    if manifest["chunk_count"] != validation["chunk_count"]:
        raise ValueError("Chunk count differs between manifest and validation")
    return LiteratureLoadInputs(
        project_root=root,
        papers_path=papers_path,
        chunks_path=chunks_path,
        manifest_path=manifest_path,
        validation=validation,
        manifest=manifest,
    )


def _copy_jsonl(table: str, path: Path) -> str:
    payload = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if any(line == r"\." for line in payload.splitlines()):
        raise ValueError(f"JSONL contains a psql end-of-data marker: {path}")
    return (
        f"COPY {table} (payload) FROM STDIN WITH "
        "(FORMAT CSV, DELIMITER E'\\x1f', QUOTE E'\\x1e', ESCAPE E'\\x1e');\n"
        f"{payload.rstrip(chr(10))}\n"
        "\\."
    )


def build_literature_load_sql(
    inputs: LiteratureLoadInputs, *, replace: bool = False
) -> str:
    validation = inputs.validation
    outputs = validation["output_files"]
    if replace:
        guard = """
TRUNCATE TABLE
    literature.chunks,
    literature.papers,
    literature.corpus_runs;
"""
    else:
        guard = """
DO $load_guard$
BEGIN
    IF EXISTS (SELECT 1 FROM literature.corpus_runs) THEN
        RAISE EXCEPTION
            'literature schema already contains data; rerun with --replace for a full reload';
    END IF;
END
$load_guard$;
"""

    papers_copy = _copy_jsonl("staging_literature_papers", inputs.papers_path)
    chunks_copy = _copy_jsonl("staging_literature_chunks", inputs.chunks_path)
    return f"""\
BEGIN;
{guard}
CREATE TEMP TABLE staging_literature_papers (payload jsonb) ON COMMIT DROP;
CREATE TEMP TABLE staging_literature_chunks (payload jsonb) ON COMMIT DROP;

{papers_copy}
{chunks_copy}

INSERT INTO literature.corpus_runs (
    corpus_version,
    selection_date,
    paper_count,
    chunk_count,
    papers_file_sha256,
    chunks_file_sha256,
    manifest_file_sha256
)
VALUES (
    {sql_literal(str(inputs.manifest['corpus_version']))},
    {sql_literal(str(inputs.manifest['selection_date']))}::date,
    {int(validation['paper_count'])},
    {int(validation['chunk_count'])},
    {sql_literal(str(outputs['papers']['sha256']))},
    {sql_literal(str(outputs['chunks']['sha256']))},
    {sql_literal(str(outputs['manifest']['sha256']))}
);

INSERT INTO literature.papers (
    paper_id,
    pmid,
    pmcid,
    doi,
    title,
    authors,
    publication_year,
    journal,
    study_type,
    population,
    topics,
    selection_reason,
    source_url,
    source_format,
    license,
    license_url,
    source_sha256,
    retrieved_at,
    corpus_version
)
SELECT
    payload->>'paper_id',
    payload->>'pmid',
    payload->>'pmcid',
    payload->>'doi',
    payload->>'title',
    payload->'authors',
    (payload->>'year')::integer,
    payload->>'journal',
    payload->>'study_type',
    payload->>'population',
    ARRAY(SELECT jsonb_array_elements_text(payload->'topics')),
    payload->>'selection_reason',
    payload->>'source_url',
    payload->>'source_format',
    payload->>'license',
    NULLIF(payload->>'license_url', ''),
    payload->>'source_sha256',
    (payload->>'retrieved_at')::date,
    payload->>'corpus_version'
FROM staging_literature_papers;

INSERT INTO literature.chunks (
    chunk_id,
    paper_id,
    section,
    paragraph_start,
    paragraph_end,
    word_count,
    text,
    text_sha256,
    source_sha256,
    corpus_version
)
SELECT
    payload->>'chunk_id',
    payload->>'paper_id',
    payload->>'section',
    (payload->>'paragraph_start')::integer,
    (payload->>'paragraph_end')::integer,
    (payload->>'word_count')::integer,
    payload->>'text',
    payload->>'text_sha256',
    payload->>'source_sha256',
    payload->>'corpus_version'
FROM staging_literature_chunks;

DO $validate$
DECLARE
    actual bigint;
BEGIN
    SELECT count(*) INTO actual FROM literature.papers;
    IF actual <> {int(validation['paper_count'])} THEN
        RAISE EXCEPTION 'literature paper count mismatch: expected %, got %', {int(validation['paper_count'])}, actual;
    END IF;

    SELECT count(*) INTO actual FROM literature.chunks;
    IF actual <> {int(validation['chunk_count'])} THEN
        RAISE EXCEPTION 'literature chunk count mismatch: expected %, got %', {int(validation['chunk_count'])}, actual;
    END IF;

    SELECT count(*) INTO actual
    FROM (
        SELECT p.paper_id
        FROM literature.papers AS p
        LEFT JOIN literature.chunks AS c USING (paper_id)
        GROUP BY p.paper_id
        HAVING count(c.chunk_id) = 0
    ) AS papers_without_chunks;
    IF actual <> 0 THEN
        RAISE EXCEPTION 'literature papers without chunks: %', actual;
    END IF;

    SELECT count(*) INTO actual
    FROM literature.chunks AS c
    JOIN literature.papers AS p USING (paper_id)
    WHERE c.source_sha256 <> p.source_sha256
       OR c.corpus_version <> p.corpus_version;
    IF actual <> 0 THEN
        RAISE EXCEPTION 'literature chunk provenance mismatch: %', actual;
    END IF;
END
$validate$;

COMMIT;

SELECT json_build_object(
    'corpus_version', (SELECT corpus_version FROM literature.corpus_runs),
    'papers', (SELECT count(*) FROM literature.papers),
    'chunks', (SELECT count(*) FROM literature.chunks),
    'topics', (SELECT count(DISTINCT topic) FROM literature.papers, unnest(topics) AS topic)
) AS phase4_load_summary;
"""


def load_literature_database(
    *,
    project_root: str | Path,
    config: DatabaseConfig,
    password: str | None,
    psql_path: str | Path | None = None,
    replace: bool = False,
) -> str:
    inputs = load_literature_inputs(project_root)
    apply_migrations(
        project_root=inputs.project_root,
        config=config,
        password=password,
        psql_path=psql_path,
    )
    result = run_psql(
        config=config,
        sql=build_literature_load_sql(inputs, replace=replace),
        password=password,
        psql_path=psql_path,
    )
    return result.stdout
