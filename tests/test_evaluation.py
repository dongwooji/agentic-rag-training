from __future__ import annotations

import pytest
import json
import subprocess
import sys
import unittest
from pathlib import Path

from src.evaluation.reference import compute_reference
from src.evaluation.validation import EXPECTED_DISTRIBUTION, sha256_file, validate_dataset


ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "data/evaluation/eval_dataset_v1_draft.json"
REVIEW = ROOT / "data/evaluation/human_review_v1.json"
FROZEN = ROOT / "data/evaluation/eval_dataset_v1.json"
MANIFEST = ROOT / "data/evaluation/eval_dataset_v1.manifest.json"


class EvaluationSetPhase5Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = json.loads(DRAFT.read_text(encoding="utf-8"))
        cls.validation = validate_dataset(DRAFT, ROOT)

    @pytest.mark.requires_local_artifacts
    def test_distribution_and_automated_validation(self) -> None:
        self.assertTrue(self.validation["all_automated_checks_passed"])
        self.assertEqual(self.validation["case_count"], 30)
        self.assertEqual(self.validation["category_counts"], EXPECTED_DISTRIBUTION)
        self.assertEqual(self.validation["structured_reference_check_count"], 15)
        self.assertEqual(self.validation["evidence_group_count"], 44)
        self.assertEqual(self.validation["required_evidence_group_count"], 43)
        self.assertEqual(self.validation["optional_evidence_group_count"], 1)
        self.assertEqual(self.validation["group_match_counts"], {"any": 43, "all": 1})
        self.assertTrue(self.validation["checks"]["complete_evidence_at_5_feasible"])
        self.assertEqual(
            self.validation["complete_evidence_at_5"][
                "maximum_minimum_required_distinct_chunks"
            ],
            2,
        )
        self.assertEqual(self.validation["complete_evidence_at_5"]["cases_over_5"], {})

    @pytest.mark.requires_local_artifacts
    def test_eval_was_created_without_retrieval_outputs(self) -> None:
        self.assertTrue(self.dataset["protocol"]["created_before_retrieval_baseline"])
        self.assertFalse(self.dataset["protocol"]["retrieval_outputs_used_for_labeling"])
        self.assertTrue(self.validation["checks"]["no_retrieval_output_leakage"])

    @pytest.mark.requires_local_artifacts
    def test_gold_chunks_and_source_hashes_are_stable(self) -> None:
        self.assertTrue(self.validation["checks"]["gold_chunk_ids_exist"])
        self.assertTrue(self.validation["checks"]["claim_evidence_groups_valid"])
        self.assertTrue(self.validation["checks"]["criteria_and_limitations_mapped"])
        self.assertTrue(self.validation["checks"]["source_hashes_match"])
        self.assertEqual(self.validation["literature_query_count"], 18)
        self.assertGreaterEqual(self.validation["unique_gold_chunk_count"], 20)

    @pytest.mark.requires_local_artifacts
    def test_unanswerable_cases_require_abstention_without_tools(self) -> None:
        cases = [
            case for case in self.dataset["cases"] if case["category"] == "unanswerable"
        ]
        self.assertEqual(len(cases), 6)
        for case in cases:
            self.assertEqual(case["expected_behavior"], "abstain")
            self.assertEqual(case["required_tools"], [])
            self.assertTrue(case["gold"]["missing_fields"])

    @pytest.mark.requires_local_artifacts
    def test_representative_reference_metrics(self) -> None:
        gap = compute_reference("longest_training_gap", {})
        self.assertAlmostEqual(gap["gap_days"], 41.73512731481481)
        shoulder = compute_reference(
            "e1rm_window_summary",
            {"exercise": "Seated Shoulder Press (Barbell)", "window_sessions": 5},
        )
        self.assertAlmostEqual(shoulder["change_pct"], 39.393939393939384)

    @pytest.mark.requires_local_artifacts
    def test_human_review_is_approved_for_the_current_draft(self) -> None:
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
        self.assertEqual(review["status"], "approved")
        self.assertEqual(review["dataset_sha256"], sha256_file(DRAFT))
        self.assertEqual(len(review["cases"]), 30)
        for item in review["cases"]:
            self.assertEqual(item["status"], "approved")
            self.assertTrue(item["question_and_category_correct"])
            self.assertTrue(item["evidence_correct"])
            self.assertTrue(item["limitations_sufficient"])
        self.assertEqual(self.validation["human_review"]["approved"], 30)
        self.assertEqual(self.validation["human_review"]["pending"], 0)
        self.assertTrue(self.validation["checks"]["human_review_approved"])
        self.assertTrue(self.validation["ready_to_freeze"])

    @pytest.mark.requires_local_artifacts
    def test_frozen_dataset_and_manifest_match_approved_draft(self) -> None:
        self.assertTrue(FROZEN.exists())
        self.assertTrue(MANIFEST.exists())
        frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(manifest["status"], "frozen")
        self.assertEqual(manifest["draft_sha256"], sha256_file(DRAFT))
        self.assertEqual(manifest["dataset_sha256"], sha256_file(FROZEN))
        self.assertEqual(manifest["review_sha256"], sha256_file(REVIEW))
        self.assertEqual(frozen["frozen_at"], review["reviewed_at"])
        self.assertTrue(
            all(case["review"]["human_status"] == "approved" for case in frozen["cases"])
        )
        for draft_case, frozen_case in zip(self.dataset["cases"], frozen["cases"]):
            self.assertEqual(draft_case["id"], frozen_case["id"])
            self.assertEqual(draft_case["gold"], frozen_case["gold"])

    @pytest.mark.requires_local_artifacts
    def test_freeze_command_refuses_without_confirmation(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/freeze_evaluation_set.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--confirm-human-review", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
