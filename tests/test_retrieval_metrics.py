from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.retrieval_metrics import (
    chunk_recall_at_k,
    complete_evidence_at_k,
    evaluate_retrieval,
    evidence_group_recall_at_k,
    reciprocal_rank,
)
from src.evaluation.validation import validate_dataset


ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "data/evaluation/eval_dataset_v1_draft.json"


class ClaimEvidenceMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dataset = json.loads(DRAFT.read_text(encoding="utf-8"))
        cls.cases = {case["id"]: case for case in dataset["cases"]}

    def test_any_groups_and_all_required_semantics(self) -> None:
        case = self.cases["LIT-001"]
        conclusion = "paper_pmc6081873_conclusions_001_f2473a22"
        abstract = "paper_pmc6081873_abstract_001_b783fdd6"
        ranked = ["not_gold", conclusion, abstract]
        self.assertAlmostEqual(evidence_group_recall_at_k(case, ranked, 2), 2 / 3)
        self.assertEqual(complete_evidence_at_k(case, ranked, 2), 0.0)
        self.assertEqual(evidence_group_recall_at_k(case, ranked, 3), 1.0)
        self.assertEqual(complete_evidence_at_k(case, ranked, 3), 1.0)

    def test_match_all_group_requires_every_chunk(self) -> None:
        case = self.cases["LIT-010"]
        definition = (
            "paper_pmc13236796_definitional_inconsistencies_long_term_progressi_001_c16adcc6"
        )
        individualization = "paper_pmc12965823_discussion_002_daf8292e"
        self.assertEqual(
            evidence_group_recall_at_k(case, [definition], 1),
            2 / 3,
        )
        self.assertEqual(complete_evidence_at_k(case, [definition], 1), 0.0)
        self.assertEqual(
            evidence_group_recall_at_k(case, [definition, individualization], 2),
            1.0,
        )
        self.assertEqual(
            complete_evidence_at_k(case, [definition, individualization], 2), 1.0
        )

    def test_optional_groups_do_not_block_complete_evidence(self) -> None:
        case = self.cases["HYB-001"]
        required = "paper_pmc12965823_abstract_001_8343007d"
        self.assertEqual(evidence_group_recall_at_k(case, [required], 1), 1.0)
        self.assertEqual(complete_evidence_at_k(case, [required], 1), 1.0)

    def test_chunk_recall_and_mrr_are_retained(self) -> None:
        case = self.cases["LIT-001"]
        conclusion = "paper_pmc6081873_conclusions_001_f2473a22"
        abstract = "paper_pmc6081873_abstract_001_b783fdd6"
        ranked = ["not_gold", conclusion, abstract]
        self.assertAlmostEqual(chunk_recall_at_k(case, ranked, 2), 1 / 3)
        self.assertAlmostEqual(chunk_recall_at_k(case, ranked, 3), 2 / 3)
        self.assertEqual(reciprocal_rank(case, ranked), 0.5)

    def test_macro_evaluator_reports_all_metrics(self) -> None:
        cases = [self.cases["LIT-001"], self.cases["HYB-001"]]
        results = {
            "LIT-001": [
                "paper_pmc6081873_conclusions_001_f2473a22",
                "paper_pmc6081873_abstract_001_b783fdd6",
            ],
            "HYB-001": ["paper_pmc12965823_abstract_001_8343007d"],
        }
        metrics = evaluate_retrieval(cases, results, ks=(1, 2))
        self.assertEqual(metrics["case_count"], 2)
        self.assertIn("evidence_group_recall@1", metrics["macro"])
        self.assertIn("complete_evidence@2", metrics["macro"])
        self.assertIn("chunk_recall@2", metrics["macro"])
        self.assertIn("mrr", metrics["macro"])

    def test_validator_rejects_bad_group_semantics_and_mapping(self) -> None:
        dataset = json.loads(DRAFT.read_text(encoding="utf-8"))
        broken = copy.deepcopy(dataset)
        case = next(item for item in broken["cases"] if item["id"] == "LIT-001")
        case["gold"]["literature_evidence_groups"][0]["match"] = "sometimes"
        case["gold"]["answer_criteria"][0]["literature_evidence_group_ids"] = [
            "EG-DOES-NOT-EXIST"
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "broken.json"
            path.write_text(
                json.dumps(broken, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = validate_dataset(path, ROOT)
        self.assertFalse(result["all_automated_checks_passed"])
        self.assertFalse(result["checks"]["claim_evidence_groups_valid"])
        self.assertFalse(result["checks"]["criteria_and_limitations_mapped"])


if __name__ == "__main__":
    unittest.main()
