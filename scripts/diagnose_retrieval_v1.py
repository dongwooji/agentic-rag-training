"""Read-only Retrieval v1 diagnostics; writes only reports/diagnostics outputs.

No production configuration, corpus, model, baseline, or Gold is modified.
Gold is used only after rankings have been fixed, in diagnostic evaluation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import statistics
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
SNAPSHOT = ROOT / "data/models/huggingface" / (
    "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2"
) / "snapshots" / REVISION
PROTECTED_ROOTS = (
    "data/literature", "data/evaluation", "reports/baselines", "config", "src",
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def protected_hashes() -> dict[str, str]:
    paths = []
    def fail(error: OSError) -> None:
        raise error
    for protected in [*(ROOT / name for name in PROTECTED_ROOTS), SNAPSHOT]:
        for directory, subdirs, files in os.walk(protected, onerror=fail):
            subdirs[:] = [name for name in subdirs if name != "__pycache__"]
            paths.extend(Path(directory) / name for name in files if not name.endswith(".pyc"))
    return {path.relative_to(ROOT).as_posix(): sha256(path) for path in sorted(set(paths))}


def hash_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))


def validate_recorded_hashes(current: dict[str, str]) -> dict[str, Any]:
    """Verify hash declarations, without interpreting stored case/Gold fields."""
    checks: list[dict[str, Any]] = []
    def check(path: str, expected: str, source: str) -> None:
        if path not in current:
            raise RuntimeError(f"Declared protected artifact not found: {path}")
        passed = current[path] == expected
        historical_code = path.startswith(("src/", "config/"))
        checks.append({"path": path, "source": source, "expected_sha256": expected,
                       "actual_sha256": current[path], "passed": passed,
                       "historical_source_reference": historical_code})
        if not passed and not historical_code:
            raise RuntimeError(f"Existing recorded hash mismatch: {path} ({source})")
    reproduction_path = "reports/baselines/hybrid_baseline_v1/reproducibility.json"
    reproduction = read_json(ROOT / reproduction_path)
    check("data/literature/processed/chunks.jsonl", reproduction["inputs"]["corpus_chunks_sha256"], reproduction_path)
    check("data/literature/manifests/corpus_v1.json", reproduction["inputs"]["corpus_manifest_sha256"], reproduction_path)
    check("data/evaluation/eval_dataset_v1.json", reproduction["inputs"]["eval_dataset_sha256"], reproduction_path)
    check("data/evaluation/eval_dataset_v1.manifest.json", reproduction["inputs"]["eval_manifest_sha256"], reproduction_path)
    from ast import literal_eval, parse, Assign, Name
    module = parse((ROOT / "src/retrieval/hybrid.py").read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, Assign) and any(isinstance(t, Name) and t.id == "FROZEN_PHASE7_MANIFEST_SHA256" for t in node.targets):
            check("reports/baselines/hybrid_baseline_v1/manifest.json", literal_eval(node.value), "src/retrieval/hybrid.py")
    check("reports/baselines/dense_baseline_v1/manifest.json", reproduction["dense_reference"]["manifest_sha256"], reproduction_path)
    for name in sorted(current):
        if not name.startswith("reports/baselines/") or not name.endswith("/manifest.json"):
            continue
        manifest = read_json(ROOT / name)
        for item in manifest.get("artifacts", []):
            if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
                continue
            relative = (ROOT / name).parent / item["path"]
            # Manifest paths may be project-relative instead of manifest-relative.
            if relative.relative_to(ROOT).as_posix() not in current:
                relative = ROOT / item["path"]
            key = relative.relative_to(ROOT).as_posix()
            if key in current:
                check(key, item["sha256"], name)
        for relative_name, expected in manifest.get("artifact_hashes", {}).items():
            key = ((ROOT / name).parent / relative_name).relative_to(ROOT).as_posix()
            check(key, expected, name)
    return {"check_count": len(checks),
            "all_frozen_artifact_checks_passed": all(c["passed"] for c in checks if not c["historical_source_reference"]),
            "historical_source_mismatches": [c for c in checks if not c["passed"]], "checks": checks}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/diagnostics/retrieval_v1"))
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--dense-mode", choices=("stored", "memory"), default="stored")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = (ROOT / args.output).resolve()
    diagnostic_root = (ROOT / "reports/diagnostics").resolve()
    if not output.is_relative_to(diagnostic_root) or output == diagnostic_root:
        raise ValueError("Output must be a child of reports/diagnostics; baseline writes are forbidden")
    output.mkdir(parents=True, exist_ok=True)
    before_path = output / "protected_hashes_before.json"
    if args.capture_only:
        if before_path.exists():
            raise FileExistsError("Initial hash snapshot already exists")
        current = protected_hashes()
        validation = validate_recorded_hashes(current)
        write_json(before_path, {"captured_at_utc": datetime.now(timezone.utc).isoformat(), "files": current,
                                 "recorded_hash_validation": validation})
        print(json.dumps({"protected_files": len(current), "expected_hash_checks": validation["check_count"]}))
        return
    if not before_path.exists():
        raise FileNotFoundError("Run --capture-only before measurements")
    before = read_json(before_path)["files"]
    differences = hash_changes(before, protected_hashes())
    if differences:
        raise RuntimeError(f"Protected files changed before diagnosis: {differences}")
    try:
        run_diagnostics(output, args.dense_mode)
    finally:
        after = protected_hashes()
        differences = hash_changes(before, after)
        write_json(output / "protected_hashes_after.json", {"captured_at_utc": datetime.now(timezone.utc).isoformat(),
                                                            "files": after, "changed_files": differences,
                                                            "all_unchanged": not differences,
                                                            "recorded_hash_validation": validate_recorded_hashes(after)})
    if differences:
        raise RuntimeError(f"Protected files changed during diagnosis: {differences}")
    print("Diagnostic measurements complete; all protected hashes unchanged.")


def token_length_diagnostics(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import numpy as np
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(SNAPSHOT / "tokenizer.json"))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    maximum = int(read_json(SNAPSHOT / "sentence_bert_config.json")["max_seq_length"])
    lengths = [len(item.ids) for item in tokenizer.encode_batch([chunk["text"] for chunk in chunks])]
    rows = [{"chunk_id": chunk["chunk_id"], "paper_id": chunk["paper_id"], "section": chunk["section"],
             "word_count": chunk["word_count"], "token_count": length, "max_seq_length": maximum,
             "truncated_token_count": max(0, length - maximum),
             "model_input_token_count": min(length, maximum),
             "retained_token_ratio": min(length, maximum) / length}
            for chunk, length in zip(chunks, lengths, strict=True)]
    distribution = {"mean": statistics.mean(lengths), "median": statistics.median(lengths),
                    **{f"p{p}": float(np.percentile(lengths, p, method="linear")) for p in (90, 95, 99)},
                    "minimum": min(lengths), "maximum": max(lengths)}
    result = {"chunk_count": len(rows), "model_id": MODEL_ID, "model_revision": REVISION,
              "max_seq_length": maximum, "tokenizer_model_max_length": read_json(SNAPSHOT / "tokenizer_config.json")["model_max_length"],
              "tokenizer_file_sha256": sha256(SNAPSHOT / "tokenizer.json"),
              "special_tokens_per_single_input": tokenizer.num_special_tokens_to_add(False),
              "token_count_includes_special_tokens": True, "percentile_method": "numpy linear",
              "token_count": distribution,
              "word_count": {"mean": statistics.mean(c["word_count"] for c in chunks),
                             "maximum": max(c["word_count"] for c in chunks)},
              "mean_retained_token_ratio": statistics.mean(row["retained_token_ratio"] for row in rows),
              "weighted_retained_token_ratio": sum(min(n, maximum) for n in lengths) / sum(lengths),
              "total_truncated_tokens": sum(row["truncated_token_count"] for row in rows),
              "definitions": {"truncated_token_count": "max(token_count - max_seq_length, 0): removed token count",
                              "retained_token_ratio": "min(token_count, max_seq_length) / token_count; proxy only"}}
    for multiplier, label in ((1, "over_limit"), (2, "over_2x_limit"), (3, "over_3x_limit")):
        count = sum(length > maximum * multiplier for length in lengths)
        result[label] = {"count": count, "ratio": count / len(lengths)}
    return rows, result


def ranked_hits(hits: Any) -> list[dict[str, Any]]:
    return [{"rank": rank, "chunk_id": hit.chunk_id, "score": float(hit.score)}
            for rank, hit in enumerate(hits, 1)]


def ids(rows: list[dict[str, Any]]) -> list[str]:
    return [row["chunk_id"] for row in rows]


def hybrid_hits(dense: list[dict[str, Any]], bm25: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from src.retrieval.rrf import reciprocal_rank_fusion
    response = reciprocal_rank_fusion(ids(dense[:10]), ids(bm25[:10]), rrf_k=60, top_k=10)
    return [{"rank": rank, "chunk_id": hit.chunk_id, "score": hit.score,
             "dense_rank": hit.dense_rank, "bm25_rank": hit.bm25_rank,
             "dense_contribution": hit.dense_contribution, "bm25_contribution": hit.bm25_contribution}
            for rank, hit in enumerate(response.hits, 1)]


def bm25_diagnostics(index: Any, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in questions:
        response = index.search(record["question"], top_k=50)
        top10 = ranked_hits(response.hits[:10])
        rows.append({"case_id": record["case_id"], "query": record["question"],
                     "has_hangul": bool(re.search(r"[가-힣]", record["question"])),
                     "query_token_count": response.query_token_count,
                     "matched_query_terms": response.matched_query_terms,
                     "matched_query_term_count": len(response.matched_query_terms),
                     "top10": top10, "top50": ranked_hits(response.hits),
                     "positive_score_document_count_top10": sum(hit["score"] > 0 for hit in top10),
                     "all_top10_scores_zero": all(hit["score"] == 0 for hit in top10),
                     "zero_match_chunk_id_tie_order": ids(top10) == sorted(index.chunk_ids)[:10] if not response.matched_query_terms else None})
    return rows


def zero_match_analysis(record: dict[str, Any], dense: list[dict[str, Any]]) -> dict[str, Any]:
    bm25 = record["top10"]
    hybrid = hybrid_hits(dense[:10], bm25)
    dense_rank = {hit["chunk_id"]: hit["rank"] for hit in dense[:10]}
    bm25_by_id = {hit["chunk_id"]: hit for hit in bm25}
    entered = []
    promoted = []
    for hit in hybrid:
        detail = {**hit, "bm25_score": bm25_by_id.get(hit["chunk_id"], {}).get("score")}
        if hit["chunk_id"] not in dense_rank:
            entered.append(detail)
        elif hit["rank"] < dense_rank[hit["chunk_id"]]:
            promoted.append({**detail, "original_dense_rank": dense_rank[hit["chunk_id"]]})
    return {"case_id": record["case_id"], "query": record["query"], "dense_top10": dense[:10],
            "bm25_top10": bm25, "hybrid_top10": hybrid,
            "bm25_rank_contributions": [{**hit, "rrf_contribution": 1 / (60 + hit["rank"])} for hit in bm25],
            "entered_hybrid_without_dense_top10": entered, "promoted_above_dense_rank": promoted,
            "displaced_dense_top10": [hit for hit in dense[:10] if hit["chunk_id"] not in set(ids(hybrid))],
            "semantic_irrelevance_not_asserted": True}


def diversity_row(case_id: str, ranking: list[dict[str, Any]], chunks_by_id: dict[str, Any]) -> dict[str, Any]:
    paper_counts = Counter(chunks_by_id[hit["chunk_id"]]["paper_id"] for hit in ranking[:10])
    sections = Counter((chunks_by_id[hit["chunk_id"]]["paper_id"], chunks_by_id[hit["chunk_id"]]["section"]) for hit in ranking[:10])
    return {"case_id": case_id, "unique_paper_count_top10": len(paper_counts),
            "max_chunks_from_one_paper": max(paper_counts.values()),
            "paper_counts": dict(sorted(paper_counts.items())),
            "same_paper_at_least_half_top10": max(paper_counts.values()) >= 5,
            "max_chunks_from_same_paper_section": max(sections.values()),
            "chunks_in_repeated_paper_sections": sum(count for count in sections.values() if count > 1),
            "section_counts": [{"paper_id": paper, "section": section, "chunk_count": count}
                               for (paper, section), count in sorted(sections.items())]}


def evaluate_rankings(case: dict[str, Any], ranking: list[dict[str, Any]]) -> dict[str, float]:
    from src.evaluation.retrieval_metrics import evidence_group_recall_at_k, complete_evidence_at_k
    return {"evidence_group_recall@10": evidence_group_recall_at_k(case, ids(ranking), 10),
            "complete_evidence@10": complete_evidence_at_k(case, ids(ranking), 10)}


def depth_analysis(case: dict[str, Any], dense: list[dict[str, Any]], bm25: list[dict[str, Any]]) -> dict[str, Any]:
    from src.evaluation.retrieval_metrics import required_literature_groups, group_is_covered
    candidates10 = set(ids(dense[:10])) | set(ids(bm25[:10]))
    # Zero-score BM25 ties are not evidence of retrieval opportunity.
    positive_bm25 = [hit for hit in bm25 if hit["score"] > 0]
    candidates50 = set(ids(dense)) | set(ids(positive_bm25))
    dense_deep = {hit["chunk_id"]: hit for hit in dense if 11 <= hit["rank"] <= 50}
    bm25_deep = {hit["chunk_id"]: hit for hit in bm25 if 11 <= hit["rank"] <= 50}
    groups = []
    for group in required_literature_groups(case):
        evidence = []
        for chunk_id in group["chunk_ids"]:
            dense_hit, bm25_hit = dense_deep.get(chunk_id), bm25_deep.get(chunk_id)
            if dense_hit or bm25_hit:
                evidence.append({"chunk_id": chunk_id, "dense_rank": dense_hit["rank"] if dense_hit else None,
                                 "bm25_rank": bm25_hit["rank"] if bm25_hit else None,
                                 "bm25_score": bm25_hit["score"] if bm25_hit else None,
                                 "excluded_from_depth10_union": chunk_id not in candidates10,
                                 "valid_deep_retrieval": bool(dense_hit) or bool(bm25_hit and bm25_hit["score"] > 0)})
        if evidence:
            groups.append({"group_id": group["id"], "match": group["match"], "rank11_50_gold": evidence,
                           "covered_by_candidate_union10": group_is_covered(group, candidates10),
                           "covered_by_positive_candidate_union50": group_is_covered(group, candidates50),
                           "lost_evidence_opportunity": any(e["excluded_from_depth10_union"] and e["valid_deep_retrieval"] for e in evidence),
                           "newly_fully_coverable": not group_is_covered(group, candidates10) and group_is_covered(group, candidates50)})
    return {"case_id": case["id"], "dense_top50": dense, "bm25_top50": bm25,
            "candidate_union_size_depth10": len(candidates10), "candidate_union_size_depth50_positive_bm25": len(candidates50),
            "groups": groups, "has_valid_gold_rank11_50": any(e["valid_deep_retrieval"] for g in groups for e in g["rank11_50_gold"]),
            "lost_evidence_group_count": sum(g["lost_evidence_opportunity"] for g in groups),
            "newly_fully_coverable_group_count": sum(g["newly_fully_coverable"] for g in groups)}


class MemoryDense:
    """Same pinned weights, transient CPU vectors; never persists embeddings."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        import numpy as np
        import torch
        from sentence_transformers import SentenceTransformer
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.model = SentenceTransformer(str(SNAPSHOT), device="cpu", local_files_only=True)
        self.chunks = chunks
        self.vectors = np.asarray(self.model.encode([chunk["text"] for chunk in chunks], batch_size=32,
            normalize_embeddings=True, convert_to_numpy=True, precision="float32", show_progress_bar=False), dtype=np.float32)
        if self.vectors.shape != (488, 384) or not np.isfinite(self.vectors).all():
            raise RuntimeError("Unexpected transient corpus vectors")

    def search_many(self, questions: list[str]) -> dict[str, list[dict[str, Any]]]:
        import numpy as np
        vectors = self.model.encode(questions, batch_size=32, normalize_embeddings=True,
                                    convert_to_numpy=True, precision="float32", show_progress_bar=False)
        # Double-precision cosine, matching the database's exact cosine definition.
        documents = self.vectors.astype(np.float64)
        documents /= np.linalg.norm(documents, axis=1, keepdims=True)
        queries = np.asarray(vectors, dtype=np.float64)
        queries /= np.linalg.norm(queries, axis=1, keepdims=True)
        scores = queries @ documents.T
        rankings = {}
        for question, score in zip(questions, scores, strict=True):
            order = sorted(range(len(self.chunks)), key=lambda i: (-float(score[i]), self.chunks[i]["chunk_id"]))[:50]
            rankings[question] = [{"rank": rank, "chunk_id": self.chunks[i]["chunk_id"], "score": float(score[i])}
                                  for rank, i in enumerate(order, 1)]
        return rankings


