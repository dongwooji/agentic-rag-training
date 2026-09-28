"""Run and freeze the untuned Phase 6 dense retrieval baseline exactly once."""

from __future__ import annotations

import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.database.config import DatabaseConfig
from src.retrieval.baseline import (
    BASELINE_VERSION,
    EMBEDDING_RUN_ID,
    TOP_K,
    build_reproducibility_record,
    embed_frozen_corpus,
    evaluate_dense_retrieval,
    load_frozen_retrieval_inputs,
    retrieve_frozen_questions,
    write_immutable_baseline_artifacts,
)
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.postgres import PgVectorStore


def parse_args() -> argparse.Namespace:
    defaults = DatabaseConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=defaults.host)
    parser.add_argument("--port", type=int, default=defaults.port)
    parser.add_argument("--database", default=defaults.database)
    parser.add_argument("--user", default=defaults.user)
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=Path("data/models/huggingface"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/baselines/dense_baseline_v1"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = (PROJECT_ROOT / args.output).resolve()
    if output_dir.exists():
        raise FileExistsError(
            f"Immutable baseline already exists; refusing to overwrite {output_dir}"
        )

    inputs = load_frozen_retrieval_inputs(PROJECT_ROOT)
    print(
        f"Validated frozen inputs: {len(inputs.chunks)} chunks, "
        f"{len(inputs.cases)} literature-bearing cases."
    )
    password = os.environ.get("PGPASSWORD")
    if password is None:
        password = getpass(f"Password for PostgreSQL role {args.user}: ")
    config = DatabaseConfig(
        host=args.host,
        port=args.port,
        database=args.database,
        user=args.user,
    )
    cache_dir = (PROJECT_ROOT / args.model_cache).resolve()
    print("Loading pinned embedding model revision on CPU...")
    encoder = MiniLMEncoder(cache_dir=cache_dir, device="cpu")

    with PgVectorStore(config=config, password=password) as store:
        store.validate_prerequisites(
            corpus_version=inputs.corpus_version,
            corpus_chunks_sha256=inputs.corpus_chunks_sha256,
            expected_chunk_count=len(inputs.chunks),
        )
        store.ensure_embedding_run(
            embedding_run_id=EMBEDDING_RUN_ID,
            baseline_version=BASELINE_VERSION,
            corpus_version=inputs.corpus_version,
            corpus_chunks_sha256=inputs.corpus_chunks_sha256,
            expected_chunk_count=len(inputs.chunks),
            encoder=encoder.metadata,
        )
        print("Embedding 488 frozen literature chunks...")
        corpus_embeddings, corpus_embedding_ms = embed_frozen_corpus(
            inputs.chunks, encoder
        )
        storage_started = perf_counter()
        stored_count = store.store_embeddings(
            embedding_run_id=EMBEDDING_RUN_ID,
            chunks=inputs.chunks,
            embeddings=corpus_embeddings,
        )
        corpus_storage_ms = (perf_counter() - storage_started) * 1000.0
        print(f"Validated {stored_count} pgvector embeddings. Running 18 queries...")

        # Deliberately strip every case down to ID + frozen question before ranking.
        question_records = [
            {"case_id": case["id"], "question": case["question"]}
            for case in inputs.cases
        ]
        raw_results = retrieve_frozen_questions(
            question_records,
            encoder=encoder,
            store=store,
            top_k=TOP_K,
        )
        database_metadata = store.server_metadata()

    # Gold labels first enter the computation here, after every ranking is fixed.
    metrics, results = evaluate_dense_retrieval(inputs.cases, raw_results)
    inputs.assert_unchanged()
    reproducibility = build_reproducibility_record(
        inputs=inputs,
        encoder=encoder.metadata,
        database=database_metadata,
        corpus_embedding_ms=corpus_embedding_ms,
        corpus_storage_ms=corpus_storage_ms,
    )
    manifest = write_immutable_baseline_artifacts(
        output_dir=output_dir,
        metrics=metrics,
        results=results,
        reproducibility=reproducibility,
    )
    inputs.assert_unchanged()

    primary = {
        name: value
        for name, value in metrics["macro"].items()
        if name == "mrr" or name.endswith("@5") or name.endswith("@10")
    }
    print(json.dumps(primary, ensure_ascii=False, indent=2))
    print(f"Frozen baseline report: {output_dir / 'REPORT.md'}")
    print(f"Artifact manifest contains {len(manifest['artifacts'])} hashed files.")


if __name__ == "__main__":
    main()

