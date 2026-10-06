import pytest
import hashlib
from pathlib import Path

from src.recovery.contracts import RecoveryErrorCode

from graph_test_support import (
    INITIAL_QUERY,
    LITERATURE_QUESTION,
    LITERATURE_SUBQUESTION,
    build_workflow,
    literature_hit,
    recovery_payload,
)


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


def _invoke(workflow):
    return workflow.invoke(
        LITERATURE_QUESTION,
        literature_subquestion=LITERATURE_SUBQUESTION,
        initial_query=INITIAL_QUERY,
    )


def test_one_recovery_then_sufficient_reaches_answer_ready_and_top_ten() -> None:
    initial = [literature_hit(f"initial-{rank}", rank) for rank in range(1, 11)]
    recovery_hits = [
        literature_hit("shared", 1),
        *[literature_hit(f"recovery-{rank}", rank + 1) for rank in range(1, 10)],
    ]
    initial[1] = literature_hit("shared", 2)
    recovery_query = "VBT trained athletes direct maximal strength outcomes"
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[initial, recovery_hits],
        grader_results=[False, True],
        recovery_payloads=[recovery_payload(recovery_query)],
    )
    state = _invoke(workflow)

    assert state["final_status"] == "answer_ready"
    assert state["retry_count"] == 1
    assert state["query_history"] == [INITIAL_QUERY, recovery_query]
    assert len(literature.calls) == 2
    assert len(grader.calls) == 2
    assert len(recovery.calls) == 1
    assert len(state["fused_evidence"]) == 10
    assert len({item["chunk_id"] for item in state["fused_evidence"]}) == 10
    assert len(state["fusion_history"]) == 1
    assert state["fusion_history"][0]["provenance"]["shared_count"] == 1
    assert recovery.calls[0].query_history == [INITIAL_QUERY]
    assert recovery.calls[0].retry_count == 0


def test_two_unsuccessful_recoveries_end_at_abstain_ready() -> None:
    initial = [literature_hit("initial", 1)]
    first_query = "VBT trained athletes strength randomized trial"
    second_query = "VBT resistance-trained maximal strength meta-analysis"
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[
            initial,
            [literature_hit("recovery-1", 1)],
            [literature_hit("recovery-2", 1)],
        ],
        grader_results=[False, False, False],
        recovery_payloads=[
            recovery_payload(first_query),
            recovery_payload(second_query),
        ],
    )
    state = _invoke(workflow)

    assert state["final_status"] == "abstain_ready"
    assert state["retry_count"] == state["max_retry"] == 2
    assert state["query_history"] == [INITIAL_QUERY, first_query, second_query]
    assert len(literature.calls) == 3
    assert len(grader.calls) == 3
    assert len(recovery.calls) == 2
    assert len(state["fusion_history"]) == 2
    assert len(state["fused_evidence"]) <= 10
    assert recovery.calls[1].retry_count == 1
    assert recovery.calls[1].query_history == [INITIAL_QUERY, first_query]


def test_duplicate_recovery_query_stops_without_search_or_retry_increment() -> None:
    initial = [literature_hit("initial", 1)]
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[initial],
        grader_results=[False],
        recovery_payloads=[
            recovery_payload("  vbt   MAXIMAL strength trained athletes ")
        ],
    )
    state = _invoke(workflow)

    assert state["final_status"] == "abstain_ready"
    assert state["retry_count"] == 0
    assert state["query_history"] == [INITIAL_QUERY]
    assert len(literature.calls) == 1
    assert len(grader.calls) == 1
    assert len(recovery.calls) == 1
    assert state["errors"][-1]["code"] == "duplicate_query"


def test_recovery_provider_failure_is_fail_closed_without_search() -> None:
    initial = [literature_hit("initial", 1)]
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[initial],
        grader_results=[False],
        recovery_payloads=[RecoveryErrorCode.PROVIDER_FAILURE],
    )
    state = _invoke(workflow)

    assert state["final_status"] == "execution_failure"
    assert state["retry_count"] == 0
    assert len(literature.calls) == 1
    assert len(grader.calls) == 1
    assert len(recovery.calls) == 1
    assert state["errors"][-1]["code"] == "provider_failure"


def test_recovery_search_failure_does_not_consume_retry_budget() -> None:
    recovery_query = "VBT trained athletes strength evidence"
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[
            [literature_hit("initial", 1)],
            None,
        ],
        grader_results=[False],
        recovery_payloads=[recovery_payload(recovery_query)],
    )
    state = _invoke(workflow)

    assert state["final_status"] == "execution_failure"
    assert state["retry_count"] == 0
    assert state["query_history"] == [INITIAL_QUERY]
    assert len(literature.calls) == 2
    assert len(grader.calls) == 1
    assert len(recovery.calls) == 1
    assert state["errors"][-1]["stage"] == "recovery_search"


def test_empty_recovery_search_is_regraded_and_consumes_bounded_attempts() -> None:
    first_query = "VBT strength trained population evidence"
    second_query = "VBT maximal strength trained adults trial"
    workflow, _, _, literature, grader, recovery = build_workflow(
        literature_responses=[
            [literature_hit("initial", 1)],
            [],
            [],
        ],
        grader_results=[False, False, False],
        recovery_payloads=[
            recovery_payload(first_query),
            recovery_payload(second_query),
        ],
    )
    state = _invoke(workflow)

    assert state["final_status"] == "abstain_ready"
    assert state["retry_count"] == 2
    assert len(grader.calls) == 3
    assert len(literature.calls) == 3
    assert len(state["fusion_history"]) == 2
    assert state["fused_evidence"][0]["chunk_id"] == "initial"
    recovery_records = [
        item for item in state["tool_results"] if item.get("phase") == "recovery"
    ]
    assert [item["status"] for item in recovery_records] == ["empty", "empty"]


@pytest.mark.requires_local_artifacts
def test_frozen_artifact_manifests_remain_unchanged() -> None:
    for relative_path, expected_hash in FROZEN_MANIFEST_HASHES.items():
        actual = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual == expected_hash
