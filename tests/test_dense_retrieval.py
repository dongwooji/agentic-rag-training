from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np

from src.retrieval.dense import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    EXPECTED_EMBEDDING_DIMENSION,
)
from src.retrieval.postgres import vector_literal


ROOT = Path(__file__).resolve().parents[1]


class DenseRetrievalRuntimeTest(unittest.TestCase):
    def test_model_is_pinned_multilingual_and_384_dimensions(self) -> None:
        self.assertEqual(
            DEFAULT_MODEL_ID,
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        self.assertRegex(DEFAULT_MODEL_REVISION, r"^[0-9a-f]{40}$")
        self.assertEqual(EXPECTED_EMBEDDING_DIMENSION, 384)

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


if __name__ == "__main__":
    unittest.main()

