import hashlib
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.recovery.evidence_fusion import (
    EVIDENCE_BUDGET,
    FUSION_POLICY,
    FusionReason,
    FusionSource,
    RecoveryEvidenceFusionInput,
    fuse_recovery_evidence,
)
from src.retrieval.rrf import DEFAULT_RRF_K


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MANIFEST_HASHES = {
    "reports/baselines/hybrid_baseline_v1/manifest.json": (
        "015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f"
    ),
    "reports/baselines/router_baseline_v1/manifest.json": (
        "203dd444e1aeb4cbee84b0f8c1d036179d99e8f80f1458aba1293604cc83779c"
    ),
    "reports/baselines/grader_baseline_v1/manifest.json": (
        "e32ab0476ba7ac82b4f16768e08a4233ff3695012426a0e036587c864bf093ba"
    ),
    "reports/baselines/grader_v2_baseline/manifest.json": (
        "870764d296dd469050d4699140cf5ef0508eb8d31585214082c0d8df3b963a5d"
    ),
    "reports/baselines/grader_v2_1_baseline/manifest.json": (
        "b96fc923d4ce2fe82f6d10e2dfe26e4b8cb257aa7752bd39e2743b8b49264556"
    ),
}


def _hit(chunk_id: str, rank: int) -> dict:
    text = f"Frozen literature text for {chunk_id}."
    return {
        "chunk_id": chunk_id,
        "paper_id": f"paper-{chunk_id}",
        "pmcid": f"PMC-{chunk_id}",
        "pmid": None,
        "doi": None,
        "title": f"Title {chunk_id}",
        "section": "Results",
        "text": text,
        "rank": rank,
        "rrf_score": 0.03 / rank,
        "dense_rank": rank,
        "dense_score": 0.9 - rank / 100,
        "bm25_rank": rank,
        "bm25_score": 10.0 - rank / 10,
        "dense_rrf_contribution": 0.015 / rank,
        "bm25_rrf_contribution": 0.015 / rank,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(chunk_id.encode()).hexdigest(),
        "corpus_version": "literature_corpus_v1",
    }


def _input(
    initial: list[dict],
    recovery: list[dict],
) -> RecoveryEvidenceFusionInput:
    return RecoveryEvidenceFusionInput.model_validate(
        {
            "initial_results": initial,
            "recovery_results": recovery,
            "initial_query": "VBT maximal strength",
            "recovery_query": "VBT trained athletes maximal strength outcomes",
            "query_history": ["velocity based training strength"],
        }
    )


def test_initial_ten_plus_recovery_ten_stays_within_top_ten() -> None:
    initial = [_hit(f"initial-{rank:02d}", rank) for rank in range(1, 11)]
    recovery = [_hit(f"recovery-{rank:02d}", rank) for rank in range(1, 11)]
    result = fuse_recovery_evidence(_input(initial, recovery))

    assert len(result.evidence) == EVIDENCE_BUDGET == 10
    assert [item.final_rank for item in result.evidence] == list(range(1, 11))
    assert result.provenance.candidate_count == 20
    assert result.provenance.selected_count == 10


def test_same_chunk_is_deduplicated_and_both_sources_are_preserved() -> None:
    initial = [_hit("shared", 1), _hit("initial-only", 2)]
    recovery = [_hit("shared", 1), _hit("recovery-only", 2)]
    result = fuse_recovery_evidence(_input(initial, recovery))

    assert [item.chunk_id for item in result.evidence].count("shared") == 1
    shared = next(item for item in result.evidence if item.chunk_id == "shared")
    assert shared.sources == [FusionSource.INITIAL, FusionSource.RECOVERY]
    assert shared.initial_rank == shared.recovery_rank == 1
    assert shared.fusion_reason == FusionReason.BOTH_RETRIEVALS
    assert shared.initial_retrieval.query == "VBT maximal strength"
    assert shared.recovery_retrieval.query == (
        "VBT trained athletes maximal strength outcomes"
    )
    assert result.provenance.shared_count == 1
    assert result.provenance.candidate_count == 3


