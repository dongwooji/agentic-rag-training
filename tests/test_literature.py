import pytest
from pathlib import Path
import json
import unittest

from src.literature.database import (
    build_literature_load_sql,
    load_literature_inputs,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class LiteratureCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (PROJECT_ROOT / "config" / "literature_corpus_v1.json").read_text(
                encoding="utf-8"
            )
        )
        cls.selection = json.loads(
            (PROJECT_ROOT / "config" / "literature_selection_v1.json").read_text(
                encoding="utf-8"
            )
        )
        cls.validation = json.loads(
            (PROJECT_ROOT / "reports" / "literature_corpus_validation.json").read_text(
                encoding="utf-8"
            )
        )
        cls.papers = read_jsonl(
            PROJECT_ROOT / "data" / "literature" / "processed" / "papers.jsonl"
        )
        cls.chunks = read_jsonl(
            PROJECT_ROOT / "data" / "literature" / "processed" / "chunks.jsonl"
        )

    @pytest.mark.requires_local_artifacts
    def test_selection_and_corpus_counts(self) -> None:
        self.assertEqual(len(self.selection["selected"]), 22)
        self.assertEqual(len(self.papers), 22)
        self.assertEqual(len(self.chunks), 488)
        self.assertTrue(self.validation["all_checks_passed"])
        self.assertTrue(all(self.validation["checks"].values()))

    @pytest.mark.requires_local_artifacts
    def test_every_paper_has_traceable_source_and_license(self) -> None:
        for paper in self.papers:
            self.assertTrue(paper["pmid"])
            self.assertTrue(paper["pmcid"].startswith("PMC"))
            self.assertTrue(paper["doi"])
            self.assertTrue(paper["source_url"].startswith("https://pmc.ncbi.nlm.nih.gov/"))
            self.assertTrue(paper["license"])
            self.assertEqual(len(paper["source_sha256"]), 64)

    @pytest.mark.requires_local_artifacts
    def test_chunks_preserve_section_and_provenance(self) -> None:
        paper_ids = {paper["paper_id"] for paper in self.papers}
        for chunk in self.chunks:
            self.assertIn(chunk["paper_id"], paper_ids)
            self.assertTrue(chunk["section"])
            self.assertGreater(chunk["word_count"], 0)
            self.assertLessEqual(chunk["word_count"], 610)
            self.assertEqual(len(chunk["text_sha256"]), 64)
            self.assertNotIn("<table-wrap", chunk["text"])

    @pytest.mark.requires_local_artifacts
    def test_topic_coverage_matches_eda_questions(self) -> None:
        topics = {topic for paper in self.papers for topic in paper["topics"]}
        expected = {
            "training_volume",
            "training_frequency",
            "strength_adaptation",
            "detraining",
            "training_to_failure",
            "periodization_progression",
            "progressive_overload",
        }
        self.assertTrue(expected.issubset(topics))

    @pytest.mark.requires_local_artifacts
    def test_candidate_screen_records_inclusions_and_api_failures(self) -> None:
        screened = json.loads(
            (
                PROJECT_ROOT
                / "data"
                / "literature"
                / "manifests"
                / "screened_candidates_v1.json"
            ).read_text(encoding="utf-8")
        )
        included = [
            row for row in screened["candidates"] if row["selection_status"] == "included"
        ]
        self.assertEqual(len(included), 22)
        failed_sources = {
            row["pmcid"]: row["selection_reason"]
            for row in screened["candidates"]
            if row.get("pmcid") in {"PMC5005843", "PMC13377779"}
        }
        self.assertEqual(set(failed_sources), {"PMC5005843", "PMC13377779"})
        self.assertTrue(
            all("could not be acquired" in reason for reason in failed_sources.values())
        )

    @pytest.mark.requires_local_artifacts
    def test_phase4_does_not_add_embeddings_or_retrieval_tuning(self) -> None:
        migration = (
            PROJECT_ROOT / "db" / "migrations" / "002_literature_schema.sql"
        ).read_text(encoding="utf-8").casefold()
        self.assertNotIn("vector(", migration)
        self.assertNotIn("create extension vector", migration)
        self.assertNotIn("embedding vector", migration)
        self.assertFalse(self.config["selection_policy"]["use_retrieval_outputs"])

    @pytest.mark.requires_local_artifacts
    def test_database_load_sql_is_transactional_and_validated(self) -> None:
        inputs = load_literature_inputs(PROJECT_ROOT)
        sql = build_literature_load_sql(inputs)
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertIn("COPY staging_literature_papers", sql)
        self.assertIn("IF actual <> 22", sql)
        self.assertIn("IF actual <> 488", sql)
        self.assertIn("COMMIT;", sql)
        self.assertIn("already contains data", sql)


if __name__ == "__main__":
    unittest.main()
