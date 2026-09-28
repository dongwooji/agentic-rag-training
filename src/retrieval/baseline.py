"""Frozen inputs, evaluation, and immutable artifacts for the Phase 6 baseline."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from statistics import mean, median
import tempfile
from time import perf_counter
from typing import Any, Sequence

import numpy as np

from src.evaluation.retrieval_metrics import (
    evaluate_retrieval,
    group_is_covered,
    required_literature_groups,
)

from .dense import EncoderMetadata
from .postgres import SearchResponse


BASELINE_VERSION = "dense_baseline_v1"
EMBEDDING_RUN_ID = "dense_multilingual_minilm_l12_v2_literature_corpus_v1"
CORPUS_VERSION = "literature_corpus_v1"
EVAL_DATASET_VERSION = "eval_dataset_v1"
TOP_K = 10
PRIMARY_KS = (5, 10)
DIAGNOSTIC_KS = (1, 3)
ALL_KS = DIAGNOSTIC_KS + PRIMARY_KS
LITERATURE_CATEGORIES = frozenset({"literature_only", "hybrid"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@dataclass(frozen=True)
class FrozenRetrievalInputs:
    project_root: Path
    eval_path: Path
    eval_manifest_path: Path
    corpus_chunks_path: Path
    corpus_manifest_path: Path
    eval_dataset_version: str
    eval_dataset_sha256: str
    eval_manifest_sha256: str
    corpus_version: str
    corpus_chunks_sha256: str
    corpus_manifest_sha256: str
    chunks: list[dict[str, Any]]
    cases: list[dict[str, Any]]

    def current_hashes(self) -> dict[str, str]:
        return {
            "eval_dataset": sha256_file(self.eval_path),
            "eval_manifest": sha256_file(self.eval_manifest_path),
            "corpus_chunks": sha256_file(self.corpus_chunks_path),
            "corpus_manifest": sha256_file(self.corpus_manifest_path),
        }

    def expected_hashes(self) -> dict[str, str]:
        return {
            "eval_dataset": self.eval_dataset_sha256,
            "eval_manifest": self.eval_manifest_sha256,
            "corpus_chunks": self.corpus_chunks_sha256,
            "corpus_manifest": self.corpus_manifest_sha256,
        }

    def assert_unchanged(self) -> None:
        if self.current_hashes() != self.expected_hashes():
            raise RuntimeError("A frozen Phase 6 input changed during baseline execution")


def load_frozen_retrieval_inputs(project_root: str | Path) -> FrozenRetrievalInputs:
    root = Path(project_root).resolve()
    eval_path = root / "data/evaluation/eval_dataset_v1.json"
    eval_manifest_path = root / "data/evaluation/eval_dataset_v1.manifest.json"
    chunks_path = root / "data/literature/processed/chunks.jsonl"
    corpus_manifest_path = root / "data/literature/manifests/corpus_v1.json"
    paths = (eval_path, eval_manifest_path, chunks_path, corpus_manifest_path)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing frozen inputs: " + ", ".join(missing))

    eval_manifest = json.loads(eval_manifest_path.read_text(encoding="utf-8"))
    dataset = json.loads(eval_path.read_text(encoding="utf-8"))
    corpus_manifest = json.loads(corpus_manifest_path.read_text(encoding="utf-8"))
    chunks = _read_jsonl(chunks_path)

    eval_hash = sha256_file(eval_path)
    corpus_hash = sha256_file(chunks_path)
    expected_corpus_source = next(
        (
            item["sha256"]
            for item in eval_manifest.get("source_artifacts", [])
            if item.get("path") == "data/literature/processed/chunks.jsonl"
        ),
        None,
    )
    checks = {
        "eval_manifest_frozen": eval_manifest.get("status") == "frozen",
        "eval_dataset_frozen": dataset.get("status") == "frozen",
        "eval_version_manifest": eval_manifest.get("dataset_version")
        == EVAL_DATASET_VERSION,
        "eval_version_dataset": dataset.get("dataset_version") == EVAL_DATASET_VERSION,
        "eval_hash": eval_hash == eval_manifest.get("dataset_sha256"),
        "corpus_version_manifest": corpus_manifest.get("corpus_version")
        == CORPUS_VERSION,
        "corpus_version_dataset": dataset.get("source_versions", {}).get(
            "literature_corpus"
        )
        == CORPUS_VERSION,
        "corpus_hash": corpus_hash == expected_corpus_source,
        "corpus_chunk_count": len(chunks) == corpus_manifest.get("chunk_count") == 488,
        "corpus_chunk_version": all(
            chunk.get("corpus_version") == CORPUS_VERSION for chunk in chunks
        ),
        "corpus_chunk_ids_unique": len({chunk.get("chunk_id") for chunk in chunks})
        == len(chunks),
        "corpus_text_hashes_present": all(
            len(str(chunk.get("text_sha256", ""))) == 64 and chunk.get("text")
            for chunk in chunks
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError("Frozen Phase 6 input validation failed: " + ", ".join(failed))

    cases = [
        case for case in dataset["cases"] if case.get("category") in LITERATURE_CATEGORIES
    ]
    if len(cases) != 18:
        raise RuntimeError(f"Expected 18 literature-bearing cases, found {len(cases)}")
    for case in cases:
        groups = required_literature_groups(case)
        if not groups:
            raise RuntimeError(f"Case {case['id']} has no required literature evidence")
        if not str(case.get("question", "")).strip():
            raise RuntimeError(f"Case {case['id']} has an empty frozen question")

    inputs = FrozenRetrievalInputs(
        project_root=root,
        eval_path=eval_path,
        eval_manifest_path=eval_manifest_path,
        corpus_chunks_path=chunks_path,
        corpus_manifest_path=corpus_manifest_path,
        eval_dataset_version=EVAL_DATASET_VERSION,
        eval_dataset_sha256=eval_hash,
        eval_manifest_sha256=sha256_file(eval_manifest_path),
        corpus_version=CORPUS_VERSION,
        corpus_chunks_sha256=corpus_hash,
        corpus_manifest_sha256=sha256_file(corpus_manifest_path),
        chunks=chunks,
        cases=cases,
    )
    inputs.assert_unchanged()
    return inputs


def embed_frozen_corpus(
    chunks: Sequence[dict[str, Any]], encoder: Any
) -> tuple[np.ndarray, float]:
    """Embed only frozen corpus text; no evaluation labels enter this function."""

    started = perf_counter()
    embeddings = encoder.encode(
        [str(chunk["text"]) for chunk in chunks], show_progress=True
    )
    elapsed_ms = (perf_counter() - started) * 1000.0
    return embeddings, elapsed_ms


def retrieve_frozen_questions(
    question_records: Sequence[dict[str, str]],
    *,
    encoder: Any,
    store: Any,
    embedding_run_id: str = EMBEDDING_RUN_ID,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:
    """Retrieve from frozen question text without accepting or reading gold labels."""

    results: list[dict[str, Any]] = []
    for record in question_records:
        total_started = perf_counter()
        embedding_started = perf_counter()
        query_vector = encoder.encode([record["question"]], show_progress=False)[0]
        embedding_ms = (perf_counter() - embedding_started) * 1000.0
        response: SearchResponse = store.search_exact_cosine(
            query_vector,
            embedding_run_id=embedding_run_id,
            top_k=top_k,
        )
        total_ms = (perf_counter() - total_started) * 1000.0
        results.append(
            {
                "case_id": record["case_id"],
                "question": record["question"],
                "retrieved": [
                    {
                        "rank": rank,
                        "chunk_id": hit.chunk_id,
                        "score": hit.score,
                    }
                    for rank, hit in enumerate(response.hits, start=1)
                ],
                "latency_ms": {
                    "query_embedding": embedding_ms,
                    "database_search": response.database_search_ms,
                    "end_to_end": total_ms,
                },
            }
        )
    return results


def _missed_groups(case: dict[str, Any], ranked: list[str], k: int) -> list[dict[str, Any]]:
    top_k = ranked[:k]
    retrieved = set(top_k)
    missed: list[dict[str, Any]] = []
    for group in required_literature_groups(case):
        if group_is_covered(group, top_k):
            continue
        gold_ids = list(group["chunk_ids"])
        missed.append(
            {
                "evidence_group_id": group["id"],
                "claim": group["claim"],
                "match": group["match"],
                "gold_chunk_ids": gold_ids,
                "retrieved_gold_chunk_ids": [item for item in gold_ids if item in retrieved],
                "missing_chunk_ids": [item for item in gold_ids if item not in retrieved],
            }
        )
    return missed


def evaluate_dense_retrieval(
    cases: Sequence[dict[str, Any]], retrieval_results: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply frozen gold semantics after rankings have already been produced."""

    by_case = {item["case_id"]: item for item in retrieval_results}
    ranked = {
        case_id: [hit["chunk_id"] for hit in result["retrieved"]]
        for case_id, result in by_case.items()
    }
    evaluation = evaluate_retrieval(list(cases), ranked, ks=ALL_KS)
    enriched: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["id"]
        result = by_case[case_id]
        enriched.append(
            {
                **result,
                "category": case["category"],
                "metrics": evaluation["per_case"][case_id],
                "missed_required_evidence@5": _missed_groups(case, ranked[case_id], 5),
                "missed_required_evidence@10": _missed_groups(case, ranked[case_id], 10),
            }
        )

    latency_fields = ("query_embedding", "database_search", "end_to_end")
    latency = {
        field: _latency_summary(
            [float(item["latency_ms"][field]) for item in retrieval_results]
        )
        for field in latency_fields
    }
    metrics = {
        **evaluation,
        "primary_ks": list(PRIMARY_KS),
        "diagnostic_ks": list(DIAGNOSTIC_KS),
        "latency_ms": latency,
    }
    return metrics, enriched


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("Cannot summarize empty latency values")
    return {
        "mean": mean(values),
        "median": median(values),
        "p95": float(np.percentile(np.asarray(values), 95)),
        "min": min(values),
        "max": max(values),
    }


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def build_markdown_report(
    *,
    metrics: dict[str, Any],
    results: Sequence[dict[str, Any]],
    reproducibility: dict[str, Any],
) -> str:
    macro = metrics["macro"]
    lines = [
        "# Phase 6 Dense Retrieval Baseline",
        "",
        "> Immutable, untuned first baseline. Gold labels were used only after retrieval for evaluation.",
        "",
        "## Configuration",
        "",
        f"- Baseline: `{reproducibility['baseline_version']}`",
        f"- Embedding model: `{reproducibility['embedding']['model_id']}`",
        f"- Model revision: `{reproducibility['embedding']['model_revision']}`",
        f"- Embedding dimension: {reproducibility['embedding']['embedding_dimension']}",
        "- Similarity: cosine (`pgvector <=>`), exact search with no ANN index",
        f"- Top-K: {reproducibility['retrieval']['top_k']}",
        f"- Corpus: `{reproducibility['inputs']['corpus_version']}` / `{reproducibility['inputs']['corpus_chunks_sha256']}`",
        f"- Evaluation: `{reproducibility['inputs']['eval_dataset_version']}` / `{reproducibility['inputs']['eval_dataset_sha256']}`",
        "- Query input: frozen `question` field only",
        "- Tuning performed: no",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | Value | Contract |",
        "|---|---:|---|",
    ]
    for name in (
        "evidence_group_recall@5",
        "evidence_group_recall@10",
        "complete_evidence@5",
        "complete_evidence@10",
        "chunk_recall@5",
        "chunk_recall@10",
        "mrr",
        "evidence_group_recall@1",
        "evidence_group_recall@3",
        "complete_evidence@1",
        "complete_evidence@3",
        "chunk_recall@1",
        "chunk_recall@3",
    ):
        contract = "primary" if name.endswith("@5") or name.endswith("@10") or name == "mrr" else "diagnostic"
        lines.append(f"| `{name}` | {macro[name]:.6f} | {contract} |")

    lines.extend(
        [
            "",
            "## Retrieval latency",
            "",
            "All values are milliseconds across the 18 queries. Model loading and one-time corpus embedding/storage are excluded from per-query latency.",
            "",
            "| Stage | Mean | Median | P95 | Min | Max |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for stage, values in metrics["latency_ms"].items():
        lines.append(
            f"| `{stage}` | {values['mean']:.3f} | {values['median']:.3f} | "
            f"{values['p95']:.3f} | {values['min']:.3f} | {values['max']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Per-case metrics",
            "",
            "| Case | EGR@5 | EGR@10 | Complete@5 | Complete@10 | Chunk@5 | Chunk@10 | RR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for result in results:
        values = result["metrics"]
        lines.append(
            f"| `{result['case_id']}` | {values['evidence_group_recall@5']:.4f} | "
            f"{values['evidence_group_recall@10']:.4f} | {values['complete_evidence@5']:.4f} | "
            f"{values['complete_evidence@10']:.4f} | {values['chunk_recall@5']:.4f} | "
            f"{values['chunk_recall@10']:.4f} | {values['reciprocal_rank']:.4f} |"
        )

    lines.extend(["", "## Missed required Gold evidence at Top-10", ""])
    any_missed = False
    for result in results:
        missed = result["missed_required_evidence@10"]
        if not missed:
            continue
        any_missed = True
        lines.extend([f"### {result['case_id']}", ""])
        for group in missed:
            lines.append(
                f"- `{group['evidence_group_id']}` (`{group['match']}`): "
                f"{group['claim']} Missing: "
                + ", ".join(f"`{item}`" for item in group["missing_chunk_ids"])
            )
        lines.append("")
    if not any_missed:
        lines.extend(["No required evidence groups were missed at Top-10.", ""])

    lines.extend(
        [
            "## Failure-analysis artifacts",
            "",
            "`retrieval_results.jsonl` contains every frozen question, Top-10 chunk IDs and scores, per-case metrics, latency, and missed required evidence at @5/@10. `reproducibility.json` contains pinned software/model/database metadata and frozen input hashes.",
            "",
            "No retrieval setting or Gold label was changed after observing these results.",
            "",
        ]
    )
    return "\n".join(lines)


def write_immutable_baseline_artifacts(
    *,
    output_dir: str | Path,
    metrics: dict[str, Any],
    results: Sequence[dict[str, Any]],
    reproducibility: dict[str, Any],
) -> dict[str, Any]:
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(
            f"Refusing to overwrite immutable first-baseline artifacts: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        (temp / "metrics.json").write_text(_json_text(metrics), encoding="utf-8")
        (temp / "reproducibility.json").write_text(
            _json_text(reproducibility), encoding="utf-8"
        )
        with (temp / "retrieval_results.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
            for result in results:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        (temp / "REPORT.md").write_text(
            build_markdown_report(
                metrics=metrics,
                results=results,
                reproducibility=reproducibility,
            ),
            encoding="utf-8",
        )
        artifact_names = (
            "metrics.json",
            "retrieval_results.jsonl",
            "reproducibility.json",
            "REPORT.md",
        )
        manifest = {
            "baseline_version": reproducibility["baseline_version"],
            "status": "frozen",
            "created_at_utc": reproducibility["created_at_utc"],
            "artifacts": [
                {
                    "path": name,
                    "sha256": sha256_file(temp / name),
                    "bytes": (temp / name).stat().st_size,
                }
                for name in artifact_names
            ],
            "mutation_policy": (
                "Never overwrite dense_baseline_v1; create a new version for any later run."
            ),
        }
        (temp / "manifest.json").write_text(_json_text(manifest), encoding="utf-8")
        temp.replace(target)
        return manifest
    except BaseException:
        if temp.exists():
            shutil.rmtree(temp)
        raise


def build_reproducibility_record(
    *,
    inputs: FrozenRetrievalInputs,
    encoder: EncoderMetadata,
    database: dict[str, str],
    corpus_embedding_ms: float,
    corpus_storage_ms: float,
) -> dict[str, Any]:
    return {
        "baseline_version": BASELINE_VERSION,
        "status": "untuned_first_baseline",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "corpus_version": inputs.corpus_version,
            "corpus_chunks_path": inputs.corpus_chunks_path.relative_to(
                inputs.project_root
            ).as_posix(),
            "corpus_chunks_sha256": inputs.corpus_chunks_sha256,
            "corpus_manifest_sha256": inputs.corpus_manifest_sha256,
            "corpus_chunk_count": len(inputs.chunks),
            "eval_dataset_version": inputs.eval_dataset_version,
            "eval_dataset_path": inputs.eval_path.relative_to(
                inputs.project_root
            ).as_posix(),
            "eval_dataset_sha256": inputs.eval_dataset_sha256,
            "eval_manifest_sha256": inputs.eval_manifest_sha256,
            "literature_bearing_case_count": len(inputs.cases),
        },
        "embedding": asdict(encoder),
        "retrieval": {
            "store": "PostgreSQL/pgvector",
            "embedding_run_id": EMBEDDING_RUN_ID,
            "similarity_metric": "cosine",
            "distance_operator": "<=>",
            "search_mode": "exact",
            "ann_index": None,
            "top_k": TOP_K,
            "tie_breaker": "chunk_id ascending",
            "query_source": "frozen eval case question field only",
        },
        "evaluation": {
            "contract_source": "frozen eval_dataset_v1 protocol",
            "primary_ks": list(PRIMARY_KS),
            "diagnostic_ks": list(DIAGNOSTIC_KS),
            "gold_application_stage": "after ranking only",
        },
        "database": database,
        "one_time_latency_ms": {
            "corpus_embedding": corpus_embedding_ms,
            "corpus_storage_and_validation": corpus_storage_ms,
        },
        "controls": {
            "retrieval_tuning_performed": False,
            "gold_used_for_ranking": False,
            "gold_labels_modified": False,
            "frozen_corpus_modified": False,
            "bm25_rrf_or_hybrid_used": False,
            "llm_generation_or_agent_used": False,
            "automatic_post_result_changes": False,
        },
    }