def source_trace() -> dict[str, Any]:
    references = {
        "dense_corpus_input": ("src/retrieval/baseline.py", '[str(chunk["text"]) for chunk in chunks]'),
        "bm25_document_input": ("src/retrieval/bm25.py", 'tokenized = [tokenize(str(document["text"]))'),
        "phase_a_invocation": ("src/graph/interpreted_workflow.py", "state = workflow.invoke(question, literature_subquestion=interpretation.literature_subquestion"),
        "phase_a_original_initial_query": ("src/graph/interpreted_workflow.py", "initial_query=question, tool_inputs=tool_inputs"),
        "initial_query_precedence": ("src/graph/workflow.py", "initial_query.strip()"),
        "initial_tool_input": ("src/graph/nodes.py", '"query": state["initial_query"]'),
        "hybrid_query_dense": ("src/retrieval/hybrid.py", "query_vector = self._encoder.encode([query]"),
        "hybrid_query_bm25": ("src/retrieval/hybrid.py", "bm25 = self._bm25.search(query, top_k=SOURCE_DEPTH)"),
        "hybrid_bm25_unfiltered": ("src/retrieval/hybrid.py", "bm25_ids = [str(hit.chunk_id) for hit in bm25.hits]"),
        "runtime_grader_scope": ("src/graph/nodes.py", '"literature_subquestion": state["literature_subquestion"]'),
        "recovery_query_input": ("src/graph/nodes.py", 'query=state["current_query"]'),
        "subquestion_preserves_initial": ("src/graph/literature_scope.py", "The initial retrieval query is intentionally unaffected"),
        "dense_model_encode": ("src/retrieval/dense.py", "vectors = self._model.encode("),
        "source_depth": ("src/retrieval/hybrid.py", "SOURCE_DEPTH = 10"),
        "rrf_rank_contribution": ("src/retrieval/rrf.py", "1.0 / (rrf_k + bm25_rank)"),
    }
    result = {}
    for key, (filename, needle) in references.items():
        lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
        matches = [i for i, line in enumerate(lines, 1) if needle in line]
        result[key] = {"path": filename, "lines": matches, "verified": bool(matches), "needle": needle}
    return result


