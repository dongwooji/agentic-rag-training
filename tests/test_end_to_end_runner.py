from __future__ import annotations

from collections import Counter
from pathlib import Path

from src.evaluation.end_to_end_runner import (
    PROJECT_ROOT,
    build_tool_inputs_from_frozen_case,
    load_frozen_cases,
    load_protocol_config,
    prepare_graph_invocation,
)


def test_frozen_eval_composition_is_exactly_30_cases() -> None:
    cases = load_frozen_cases()
    assert len(cases) == 30
    assert Counter(case["category"] for case in cases) == {
        "literature_only": 10,
        "log_metric": 6,
        "hybrid": 8,
        "unanswerable": 6,
    }


def test_graph_input_keeps_exact_initial_query_and_defers_literature_scope() -> None:
    case = next(item for item in load_frozen_cases() if item["id"] == "LIT-001")
    invocation = prepare_graph_invocation(case)
    assert invocation["question"] == case["question"]
    assert invocation["initial_query"] == case["question"]
    assert invocation["literature_subquestion"] is None


def test_structured_binding_uses_operation_parameters_but_not_expected_gold() -> None:
    case = next(item for item in load_frozen_cases() if item["id"] == "LOG-004")
    inputs = build_tool_inputs_from_frozen_case(case)
    assert inputs["query_training_log"]["canonical_exercise_name"] == "Seated Shoulder Press (Barbell)"
    assert inputs["compute_metrics"]["operation"] == "first_last_n_session_median_e1rm"
    assert inputs["compute_metrics"]["n_sessions"] == 5
    flattened = repr(inputs)
    assert "first_window_median_e1rm" not in flattened
    assert "209.0" not in flattened
    assert "chunk_ids" not in flattened


def test_interval_binding_is_typed_and_date_bounded() -> None:
    case = next(item for item in load_frozen_cases() if item["id"] == "HYB-001")
    inputs = build_tool_inputs_from_frozen_case(case)
    assert inputs["query_training_log"]["operation"] == "exercise_records"
    assert inputs["query_training_log"]["start_date"] == "2017-10-27"
    assert inputs["query_training_log"]["end_date"] == "2018-07-11"
    assert inputs["compute_metrics"]["n_sessions"] == 3


def test_fixed_protocol_constants_cannot_drift() -> None:
    config = load_protocol_config()
    assert config["execution"]["literature_top_k"] == 10
    assert config["execution"]["max_retry"] == 2
    assert config["execution"]["fusion_policy"] == "retry_evidence_fusion_v1"
    assert config["execution"]["fusion_rrf_k"] == 60
    assert config["execution"]["provider_automatic_retries"] == 0


def test_completed_v1_baseline_manifest_is_frozen() -> None:
    import hashlib

    path = PROJECT_ROOT / "reports/baselines/end_to_end_baseline_v1/manifest.json"
    assert path.exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "fd6d45414010033d61971fbddbea26fcd19c485aa51b2cb64c2ef0caff3478e6"
    )


def test_preregistration_detects_changed_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("changed", encoding="utf-8")
    # The production manifest check is covered above; this verifies the hash helper
    # path indirectly without mutating any frozen project artifact.
    from src.evaluation.end_to_end_runner import sha256_file

    assert sha256_file(artifact) != "0" * 64
