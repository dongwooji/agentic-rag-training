"""Synthetic observer checks: no real requests, DB, acceptance files or Gold."""
from types import SimpleNamespace
import pytest
from scripts.run_phase_a_acceptance_v1 import ResponsesObserver, save, scoring_state, summarize


def test_observer_delegates_exact_request_once_and_collects_actual_usage():
    calls, events = [], []
    response = SimpleNamespace(model="synthetic", status="completed", usage=SimpleNamespace(
        input_tokens=100, output_tokens=20, total_tokens=120,
        input_tokens_details=SimpleNamespace(cached_tokens=10)))
    def parse(**kwargs):
        calls.append(kwargs)
        return response
    observer = ResponsesObserver(SimpleNamespace(parse=parse), "interpreter", events,
        {"input": 1, "cached_input": .1, "output": 2})
    assert observer.parse(model="synthetic", input="synthetic request") is response
    assert calls == [{"model": "synthetic", "input": "synthetic request"}]
    assert len(events) == 1
    assert summarize(events)["input_tokens"] == 100
    assert summarize(events)["estimated_cost_usd"] == .000131
    assert summarize(events)["actual_cost_usd"] is None


def test_missing_usage_and_failed_request_are_unknown_without_retry():
    events, calls = [], []
    def parse(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("synthetic sensitive detail must not be saved")
    observer = ResponsesObserver(SimpleNamespace(parse=parse), "answer", events, {})
    with pytest.raises(TimeoutError):
        observer.parse(model="synthetic")
    assert len(calls) == 1
    assert events[0]["exception_type"] == "TimeoutError"
    assert "sensitive" not in str(events)
    assert summarize(events)["input_tokens"] is None
    assert summarize(events)["estimated_cost_usd"] is None


def test_empty_call_count_is_known_zero_and_exclusive_output_preserves_first(tmp_path):
    assert summarize([])["input_tokens"] == 0
    path = tmp_path / "first.json"
    save(path, {"first": True})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        save(path, {"second": True})
    assert path.read_bytes() == original


def test_state_projection_retains_executed_operations_without_raw_record_rows():
    state = dict(question_interpretation={"personal_record_requested": True},
        tool_results=[dict(tool="compute_metrics", requested_operation="weekly_volume",
            status="success", result={"raw_record": "must not persist"})],
        final_response={"final_status": "answer_ready", "answer_text": "unneeded"},
        errors=[{"stage": "synthetic", "code": "failure", "detail": "unsafe"}])
    result = scoring_state(state)
    assert result["tool_results"] == [{"tool": "compute_metrics", "status": "success", "requested_operation": "weekly_volume"}]
    assert "must not persist" not in str(result)
    assert "unsafe" not in str(result)