def run_diagnostics(output: Path, dense_mode: str) -> None:
    from src.retrieval.bm25 import BM25Index
    # Load this standalone existing module without importing the graph runtime,
    # so diagnostics cannot initialize a workflow or provider as a side effect.
    import importlib.util
    specification = importlib.util.spec_from_file_location("diagnostic_existing_literature_scope", ROOT / "src/graph/literature_scope.py")
    scope = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(scope)
    derive_literature_subquestion = scope.derive_literature_subquestion
    from src.routing.deterministic import DeterministicRouter
    from src.routing.contracts import RouterInput
    chunks = read_jsonl(ROOT / "data/literature/processed/chunks.jsonl")
    by_id = {c["chunk_id"]: c for c in chunks}
    # Strip Gold before query generation/ranking. Keep labels for evaluation only.
    cases = [c for c in read_json(ROOT / "data/evaluation/eval_dataset_v1.json")["cases"]
             if c["category"] in ("literature_only", "hybrid")]
    questions = [{"case_id": c["id"], "question": c["question"]} for c in cases]
    token_rows, token_metrics = token_length_diagnostics(chunks)
    write_jsonl(output / "chunk_token_lengths.jsonl", token_rows)
    print("Token lengths measured for 488 frozen chunks.", flush=True)
    index = BM25Index(chunks)
    stored_bm25 = {r["case_id"]: r for r in read_jsonl(ROOT / "reports/baselines/hybrid_baseline_v1/bm25_results.jsonl")}
    stored_dense = {r["case_id"]: r for r in read_jsonl(ROOT / "reports/baselines/dense_baseline_v1/retrieval_results.jsonl")}
    stored_hybrid = {r["case_id"]: r for r in read_jsonl(ROOT / "reports/baselines/hybrid_baseline_v1/hybrid_results.jsonl")}
    bm25_rows = bm25_diagnostics(index, questions)
    bm25_by_case = {r["case_id"]: r for r in bm25_rows}
    write_jsonl(output / "bm25_query_diagnostics.jsonl", bm25_rows)
    reproduction_checks = []
    for record in questions:
        case_id = record["case_id"]
        b = bm25_by_case[case_id]["top10"]
        d = stored_dense[case_id]["retrieved"]
        h = hybrid_hits(d, b)
        if ids(b) != ids(stored_bm25[case_id]["retrieved"]) or ids(h) != ids(stored_hybrid[case_id]["retrieved"]):
            raise RuntimeError(f"Frozen BM25/Hybrid ranks were not reproduced: {case_id}")
        reproduction_checks.append({"case_id": case_id, "bm25_top10_exact_match": True,
                                    "hybrid_top10_exact_match": True,
                                    "max_bm25_score_difference": max(abs(x["score"] - y["score"]) for x, y in zip(b, stored_bm25[case_id]["retrieved"], strict=True))})
    zero_rows = [zero_match_analysis(row, stored_dense[row["case_id"]]["retrieved"])
                 for row in bm25_rows if row["matched_query_term_count"] == 0]
    write_jsonl(output / "rrf_zero_match_analysis.jsonl", zero_rows)
    diversity = [diversity_row(record["case_id"], stored_hybrid[record["case_id"]]["retrieved"], by_id) for record in questions]
    write_jsonl(output / "diversity_analysis.jsonl", diversity)
    router = DeterministicRouter()
    subquestions = []
    for record in questions:
        route = router.route(RouterInput(record["question"])).to_dict()
        sub = derive_literature_subquestion(record["question"], route)
        subquestions.append({**record, "literature_subquestion": sub, "route_task_type": route["task_type"],
                             "changed": sub != record["question"], "source": "existing deterministic literature_scope; no Gold or LLM"})
    write_jsonl(output / "deterministic_subquestions.jsonl", subquestions)
    model = None
    raw_dense50: dict[str, Any] = {}
    sub_dense50: dict[str, Any] = {}
    dense_validation: dict[str, Any] = {"mode": dense_mode, "available": False}
    if dense_mode == "memory":
        print("Loading pinned local weights; encoding transient corpus/query vectors only.", flush=True)
        engine = MemoryDense(chunks)
        model = engine.model
        ranking_queries = list(dict.fromkeys([r["question"] for r in questions] +
                                            [r["literature_subquestion"] for r in subquestions if r["changed"]]))
        all_dense = engine.search_many(ranking_queries)
        checks = []
        for record in questions:
            fresh = all_dense[record["question"]]
            stored = stored_dense[record["case_id"]]["retrieved"]
            matches = ids(fresh[:10]) == ids(stored)
            checks.append({"case_id": record["case_id"], "top10_exact_rank_match": matches,
                           "max_top10_score_difference": max(abs(a["score"] - b["score"]) for a, b in zip(fresh[:10], stored, strict=True)),
                           "fresh_top10": fresh[:10], "stored_top10": stored})
            if matches:
                raw_dense50[record["case_id"]] = fresh
            else:
                raise RuntimeError(f"Transient Dense Top-10 differs from frozen results: {record['case_id']}; refusing depth/query attribution")
        sub_dense50 = {r["case_id"]: all_dense[r["literature_subquestion"]] for r in subquestions if r["changed"]}
        dense_validation = {"mode": "transient_in_memory_exact_cosine", "available": True,
                            "all_frozen_top10_ranks_reproduced": all(c["top10_exact_rank_match"] for c in checks), "checks": checks,
                            "database_status": "not used; credential unavailable in this session",
                            "embedding_persistence": "none; no DB or existing artifact writes",
                            "limitation": "Same pinned weights and ST/Transformers/Torch versions; Python and NumPy differ from baseline. Transient cosine scores can differ slightly from pgvector."}
    write_json(output / "dense_reproduction.json", dense_validation)
    samples = []
    if model is not None:
        import inspect
        transformer = model[0]
        maximum = token_metrics["max_seq_length"]
        if model.max_seq_length != maximum:
            raise RuntimeError("Actual SentenceTransformer max_seq_length differs from pinned config")
        for length_row in sorted(token_rows, key=lambda r: r["token_count"], reverse=True)[:3]:
            text = by_id[length_row["chunk_id"]]["text"]
            uncapped = model.tokenizer(text, truncation=False, padding=False)["input_ids"]
            features = model.tokenize([text])
            used = int(features["attention_mask"].sum().item())
            if len(uncapped) != length_row["token_count"] or used != maximum:
                raise RuntimeError("Raw tokenizer count or actual truncation sample mismatch")
            samples.append({"chunk_id": length_row["chunk_id"], "original_token_count": len(uncapped),
                            "actual_model_input_token_count": used, "actual_input_tensor_width": int(features["input_ids"].shape[1]),
                            "max_seq_length": int(model.max_seq_length), "actual_special_token_count": model.tokenizer.num_special_tokens_to_add(pair=False),
                            "automatic_truncation_verified": True})
        write_json(output / "model_tokenization_trace.json", {
            "module_class": type(transformer).__name__, "source_file": inspect.getfile(type(transformer)),
            "tokenize_source": inspect.getsource(type(transformer).tokenize),
            "preprocess_source": inspect.getsource(type(transformer).preprocess) if hasattr(transformer, "preprocess") else None,
            "max_seq_length": int(model.max_seq_length), "samples": samples,
            "production_length_guard": False, "production_rechunking": False,
            "ratio_limitations": "Diagnostic ratio includes special tokens; actual truncation retains ending special tokens rather than the original prefix literally."})
    alternative_bm25_by_case = {
        sub["case_id"]: ranked_hits(index.search(sub["literature_subquestion"], top_k=10).hits)
        for sub in subquestions if sub["changed"] and sub["case_id"] in sub_dense50
    }
    comparisons = []
    # All original/alternative source rankings are fixed before Gold evaluation.
    for case, sub in zip(cases, subquestions, strict=True):
        raw = {"dense_top10": stored_dense[case["id"]]["retrieved"], "bm25_top10": bm25_by_case[case["id"]]["top10"],
               "hybrid_top10": stored_hybrid[case["id"]]["retrieved"]}
        row = {"case_id": case["id"], "original_question": case["question"], "literature_subquestion": sub["literature_subquestion"],
               "initial_query_in_phase_a": case["question"], "raw": raw,
               "status": "compared" if sub["changed"] and case["id"] in sub_dense50 else "skipped",
               "skip_reason": None if sub["changed"] and case["id"] in sub_dense50 else "unchanged deterministic subquestion" if not sub["changed"] else "Dense backend unavailable"}
        if row["status"] == "compared":
            alternative_bm25 = alternative_bm25_by_case[case["id"]]
            alternative_dense = sub_dense50[case["id"]][:10]
            alternative = {"dense_top10": alternative_dense, "bm25_top10": alternative_bm25,
                           "hybrid_top10": hybrid_hits(alternative_dense, alternative_bm25)}
            for mode, rankings in (("raw", raw), ("literature_only", alternative)):
                row[mode] = {**rankings, "metrics": {name.removesuffix("_top10"): evaluate_rankings(case, hits) for name, hits in rankings.items()}}
        comparisons.append(row)
    write_jsonl(output / "retrieval_query_comparison.jsonl", comparisons)
    depths = []
    for case in cases:
        if case["id"] in raw_dense50:
            depths.append(depth_analysis(case, raw_dense50[case["id"]], bm25_by_case[case["id"]]["top50"]))
        else:
            row = depth_analysis(case, [], bm25_by_case[case["id"]]["top50"])
            row["status"] = "BM25-only; Dense depth unavailable; opportunity counts are not complete"
            depths.append(row)
    write_jsonl(output / "candidate_depth_analysis.jsonl", depths)
    hangul_rows = [r for r in bm25_rows if r["has_hangul"]]
    matched = len(bm25_rows) - len(zero_rows)
    metrics = {"diagnostic_version": "retrieval_v1_structural_diagnostics_v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
               "independent_final_evaluation": False, "no_llm_calls": True,
               "token_lengths": token_metrics, "truncation": {"automatic_truncation_samples": samples, "affected_chunks": token_metrics["over_limit"]},
               "bm25": {"case_count": len(bm25_rows), "zero_match_case_count": len(zero_rows),
                        "zero_match_ratio": len(zero_rows) / len(bm25_rows), "hangul_case_count": len(hangul_rows),
                        "hangul_zero_match_case_count": sum(r["matched_query_term_count"] == 0 for r in hangul_rows),
                        "hangul_zero_match_ratio": sum(r["matched_query_term_count"] == 0 for r in hangul_rows) / len(hangul_rows) if hangul_rows else None,
                        "matched_case_count": matched, "english_corpus_hangul_token_count": sum(bool(re.search(r"[가-힣]", term)) for term in index._postings),
                        "pure_hangul_case_count": sum(not re.search(r"[A-Za-z0-9]", r["query"]) for r in bm25_rows),
                        "pure_hangul_zero_match_case_count": sum(not re.search(r"[A-Za-z0-9]", r["query"]) and r["matched_query_term_count"] == 0 for r in bm25_rows),
                        "index_metadata": index.metadata},
               "zero_match_rrf": {"case_count": len(zero_rows), "entered_hybrid_chunk_count": sum(len(r["entered_hybrid_without_dense_top10"]) for r in zero_rows),
                                  "promoted_dense_chunk_count": sum(len(r["promoted_above_dense_rank"]) for r in zero_rows),
                                  "displaced_dense_chunk_count": sum(len(r["displaced_dense_top10"]) for r in zero_rows)},
               "diversity": {"case_count": len(diversity), "mean_unique_papers_top10": statistics.mean(r["unique_paper_count_top10"] for r in diversity),
                             "median_unique_papers_top10": statistics.median(r["unique_paper_count_top10"] for r in diversity),
                             "minimum_unique_papers_top10": min(r["unique_paper_count_top10"] for r in diversity),
                             "same_paper_at_least_half_case_count": sum(r["same_paper_at_least_half_top10"] for r in diversity),
                             "maximum_chunks_from_one_paper": max(r["max_chunks_from_one_paper"] for r in diversity)},
               "candidate_depth": {"production_dense_depth": 10, "production_bm25_depth": 10, "rrf_max_input_occurrences": 20,
                                   "final_top_k": 10, "diagnostic_depth": 50, "dense_depth_available": bool(raw_dense50),
                                   "actual_unique_candidate_counts": {r["case_id"]: len(set(ids(stored_dense[r["case_id"]]["retrieved"])) | set(ids(bm25_by_case[r["case_id"]]["top10"]))) for r in questions},
                                   "cases_with_valid_gold_rank11_50": sum(r["has_valid_gold_rank11_50"] for r in depths),
                                   "lost_evidence_group_opportunity_count": sum(r["lost_evidence_group_count"] for r in depths),
                                   "newly_fully_coverable_group_count": sum(r["newly_fully_coverable_group_count"] for r in depths),
                                   "zero_score_bm25_not_counted_as_valid_opportunity": True},
               "metadata": {field: {"present_count": sum(field in c for c in chunks), "nonempty_count": sum(bool(c.get(field)) for c in chunks),
                                    "used_in_dense_input": field == "text", "used_in_bm25_input": field == "text"}
                            for field in ("title", "section", "population", "study_type", "topics", "text")},
               "query_comparison": comparison_summary(comparisons), "frozen_rank_reproduction": reproduction_checks,
               "dense_validation": dense_validation, "source_trace": source_trace(),
               "environment": {"python": sys.version, "packages": {n: importlib.metadata.version(n) for n in ("numpy", "tokenizers", "pytest")}},
               "limitations": ["Existing frozen evaluation reused for diagnosis only; not independent generalization.",
                               "Lexical match is not semantic relevance; mixed-language questions can match dates and generic terms.",
                               "Candidate inclusion at depth 50 does not guarantee RRF Top-10 or final-answer improvement.",
                               "Metadata omission is confirmed; benefit of adding metadata remains unmeasured."]}
    if model is not None:
        metrics["environment"]["packages"].update({n: importlib.metadata.version(n) for n in ("torch", "transformers", "sentence-transformers")})
    metrics["diagnostic_script_sha256"] = sha256(Path(__file__))
    write_json(output / "metrics.json", metrics)
    write_summary(output, metrics, zero_rows, comparisons, diversity)
    print(json.dumps({"over_limit_chunks": token_metrics["over_limit"]["count"], "zero_match_cases": len(zero_rows),
                      "query_comparisons": metrics["query_comparison"]["compared_case_count"], "depth": metrics["candidate_depth"]}), flush=True)


def comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    compared = [row for row in rows if row["status"] == "compared"]
    summary: dict[str, Any] = {"compared_case_count": len(compared), "skipped_case_count": len(rows) - len(compared)}
    if compared:
        for mode in ("raw", "literature_only"):
            summary[mode] = {method: {metric: statistics.mean(r[mode]["metrics"][method][metric] for r in compared)
                                     for metric in ("evidence_group_recall@10", "complete_evidence@10")}
                             for method in ("dense", "bm25", "hybrid")}
        summary["hybrid_egr_improved_case_count"] = sum(r["literature_only"]["metrics"]["hybrid"]["evidence_group_recall@10"] > r["raw"]["metrics"]["hybrid"]["evidence_group_recall@10"] for r in compared)
        summary["hybrid_egr_worsened_case_count"] = sum(r["literature_only"]["metrics"]["hybrid"]["evidence_group_recall@10"] < r["raw"]["metrics"]["hybrid"]["evidence_group_recall@10"] for r in compared)
    return summary


def write_summary(output: Path, m: dict[str, Any], zero: list[dict[str, Any]], comparisons: list[dict[str, Any]], diversity: list[dict[str, Any]]) -> None:
    t = m["token_lengths"]
    b, d, depth, q = m["bm25"], m["diversity"], m["candidate_depth"], m["query_comparison"]
    p = lambda ratio: f"{100 * ratio:.2f}%"
    lines = ["# Retrieval v1 구조 진단", "", "기존 고정 평가를 재사용한 진단이며 독립적인 최종 성능 평가가 아니다. 검색·모델·청킹·Gold·baseline은 변경하지 않았다.", "",
             "## 1. 청크 토큰 길이", "", f"- 청크 {t['chunk_count']}개, 고정 revision `{REVISION}`, 모델 한도 {t['max_seq_length']}토큰.",
             f"- tokenizer 자체 한도는 {t['tokenizer_model_max_length']}지만 SentenceTransformer 실제 한도는 128이다.",
             f"- 실제 단어 수 평균 {t['word_count']['mean']:.2f}, 최대 {t['word_count']['maximum']}. 설정의 maximum_words=550은 overlap/tail 처리로 넘을 수 있다.",
             "- 정확한 로컬 tokenizer.json으로 truncation/padding 없이 계산했다. 단일 입력 특수 토큰 2개를 포함한다.",
             "- 백분위는 NumPy linear 보간. 단어 수를 토큰 수로 추정하지 않았다.", "", "| 평균 | 중앙값 | p90 | p95 | p99 | 최대 |", "|---:|---:|---:|---:|---:|---:|",
             "| " + " | ".join(f"{t['token_count'][name]:.2f}" for name in ("mean", "median", "p90", "p95", "p99", "maximum")) + " |", "",
             *[f"- {label}: {t[key]['count']}개 / {p(t[key]['ratio'])}" for label, key in (("128 초과", "over_limit"), ("256 초과", "over_2x_limit"), ("384 초과", "over_3x_limit"))],
             "", "## 2. 실제 truncation", "", f"- 한도 초과 {t['over_limit']['count']}개 ({p(t['over_limit']['ratio'])}). 입력 형태가 유지되는 한 이 청크들의 뒷부분은 모델 입력에서 잘린다.",
             f"- 평균 보존 비율 {p(t['mean_retained_token_ratio'])}; 전체 토큰 가중 보존 비율 {p(t['weighted_retained_token_ratio'])}.",
             "- retained_token_ratio=min(token_count,128)/token_count. 잘려나간 수는 truncated_token_count=max(token_count-128,0). 특수 토큰의 재배치 때문에 실제 본문 보존 비율과 다르다.",
             "- corpus embedding과 MiniLMEncoder.encode에 길이 검사·경고·재분할이 없다. 모델 tokenize에서 자동 truncation한다."]
    for sample in m["truncation"]["automatic_truncation_samples"]:
        lines.append(f"- 실제 샘플 `{sample['chunk_id']}`: {sample['original_token_count']} → {sample['actual_model_input_token_count']}토큰.")
    lines += ["", "## 3. BM25 zero-match", "", f"- 문헌 포함 {b['case_count']}문항 중 {b['zero_match_case_count']}문항 ({p(b['zero_match_ratio'])}).",
              "- zero-match에서 전체 문서 점수는 0이다. Top-10은 chunk_id 오름차순으로 반환된다. 검색 성공으로 해석할 수 없다.",
              "", "## 4. 한국어 질의", "", f"- Hangul 포함 {b['hangul_case_count']}문항 중 zero-match {b['hangul_zero_match_case_count']}문항 ({p(b['hangul_zero_match_ratio'])}).",
              f"- corpus 한글 검색어 {b['english_corpus_hangul_token_count']}개; 순수 한글 질문 {b['pure_hangul_case_count']}문항.",
              f"- 이 평가에 있는 순수 한글 {b['pure_hangul_case_count']}문항 중 zero-match {b['pure_hangul_zero_match_case_count']}문항. 표본이 작아 모든 한국어 질문의 실패율로 일반화할 수 없다.",
              "- 나머지 질문은 영어 용어·숫자 등이 일치한다. positive score는 질문 의미를 충분히 반영했다는 증거가 아니다.",
              "", "## 5. zero-match RRF 영향", ""]
    for row in zero:
        lines += [f"- `{row['case_id']}`: {row['query']}",
                  f"- Dense 밖에서 Hybrid로 들어온 청크 {len(row['entered_hybrid_without_dense_top10'])}개, 기존 Dense 순위 상승 {len(row['promoted_above_dense_rank'])}개, 밀려난 Dense 청크 {len(row['displaced_dense_top10'])}개."]
        for hit in row["entered_hybrid_without_dense_top10"]:
            lines.append(f"  - Hybrid {hit['rank']}위 `{hit['chunk_id']}`: BM25 {hit['bm25_rank']}위, score=0, 기여도 {hit['bm25_contribution']:.8f}.")
    lines += ["- 모든 BM25 rank는 점수와 관계없이 1/(60+rank)를 받는다. Dense Top-10 밖이라는 사실만으로 의미상 무관하다고 단정하지 않는다.",
              "", "## 6. 원 질문과 문헌 전용 질문 사용 구조", "",
              "- Phase A InterpretedWorkflow는 original_question을 보존하고 `literature_subquestion=interpretation.literature_subquestion`, `initial_query=question`으로 기존 workflow를 호출한다.",
              "- workflow는 명시된 initial_query를 subquestion보다 우선한다. 초기 Literature Tool은 initial_query를 사용한다.",
              "- literature_subquestion은 Runtime Grader 및 Recovery의 문헌 범위에 사용된다. Recovery가 만든 질의는 current_query로 재검색한다.",
              "- 일반 AgenticRAGWorkflow는 initial_query가 없고 subquestion을 명시하면 subquestion을 검색할 수 있다. Phase A 호출이 원문을 명시하는 구조를 구분해야 한다.",
              "", "## 7. 두 검색 질의 진단 비교", "", f"- 기존 deterministic literature_scope로 달라진 {q['compared_case_count']}문항을 비교했다. Gold/LLM으로 질의를 만들지 않았다."]
    if q["compared_case_count"]:
        lines += ["", "| 검색 | 원문 EGR@10 | 문헌전용 EGR@10 | 원문 Complete@10 | 문헌전용 Complete@10 |", "|---|---:|---:|---:|---:|"]
        for method in ("dense", "bm25", "hybrid"):
            a, alt = q["raw"][method], q["literature_only"][method]
            lines.append(f"| {method} | {a['evidence_group_recall@10']:.4f} | {alt['evidence_group_recall@10']:.4f} | {a['complete_evidence@10']:.4f} | {alt['complete_evidence@10']:.4f} |")
        lines.append(f"- Hybrid EGR 향상 {q['hybrid_egr_improved_case_count']}문항, 하락 {q['hybrid_egr_worsened_case_count']}문항. 이 결과로 일반화 성능이나 영어 번역 효과를 주장하지 않는다.")
    lines += ["", "## 8. 검색에서 사용하는 metadata", "", "| 필드 | 존재 | 값 있음 | Dense | BM25 |", "|---|---:|---:|---|---|"]
    for field, value in m["metadata"].items():
        lines.append(f"| {field} | {value['present_count']} | {value['nonempty_count']} | {'사용' if value['used_in_dense_input'] else '미사용'} | {'사용' if value['used_in_bm25_input'] else '미사용'} |")
    lines += ["- 제목·섹션·대상·연구 유형·주제는 저장하지만 검색 입력은 text만 사용한다. title/section은 검색 후 Tool 결과에 제공된다.",
              "", "## 9. Top-10 문헌 다양성", "", f"- 고유 논문 수 평균 {d['mean_unique_papers_top10']:.2f}, 중앙값 {d['median_unique_papers_top10']}, 최소 {d['minimum_unique_papers_top10']}.",
              f"- 한 논문에서 5개 이상: {d['same_paper_at_least_half_case_count']}/{d['case_count']}문항. 한 논문 최대 {d['maximum_chunks_from_one_paper']}청크.",
              "- 동일 section은 (paper_id, section) 쌍으로 집계했다. 서로 다른 논문의 Discussion을 하나로 합치지 않았다.",
              "- 집중도는 측정됐지만 같은 논문의 여러 근거가 필요한 질문일 수 있어 다양성 부족이나 답변 품질 저하로 단정하지 않는다.",
              "", "## 10. 후보 depth 제한", "", "- Dense 10 + BM25 10, RRF 입력은 최대 20개 순위 항목의 합집합, 최종 10개. 실제 고유 후보 수는 중복에 따라 작아진다.",
              f"- 11~50위에 유효 Gold가 있는 문항 {depth['cases_with_valid_gold_rank11_50']}개; depth10 합집합 밖의 근거 진입 기회를 잃은 required group {depth['lost_evidence_group_opportunity_count']}개.",
              f"- depth50 후보 합집합에서 새로 전체 충족 가능한 required group {depth['newly_fully_coverable_group_count']}개.",
              "- any/all 의미를 유지했다. 부분 근거 진입 기회와 group 전체 충족 기회를 구분했다. zero-score BM25 tie는 유효 발견으로 세지 않는다.",
              "- depth50으로 바꾸면 최종 RRF Top-10이 좋아진다는 의미가 아니다. 이번 진단에서 production depth는 변경하지 않았다.",
              "", "## 11. 실제 확인된 문제", "", "- 대다수 청크가 모델 한도를 넘어 자동 truncation된다.",
              "- zero-match BM25의 임의 tie 순위가 RRF 기여도를 받고 실제 최종 Top-10에 들어온다.",
              "- Phase A 초기 검색은 문헌 전용 질문 대신 전체 원문을 사용한다.",
              "- metadata는 본문 검색 표현에 포함되지 않으며 depth10 밖에 필요한 근거가 존재한다.",
              "", "## 12. 아직 추정인 문제와 한계", "", "- truncation이 전체 검색 실패 중 얼마를 설명하는지는 대조 실험이 필요하다.",
              "- 한국어 Dense의 영어 대비 열세, metadata 추가 효과, 문헌 다양성 제약의 이득, depth 확대의 최종 성능 이득은 이번에 검증하지 않았다.",
              "- 메모리 Dense는 같은 revision의 같은 가중치와 ST/Transformers/Torch 버전으로 실행했지만 Python/NumPy 환경은 다르다. 기존 18문항 Top-10 완전 일치를 먼저 확인했다. DB cosine 점수와 미세한 차이는 가능하다.",
              "- DB 인증 정보가 없어 기존 DB는 읽거나 수정하지 않았다. Corpus 벡터는 메모리에서만 계산하고 파일에 저장하지 않았다.",
              "", "## 13. Retrieval v2 변수 제안", "", "1. 모델과 질의를 고정하고 토큰 한도를 맞춘 청킹의 영향부터 별도 실험한다.",
              "2. 별도 실험으로 zero-match/zero-score BM25 처리만 변경한다.",
              "3. 문헌 질문 추출과 영어 질의 변환은 서로 다른 변수로 비교한다.",
              "4. 이후 metadata 포함, source depth, 문헌 다양성 제약, 모델 교체를 각각 독립 비교한다.",
              "- 개발용 평가와 최종 별도 평가를 분리한다. 기존 30문항으로 반복 튜닝하지 않는다. 이번 작업은 제안에서 종료한다.",
              "", "## 재현성과 보존", "", "- 시작/종료 protected_hashes_*.json은 corpus, eval/Gold, 모든 기존 baseline/manifest, config, src, 고정 모델 파일의 SHA-256을 비교한다.",
              "- 종료 기록의 all_unchanged와 changed_files를 확인한다. baseline manifest가 선언한 artifact hash도 재검증한다.",
              "- 과거 사전등록의 코드 hash와 현재 post-baseline 코드는 다를 수 있다. 이는 시작 시점부터 존재한 차이로 별도 historical_source_mismatches에 기록했다.",
              "- 세부 측정: metrics.json 및 각 JSONL. 실제 모델 tokenize 소스: model_tokenization_trace.json. 재현 검증: dense_reproduction.json.",
              "- 실행: `.build/retrieval-v1-diagnostic-env/Scripts/python.exe scripts/diagnose_retrieval_v1.py --dense-mode memory` (최초에는 --capture-only)."]
    (output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
