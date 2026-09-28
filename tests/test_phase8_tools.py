from __future__ import annotations

from datetime import datetime, timedelta
import shutil
import tempfile
from pathlib import Path
import unittest

import numpy as np

from src.retrieval.hybrid import (
    FROZEN_CORPUS_CHUNKS_SHA256,
    FROZEN_PHASE7_MANIFEST_SHA256,
    FrozenHybridRetriever,
    load_frozen_literature_assets,
)
from src.retrieval.postgres import SearchHit, SearchResponse
from src.tools.contracts import ToolErrorCode, ToolStatus
from src.tools.literature import LiteratureInput, LiteratureOperation, LiteratureTool
from src.tools.metric import MetricInput, MetricOperation, MetricTool
from src.tools.training_log import (
    TrainingLogInput,
    TrainingLogOperation,
    TrainingLogTool,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeTrainingRepository:
    def __init__(self) -> None:
        self.names = {"Bench Press (Barbell)"}
        self.records = [
            {
                "set_id": "set-1",
                "session_id": "session-1",
                "started_at": datetime(2024, 1, 3, 10),
                "workout_name": "Push",
                "exercise_name": "Bench Press (Barbell)",
                "set_order": 1,
                "weight": 100.0,
                "weight_unit": "unknown_source_unit",
                "reps": 5,
                "is_outlier": False,
                "outlier_reason": None,
                "include_in_volume_metrics": True,
                "include_in_e1rm": True,
                "e1rm_formula_version": "epley_v1",
                "representative_source_row": 10,
                "source_file_sha256": "a" * 64,
                "preprocessing_version": "preprocessing_v1",
                "lineage": [
                    {
                        "source_row": 10,
                        "row_status": "kept",
                        "raw_record_id": "raw-10",
                    }
                ],
            }
        ]

    def resolve_canonical_exercise(self, name: str) -> str | None:
        return name if name in self.names else None

    def exercise_records(
        self,
        canonical_name: str,
        *,
        start_at: datetime | None,
        end_before: datetime | None,
        limit: int,
        include_lineage: bool,
    ) -> list[dict]:
        rows = [dict(item) for item in self.records]
        if start_at:
            rows = [item for item in rows if item["started_at"] >= start_at]
        if end_before:
            rows = [item for item in rows if item["started_at"] < end_before]
        if not include_lineage:
            for item in rows:
                item["lineage"] = None
        return rows[:limit]

    def exercise_first_last(
        self, canonical_name: str, *, include_lineage: bool
    ) -> dict | None:
        if not self.records:
            return None
        return {"first_record": self.records[0], "last_record": self.records[-1]}

    def get_session(self, session_id: str, *, include_lineage: bool) -> dict | None:
        if session_id != "session-1":
            return None
        return {
            "session_id": session_id,
            "started_at": self.records[0]["started_at"],
            "sets": self.records,
        }

    def list_sessions(
        self,
        *,
        canonical_name: str | None,
        start_at: datetime | None,
        end_before: datetime | None,
        limit: int,
    ) -> list[dict]:
        if start_at and self.records[0]["started_at"] < start_at:
            return []
        return [
            {
                "session_id": "session-1",
                "started_at": self.records[0]["started_at"],
                "set_count": 1,
                "exercise_names": ["Bench Press (Barbell)"],
            }
        ][:limit]


class TrainingLogToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeTrainingRepository()
        self.tool = TrainingLogTool(self.repository)

    def test_normal_exercise_query_has_lineage_and_provenance(self) -> None:
        response = self.tool.execute(
            TrainingLogInput(
                operation=TrainingLogOperation.EXERCISE_RECORDS,
                canonical_exercise_name="Bench Press (Barbell)",
                start_date="2024-01-01",
                end_date="2024-01-31",
            )
        )
        self.assertTrue(response.success)
        self.assertEqual(response.status, ToolStatus.SUCCESS)
        self.assertEqual(response.result["record_count"], 1)
        self.assertEqual(
            response.result["records"][0]["lineage"][0]["source_row"], 10
        )
        payload = response.to_dict()
        self.assertEqual(payload["provenance"]["preprocessing_version"], "preprocessing_v1")
        self.assertEqual(payload["result"]["records"][0]["started_at"], "2024-01-03T10:00:00")

    def test_valid_empty_range_is_explicit_empty(self) -> None:
        response = self.tool.execute(
            TrainingLogInput(
                operation="exercise_records",
                canonical_exercise_name="Bench Press (Barbell)",
                start_date="2030-01-01",
            )
        )
        self.assertTrue(response.success)
        self.assertEqual(response.status, ToolStatus.EMPTY)
        self.assertEqual(response.result["record_count"], 0)

    def test_unknown_exercise_is_not_guessed(self) -> None:
        response = self.tool.execute(
            TrainingLogInput(
                operation="exercise_records",
                canonical_exercise_name="Bench-ish Press",
            )
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error.code, ToolErrorCode.UNKNOWN_EXERCISE)

    def test_invalid_date_range_is_structured_failure(self) -> None:
        response = self.tool.execute(
            TrainingLogInput(
                operation="exercise_records",
                canonical_exercise_name="Bench Press (Barbell)",
                start_date="2024-02-01",
                end_date="2024-01-01",
            )
        )
        self.assertEqual(response.error.code, ToolErrorCode.INVALID_DATE_RANGE)

    def test_missing_session_is_structured_failure(self) -> None:
        response = self.tool.execute(
            TrainingLogInput(operation="get_session", session_id="missing")
        )
        self.assertEqual(response.error.code, ToolErrorCode.NOT_FOUND)


def metric_record(
    *,
    set_id: str,
    started_at: datetime,
    weight: float = 100.0,
    reps: int = 5,
    e1rm: bool = True,
    volume: bool = True,
    outlier: bool = False,
) -> dict:
    return {
        "set_id": set_id,
        "session_id": f"session-{started_at.date().isoformat()}",
        "started_at": started_at,
        "exercise_name": "Bench Press (Barbell)",
        "set_order": 1,
        "weight": weight,
        "reps": reps,
        "is_outlier": outlier,
        "outlier_reason": "extreme_weight_ge_1000" if outlier else None,
        "validation_reason": None,
        "include_in_e1rm": e1rm,
        "include_in_volume_metrics": volume,
        "e1rm_formula_version": "epley_v1",
    }


class MetricToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = MetricTool()

    def test_e1rm_respects_stored_eligibility_and_outlier(self) -> None:
        records = [
            metric_record(set_id="ok", started_at=datetime(2024, 1, 1)),
            metric_record(
                set_id="outlier",
                started_at=datetime(2024, 1, 1),
                weight=1200,
                e1rm=False,
                volume=False,
                outlier=True,
            ),
            metric_record(
                set_id="high-reps",
                started_at=datetime(2024, 1, 1),
                reps=25,
                e1rm=False,
                volume=True,
                outlier=True,
            ),
        ]
        response = self.tool.execute(
            MetricInput(operation="estimated_1rm", records=records)
        )
        self.assertTrue(response.success)
        self.assertEqual(response.result["eligible_set_count"], 1)
        self.assertAlmostEqual(
            response.result["set_e1rm"][0]["estimated_1rm"], 116.6666667
        )
        self.assertEqual(response.result["excluded_set_count"], 2)
        self.assertIn("preprocessing_policy_sha256", response.provenance)

    def test_inconsistent_eligibility_is_not_silently_corrected(self) -> None:
        record = metric_record(
            set_id="bad", started_at=datetime(2024, 1, 1), reps=13, e1rm=True
        )
        response = self.tool.execute(
            MetricInput(operation="estimated_1rm", records=[record])
        )
        self.assertFalse(response.success)
        self.assertEqual(response.error.code, ToolErrorCode.DATA_INTEGRITY_ERROR)

    def test_first_last_median_weekly_volume_frequency_and_gap(self) -> None:
        records = [
            metric_record(
                set_id=f"s{index}",
                started_at=datetime(2024, 1, 1) + timedelta(days=index * 7),
                weight=100 + index,
            )
            for index in range(6)
        ]
        median_response = self.tool.execute(
            MetricInput(
                operation=MetricOperation.FIRST_LAST_MEDIAN_E1RM,
                records=records,
                n_sessions=3,
            )
        )
        self.assertEqual(median_response.result["used_n_sessions"], 3)
        self.assertGreater(
            median_response.result["last_median_e1rm"],
            median_response.result["first_median_e1rm"],
        )
        volume = self.tool.execute(MetricInput(operation="weekly_volume", records=records))
        frequency = self.tool.execute(
            MetricInput(operation="weekly_frequency", records=records)
        )
        gaps = self.tool.execute(MetricInput(operation="training_gap", records=records))
        self.assertEqual(len(volume.result["weeks"]), 6)
        self.assertTrue(all(item["frequency"] == 1 for item in frequency.result["weeks"]))
        self.assertEqual(gaps.result["gap_count"], 5)
        self.assertEqual(gaps.result["gaps"][0]["gap_days"], 7.0)

    def test_plateau_uses_existing_eda_rule_and_is_only_candidate(self) -> None:
        records = [
            metric_record(
                set_id=f"p{index}",
                started_at=datetime(2024, 1, 1) + timedelta(days=index * 7),
                weight=100,
            )
            for index in range(8)
        ]
        response = self.tool.execute(
            MetricInput(operation="plateau_candidates", records=records)
        )
        self.assertEqual(response.result["candidate_count"], 1)
        self.assertEqual(
            response.result["candidates"][0]["classification"],
            "performance_plateau_candidate",
        )

    def test_empty_and_invalid_date_ranges(self) -> None:
        empty = self.tool.execute(
            MetricInput(operation="weekly_volume", records=[])
        )
        self.assertEqual(empty.status, ToolStatus.EMPTY)
        invalid = self.tool.execute(
            MetricInput(
                operation="weekly_volume",
                records=[metric_record(set_id="x", started_at=datetime(2024, 1, 1))],
                start_date="2024-02-01",
                end_date="2024-01-01",
            )
        )
        self.assertEqual(invalid.error.code, ToolErrorCode.INVALID_DATE_RANGE)


class FakeEncoder:
    def encode(self, texts: list[str], *, show_progress: bool = False) -> np.ndarray:
        return np.ones((len(texts), 384), dtype=np.float32)


class FakeVectorStore:
    def __init__(self, chunk_ids: list[str]) -> None:
        self.chunk_ids = chunk_ids

    def search_exact_cosine(
        self, query_embedding: np.ndarray, *, embedding_run_id: str, top_k: int
    ) -> SearchResponse:
        return SearchResponse(
            hits=[
                SearchHit(chunk_id=chunk_id, score=1.0 - rank / 100.0)
                for rank, chunk_id in enumerate(self.chunk_ids[:top_k], 1)
            ],
            database_search_ms=0.1,
        )


class LiteratureToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assets = load_frozen_literature_assets(PROJECT_ROOT)
        cls.retriever = FrozenHybridRetriever(
            assets=cls.assets,
            encoder=FakeEncoder(),
            vector_store=FakeVectorStore(
                [str(chunk["chunk_id"]) for chunk in cls.assets.chunks]
            ),
        )
        cls.tool = LiteratureTool(assets=cls.assets, retriever=cls.retriever)

    def test_frozen_assets_and_config_are_pinned(self) -> None:
        self.assertEqual(self.assets.corpus_chunks_sha256, FROZEN_CORPUS_CHUNKS_SHA256)
        self.assertEqual(
            self.assets.phase7_manifest_sha256, FROZEN_PHASE7_MANIFEST_SHA256
        )
        self.assertEqual(self.assets.bm25_metadata["k1"], 1.2)
        self.assertEqual(self.assets.bm25_metadata["b"], 0.75)
        self.assertEqual(self.assets.phase7_reproducibility["rrf"]["rrf_k"], 60)
        self.assertEqual(
            self.assets.phase7_reproducibility["rrf"]["weights"],
            {"dense": 1.0, "bm25": 1.0},
        )

    def test_literature_top_k_contains_text_scores_and_provenance(self) -> None:
        response = self.tool.execute(
            LiteratureInput(
                operation=LiteratureOperation.SEARCH,
                query="periodization progressive overload",
                top_k=3,
            )
        )
        self.assertTrue(response.success)
        self.assertEqual(len(response.result["hits"]), 3)
        first = response.result["hits"][0]
        for field in (
            "chunk_id",
            "paper_id",
            "pmcid",
            "title",
            "section",
            "text",
            "rank",
            "rrf_score",
            "dense_rank",
            "dense_score",
            "bm25_rank",
            "bm25_score",
            "corpus_version",
        ):
            self.assertIn(field, first)
        self.assertEqual(response.provenance["retrieval_version"], "hybrid_baseline_v1")

    def test_invalid_query_and_top_k_are_structured(self) -> None:
        blank = self.tool.execute(
            LiteratureInput(operation="search", query="", top_k=3)
        )
        too_deep = self.tool.execute(
            LiteratureInput(operation="search", query="valid", top_k=11)
        )
        self.assertEqual(blank.error.code, ToolErrorCode.INVALID_INPUT)
        self.assertEqual(too_deep.error.code, ToolErrorCode.INVALID_INPUT)

    def test_tampered_frozen_corpus_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "data/literature/processed").mkdir(parents=True)
            (root / "data/literature/manifests").mkdir(parents=True)
            (root / "reports/baselines").mkdir(parents=True)
            shutil.copy2(
                PROJECT_ROOT / "data/literature/processed/chunks.jsonl",
                root / "data/literature/processed/chunks.jsonl",
            )
            shutil.copy2(
                PROJECT_ROOT / "data/literature/manifests/corpus_v1.json",
                root / "data/literature/manifests/corpus_v1.json",
            )
            shutil.copytree(
                PROJECT_ROOT / "reports/baselines/hybrid_baseline_v1",
                root / "reports/baselines/hybrid_baseline_v1",
            )
            chunk_path = root / "data/literature/processed/chunks.jsonl"
            with chunk_path.open("a", encoding="utf-8") as handle:
                handle.write(" ")
            with self.assertRaisesRegex(RuntimeError, "chunks hash changed"):
                load_frozen_literature_assets(root)


if __name__ == "__main__":
    unittest.main()
