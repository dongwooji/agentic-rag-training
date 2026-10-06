import pytest
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from scripts.eda_workouts import (
    EXPECTED_COLUMNS,
    calculate_e1rm,
    detect_plateau_candidates,
    exact_deduplicated_view,
    load_workouts,
    normalize_exercise_name,
)


RAW_PATH = Path("data/raw/weightlifting_721_workouts.csv")


class EdaWorkoutsTest(unittest.TestCase):
    @pytest.mark.requires_local_artifacts
    def test_source_dataset_contract(self) -> None:
        frame = load_workouts(RAW_PATH)

        self.assertEqual(list(frame.columns[1:11]), EXPECTED_COLUMNS)
        self.assertEqual(len(frame), 9_932)
        self.assertTrue(frame["date_ts"].notna().all())
        self.assertEqual(frame["date_ts"].nunique(), 583)
        self.assertEqual(frame["date_ts"].dt.normalize().nunique(), 570)
        self.assertEqual(frame["Exercise Name"].nunique(), 83)
        self.assertEqual(
            int(frame.duplicated(subset=EXPECTED_COLUMNS, keep="first").sum()), 790
        )
        self.assertEqual(len(exact_deduplicated_view(frame)), 9_142)

    def test_epley_calculation(self) -> None:
        result = calculate_e1rm(pd.Series([100.0, 120.0]), pd.Series([0, 5]))
        np.testing.assert_allclose(result.to_numpy(), [100.0, 140.0])

    def test_name_normalization_handles_case_and_spacing(self) -> None:
        self.assertEqual(
            normalize_exercise_name(" Bicep Curl (barbell ) "),
            normalize_exercise_name("bicep   curl (BARBELL)"),
        )

    def test_plateau_detector_distinguishes_flat_from_rising_series(self) -> None:
        dates = pd.date_range("2026-01-01", periods=10, freq="7D")
        flat = pd.DataFrame(
            {
                "date_ts": dates,
                "exercise_name": "Flat Lift",
                "session_best_e1rm": [100, 101, 100, 101, 100, 100.5, 101, 100, 101, 100],
            }
        )
        rising = pd.DataFrame(
            {
                "date_ts": dates,
                "exercise_name": "Rising Lift",
                "session_best_e1rm": np.linspace(100, 125, 10),
            }
        )

        result = detect_plateau_candidates(pd.concat([flat, rising], ignore_index=True))

        self.assertIn("Flat Lift", set(result["exercise_name"]))
        self.assertNotIn("Rising Lift", set(result["exercise_name"]))


if __name__ == "__main__":
    unittest.main()
