"""External one-pass REST observer for two unchanged, clean runtime worktrees.

No Gold/REVIEW/build access. A separate process imports only the selected runtime.
Every POST has a durable intent marker; interrupted or failed calls cannot retry.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from threading import Thread
from time import perf_counter, sleep
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASELINE = "685e2b9194419fbf757df2df2156299b77e4afe3"
CHANGED = "6c68ea996f967bcdda47bfed2671aae1ae583969"
INPUT_HASHES = {
    "questions.jsonl": "e8f7609df2ec70e92c413914bca0ccf179436026859b692b5cda3c452f3fc5fc",
    "gold.json": "ba4ae2af655bb401344d3058af7243d94ef0f3bc082ec43a8a14f40fda25b7ae",
    "REVIEW.md": "03bcaebe0d1f7489473b5664dc15db0274a8a58644d25cad19f3a5bd7ed109de",
    "build.py": "3268c4e718f7a5cd1ac271e55e5c9646c1351c8d593f56ef489c17f05d8c57e1",
    "manifest.json": "0b0a038c67de03d75b5733e6059bbf8c2d115eeca6b49cf48cdb123894cb81d7",
}
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def now():
    return datetime.now(timezone.utc).isoformat()


def attr(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def summarize(events):
    # Zero is exact when there were no calls. Any missing usage makes the total
    # unknown; known subtotals remain separately visible, never imputed as zero.
    totals = {key: sum(x["usage"][key] for x in events) if all(x["usage"][key] is not None for x in events) else None for key in USAGE_KEYS}
    return dict(calls=len(events), by_stage=dict(Counter(x["stage"] for x in events)),
        **totals, actual_cost_usd=None, actual_cost_status="provider_billing_not_supplied",
        usage_unavailable_calls=sum(not x["usage_available"] for x in events),
        known_subtotals={key: sum(x["usage"][key] for x in events if x["usage"][key] is not None) for key in USAGE_KEYS})


class ResponsesObserver:
    def __init__(self, delegate, stage, events, pricing):
        self.delegate, self.stage, self.events, self.pricing = delegate, stage, events, pricing

    def __getattr__(self, name):
        original = getattr(self.delegate, name)
        if name not in {"parse", "create"}:
            return original

        def invoke(*args, **kwargs):
            start = perf_counter()
            event = dict(stage=self.stage, method=name, started_at_utc=now(),
                model=kwargs.get("model"), usage_available=False,
                usage={key: None for key in USAGE_KEYS}, exception_type=None,
                pricing_snapshot=self.pricing, response_received=False)
            try:
                result = original(*args, **kwargs)
                event["response_received"] = True
                event["model"] = attr(result, "model", event["model"])
                event["response_status"] = attr(result, "status")
                usage = attr(result, "usage")
                event["usage_available"] = usage is not None
                if usage is not None:
                    for key in ("input_tokens", "output_tokens", "total_tokens"):
                        event["usage"][key] = attr(usage, key)
                    event["usage"]["cached_input_tokens"] = attr(attr(usage, "input_tokens_details"), "cached_tokens")
                    values = event["usage"]
                    if all(values[k] is not None for k in ("input_tokens", "cached_input_tokens", "output_tokens")) and self.pricing:
                        from src.llm_support import estimate_cost_usd
                        values["estimated_cost_usd"] = estimate_cost_usd(input_tokens=values["input_tokens"],
                            cached_input_tokens=values["cached_input_tokens"], output_tokens=values["output_tokens"], pricing=self.pricing)
                return result
            except Exception as error:
                # No raw exceptions, URLs, headers or provider payloads.
                event["exception_type"] = type(error).__name__
                raise
            finally:
                event["latency_ms"] = (perf_counter() - start) * 1000
                self.events.append(event)
        return invoke


class Capture:
    def __init__(self, runtime):
        self.runtime, self.states = runtime, []

    def invoke(self, question):
        result = self.runtime.invoke(question)
        self.states.append(result)
        return result


def scoring_state(state):
    """Only observation fields needed by the frozen scorer; no raw record rows."""
    interpretation = state.get("question_interpretation")
    trace = state.get("interpretation_trace") or {}
    provider = trace.get("provider") or {}
    final = state.get("final_response") or {}
    records = [{k: item.get(k) for k in ("tool", "status", "requested_operation", "phase", "retry_count") if k in item}
        for item in state.get("tool_results", []) if isinstance(item, dict)]
    return dict(question_interpretation=interpretation,
        interpretation_trace={k: trace[k] for k in ("execution_status", "error_code", "phase_a_semantics") if k in trace} | {
            "provider": {k: provider[k] for k in ("model", "prompt_sha256", "config_sha256", "api_requests", "latency_ms", "usage", "raw_interpretation", "error") if k in provider}},
        final_response={"final_status": final.get("final_status"), "provenance": {
            "response_metadata": (final.get("provenance") or {}).get("response_metadata", {})}},
        final_status=state.get("final_status"), tool_results=records,
        route=state.get("route"), selected_tools=state.get("selected_tools"),
        errors=[{k: x.get(k) for k in ("stage", "code")} for x in state.get("errors", [])])


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()


def metadata(root, runtime, dependencies, variant, input_dir):
    expected = BASELINE if variant == "baseline" else CHANGED
    commit, status = git(root, "rev-parse", "HEAD"), git(root, "status", "--porcelain")
    if commit != expected or status:
        raise RuntimeError("runtime_worktree_not_clean_or_wrong_commit")
    interpreter_config = "config/question_interpreter.json" if variant == "baseline" else "config/question_interpreter_semantics_v1.json"
    paths = [interpreter_config, "config/retrieval_step3b_translation_v1.json", "config/runtime_evidence_grader.json",
        "config/recovery_agent.json", "config/final_answer.json", "config/literature_query_model_v1.json",
        "config/preprocessing_v1.json", "config/exercise_aliases_v1.csv", "config/runtime_exercise_relations_v1.json"]
    configs = {}
    for relative in list(paths):
        if relative.endswith(".json"):
            config = json.loads((root / relative).read_text(encoding="utf-8"))
            configs[relative] = config
            if config.get("max_retries", 0) != 0:
                raise RuntimeError("provider_retries_not_zero")
            if "prompt_path" in config:
                paths.append(config["prompt_path"])
            for stage in ("generation", "translation"):
                if stage in config and "prompt_path" in config[stage]:
                    paths.append(config[stage]["prompt_path"])
    if variant == "changed":
        paths.append("config/phase_a_semantics_v1.json")
    assets = runtime._literature_tool._assets
    paths += ["data/literature/processed/chunks.jsonl", "data/literature/manifests/corpus_v1.json",
        "reports/baselines/hybrid_baseline_v1/manifest.json", "reports/baselines/hybrid_baseline_v1/reproducibility.json",
        "reports/baselines/hybrid_baseline_v1/bm25_index_metadata.json", "data/processed/workout_sets.csv"]
    paths += ["reports/baselines/hybrid_baseline_v1/" + p for p in assets.phase7_artifact_hashes]
    from src.database.config import DatabaseConfig
    repository = runtime._workflow.interpreter.canonicalizer.repository
    catalog = runtime._workflow.interpreter.exercise_catalog.names()
    bounds = repository.record_date_bounds()
    connection = runtime._literature_tool._retriever._vector_store._connection
    fingerprints = {}
    # SELECT-only server fingerprints; no raw records or vector values leave DB.
    with connection.cursor() as cursor:
        for table in ("training.exercises", "training.workout_sessions", "training.workout_sets", "retrieval.embedding_runs", "retrieval.chunk_embeddings", "literature.corpus_runs"):
            cursor.execute("SELECT count(*), md5(string_agg(row_hash, '' ORDER BY row_hash)) FROM (SELECT md5(row_to_json(t)::text) AS row_hash FROM " + table + " t) x")
            count, digest = cursor.fetchone()
            fingerprints[table] = dict(count=count, content_fingerprint_md5=digest)
    connection.rollback()
    return dict(version="phase_a_acceptance_run_v1", variant=variant, commit_sha=commit, git_status_porcelain=status,
        interpreter_config=interpreter_config, file_sha256={p: sha(root / p) for p in dict.fromkeys(paths)},
        provider="OpenAI", model_configs=configs, interpretation_mode=os.environ["AGENTIC_RAG_QUESTION_INTERPRETATION"],
        database=asdict(DatabaseConfig.from_environment()), database_fingerprints=fingerprints,
        corpus_version=assets.corpus_version, encoder=asdict(runtime._literature_tool._retriever._encoder.metadata),
        catalog_sha256=hashlib.sha256(json.dumps(list(catalog), ensure_ascii=False).encode()).hexdigest(),
        record_date_bounds=[str(x) for x in bounds] if bounds else None,
        frozen_input_sha256={name: sha(input_dir / name) for name in INPUT_HASHES},
        python=platform.python_version(), packages={p: importlib.metadata.version(p) for p in ("openai", "pydantic", "fastapi", "uvicorn", "psycopg", "langgraph")},
        environment={k: os.environ.get(k) for k in ("OPENAI_BASE_URL", "OPENAI_PROJECT_ID", "OPENAI_ORG_ID", "AGENTIC_RAG_RETRIEVAL_CONFIG", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "OMP_NUM_THREADS", "MKL_NUM_THREADS")},
        secrets_source="same_explicit_--secrets-json; values excluded; no credentials hash",
        runner_sha256=sha(Path(__file__)), scorer_sha256=sha(Path(__file__).resolve().parents[1] / "src/evaluation/phase_a_semantics.py"))


def execute(args):
    root, inputs, output = args.runtime_root.resolve(), args.inputs.resolve(), args.output.resolve()
    if {name: sha(inputs / name) for name in INPUT_HASHES} != INPUT_HASHES:
        raise RuntimeError("frozen_inputs_hash_mismatch")
    sys.path.insert(0, str(root))
    os.chdir(root)
    secrets = json.loads(args.secrets_json.read_text(encoding="utf-8-sig"))
    for key in ("OPENAI_API_KEY", "PGPASSWORD"):
        os.environ[key] = secrets[key]
    del secrets
    os.environ["AGENTIC_RAG_QUESTION_INTERPRETATION"] = "phase_a"
    if os.environ.get("AGENTIC_RAG_RETRIEVAL_CONFIG"):
        raise RuntimeError("unexpected_retrieval_override")
    import src.api.dependencies as dependencies
    from src.api.app import create_app
    events = []
    constructors = {"OpenAIRuntimeEvidenceProvider": "grader", "OpenAIEvidenceRecoveryProvider": "recovery",
        "OpenAIFinalAnswerProvider": "answer", "OpenAIQuestionInterpreterProvider": "interpreter",
        "OpenAILiteratureQueryProvider": "query"}
    try:
        with ExitStack() as stack:
            for name, stage in constructors.items():
                constructor = getattr(dependencies, name)

                def factory(*a, _ctor=constructor, _stage=stage, **kw):
                    provider = _ctor(*a, **kw)
                    actual = "step2" if kw.get("stage") == "generation" else "step3" if kw.get("stage") == "translation" else _stage
                    if provider.client is not None:
                        if provider.client.max_retries != 0:
                            raise RuntimeError("sdk_retries_not_zero")
                        provider.client.responses = ResponsesObserver(provider.client.responses, actual, events, provider.config.get("pricing_usd_per_million_tokens"))
                    return provider
                stack.enter_context(patch.object(dependencies, name, factory))
            runtime = dependencies.get_workflow()
        before = metadata(root, runtime, dependencies, args.variant, inputs)
        if args.prepare_only:
            save(output, before)
            print(f"{args.variant}: clean worktree preflight saved; zero POSTs", flush=True)
            return
        frozen_preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
        if before != frozen_preflight:
            raise RuntimeError("preflight_environment_changed")
        output.mkdir(parents=True, exist_ok=False)
        save(output / "preflight.json", before)
        questions_path = args.questions.resolve() if args.questions else inputs / "questions.jsonl"
        questions = [json.loads(line) for line in questions_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(questions) != 20:
            raise RuntimeError("expected_twenty_questions")
        capture = Capture(runtime)
        app = create_app()
        app.dependency_overrides[dependencies.get_workflow] = lambda: capture
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="critical", access_log=False))
        thread = Thread(target=server.run, daemon=True)
        thread.start()
        deadline = perf_counter() + 30
        while not server.started:
            if not thread.is_alive() or perf_counter() > deadline:
                raise RuntimeError("server_not_started_no_requests_sent")
            sleep(.1)
        cases = []
        started = now()
        try:
            for index, question in enumerate(questions, 1):
                request_json = dict(question=question["question"], debug=True)
                item = dict(case_id=question["case_id"], pair_id=question["pair_id"], sequence=index,
                    request_json=request_json, started_at_utc=now())
                save(output / f"{index:02d}.intent.json", item)
                event_offset, state_offset = len(events), len(capture.states)
                start = perf_counter()
                error_type = None
                request = Request(f"http://127.0.0.1:{args.port}/query", data=json.dumps(request_json, ensure_ascii=False).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urlopen(request, timeout=900) as response:
                        http_status, body = response.status, response.read()
                except HTTPError as error:
                    http_status, body = error.code, error.read()
                except Exception as error:
                    http_status, body, error_type = None, b"", type(error).__name__
                latency = (perf_counter() - start) * 1000
                with (output / f"{index:02d}.response.json").open("xb") as stream:
                    stream.write(body)
                try:
                    payload = json.loads(body) if body else None
                except ValueError:
                    payload, error_type = None, "NonJSONResponse"
                state = capture.states[-1] if len(capture.states) > state_offset else {}
                calls = events[event_offset:]
                raw_state = scoring_state(state)
                item.update(http_status=http_status, response=payload, final_status=(payload or {}).get("final_status"),
                    trace=((payload or {}).get("response_metadata") or {}).get("trace"), latency_ms=latency,
                    llm_events=calls, usage=summarize(calls), scoring_state=raw_state, actual_tools=raw_state["tool_results"],
                    transport_error_type=error_type,
                    execution_error=bool(http_status != 200 or (payload or {}).get("final_status") == "execution_failure"),
                    completed_at_utc=now(), response_bytes_sha256=sha(output / f"{index:02d}.response.json"))
                save(output / f"{index:02d}.case.json", item)
                cases.append(item)
                print(f"{args.variant} {index:02d}/20 {item['case_id']} HTTP={http_status} terminal={item['final_status']} calls={len(calls)}", flush=True)
            after = metadata(root, runtime, dependencies, args.variant, inputs)
        finally:
            server.should_exit = True
            thread.join(timeout=15)
        save(output / "raw.json", dict(version="phase_a_acceptance_raw_v1", variant=args.variant,
            commit_sha=before["commit_sha"], started_at_utc=started, completed_at_utc=now(), requests_per_case=1,
            questions_sha256=sha(questions_path), cases=cases, total_usage=summarize(events)))
        if before != after:
            raise RuntimeError("runtime_environment_changed_results_preserved")
        manifest = dict(version="phase_a_acceptance_frozen_run_v1", status="frozen", frozen_at_utc=now(),
            commit_sha=before["commit_sha"], run_count_per_case=1, execution_order=[x["case_id"] for x in cases],
            preflight_sha256=sha(output / "preflight.json"), raw_sha256=sha(output / "raw.json"),
            artifacts={p.name: sha(p) for p in sorted(output.iterdir()) if p.is_file()},
            postflight_matches_preflight=before == after, requests_sent=len(cases), automatic_retries=0, clarification_followups=0)
        save(output / "run_manifest.json", manifest)
        save(output / "FREEZE.json", dict(run_manifest_sha256=sha(output / "run_manifest.json"), raw_sha256=sha(output / "raw.json"), frozen_at_utc=now()))
        print(f"{args.variant}: frozen {len(cases)} requests; manifest={sha(output / 'run_manifest.json')}", flush=True)
    finally:
        dependencies.close_workflow()
        for key in ("OPENAI_API_KEY", "PGPASSWORD"):
            os.environ.pop(key, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--secrets-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=("baseline", "changed"), required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--questions", type=Path, help="Separate frozen regression projection, only after acceptance freeze")
    parser.add_argument("--port", type=int, default=8021)
    execute(parser.parse_args())


if __name__ == "__main__":
    main()
