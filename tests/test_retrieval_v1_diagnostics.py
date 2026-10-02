"""Diagnostic accounting safeguards; no production behavior changes."""

from __future__ import annotations

import pytest

from scripts.diagnose_retrieval_v1 import (
    bm25_diagnostics,
    depth_analysis,
    diversity_row,
    hash_changes,
    main,
    zero_match_analysis,
)
from src.retrieval.bm25 import BM25Index


def hit(chunk_id, rank, score=1.0):
    return {"chunk_id": chunk_id, "rank": rank, "score": score}


def test_zero_match_diagnostic_exposes_current_rrf_tie_contribution():
    index = BM25Index([
        {"chunk_id": "b", "text": "training frequency"},
        {"chunk_id": "a", "text": "muscle hypertrophy"},
        *[{"chunk_id": f"z{i:02d}", "text": "training"} for i in range(48)],
    ])
    row = bm25_diagnostics(index, [{"case_id": "probe", "question": "근비대"}])[0]
    assert row["matched_query_term_count"] == 0
    assert row["all_top10_scores_zero"]
    assert row["zero_match_chunk_id_tie_order"]
    analysis = zero_match_analysis(row, [hit("z47", 1), hit("a", 2)])
    assert analysis["hybrid_top10"][0]["chunk_id"] == "a"
    assert analysis["promoted_above_dense_rank"][0]["original_dense_rank"] == 2
    assert any(h["chunk_id"] == "b" and h["bm25_score"] == 0 for h in analysis["entered_hybrid_without_dense_top10"])


def test_depth_preserves_all_group_semantics_and_excludes_zero_score_ties():
    case = {"id": "probe", "gold": {"literature_evidence_groups": [
        {"id": "all", "required": True, "match": "all", "chunk_ids": ["g1", "g2"]},
        {"id": "zero", "required": True, "match": "any", "chunk_ids": ["g3"]},
    ]}}
    dense = [hit(f"d{i}", i) for i in range(1, 11)] + [hit("g1", 11)]
    bm25 = [hit("g2", 1)] + [hit(f"b{i}", i) for i in range(2, 11)] + [hit("g3", 11, 0)]
    row = depth_analysis(case, dense, bm25)
    assert row["lost_evidence_group_count"] == 1
    assert row["newly_fully_coverable_group_count"] == 1
    assert row["groups"][0]["covered_by_candidate_union10"] is False
    assert row["groups"][0]["covered_by_positive_candidate_union50"] is True
    assert row["groups"][1]["lost_evidence_opportunity"] is False


def test_deep_gold_already_in_other_source_top10_is_not_a_lost_opportunity():
    case = {"id": "probe", "gold": {"literature_evidence_groups": [
        {"id": "any", "required": True, "match": "any", "chunk_ids": ["g"]},
    ]}}
    row = depth_analysis(case, [hit("g", 11)], [hit("g", 1)])
    assert row["has_valid_gold_rank11_50"]
    assert row["lost_evidence_group_count"] == 0
    assert row["newly_fully_coverable_group_count"] == 0


def test_section_diversity_does_not_merge_different_papers():
    chunks = {"a": {"paper_id": "p1", "section": "Discussion"},
              "b": {"paper_id": "p1", "section": "Discussion"},
              "c": {"paper_id": "p2", "section": "Discussion"}}
    row = diversity_row("probe", [hit("a", 1), hit("b", 2), hit("c", 3)], chunks)
    assert row["unique_paper_count_top10"] == 2
    assert row["max_chunks_from_same_paper_section"] == 2
    assert row["chunks_in_repeated_paper_sections"] == 2


def test_integrity_check_detects_deleted_added_and_changed_files():
    assert hash_changes({"deleted": "1", "changed": "2", "same": "3"},
                        {"added": "1", "changed": "4", "same": "3"}) == ["added", "changed", "deleted"]


def test_output_guard_refuses_baseline_directory(monkeypatch):
    monkeypatch.setattr("sys.argv", ["diagnose_retrieval_v1.py", "--output", "reports/baselines/dense_baseline_v1"])
    with pytest.raises(ValueError, match="baseline writes are forbidden"):
        main()
