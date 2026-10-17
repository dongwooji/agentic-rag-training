"""Synthetic scorer/runner examples only; no actual acceptance content."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.evaluation.phase_a_acceptance_v2 import GoldFile, score, compare
from src.evaluation import acceptance_v2_runner as runner
from test_phase_a_semantics_evaluation import gold as v1_gold, observation


def gold(**updates):
    value = v1_gold()
    value["version"] = "phase_a_evidence_gold_v2"
    value["cases"][0].update(literature_answerability="answerable", **updates)
    return value


def test_v2_extends_v1_without_changing_operation_or_behavior_scoring():
    from src.evaluation.phase_a_semantics import score as old_score
    before = old_score(v1_gold(), [observation()])
    after = score(gold(), [observation()])
    assert all(after["totals"][key] == value for key, value in before["totals"].items())
    assert set(GoldFile.model_fields) == {"version", "cases"}


@pytest.mark.parametrize("answerability,required,valid", [
    ("answerable", ["literature"], True), ("unanswerable", ["literature"], True),
    ("not_applicable", ["training_records"], True), ("not_applicable", ["literature"], False),
    ("answerable", ["training_records"], False), (None, ["literature"], False)])
def test_answerability_schema_scope(answerability, required, valid):
    value = gold(); value["cases"][0].update(literature_answerability=answerability, required_evidence_sources=required)
    if valid:
        GoldFile.model_validate(value)
    else:
        with pytest.raises(ValueError): GoldFile.model_validate(value)


def test_extra_errors_are_counts_not_redefinitions_of_existing_final_behavior():
    result = score(gold(), [observation(behavior="abstain")])
    assert result["totals"]["answerable_abstain"] == 1
    assert result["totals"]["unjustified_clarification_or_abstain"] == 1
    g = gold(); g["cases"][0].update(literature_answerability="unanswerable", expected_final_behavior="abstain")
    assert score(g, [observation()])["totals"]["unanswerable_answer"] == 1
    assert score(g, [observation(behavior="clarification")])["totals"]["unanswerable_answer"] == 0
    assert score(gold(), [observation(behavior="execution_failure")])["totals"]["answerable_abstain"] == 0


def test_all_four_adoption_conditions_are_mandatory():
    old = score(gold(), [observation(behavior="abstain")])
    new = score(gold(), [observation()])
    assert compare(old, new)["eligible_for_adoption"]
    assert not compare(new, new)["eligible_for_adoption"]
    for key in ("unanswerable_answer", "execution_failure"):
        worse = deepcopy(new); worse["totals"][key] = 1
        assert not compare(old, worse)["eligible_for_adoption"]
    worse = deepcopy(new); worse["totals"]["final_behavior_accuracy"] = -1
    assert not compare(old, worse)["eligible_for_adoption"]


def question():
    return dict(case_id="synthetic-case", pair_id="synthetic-pair", question="합성 문헌 입력")


def test_runner_issues_one_debug_query_without_retry_or_clarification_resume(tmp_path):
    calls = []
    def post(path, **kw):
        calls.append((path, kw))
        raise TimeoutError("never serialize this exception detail")
    raw = runner.execute_questions([question()], post, tmp_path)
    assert calls == [("/query", dict(json=dict(question="합성 문헌 입력", debug=True)))]
    assert raw[0]["http_status"] is None and raw[0]["transport_error"] == "TimeoutError"
    assert "exception detail" not in (tmp_path / "raw.json").read_text(encoding="utf-8")
    assert runner.observations(raw, tmp_path)[0]["behavior"] == "execution_failure"
    with pytest.raises(FileExistsError): runner.save_exclusive(tmp_path / "raw.json", [])


def test_http_failure_overrides_successful_saved_graph_state(tmp_path):
    (tmp_path / "observations").mkdir()
    runner.save_exclusive(tmp_path / "observations/001.json", observation())
    raw = [dict(case_id="synthetic-case", response=dict(final_status="answer_ready"),
                http_status=500, transport_error=None)]
    actual = runner.observations(raw, tmp_path)[0]
    assert actual["behavior"] == "execution_failure"
    assert score(gold(), [actual])["totals"]["execution_failure"] == 1


def frozen_run(tmp_path):
    root = tmp_path / "inputs"; root.mkdir()
    (root / "questions.jsonl").write_text(json.dumps(question(), ensure_ascii=False)+"\n", encoding="utf-8")
    runner.save_exclusive(root / "gold.json", gold())
    manifest = dict(version="phase_a_acceptance_v2_manifest", code_sha="a"*40, files={
        key: dict(path=name, sha256=runner.sha(root / name))
        for key, name in (("questions", "questions.jsonl"), ("gold", "gold.json"))})
    runner.save_exclusive(root / "manifest.json", manifest)
    out = tmp_path / "output"; out.mkdir()
    runner.save_exclusive(out / "STARTED.json", dict(environment=dict(code_sha="a"*40), variants=runner.VARIANTS))
    for variant in runner.VARIANTS:
        folder = out / variant; (folder / "observations").mkdir(parents=True)
        runner.save_exclusive(folder / "observations/001.json", observation())
        runner.save_exclusive(folder / "raw.json", [dict(**question(), http_status=200, transport_error=None,
            response=dict(final_status="answer_ready", response_metadata=dict(trace={})))])
    files = {p.relative_to(out).as_posix(): runner.sha(p) for p in out.rglob("*.json") if p.name != "STARTED.json"}
    runner.save_exclusive(out / "RAW_FREEZE.json", dict(manifest_sha256=runner.sha(root / "manifest.json"),
        started_sha256=runner.sha(out / "STARTED.json"), files=files))
    return root / "manifest.json", out


def test_offline_score_reads_gold_only_after_both_complete_frozen_raw_runs(tmp_path, monkeypatch):
    manifest, out = frozen_run(tmp_path)
    loads = []; original = runner.load
    def tracked(path):
        loads.append(Path(path).name)
        return original(path)
    monkeypatch.setattr(runner, "load", tracked)
    result = runner.score_saved(manifest, runner.sha(manifest), out)
    assert loads.index("gold.json") > max(i for i, name in enumerate(loads) if name == "raw.json")
    assert result["scores"]["A"]["totals"]["cases"] == 1
    assert not result["decision"]["eligible_for_adoption"]
    with pytest.raises(FileExistsError): runner.score_saved(manifest, runner.sha(manifest), out)


@pytest.mark.parametrize("corruption", ["raw", "freeze", "missing_B", "gold", "manifest"])
def test_scoring_rejects_missing_or_changed_frozen_inputs(tmp_path, corruption):
    manifest, out = frozen_run(tmp_path)
    expected = runner.sha(manifest)
    if corruption == "raw":
        (out / "A/raw.json").write_text("[]")
    elif corruption == "freeze":
        (out / "RAW_FREEZE.json").unlink()
    elif corruption == "missing_B":
        value = runner.load(out / "RAW_FREEZE.json"); value["files"].pop("B/raw.json")
        (out / "RAW_FREEZE.json").write_text(json.dumps(value))
    elif corruption == "gold":
        (manifest.parent / "gold.json").write_text("{}")
    else:
        manifest.write_text("{}")
    with pytest.raises((ValueError, FileNotFoundError)):
        runner.score_saved(manifest, expected, out)


def test_run_never_opens_gold_and_freezes_raw_before_scoring(tmp_path, monkeypatch):
    from contextlib import contextmanager
    inputs = tmp_path / "inputs"; inputs.mkdir()
    (inputs / "questions.jsonl").write_text(json.dumps(question())+"\n")
    manifest = dict(version="phase_a_acceptance_v2_manifest", code_sha="a"*40, files=dict(
        questions=dict(path="questions.jsonl", sha256=runner.sha(inputs / "questions.jsonl")),
        gold=dict(path="GOLD_DOES_NOT_EXIST.json", sha256="b"*64)))
    runner.save_exclusive(inputs / "manifest.json", manifest)
    calls = []
    @contextmanager
    def fake_server(variant, folder):
        def post(path, **kw):
            calls.append((variant, path, kw))
            runner.save_exclusive(folder / "observations/001.json", observation())
            return SimpleNamespace(status_code=200, json=lambda: dict(final_status="answer_ready", response_metadata=dict(trace={})))
        yield post
    monkeypatch.setattr(runner, "server", fake_server)
    monkeypatch.setattr(runner, "sealed_environment", lambda _: dict(code_sha="a"*40))
    monkeypatch.setattr(runner, "db_fingerprint", lambda: [dict(sha256="c"*64)])
    out = tmp_path / "run"
    runner.run(inputs / "manifest.json", runner.sha(inputs / "manifest.json"), out)
    assert len(calls) == 2 and (out / "RAW_FREEZE.json").exists()
    assert not (out / "SCORES.json").exists()
    with pytest.raises(FileExistsError):
        runner.run(inputs / "manifest.json", runner.sha(inputs / "manifest.json"), out)


def test_documented_schema_matches_typed_model():
    path = Path(__file__).resolve().parents[1] / "docs/schemas/phase_a_evidence_gold_v2.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == GoldFile.model_json_schema()
