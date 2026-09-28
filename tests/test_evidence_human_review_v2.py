from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from scripts.build_evaluation_evidence_human_review import (
    CASE_IDS,
    load_v2_annotations,
    minimum_required_gold_chunks,
    sha256,
)
from scripts.migrate_evidence_groups import MAPPINGS


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/evaluation/eval_dataset_v1_draft.json"
CHUNKS = ROOT / "data/literature/processed/chunks.jsonl"
OVERRIDES = ROOT / "reports/evaluation_evidence_human_review_v2_overrides.json"
REPORT = ROOT / "reports/EVALUATION_EVIDENCE_HUMAN_REVIEW_V2.md"


class EvidenceHumanReviewV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dataset = json.loads(DATASET.read_text(encoding="utf-8"))
        cls.cases = {case["id"]: case for case in dataset["cases"]}
        cls.annotations, cls.overrides = load_v2_annotations()
        cls.groups = {
            group["id"]: group
            for case_id in CASE_IDS
            for group in cls.cases[case_id]["gold"]["literature_evidence_groups"]
        }

    def test_hashes_and_requested_scope_are_fixed(self) -> None:
        self.assertEqual(
            self.overrides["dataset_before_sha256"],
            "fab928ecd1077be9da7b738711e4c1b096fe77e88b82a9fd1d39a3cd1b355464",
        )
        self.assertEqual(sha256(DATASET), self.overrides["dataset_after_sha256"])
        self.assertEqual(sha256(CHUNKS), self.overrides["corpus_sha256"])
        self.assertEqual(len(self.overrides["modified_group_ids"]), 9)
        self.assertEqual(
            set(self.overrides["removed_group_ids"]),
            {"EG-LIT-010-03", "EG-HYB-002-02"},
        )

    def test_revised_group_shapes(self) -> None:
        expected_single_gold = {
            "EG-LIT-004-03": "paper_pmc9935748_conclusions_001_d77d57c2",
            "EG-LIT-006-04": "paper_pmc7994759_conclusions_001_60ab1548",
            "EG-LIT-007-02": "paper_pmc10511399_discussion_integrating_deloading_into_the_streng_001_834932bd",
            "EG-LIT-008-05": "paper_pmc11705206_abstract_001_2277be05",
            "EG-LIT-009-02": "paper_pmc6692867_abstract_001_b33f7355",
            "EG-HYB-004-01": "paper_pmc9068575_abstract_002_8c9ac68c",
            "EG-HYB-007-01": "paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6",
        }
        for group_id, chunk_id in expected_single_gold.items():
            group = self.groups[group_id]
            self.assertEqual(group["match"], "any")
            self.assertTrue(group["required"])
            self.assertEqual(group["chunk_ids"], [chunk_id])
        self.assertNotIn("EG-LIT-010-03", self.groups)
        self.assertNotIn("EG-HYB-002-02", self.groups)

    def test_previously_approved_group_definitions_are_unchanged(self) -> None:
        modified = set(self.overrides["modified_group_ids"])
        for case_id, mapping in MAPPINGS.items():
            current_groups = {
                group["id"]: group
                for group in self.cases[case_id]["gold"]["literature_evidence_groups"]
            }
            for index, original in enumerate(mapping["groups"], start=1):
                group_id = f"EG-{case_id}-{index:02d}"
                if group_id in modified:
                    continue
                current = current_groups[group_id]
                for field in ("claim", "required", "match", "chunk_ids"):
                    self.assertEqual(current[field], original[field], f"{group_id}/{field}")

    def test_every_any_assignment_is_independently_supported(self) -> None:
        self.assertEqual(set(self.annotations["groups"]), set(self.groups))
        for group_id, group in self.groups.items():
            review = self.annotations["groups"][group_id]
            self.assertEqual(set(review["chunks"]), set(group["chunk_ids"]))
            if group["match"] == "any":
                for chunk_review in review["chunks"].values():
                    self.assertEqual(chunk_review["rating"], "SUPPORTED", group_id)

    def test_all_group_has_complementary_required_roles(self) -> None:
        all_groups = [group for group in self.groups.values() if group["match"] == "all"]
        self.assertEqual([group["id"] for group in all_groups], ["EG-LIT-010-02"])
        review = self.annotations["groups"]["EG-LIT-010-02"]
        roles = [item["role"] for item in review["chunks"].values()]
        self.assertEqual(len(roles), len(set(roles)))
        self.assertTrue(review["match_all_analysis"]["why_all"])
        self.assertIn("어느 한 chunk도", review["match_all_analysis"]["single_chunk_sufficiency"])

    def test_no_unsupported_gold_assignments_remain(self) -> None:
        ratings = Counter(
            chunk_review["rating"]
            for review in self.annotations["groups"].values()
            for chunk_review in review["chunks"].values()
        )
        decisions = Counter(
            review["suggested_decision"]
            for review in self.annotations["groups"].values()
        )
        self.assertEqual(ratings, {"SUPPORTED": 61, "PARTIAL": 2})
        self.assertEqual(decisions, {"APPROVE": 44})

    def test_complete_evidence_at_5_is_feasible_for_every_case(self) -> None:
        minimums = {
            case_id: minimum_required_gold_chunks(self.cases[case_id])
            for case_id in CASE_IDS
        }
        self.assertLessEqual(max(minimums.values()), 5)
        self.assertEqual(max(minimums.values()), 2)

    def test_v2_report_records_final_human_approval(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        self.assertIn("Human Review V2", report)
        self.assertIn("Final human review approved", report)
        self.assertIn("Final human approval is recorded for all 30 cases", report)
        self.assertIn("- [x] APPROVE", report)


if __name__ == "__main__":
    unittest.main()
