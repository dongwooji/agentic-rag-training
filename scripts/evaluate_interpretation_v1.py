"""Frozen, single-pass REST interpretation evaluation; no runtime tuning.

prepare freezes the supplied Markdown and exports the pre-PR14 source, run
passes only questions to the existing REST observer, and report scores saved
responses offline. Labels never enter the service or its providers.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data/evaluation/interpretation_eval_v1"
OUT = ROOT / "reports/interpretation_eval_v1"
OLD = ROOT / ".build/interpretation-v1-pre-pr14"
VARIANTS = ("legacy", "phase_a_before", "phase_a_fixed")
PRIOR = "f423265dfb126ab07fefd5f7f6a097469d3f4238"
ROUTES = {"hybrid": "기록+문헌", "log_lookup": "기록", "log_metric": "기록",
          "literature_only": "문헌", "unsupported": "범위 밖"}
POLICY = {
    "primary_route": "Exact Router route mapping; ambiguous/invalid/unavailable = unclassified and incorrect. No expected-label inference.",
    "channel_diagnostic": "Auxiliary only: validated personal_record_requested/literature_requested or Router rule signals; not the primary score.",
    "action": "answer_ready with tools = answer; abstain_ready with clarification metadata or ambiguous route = clarification; other abstain_ready = abstention; execution_failure never correct.",
    "unjustified": "Only answer-only labels: clarification or abstention. Failures counted separately; answer quality cautions not scored.",
    "order": "All dev variants complete before any test variant. Each question gets one POST, no clarification resume or transport retries.",
    "limitations": "One stochastic run, shared caches, fixed variant order; supplied test text visible to evaluator. No runtime changes after viewing any results.",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value, *, exclusive=False):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with Path(path).open("xb" if exclusive else "wb") as stream:
        stream.write(data)


def parse_cases(text):
    """Read table rows, preserving question text; reject malformed labels/IDs."""
    cases = []
    for line in text.splitlines():
        if not re.match(r"^\| [DT]\d{2} \|", line):
            continue
        columns = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(columns) != 5:
            raise ValueError("expected five evaluation columns")
        identifier, question, route, actions, caution = columns
        allowed = [x.strip() for x in actions.split(",")]
        if route not in set(ROUTES.values()) or not allowed or not set(allowed) <= {"답변", "되묻기", "보류"}:
            raise ValueError("invalid label")
        if not question or identifier in {x["id"] for x in cases}:
            raise ValueError("empty question or duplicate ID")
        cases.append(dict(id=identifier, split="dev" if identifier.startswith("D") else "test",
                          question=question, expected_route=route, allowed_actions=allowed, caution=caution))
    return cases


def projection(cases, split):
    """The sole inputs passed to the REST observer; no labels or cautions."""
    return [{"id": x["id"], "question": x["question"]} for x in cases if x["split"] == split]


def route_name(case):
    route = case.get("route") or {}
    return route.get("route") if isinstance(route, dict) else route


def action(case):
    response = case.get("response") or {}
    terminal = response.get("final_status") or case.get("terminal")
    if case.get("http_status") != 200:
        return "HTTP/transport_failure"
    if terminal == "execution_failure":
        return terminal
    if terminal == "answer_ready":
        return "답변" if response.get("tools_used") else "answer_without_tools"
    if terminal == "abstain_ready":
        metadata = response.get("response_metadata") or {}
        if metadata.get("clarification_required") or route_name(case) == "ambiguous":
            return "되묻기"
        return "보류"
    return "unknown"


def channel_category(case):
    interpretation = case.get("question_interpretation")
    if interpretation:
        personal, literature = interpretation["personal_record_requested"], interpretation["literature_requested"]
    else:
        signals = ((case.get("route") or {}).get("rule_match") or {}).get("signals") or {}
        if not signals:
            return "미분류"
        personal, literature = bool(signals.get("structured_log") or signals.get("metric")), bool(signals.get("literature"))
    return "기록+문헌" if personal and literature else "기록" if personal else "문헌" if literature else "범위 밖"


def score(labels, observed):
    """Offline scoring only, after response hashes have been frozen."""
    if [x["id"] for x in labels] != [x["id"] for x in observed]:
        raise ValueError("missing, duplicate or reordered observations")
    rows = []
    for label, result in zip(labels, observed):
        if result["question"] != label["question"]:
            raise ValueError("question differs from freeze")
        category = ROUTES.get(route_name(result), "미분류")
        observed_action = action(result)
        rows.append(dict(id=label["id"], expected_route=label["expected_route"], allowed_actions=label["allowed_actions"],
                         raw_route=route_name(result), category=category, action=observed_action,
                         route_correct=category == label["expected_route"],
                         action_correct=observed_action in label["allowed_actions"],
                         channel_category=channel_category(result),
                         unjustified=label["allowed_actions"] == ["답변"] and observed_action in {"되묻기", "보류"}))
    n = len(rows)
    return dict(count=n, route_correct=sum(x["route_correct"] for x in rows),
                action_correct=sum(x["action_correct"] for x in rows),
                execution_failure=sum((x.get("response") or {}).get("final_status", x.get("terminal")) == "execution_failure" for x in observed),
                http_transport_failure=sum(x["action"] == "HTTP/transport_failure" for x in rows),
                unjustified=sum(x["unjustified"] for x in rows),
                channel_correct_diagnostic=sum(x["channel_category"] == x["expected_route"] for x in rows), rows=rows)


def prepare(source):
    if EVAL.exists() or OUT.exists() or OLD.exists():
        raise FileExistsError("freeze/output/source export already exists; never overwrite")
    raw = source.read_bytes()
    cases = parse_cases(raw.decode("utf-8-sig"))
    for prefix, expected in (("D", 10), ("T", 20)):
        if [x["id"] for x in cases if x["id"].startswith(prefix)] != [f"{prefix}{i:02}" for i in range(1, expected + 1)]:
            raise ValueError("expected ordered dev10/test20")
    changes = subprocess.check_output(["git", "diff", "--name-only", f"{PRIOR}..HEAD", "--", "src", "config"], cwd=ROOT, text=True).splitlines()
    if sorted(changes) != ["src/interpretation/contracts.py", "src/interpretation/validator.py"]:
        raise ValueError("historical control differs beyond PR14 validation fix")
    archive = subprocess.check_output(["git", "archive", "--format=zip", PRIOR, "src", "config", "scripts"], cwd=ROOT)
    OLD.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        for member in bundle.infolist():
            target = (OLD / member.filename).resolve()
            if not target.is_relative_to(OLD.resolve()):
                raise ValueError("unsafe archive member")
        bundle.extractall(OLD)
    EVAL.mkdir(parents=True)
    (EVAL / "questions.md").write_bytes(raw)
    OUT.mkdir(parents=True)
    # Freeze existing protected files before adding this evaluation's outputs.
    protected = {}
    for directory in ("src", "config", "docs", "data/literature", "data/evaluation", "reports"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and not path.is_relative_to(OUT):
                protected[path.relative_to(ROOT).as_posix()] = sha(path)
    save(OUT / "protected_before.json", protected, exclusive=True)
    tracked = subprocess.check_output(["git", "ls-files", "src", "config"], cwd=ROOT, text=True).splitlines()
    sources = {variant: {p: sha((OLD if variant == "phase_a_before" else ROOT) / p) for p in tracked} for variant in VARIANTS}
    manifest = dict(status="frozen", frozen_at_utc=datetime.now(timezone.utc).isoformat(),
                    questions_sha256=sha(EVAL / "questions.md"), source_sha256=sha(source),
                    dev_count=10, test_count=20, service_default="legacy", policy=POLICY,
                    prior_commit=PRIOR, current_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    runtime_source_hashes=sources, changed_runtime_files=changes,
                    observer_sha256=sha(ROOT / "scripts/observe_service_retrieval_v2.py"))
    save(EVAL / "manifest.json", manifest, exclusive=True)
    for split in ("dev", "test"):
        save(EVAL / f"{split}_requests.json", projection(cases, split), exclusive=True)
    print(json.dumps({k: manifest[k] for k in ("status", "questions_sha256", "prior_commit", "current_commit")}, ensure_ascii=False))


def verified_manifest():
    manifest = json.loads((EVAL / "manifest.json").read_text(encoding="utf-8"))
    if sha(EVAL / "questions.md") != manifest["questions_sha256"] or manifest["policy"] != POLICY:
        raise ValueError("freeze or scoring policy changed")
    return manifest


def run(split, variant, secrets_path):
    manifest = verified_manifest()
    protocol_path = OUT / "execution_protocol.json"
    protocol = dict(runner_sha256=sha(__file__), scoring_tests_sha256=sha(ROOT / "tests/test_interpretation_eval_v1.py"), policy=POLICY)
    if protocol_path.exists():
        if json.loads(protocol_path.read_text(encoding="utf-8")) != protocol:
            raise ValueError("evaluation harness changed after execution began")
    else:
        if (split, variant) != ("dev", "legacy"):
            raise ValueError("first run must be dev/legacy")
        save(protocol_path, protocol, exclusive=True)
    if split == "test":
        for control in VARIANTS:
            frozen = OUT / "dev" / control / "completed.json"
            if not frozen.exists():
                raise ValueError("all dev settings must finish before test")
    runtime_root = OLD if variant == "phase_a_before" else ROOT
    for path, expected in manifest["runtime_source_hashes"][variant].items():
        if sha(runtime_root / path) != expected:
            raise ValueError("runtime source/config changed")
    if sha(ROOT / "scripts/observe_service_retrieval_v2.py") != manifest["observer_sha256"]:
        raise ValueError("observer changed")
    requests = json.loads((EVAL / f"{split}_requests.json").read_text(encoding="utf-8"))
    # Validate projection without ever passing label/caution fields to runtime.
    expected = projection(parse_cases((EVAL / "questions.md").read_text(encoding="utf-8-sig")), split)
    if requests != expected or any(set(x) != {"id", "question"} for x in requests):
        raise ValueError("request projection differs")
    output = OUT / split / variant
    output.mkdir(parents=True, exist_ok=False)
    for name in ("AGENTIC_RAG_RETRIEVAL_CONFIG", "AGENTIC_RAG_QUESTION_INTERPRETATION", "OPENAI_BASE_URL"):
        if os.environ.get(name):
            raise ValueError("unexpected service/provider override")
    secrets = json.loads(secrets_path.read_text(encoding="utf-8-sig"))
    for key in ("PGPASSWORD", "OPENAI_API_KEY"):
        os.environ[key] = secrets[key]
    # Fresh process: import the unchanged historical/current observer and src.
    spec = importlib.util.spec_from_file_location("frozen_rest_observer", runtime_root / "scripts/observe_service_retrieval_v2.py")
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    observer.QUESTIONS = tuple(x["question"] for x in requests)
    import src.api.dependencies as dependencies
    if Path(dependencies.__file__).resolve() != (runtime_root / "src/api/dependencies.py").resolve():
        raise ValueError("wrong runtime source imported")
    if sha(dependencies.DEFAULT_SERVICE_RETRIEVAL_CONFIG) != sha(ROOT / "config/retrieval_step3b_translation_v1.json"):
        raise ValueError("different operating retrieval config")
    from scripts import run_api_integration_smoke as boundary
    base_capture = boundary.CapturingWorkflow
    captures = []

    class Capture(base_capture):
        def __init__(self, inner):
            super().__init__(inner)
            captures.append(self)

    boundary.CapturingWorkflow = Capture
    original_urlopen = observer.urlopen
    attempts = []

    def single_post(request, **kwargs):
        if request.get_method() != "POST":
            raise ValueError("only single query POSTs are allowed")
        index = len(attempts)
        if index >= len(requests) or json.loads(request.data) != {"question": requests[index]["question"]}:
            raise ValueError("unexpected/repeated REST request")
        attempts.append(dict(id=requests[index]["id"], started_at_utc=datetime.now(timezone.utc).isoformat()))
        save(output / "attempts.json", attempts)
        return original_urlopen(request, **kwargs)

    def persist(path, report):
        report.update(kind="frozen interpretation evaluation v1; one run; no result-driven changes", split=split,
                      variant=variant, questions_sha256=manifest["questions_sha256"],
                      runtime_dependencies_file=str(Path(dependencies.__file__).resolve()),
                      runtime_commit=manifest["prior_commit"] if variant == "phase_a_before" else manifest["current_commit"])
        for row, item in zip(report["cases"], requests):
            row["id"] = item["id"]
        if captures and captures[0]._states and captures[0]._states[-1].get("original_question") == report["cases"][-1]["question"]:
            report["cases"][-1]["state"] = observer.json_safe(captures[0]._states[-1])
        save(path, report)

    observer.urlopen, observer.write_json = single_post, persist
    try:
        observer.rest(output, interpretation_mode="legacy" if variant == "legacy" else "phase_a")
        result = json.loads((output / "rest.json").read_text(encoding="utf-8"))
        if len(attempts) != len(requests) or len(result["cases"]) != len(requests):
            raise ValueError("incomplete observations, never rerun")
        save(output / "completed.json", dict(requests=len(attempts), rest_sha256=sha(output / "rest.json")), exclusive=True)
    finally:
        for key in ("PGPASSWORD", "OPENAI_API_KEY"):
            os.environ.pop(key, None)


def cell(value, limit=None):
    text = str(value).replace("\r", "").replace("\n", " / ").replace("|", "\\|")
    return text[:limit] + ("…" if limit and len(text) > limit else "") if limit else text


def report():
    manifest = verified_manifest()
    labels = parse_cases((EVAL / "questions.md").read_text(encoding="utf-8-sig"))
    metrics, reports = {}, {}
    for split in ("dev", "test"):
        for variant in VARIANTS:
            folder = OUT / split / variant
            complete = json.loads((folder / "completed.json").read_text(encoding="utf-8"))
            if sha(folder / "rest.json") != complete["rest_sha256"]:
                raise ValueError("observation changed after completion")
            saved = json.loads((folder / "rest.json").read_text(encoding="utf-8"))
            key = f"{split}/{variant}"
            reports[key] = saved
            metrics[key] = score([x for x in labels if x["split"] == split], saved["cases"])
    before = json.loads((OUT / "protected_before.json").read_text(encoding="utf-8"))
    after = {p: sha(ROOT / p) for p in before}
    save(OUT / "protected_after.json", after)
    changes = [p for p in before if before[p] != after[p]]
    if changes:
        raise ValueError("protected file changed: " + repr(changes))
    save(OUT / "scores.json", metrics)
    save(OUT / "integrity.json", dict(protected_count=len(before), changed=[], questions_sha256=sha(EVAL / "questions.md")))
    lines = ["# 질문 해석 v1 REST 평가", "", "AI 초안 평가 질문을 사용자가 제출한 바이트 그대로 동결했습니다. 질문과 라벨은 서비스 입력에 포함하지 않으며 POST 본문에는 질문만 전달했습니다.", "",
             f"질문 SHA-256: `{manifest['questions_sha256']}`", "",
             f"기존 Phase A: `{manifest['prior_commit']}`. legacy·수정 Phase A: `{manifest['current_commit']}`.", "",
             "세 설정의 src/config 차이는 PR #14의 contracts.py·validator.py 두 파일뿐입니다. 기본 legacy, corpus v1, Final V2의 운영 corpus v1용 검색 설정을 유지했습니다.", "",
             "주 채점은 Router가 반환한 경로를 직접 비교합니다. ambiguous/invalid는 미분류로 오답입니다. 해석 필드의 채널 일치 수는 보조 진단으로 따로 기록하며 주 점수에 합산하지 않습니다.", "",
             "공개 API의 abstain_ready 중 clarification metadata가 있거나 Router가 ambiguous이면 되묻기로 분류합니다. 나머지는 보류입니다. execution_failure는 항상 오답이며 HTTP/전송 실패는 별도로 셉니다. answer_ready는 실제 Tool 사용도 있어야 답변으로 채점합니다.", "",
             "## 집계", "", "| 분할 / 설정 | 경로 정확도 | 동작 정확도 | execution_failure | 부당한 보류·되묻기 | 채널 일치(보조) | HTTP/전송 실패 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for key, data in metrics.items():
        n = data["count"]
        lines.append(f"| {key} | {data['route_correct']}/{n} ({data['route_correct']/n:.0%}) | {data['action_correct']}/{n} ({data['action_correct']/n:.0%}) | {data['execution_failure']} | {data['unjustified']} | {data['channel_correct_diagnostic']}/{n} | {data['http_transport_failure']} |")
    lines += ["", "## 실행량", "", "| 분할 / 설정 | LLM 호출 | input (cached 포함) | cached | output | 추정 USD | REST 지연 합계(초) |", "|---|---:|---:|---:|---:|---:|---:|"]
    for key, saved in reports.items():
        usage = saved["total_usage"]
        lines.append(f"| {key} | {usage['calls']} | {usage['input_tokens']} | {usage['cached_input_tokens']} | {usage['output_tokens']} | {usage['estimated_cost_usd']:.6f} | {sum(x['latency_ms'] for x in saved['cases'])/1000:.1f} |")
    lines += ["", "## 제한과 무결성", "", f"기존 보호 파일 {len(before)}개 hash가 모두 동일합니다. 새 평가 원문·manifest·응답 전문·Tool 결과는 data/evaluation/interpretation_eval_v1 및 reports/interpretation_eval_v1에 로컬 보존합니다. 공개 저장소에는 질문 원문·기록·provider 결과를 올리지 않습니다.", "",
              "dev 30회 완료 후 test 60회, 총 90회 POST를 각 문항·설정에서 한 번씩 실행했습니다. 사용자 선택 재개와 오류 재실행은 없습니다. 코드·prompt·validator·라벨·검색 조건을 결과에 따라 수정하지 않았습니다.", "",
              "동일 질문에 대한 단일 확률적 실행이며 설정 순서가 고정되고 provider cache가 공유됩니다. LLM 변동과 validation 변경 효과를 완전히 분리하는 반복 실험은 아닙니다. 문서에 test 사전 비공유 문구가 있으나 사용자가 첨부 전체를 직접 제출하여 평가자가 문항을 읽었습니다. 독립적인 맹검 평가로 표현하지 않습니다. 답변 내용의 정확도와 인과 설명 품질은 이번 동작 채점 대상이 아닙니다."]
    public_lines = list(lines)
    for key, saved in reports.items():
        lines += ["", f"## {key}: 문항별 상세", "", "| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 되묻기·보류·실패 사유 | 실행 Tool | 답변 요약 |", "|---|---|---|---|---|---|---|---|---|"]
        public_lines += ["", f"## {key}: 문항별 상태", "", "| ID | HTTP | 기대 / 실제 경로 | 채널(보조) | 최종 상태 / 동작 | 경로·동작 정답 | 실행 Tool |", "|---|---|---|---|---|---|---|"]
        for observed, graded in zip(saved["cases"], metrics[key]["rows"]):
            response = observed["response"]
            reasons = [x["reason"] for x in (observed.get("question_interpretation") or {}).get("unresolved_fields", [])]
            if not reasons and graded["action"] != "답변":
                reasons = [response.get("answer_text", ""), str(observed.get("errors"))]
            tools = ", ".join(response.get("tools_used", [])) or "없음"
            common = [observed["id"], observed["http_status"], f"{graded['expected_route']} / {graded['raw_route']}", graded["channel_category"], f"{response.get('final_status')} / {graded['action']}", f"{graded['route_correct']} / {graded['action_correct']}"]
            lines.append("| " + " | ".join(cell(x) for x in common + ["; ".join(reasons), tools, cell(response.get("answer_text", ""), 350)]) + " |")
            public_lines.append("| " + " | ".join(cell(x) for x in common + [tools]) + " |")
        for observed in saved["cases"]:
            lines += ["", f"### {observed['id']}", "", "질문: " + observed["question"], "", "답변 전문:", "", observed["response"].get("answer_text", "응답 없음"), "",
                      "해석·Grader·Recovery·사용량:", "", "```json", json.dumps({k: observed.get(k) for k in ("question_interpretation", "interpretation_trace", "grader_calls", "recovery_calls", "usage", "errors")}, ensure_ascii=False, indent=2), "```"]
    (OUT / "RESULTS.md").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    (ROOT / "docs/INTERPRETATION_EVAL_V1.md").write_bytes(("\n".join(public_lines) + "\n").encode("utf-8"))
    print(json.dumps({k: {x: v for x, v in data.items() if x != "rows"} for k, data in metrics.items()}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "run", "report"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument("--variant", choices=VARIANTS)
    parser.add_argument("--secrets-json", type=Path)
    args = parser.parse_args()
    if args.stage == "prepare":
        if not args.source:
            parser.error("prepare requires --source")
        prepare(args.source)
    elif args.stage == "run":
        if not all((args.split, args.variant, args.secrets_json)):
            parser.error("run requires --split --variant --secrets-json")
        run(args.split, args.variant, args.secrets_json)
    else:
        report()


if __name__ == "__main__":
    main()
