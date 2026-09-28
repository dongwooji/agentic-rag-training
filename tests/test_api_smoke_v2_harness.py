from scripts.run_api_integration_smoke import _render_report, _summarize_state


def _state() -> dict:
    return {
        "route": {"route": "log_metric"},
        "task_type": "log_metric",
        "selected_tools": ["query_training_log", "compute_metrics"],
        "tool_input_resolution": {
            "execution_status": "ready",
            "tool_inputs": {
                "query_training_log": {
                    "operation": "exercise_records",
                    "canonical_exercise_name": "Deadlift (Barbell)",
                    "limit": 5000,
                    "include_lineage": False,
                },
                "compute_metrics": {
                    "operation": "first_last_n_session_median_e1rm",
                    "canonical_exercise_name": "Deadlift (Barbell)",
                    "records_source": "query_training_log",
                    "n_sessions": 3,
                },
            },
            "provenance": {
                "method": "deterministic",
                "response_id": None,
            },
        },
        "tool_results": [
            {
                "tool": "query_training_log",
                "status": "success",
                "requested_operation": "exercise_records",
            },
            {
                "tool": "compute_metrics",
                "status": "success",
                "requested_operation": "first_last_n_session_median_e1rm",
            },
        ],
        "retry_count": 0,
        "errors": [],
        "final_status": "answer_ready",
        "final_response": {
            "provenance": {"response_id": "response-1"},
        },
    }


def test_smoke_trace_is_bounded_and_counts_provider_requests() -> None:
    trace = _summarize_state(_state())

    assert trace["resolver"]["method"] == "deterministic"
    assert trace["resolver"]["tool_inputs"]["compute_metrics"] == {
        "operation": "first_last_n_session_median_e1rm",
        "canonical_exercise_name": "Deadlift (Barbell)",
        "n_sessions": 3,
        "records_source": "query_training_log",
    }
    assert trace["api_requests"]["total"] == 1
    assert trace["api_requests"]["by_node"]["final_answer"] == 1
    assert "limit" not in str(trace)
    assert "include_lineage" not in str(trace)


def test_smoke_report_does_not_render_secret_environment_fields() -> None:
    trace = _summarize_state(_state())
    results = {
        "started_at": "2026-09-21T00:00:00+00:00",
        "completed_at": "2026-09-21T00:01:00+00:00",
        "conclusion": "integration succeeded",
        "requests": [
            {
                "case_id": "API-SMOKE-LOG-METRIC",
                "method": "POST",
                "path": "/query",
                "question": "question",
                "http_status": 200,
                "elapsed_ms": 10.0,
                "internal_trace": trace,
                "response": {
                    "final_status": "answer_ready",
                    "route": "log_metric",
                    "tools_used": ["query_training_log", "compute_metrics"],
                    "retry_count": 0,
                    "recovery_used": False,
                    "used_literature_chunk_ids": [],
                    "latency_ms": 9.0,
                },
            }
        ],
    }

    report = _render_report(results)
    assert "deterministic" in report
    assert "query_training_log" in report
    assert "OPENAI_API_KEY" not in report
    assert "PGPASSWORD" not in report

