"""Scorer examples are synthetic schema checks, not acceptance Gold."""

from copy import deepcopy
import hashlib
import json

import pytest
from pydantic import ValidationError

from src.evaluation.phase_a_semantics import BASELINE_SHA, GoldFile, observe_state, score, compare
from scripts.score_phase_a_semantics import evaluate
from pathlib import Path
from src.interpretation.semantic_contracts import PhaseASemantics


def gold(**updates):
    case = dict(case_id="synthetic-case", pair_id="synthetic-pair", expected_answer_type="evaluative",
        required_evidence_sources=["training_records", "literature"], optional_evidence_sources=[],
        acceptable_record_operations=["exercise_records", "exercise_first_last"],
        acceptable_metric_operation_sets=[["weekly_volume"], ["weekly_volume", "weekly_frequency"]],
        clarification_required=False, allowed_clarification_fields=[], expected_final_behavior="answer",
        expected_default_recent_window=False)
    case.update(updates)
    return dict(version="phase_a_evidence_gold_v1", cases=[case])


def observation(**updates):
    value = dict(case_id="synthetic-case", answer_type="evaluative", answer_type_available=True,
        need_sources=["training_records", "literature"], optional_sources=[],
        executed_sources=["training_records", "literature"], record_operations=["exercise_records"],
        metric_operations=["weekly_volume"], clarification_fields=[], behavior="answer", terminal="answer_ready",
        default_recent_window=False)
    value.update(updates)
    return value


@pytest.mark.parametrize("record", ["exercise_records", "exercise_first_last"])
@pytest.mark.parametrize("metrics,correct", [(["weekly_volume"], True), (["weekly_frequency", "weekly_volume"], True),
    (["weekly_frequency"], False), (["weekly_volume", "training_gap"], False),
    (["weekly_volume", "weekly_volume"], False), ([], False)])
def test_alternatives_exact_set_match_ignore_order_but_reject_extra_duplicates(record, metrics, correct):
    row = score(gold(), [observation(record_operations=[record], metric_operations=metrics)])["rows"][0]
    assert row["record_operation_correct"]
    assert row["metric_operations_correct"] is correct


@pytest.mark.parametrize("updates", [
    dict(acceptable_record_operations=[]), dict(acceptable_record_operations=None),
    dict(acceptable_record_operations=[None, "exercise_records"]),
    dict(acceptable_metric_operation_sets=[]), dict(acceptable_metric_operation_sets=None),
    dict(acceptable_metric_operation_sets=[None]), dict(acceptable_metric_operation_sets=[["weekly_volume", "weekly_volume"]]),
    dict(acceptable_metric_operation_sets=[["weekly_volume"], ["weekly_volume"]]),
    dict(required_evidence_sources=["literature"], optional_evidence_sources=["literature"]),
    dict(expected_answer_type=None), dict(expected_record_operation="exercise_records"),
])
def test_invalid_schema_has_no_alias_or_ambiguous_null_interpretation(updates):
    with pytest.raises(ValidationError):
        GoldFile.model_validate(gold(**updates))


def test_empty_inner_metric_set_and_null_record_mean_no_execution():
    g = gold(required_evidence_sources=[], acceptable_record_operations=[None], acceptable_metric_operation_sets=[[]])
    row = score(g, [observation(need_sources=[], executed_sources=[], record_operations=[], metric_operations=[])])["rows"][0]
    assert row["record_operation_correct"] and row["metric_operations_correct"]
    GoldFile.model_validate(gold(acceptable_metric_operation_sets=[[], ["weekly_volume"]]))


@pytest.mark.parametrize("include_optional", [False, True])
def test_optional_is_neither_required_omission_nor_unnecessary_addition(include_optional):
    g = gold(required_evidence_sources=["training_records"], optional_evidence_sources=["literature"])
    a = observation(need_sources=["training_records"], optional_sources=["literature"] if include_optional else [])
    row = score(g, [a])["rows"][0]
    assert row["required_channel_omissions"] == row["unnecessary_channel_additions"] == 0


