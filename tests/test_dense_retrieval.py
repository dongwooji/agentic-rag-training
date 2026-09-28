from __future__ import annotations

import inspect
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.retrieval.baseline import (
    evaluate_dense_retrieval,
    load_frozen_retrieval_inputs,
    retrieve_frozen_questions,
    write_immutable_baseline_artifacts,
)
from src.retrieval.dense import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    EXPECTED_EMBEDDING_DIMENSION,
)
from src.retrieval.postgres import SearchHit, SearchResponse, vector_literal


ROOT = Path(__file__).resolve().parents[1]


class _FakeEncoder:
    def encode(self, texts: list[str], *, show_progress: bool = False) -> np.ndarray:
        vectors = np.zeros((len(texts), EXPECTED_EMBEDDING_DIMENSION), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors


class _SequentialStore:
    def __init__(self, rankings: list[list[str]]) -> None:
        self.rankings = rankings
        self.calls = 0

    def search_exact_cosine(
        self, query_embedding: np.ndarray, *, embedding_run_id: str, top_k: int
    ) -> SearchResponse:
        ranking = self.rankings[self.calls]
        self.calls += 1
        return SearchResponse(
            hits=[
                SearchHit(chunk_id=chunk_id, score=1.0 - rank / 100.0)
                for rank, chunk_id in enumerate(ranking[:top_k], start=1)
            ],
            database_search_ms=0.25,
        )


class DenseRetrievalBaselineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_frozen_retrieval_inputs(ROOT)

    def test_frozen_inputs_have_expected_versions_hashes_and_scope(self) -> None:
        self.assertEqual(self.inputs.eval_dataset_version, "eval_dataset_v1")
        self.assertEqual(self.inputs.corpus_version, "literature_corpus_v1")
        self.assertEqual(
            self.inputs.eval_dataset_sha256,
            "1b636b58612fa424dd3973dd753a1d602f611d94d591e9145b3977aff622e509",
        )
        self.assertEqual(
            self.inputs.corpus_chunks_sha256,
            "640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177",
        )
        self.assertEqual(len(self.inputs.chunks), 488)
        self.assertEqual(len(self.inputs.cases), 18)
        self.inputs.assert_unchanged()

    def test_model_is_pinned_multilingual_and_384_dimensions(self) -> None:
        self.assertEqual(
            DEFAULT_MODEL_ID,
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        self.assertRegex(DEFAULT_MODEL_REVISION, r"^[0-9a-f]{40}$")
        self.assertEqual(EXPECTED_EMBEDDING_DIMENSION, 384)

    def test_retrieval_boundary_accepts_no_gold_argument(self) -> None:
        parameters = inspect.signature(retrieve_frozen_questions).parameters
        self.assertNotIn("cases", parameters)
        self.assertNotIn("gold", parameters)

    def test_ranking_then_frozen_group_evaluation(self) -> None:
        cases = self.inputs.cases[:2]
        rankings: list[list[str]] = []
        for case in cases:
            gold = [
                chunk_id
                for group in case["gold"]["literature_evidence_groups"]
                if group["required"]
                for chunk_id in group["chunk_ids"]
            ]
            fillers = [
                chunk["chunk_id"]
                for chunk in self.inputs.chunks
                if chunk["chunk_id"] not in gold
            ]
            rankings.append((gold + fillers)[:10])
        records = [
            {"case_id": case["id"], "question": case["question"]} for case in cases
        ]
        raw = retrieve_frozen_questions(
            records,
            encoder=_FakeEncoder(),
            store=_SequentialStore(rankings),
            top_k=10,
        )
        metrics, results = evaluate_dense_retrieval(cases, raw)
        self.assertEqual(metrics["case_count"], 2)
        self.assertIn("evidence_group_recall@5", metrics["macro"])
        self.assertIn("complete_evidence@10", metrics["macro"])
        self.assertIn("chunk_recall@10", metrics["macro"])
        self.assertIn("mrr", metrics["macro"])
        self.assertEqual(len(results[0]["retrieved"]), 10)
        self.assertEqual(results[0]["retrieved"][0]["rank"], 1)

    def test_vector_literal_rejects_wrong_dimension_and_nonfinite(self) -> None:
        with self.assertRaises(ValueError):
            vector_literal(np.zeros(3, dtype=np.float32))
        invalid = np.zeros(EXPECTED_EMBEDDING_DIMENSION, dtype=np.float32)
        invalid[0] = np.nan
        with self.assertRaises(ValueError):
            vector_literal(invalid)

    def test_phase6_sql_is_exact_cosine_only(self) -> None:
        schema = (ROOT / "db/phase6/002_dense_retrieval_schema.sql").read_text(
            encoding="utf-8"
        ).lower()
        store_source = (ROOT / "src/retrieval/postgres.py").read_text(
            encoding="utf-8"
        ).lower()
        self.assertIn("vector(384)", schema)
        self.assertNotIn("hnsw", schema)
        self.assertNotIn("ivfflat", schema)
        self.assertIn("<=>", store_source)
        self.assertNotIn("bm25", store_source)
        self.assertNotIn("rrf", store_source)

    def test_immutable_output_refuses_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            existing = Path(temp_dir) / "dense_baseline_v1"
            existing.mkdir()
            marker = existing / "marker.txt"
            marker.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_immutable_baseline_artifacts(
                    output_dir=existing,
                    metrics={},
                    results=[],
                    reproducibility={},
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()