def test_chunk_ranked_high_in_both_sources_receives_both_rrf_contributions() -> None:
    initial = [_hit("initial-first", 1), _hit("shared", 2)]
    recovery = [_hit("recovery-first", 1), _hit("shared", 2)]
    result = fuse_recovery_evidence(_input(initial, recovery))
    shared = result.evidence[0]

    expected_part = 1 / (DEFAULT_RRF_K + 2)
    assert shared.chunk_id == "shared"
    assert math.isclose(shared.initial_fusion_contribution, expected_part)
    assert math.isclose(shared.recovery_fusion_contribution, expected_part)
    assert math.isclose(shared.fusion_score, 2 * expected_part)


def test_initial_only_preserves_original_rank_order() -> None:
    initial = [_hit(f"chunk-{rank}", rank) for rank in range(1, 5)]
    result = fuse_recovery_evidence(_input(initial, []))

    assert [item.chunk_id for item in result.evidence] == [
        "chunk-1",
        "chunk-2",
        "chunk-3",
        "chunk-4",
    ]
    assert all(item.sources == [FusionSource.INITIAL] for item in result.evidence)
    assert all(item.recovery_rank is None for item in result.evidence)
    assert result.provenance.selected_count == 4


def test_recovery_only_preserves_original_rank_order() -> None:
    recovery = [_hit(f"chunk-{rank}", rank) for rank in range(1, 4)]
    result = fuse_recovery_evidence(_input([], recovery))

    assert [item.chunk_id for item in result.evidence] == [
        "chunk-1",
        "chunk-2",
        "chunk-3",
    ]
    assert all(item.sources == [FusionSource.RECOVERY] for item in result.evidence)
    assert all(item.initial_rank is None for item in result.evidence)


def test_both_empty_returns_empty_without_filling() -> None:
    result = fuse_recovery_evidence(_input([], []))
    assert result.evidence == []
    assert result.provenance.candidate_count == 0
    assert result.provenance.selected_count == 0
    assert result.provenance.corpus_versions == []


def test_fewer_than_ten_candidates_are_not_backfilled() -> None:
    result = fuse_recovery_evidence(
        _input([_hit("one", 1)], [_hit("two", 1), _hit("three", 2)])
    )
    assert len(result.evidence) == 3


def test_deterministic_tie_break_ends_with_chunk_id() -> None:
    initial = [_hit("z-chunk", 1)]
    recovery = [_hit("a-chunk", 1)]
    fusion_input = _input(initial, recovery)
    first = fuse_recovery_evidence(fusion_input)
    second = fuse_recovery_evidence(fusion_input)

    assert [item.chunk_id for item in first.evidence] == ["a-chunk", "z-chunk"]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_provenance_records_policy_queries_rankings_and_tie_break() -> None:
    result = fuse_recovery_evidence(
        _input([_hit("shared", 1)], [_hit("shared", 1)])
    )
    provenance = result.provenance

    assert provenance.fusion_policy == FUSION_POLICY
    assert provenance.rrf_k == DEFAULT_RRF_K == 60
    assert provenance.evidence_budget == 10
    assert provenance.source_weights == {"initial": 1.0, "recovery": 1.0}
    assert provenance.initial_ranking == ["shared"]
    assert provenance.recovery_ranking == ["shared"]
    assert provenance.query_history == ["velocity based training strength"]
    assert provenance.tie_break[-1] == "chunk_id_lexicographic_asc"
    assert provenance.corpus_versions == ["literature_corpus_v1"]


def test_input_rejects_duplicate_source_ids_noncontiguous_rank_and_mismatch() -> None:
    with pytest.raises(ValidationError, match="duplicate chunk IDs"):
        _input([_hit("same", 1), _hit("same", 2)], [])

    with pytest.raises(ValidationError, match="contiguous rank"):
        _input([_hit("one", 1), _hit("two", 3)], [])

    initial = [_hit("same", 1)]
    recovery = [_hit("same", 1)]
    recovery[0]["text"] = "Conflicting text."
    with pytest.raises(ValidationError, match="inconsistent immutable metadata"):
        _input(initial, recovery)


@pytest.mark.requires_local_artifacts
def test_frozen_artifact_manifests_remain_unchanged() -> None:
    for relative_path, expected_hash in FROZEN_MANIFEST_HASHES.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected_hash
