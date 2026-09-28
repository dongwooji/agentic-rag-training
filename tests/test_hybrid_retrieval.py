from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.retrieval.bm25 import (
    BM25Index,
    DEFAULT_B,
    DEFAULT_K1,
    TOKENIZER_VERSION,
    tokenize,
)
from src.retrieval.hybrid_baseline import (
    PHASE7_VERSION,
    build_comparison,
    run_bm25_retrieval,
    run_rrf_fusion,
    write_immutable_phase7_artifacts,
)
from src.retrieval.rrf import DEFAULT_RRF_K, reciprocal_rank_fusion


class BM25Test(unittest.TestCase):
    def test_tokenizer_splits_latin_hangul_boundaries_and_hyphens(self) -> None:
        self.assertEqual(
            tokenize("Progressive overload이 proximity-to-failure와 RPE-based"),
            ["progressive", "overload", "이", "proximity", "to", "failure", "와", "rpe", "based"],
        )
        self.assertEqual(TOKENIZER_VERSION, "script_aware_unicode_alnum_casefold_v1")

    def test_bm25_prefers_exact_matching_document(self) -> None:
        documents = [
            {"chunk_id": "a", "text": "periodization strength training"},
            {"chunk_id": "b", "text": "hypertrophy volume muscle"},
            {"chunk_id": "c", "text": "detraining residual effects"},
        ]
        index = BM25Index(documents)
        response = index.search("periodization이 strength에 미치는 영향", top_k=3)
        self.assertEqual(response.hits[0].chunk_id, "a")
        self.assertGreater(response.hits[0].score, 0)
        self.assertEqual(response.matched_query_terms, ["periodization", "strength"])
        self.assertEqual(index.metadata["k1"], DEFAULT_K1)
        self.assertEqual(index.metadata["b"], DEFAULT_B)

    def test_zero_score_ties_are_deterministic_by_chunk_id(self) -> None:
        index = BM25Index(
            [
                {"chunk_id": "z", "text": "alpha"},
                {"chunk_id": "a", "text": "beta"},
                {"chunk_id": "m", "text": "gamma"},
            ]
        )
        response = index.search("없는질문", top_k=3)
        self.assertEqual([hit.chunk_id for hit in response.hits], ["a", "m", "z"])
        self.assertTrue(all(hit.score == 0 for hit in response.hits))


class RRFTest(unittest.TestCase):
    def test_equal_weight_rrf_formula_and_overlap(self) -> None:
        result = reciprocal_rank_fusion(
            ["dense_only", "shared", "d3"],
            ["bm25_only", "shared", "b3"],
            rrf_k=DEFAULT_RRF_K,
            top_k=5,
        )
        self.assertEqual(result.hits[0].chunk_id, "shared")
        self.assertAlmostEqual(result.hits[0].score, 2 / (DEFAULT_RRF_K + 2))
        dense_only = next(hit for hit in result.hits if hit.chunk_id == "dense_only")
        self.assertEqual(dense_only.dense_rank, 1)
        self.assertIsNone(dense_only.bm25_rank)
        self.assertAlmostEqual(dense_only.score, 1 / (DEFAULT_RRF_K + 1))

    def test_rrf_rejects_duplicate_input_ids(self) -> None:
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion(["a", "a"], ["b"], top_k=1)


class HybridComparisonTest(unittest.TestCase):
    @staticmethod
    def _result(case_id: str, question: str, ranking: list[str]) -> dict:
        return {
            "case_id": case_id,
            "question": question,
            "retrieved": [
                {"rank": rank, "chunk_id": chunk_id, "score": 1.0 / rank}
                for rank, chunk_id in enumerate(ranking, 1)
            ],
            "latency_ms": {"end_to_end": 1.0, "bm25_search": 0.2, "rrf_fusion": 0.1},
        }

    def test_any_all_group_rank_movement_is_preserved(self) -> None:
        case = {
            "id": "CASE-1",
            "category": "literature_only",
            "question": "fixed query",
            "gold": {
                "literature_evidence_groups": [
                    {
                        "id": "EG-ANY",
                        "claim": "alternative evidence",
                        "required": True,
                        "match": "any",
                        "chunk_ids": ["a1", "a2"],
                    },
                    {
                        "id": "EG-ALL",
                        "claim": "joint evidence",
                        "required": True,
                        "match": "all",
                        "chunk_ids": ["b1", "b2"],
                    },
                ]
            },
        }
        dense = self._result("CASE-1", "fixed query", ["a1", "b1", "x"])
        bm25 = self._result("CASE-1", "fixed query", ["a2", "b2", "b1"])
        hybrid = self._result("CASE-1", "fixed query", ["a1", "b1", "b2"])
        comparison, cases = build_comparison(
            cases=[case],
            dense_results=[dense],
            bm25_results=[bm25],
            hybrid_results=[hybrid],
        )
        groups = {item["evidence_group_id"]: item for item in cases[0]["evidence_group_rank_movement"]}
        self.assertEqual(groups["EG-ANY"]["completion_rank"], {"dense": 1, "bm25": 1, "hybrid": 1})
        self.assertEqual(groups["EG-ALL"]["completion_rank"], {"dense": None, "bm25": 3, "hybrid": 3})
        self.assertEqual(comparison["metrics"]["dense"]["macro"]["complete_evidence@5"], 0.0)
        self.assertEqual(comparison["metrics"]["hybrid"]["macro"]["complete_evidence@5"], 1.0)

    def test_ranking_functions_have_no_gold_input(self) -> None:
        self.assertNotIn("gold", run_bm25_retrieval.__annotations__)
        self.assertNotIn("gold", run_rrf_fusion.__annotations__)

    def test_immutable_writer_refuses_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / PHASE7_VERSION
            target.mkdir()
            marker = target / "preserve.txt"
            marker.write_text("yes", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_immutable_phase7_artifacts(
                    output_dir=target,
                    index_metadata={},
                    bm25_results=[],
                    hybrid_results=[],
                    comparison={},
                    case_comparisons=[],
                    reproducibility={},
                    report="",
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "yes")


if __name__ == "__main__":
    unittest.main()

