"""Single-pass REST execution and a later, separate Gold-reading phase."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from time import monotonic, sleep, perf_counter

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = {"A": "legacy", "B": "fixed_requirements_v1_1"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_exclusive(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def entry_path(manifest_path, entry):
    path = (manifest_path.parent / entry["path"]).resolve()
    if not path.is_relative_to(manifest_path.parent.resolve()):
        raise ValueError("manifest_path_escape")
    return path


def read_questions(manifest_path, manifest):
    entry = manifest["files"]["questions"]
    path = entry_path(manifest_path, entry)
    if sha(path) != entry["sha256"]:
        raise ValueError("questions_hash_mismatch")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(set(row) != {"case_id", "pair_id", "question"} for row in rows):
        raise ValueError("questions_only_file_required")
    if any(not isinstance(v, str) or not v.strip() for row in rows for v in row.values()):
        raise ValueError("invalid_question_field")
    if len({x["case_id"] for x in rows}) != len(rows):
        raise ValueError("duplicate_question_id")
    return rows


def empty_observation(case_id):
    return dict(case_id=case_id, answer_type=None, answer_type_available=False,
                need_sources=[], optional_sources=[], executed_sources=[], record_operations=[],
                metric_operations=[], clarification_fields=[], behavior="execution_failure",
                terminal="execution_failure", default_recent_window=False)


def execute_questions(questions, post, destination):
    """Exactly one POST per case. HTTP errors count even when a state was saved."""
    rows = []
    for index, question in enumerate(questions, 1):
        started = perf_counter()
        body, status, transport_error = None, None, None
        try:
            response = post("/query", json={"question": question["question"], "debug": True})
            status = response.status_code
            try:
                body = response.json()
            except ValueError:
                transport_error = "non_json_response"
        except Exception as error:
            # Do not serialize exception text (URLs/provider/credentials).
            transport_error = type(error).__name__
        row = dict(case_id=question["case_id"], pair_id=question["pair_id"],
                   question=question["question"], http_status=status, response=body,
                   transport_error=transport_error, latency_ms=(perf_counter()-started)*1000)
        save_exclusive(destination / f"{index:03}.response.json", row)
        rows.append(row)
    save_exclusive(destination / "raw.json", rows)
    return rows


def observations(raw, folder):
    result = []
    for index, row in enumerate(raw, 1):
        path = folder / "observations" / f"{index:03}.json"
        response = row["response"] or {}
        failure = (row["http_status"] != 200 or row["transport_error"] is not None
                   or response.get("final_status") not in {"answer_ready", "abstain_ready"})
        if not path.exists() and not failure:
            raise ValueError("successful_response_missing_observer_state")
        actual = load(path) if path.exists() else empty_observation(row["case_id"])
        actual["case_id"] = row["case_id"]
        if failure:
            actual.update(behavior="execution_failure", terminal="execution_failure")
        trace = (response.get("response_metadata") or {}).get("trace")
        if row["http_status"] == 200 and trace is None and not failure:
            raise ValueError("debug_trace_missing")
        result.append(actual)
    return result


def db_fingerprint():
    """Read-only snapshot of actual training/corpus/vector rows, never save rows."""
    import psycopg
    from psycopg import sql
    from src.database.config import DatabaseConfig
    config = DatabaseConfig.from_environment()
    tables = []
    with psycopg.connect(host=config.host, port=config.port, dbname=config.database,
                         user=config.user, password=os.environ["PGPASSWORD"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SELECT table_schema, table_name FROM information_schema.tables "
                           "WHERE table_schema IN ('training','literature','retrieval') "
                           "AND table_type='BASE TABLE' ORDER BY table_schema, table_name")
            names = cursor.fetchall()
            if not all(any(schema == wanted for schema, _ in names) for wanted in ("training", "literature", "retrieval")):
                raise ValueError("required_database_schemas_missing")
            for schema, table in names:
                cursor.execute(sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t").format(
                    sql.Identifier(schema), sql.Identifier(table)))
                hashes = sorted(hashlib.sha256(row[0].encode()).hexdigest() for row in cursor)
                tables.append(dict(schema=schema, table=table, count=len(hashes),
                                   sha256=hashlib.sha256("\n".join(hashes).encode()).hexdigest()))
    return tables


def sealed_environment(expected_sha):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    if git("rev-parse", "HEAD") != expected_sha:
        raise ValueError("code_sha_mismatch")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("tracked_worktree_must_be_clean")
    configs = {name: sha(ROOT / name) for name in git("ls-files", "config").splitlines()}
    old = load(ROOT / "config/runtime_evidence_grader.json")
    new = load(ROOT / "config/runtime_evidence_grader_fixed_requirements_v1_1.json")
    if old["model"] != new["model"]:
        raise ValueError("grader_models_must_match")
    for name in ("runtime_evidence_grader.json", "runtime_evidence_grader_fixed_requirements_v1_1.json",
                 "question_interpreter_semantics_v1.json", "literature_query_model_v1.json",
                 "recovery_agent.json", "final_answer.json"):
        if load(ROOT / "config" / name).get("max_retries") != 0:
            raise ValueError("provider_retry_not_zero_" + name)
    retrieval = load(ROOT / "config/retrieval_step3b_translation_v1.json")
    if retrieval["corpus_version"] != "literature_corpus_v1":
        raise ValueError("corpus_v1_required")
    corpus = {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path)
              for path in (ROOT / "data/literature/processed").glob("*") if path.is_file()}
    if not corpus:
        raise ValueError("corpus_files_missing")
    return dict(code_sha=expected_sha, configs=configs, corpus=corpus,
                grader_model=old["model"], interpretation_mode="phase_a", corpus_version="literature_corpus_v1")


@contextmanager
def server(variant, folder):
    import httpx
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    env = dict(os.environ, AGENTIC_RAG_GRADER_VERSION=VARIANTS[variant],
               AGENTIC_RAG_QUESTION_INTERPRETATION="phase_a",
               AGENTIC_RAG_RETRIEVAL_CONFIG=str(ROOT / "config/retrieval_step3b_translation_v1.json"),
               AGENTIC_RAG_EVAL_OUTPUT=str(folder / "observations"))
    with (folder / "server.log").open("xb") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn",
            "src.evaluation.acceptance_v2_server:create_observed_app", "--factory", "--host", "127.0.0.1",
            "--port", str(port), "--no-access-log"], cwd=ROOT, env=env, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600,
                              transport=httpx.HTTPTransport(retries=0), trust_env=False) as client:
                deadline = monotonic()+120
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("evaluation_server_startup_failed")
                    try:
                        if client.get("/health", timeout=1).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if monotonic() >= deadline:
                        raise TimeoutError("evaluation_server_startup_timeout")
                    sleep(.1)
                yield client.post
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def run(manifest_path, manifest_sha256, destination):
    manifest_path, destination = Path(manifest_path).resolve(), Path(destination).resolve()
    if sha(manifest_path) != manifest_sha256:
        raise ValueError("manifest_hash_mismatch")
    manifest = load(manifest_path)
    if manifest["version"] != "phase_a_acceptance_v2_manifest":
        raise ValueError("manifest_version_mismatch")
    questions = read_questions(manifest_path, manifest)  # No Gold open/hash here.
    environment = sealed_environment(manifest["code_sha"])
    database = db_fingerprint()
    destination.mkdir(parents=True, exist_ok=False)  # No resume/re-execution.
    save_exclusive(destination / "STARTED.json", dict(manifest_sha256=manifest_sha256,
                   environment=environment, database=database, variants=VARIANTS, order=["A", "B"]))
    for variant in VARIANTS:
        folder = destination / variant
        (folder / "observations").mkdir(parents=True)
        with server(variant, folder) as post:
            execute_questions(questions, post, folder)
        if db_fingerprint() != database or sealed_environment(manifest["code_sha"]) != environment:
            raise ValueError("execution_environment_changed_do_not_score")
    files = {path.relative_to(destination).as_posix(): sha(path)
             for variant in VARIANTS for path in (destination / variant).rglob("*.json")}
    save_exclusive(destination / "RAW_FREEZE.json", dict(manifest_sha256=manifest_sha256,
                   started_sha256=sha(destination / "STARTED.json"), files=files))


def score_saved(manifest_path, manifest_sha256, destination):
    # This is the sole stage allowed to read Gold. No runtime/server/DB calls.
    from src.evaluation.phase_a_acceptance_v2 import GoldFile, score, compare
    manifest_path, destination = Path(manifest_path).resolve(), Path(destination).resolve()
    if sha(manifest_path) != manifest_sha256:
        raise ValueError("manifest_hash_mismatch")
    manifest = load(manifest_path)
    freeze = load(destination / "RAW_FREEZE.json")
    if freeze["manifest_sha256"] != manifest_sha256:
        raise ValueError("raw_manifest_mismatch")
    if sha(destination / "STARTED.json") != freeze["started_sha256"]:
        raise ValueError("started_hash_mismatch")
    started = load(destination / "STARTED.json")
    if started["environment"]["code_sha"] != manifest["code_sha"] or started["variants"] != VARIANTS:
        raise ValueError("run_identity_mismatch")
    for name, digest in freeze["files"].items():
        file = (destination / name).resolve()
        if not file.is_relative_to(destination) or sha(file) != digest:
            raise ValueError("raw_hash_mismatch")
    questions = read_questions(manifest_path, manifest)
    observed = {}
    for variant in VARIANTS:
        if f"{variant}/raw.json" not in freeze["files"]:
            raise ValueError("both_raw_variants_must_be_frozen")
        raw = load(destination / variant / "raw.json")
        if [(x["case_id"], x["pair_id"], x["question"]) for x in raw] != [
                (x["case_id"], x["pair_id"], x["question"]) for x in questions]:
            raise ValueError("raw_question_order_mismatch")
        for index in range(1, len(raw)+1):
            observer = f"{variant}/observations/{index:03}.json"
            if (destination / observer).exists() and observer not in freeze["files"]:
                raise ValueError("unfrozen_observation")
        observed[variant] = observations(raw, destination / variant)
    # Only after both complete raw runs and observations passed integrity checks:
    entry = manifest["files"]["gold"]
    gold_path = entry_path(manifest_path, entry)
    if sha(gold_path) != entry["sha256"]:
        raise ValueError("gold_hash_mismatch")
    gold = GoldFile.model_validate(load(gold_path))
    if [(x.case_id, x.pair_id) for x in gold.cases] != [(x["case_id"], x["pair_id"]) for x in questions]:
        raise ValueError("gold_question_order_mismatch")
    scores = {variant: score(gold, observed[variant]) for variant in VARIANTS}
    result = dict(version="phase_a_acceptance_v2_scores", code_sha=manifest["code_sha"],
                  raw_freeze_sha256=sha(destination / "RAW_FREEZE.json"),
                  scores=scores, decision=compare(scores["A"], scores["B"]))
    save_exclusive(destination / "SCORES.json", result)
    return result
