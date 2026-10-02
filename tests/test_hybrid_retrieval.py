from __future__ import annotations

import unittest

from src.retrieval.bm25 import (
    BM25Index,
    DEFAULT_B,
    DEFAULT_K1,
    TOKENIZER_VERSION,
    tokenize,
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


if __name__ == "__main__":
    unittest.main()

