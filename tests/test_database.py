from pathlib import Path
import unittest

from src.database.loader import build_load_sql, load_inputs
from src.database.psql import sql_literal, validate_identifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DatabasePhase3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = load_inputs(PROJECT_ROOT)
        cls.migration = (
            PROJECT_ROOT / "db" / "migrations" / "001_training_schema.sql"
        ).read_text(encoding="utf-8")

    def test_load_inputs_match_validated_phase2_outputs(self) -> None:
        self.assertEqual(self.inputs.version, "preprocessing_v1")
        self.assertEqual(
            self.inputs.validation["reconciliation"]["processed_rows"], 8_228
        )
        self.assertEqual(
            self.inputs.validation["reconciliation"]["lineage_rows"], 9_932
        )

    def test_schema_preserves_provenance_and_known_collisions(self) -> None:
        self.assertIn("CREATE TABLE IF NOT EXISTS training.row_lineage", self.migration)
        self.assertIn("raw_row_count", self.migration)
        self.assertIn("set_order_collision_group_id", self.migration)
        self.assertNotIn("UNIQUE (session_id, exercise_id, set_order)", self.migration)

    def test_generated_load_is_transactional_and_reconciles_counts(self) -> None:
        sql = build_load_sql(self.inputs)
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertIn("COMMIT;", sql)
        self.assertIn("IF actual <> 8228", sql)
        self.assertIn("IF actual <> 9932", sql)
        self.assertIn("per-set lineage reconciliation failed", sql)
        self.assertIn("training schema already contains data", sql)

    def test_replace_mode_is_explicit(self) -> None:
        sql = build_load_sql(self.inputs, replace=True)
        self.assertIn("TRUNCATE TABLE", sql)
        self.assertIn("RESTART IDENTITY", sql)
        self.assertNotIn("training schema already contains data", sql)

    def test_sql_helpers_reject_unsafe_identifiers_and_escape_literals(self) -> None:
        self.assertEqual(validate_identifier("agentic_rag_app"), "agentic_rag_app")
        with self.assertRaises(ValueError):
            validate_identifier("app; DROP DATABASE postgres")
        self.assertEqual(sql_literal("don't"), "'don''t'")


if __name__ == "__main__":
    unittest.main()
