from pathlib import Path
import unittest

import pandas as pd

from src.preprocessing.clean_workouts import (
    build_preprocessed_views,
    load_raw_workouts,
    sha256_file,
)
from src.preprocessing.policy import (
    DEFAULT_ALIAS_PATH,
    DEFAULT_POLICY_PATH,
    load_alias_decisions,
    load_policy,
)
from src.preprocessing.validation import build_validation_summary


RAW_PATH = Path("data/raw/weightlifting_721_workouts.csv")


class PreprocessingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy(DEFAULT_POLICY_PATH)
        cls.alias_decisions = load_alias_decisions(DEFAULT_ALIAS_PATH)
        cls.source_hash = sha256_file(RAW_PATH)
        cls.raw = load_raw_workouts(RAW_PATH, cls.policy["expected_columns"])
        cls.processed, cls.lineage = build_preprocessed_views(
            cls.raw, cls.policy, cls.alias_decisions, cls.source_hash
        )

    def test_row_reconciliation(self) -> None:
        self.assertEqual(len(self.raw), 9_932)
        self.assertEqual(len(self.lineage), 9_932)
        self.assertEqual(len(self.processed), 8_228)
        self.assertEqual(int(self.lineage["is_exact_duplicate"].sum()), 790)
        self.assertEqual(int(self.lineage["is_alias_shadow"].sum()), 914)
        self.assertEqual(
            len(self.processed)
            + int(self.lineage["is_exact_duplicate"].sum())
            + int(self.lineage["is_alias_shadow"].sum()),
            len(self.raw),
        )

    def test_alias_shadow_maps_to_representative(self) -> None:
        generic = self.lineage.loc[self.lineage["source_row"].eq(3_015)].iloc[0]
        explicit = self.lineage.loc[self.lineage["source_row"].eq(3_031)].iloc[0]

        self.assertEqual(generic["row_status"], "kept")
        self.assertEqual(explicit["row_status"], "excluded_alias_shadow")
        self.assertEqual(generic["processed_set_id"], explicit["processed_set_id"])
        self.assertEqual(int(explicit["alias_shadow_of_source_row"]), 3_015)
        self.assertEqual(
            explicit["canonical_exercise_name"], "Incline Bench Press (Barbell)"
        )

    def test_deferred_aliases_remain_distinct(self) -> None:
        pairs = (
            self.processed.loc[
                self.processed["raw_exercise_name"].isin(["Leg press", "Hammer Curl"]),
                ["raw_exercise_name", "canonical_exercise_name"],
            ]
            .drop_duplicates()
            .set_index("raw_exercise_name")["canonical_exercise_name"]
            .to_dict()
        )

        self.assertEqual(pairs["Leg press"], "Leg press")
        self.assertEqual(pairs["Hammer Curl"], "Hammer Curl")

    def test_outlier_and_metric_eligibility_flags(self) -> None:
        squat_spike = self.processed.loc[
            self.processed["representative_source_row"].eq(4_815)
        ].iloc[0]
        high_rep = self.processed.loc[
            self.processed["representative_source_row"].eq(3_015)
        ].iloc[0]

        self.assertTrue(bool(squat_spike["is_outlier"]))
        self.assertEqual(squat_spike["outlier_reason"], "extreme_weight_ge_1000")
        self.assertFalse(bool(squat_spike["include_in_volume_metrics"]))
        self.assertFalse(bool(squat_spike["include_in_e1rm"]))
        self.assertTrue(bool(high_rep["is_outlier"]))
        self.assertTrue(bool(high_rep["include_in_volume_metrics"]))
        self.assertFalse(bool(high_rep["include_in_e1rm"]))

    def test_set_order_collisions_are_retained_and_flagged(self) -> None:
        collisions = self.processed.loc[self.processed["has_set_order_collision"]]

        self.assertEqual(len(collisions), 4)
        self.assertEqual(collisions["set_order_collision_group_id"].nunique(), 2)
        self.assertEqual(set(collisions["canonical_exercise_name"]), {"Good Morning (Barbell)"})

    def test_primary_exercise_session_counts_match_eda(self) -> None:
        expected = {
            "Squat (Barbell)": 173,
            "Incline Bench Press (Barbell)": 118,
            "Seated Shoulder Press (Barbell)": 106,
            "Bench Press (Barbell)": 55,
            "Deadlift (Barbell)": 33,
        }
        actual = (
            self.processed.loc[
                self.processed["canonical_exercise_name"].isin(expected)
            ]
            .groupby("canonical_exercise_name")["session_id"]
            .nunique()
            .to_dict()
        )

        self.assertEqual(actual, expected)

    def test_ids_are_deterministic(self) -> None:
        second_processed, second_lineage = build_preprocessed_views(
            self.raw, self.policy, self.alias_decisions, self.source_hash
        )

        pd.testing.assert_series_equal(
            self.processed["processed_set_id"],
            second_processed["processed_set_id"],
        )
        pd.testing.assert_series_equal(
            self.lineage["processed_set_id"], second_lineage["processed_set_id"]
        )
        pd.testing.assert_series_equal(
            self.lineage["row_status"], second_lineage["row_status"]
        )

    def test_validation_summary_passes(self) -> None:
        summary = build_validation_summary(
            raw=self.raw,
            processed=self.processed,
            lineage=self.lineage,
            policy=self.policy,
            alias_decisions=self.alias_decisions,
            source_sha256=self.source_hash,
        )

        self.assertTrue(summary["all_checks_passed"])
        self.assertTrue(all(summary["checks"].values()))


if __name__ == "__main__":
    unittest.main()