def test_channel_omission_unnecessary_addition_and_unexecuted_answer_are_separate():
    a = observation(need_sources=["training_records"], executed_sources=["training_records"])
    row = score(gold(), [a])["rows"][0]
    assert row["required_channel_omissions"] == row["required_execution_omissions"] == 1
    assert not row["final_behavior_correct"]
    g = gold(required_evidence_sources=["training_records"])
    assert score(g, [observation()])["rows"][0]["unnecessary_channel_additions"] == 1


def test_purpose_vs_argument_clarification_and_allowed_fields():
    g = gold(clarification_required=True, allowed_clarification_fields=["n_sessions"], expected_final_behavior="clarification",
             acceptable_record_operations=[None], acceptable_metric_operation_sets=[[]])
    a = observation(behavior="clarification", executed_sources=[], record_operations=[], metric_operations=[], clarification_fields=["n_sessions"])
    assert score(g, [a])["rows"][0]["clarification_correct"]
    a["clarification_fields"] = ["question"]
    assert not score(g, [a])["rows"][0]["clarification_correct"]


def test_success_criteria_use_same_denominator_and_strict_improvements():
    before = score(gold(), [observation(need_sources=["training_records"], behavior="clarification")])
    after = score(gold(), [observation()])
    assert compare(before, after)["accepted"]
    assert not compare(after, after)["accepted"]
    failed = score(gold(), [observation(behavior="execution_failure")])
    assert not compare(before, failed)["accepted"]


def test_one_observation_per_case_is_mandatory():
    with pytest.raises(ValueError):
        score(gold(), [observation(), observation()])
    with pytest.raises(ValueError):
        score(gold(), [])


def test_observer_uses_actual_execution_not_planned_tools_or_gold():
    state = dict(question_interpretation=dict(personal_record_requested=True, literature_requested=True),
        selected_tools=["query_training_log", "compute_metrics", "search_literature"], tool_results=[],
        final_response=dict(final_status="abstain_ready", provenance=dict(response_metadata=dict(clarification_required=True))))
    a = observe_state("synthetic-case", state)
    assert a["executed_sources"] == a["metric_operations"] == a["record_operations"] == []
    assert not a["answer_type_available"] and a["answer_type"] is None
    assert a["behavior"] == "clarification" and a["usage"] is None


def test_failed_arguments_keep_previously_identified_evidence_need_in_observer():
    state = dict(interpretation_trace=dict(phase_a_semantics=dict(evidence_need=dict(
        answer_type="causal", required_sources=["training_records", "literature"], optional_sources=[]))),
        final_response=dict(final_status="execution_failure"), tool_results=[])
    a = observe_state("synthetic-case", state)
    assert a["answer_type"] == "causal"
    assert a["need_sources"] == ["training_records", "literature"]
    assert a["executed_sources"] == [] and a["behavior"] == "execution_failure"


def test_frozen_manifest_hash_commit_and_run_count_checked_offline(tmp_path):
    data = dict(questions=[dict(case_id="synthetic-case", pair_id="synthetic-pair", question="합성 입력")], gold=gold())
    for name, sha in (("before", BASELINE_SHA), ("after", "a" * 40)):
        data[name] = dict(commit_sha=sha, run_count_per_case=1, execution_order=["synthetic-case"], model_config_hashes={},
            stage_llm_calls=None, usage=None, latency_ms=None, observations=[observation()])
    manifest = dict(baseline_sha=BASELINE_SHA, changed_sha="a" * 40, files={})
    for name, value in data.items():
        path = tmp_path / (name + (".jsonl" if name == "questions" else ".json"))
        path.write_text(json.dumps(value[0] if name == "questions" else value), encoding="utf-8")
        manifest["files"][name] = dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    report = evaluate(path)
    assert report["baseline_sha"] == BASELINE_SHA and report["changed_sha"] == "a" * 40
    (tmp_path / "gold.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash_mismatch_gold"):
        evaluate(path)


def test_documented_versioned_schemas_match_typed_contracts():
    root = Path(__file__).resolve().parents[1] / "docs/schemas"
    assert json.loads((root / "phase_a_evidence_gold_v1.schema.json").read_text()) == GoldFile.model_json_schema()
    assert json.loads((root / "phase_a_semantics_v1.schema.json").read_text()) == PhaseASemantics.model_json_schema()
