"""Read-only operating-path comparison and one-pass REST observation, no Gold.

Run with C:/mini/python.exe and explicit --secrets-json. Never loads secrets
automatically. Output directories must be new; completed requests are persisted
immediately and never retried by this harness.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from threading import Thread
from time import perf_counter, sleep
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUESTIONS = (
    '근비대가 목적이면 한 세트에 몇 개 정도 하는 게 좋아? 무게 가볍게 해서 많이 하는 것도 효과 있어?',
    '같은 운동량이면 주 2번에 몰아서 하는 거랑 3번으로 나눠서 하는 거랑 근력 차이 많이 나?',
    '매 세트 실패할 때까지 해야 근육이 커져? 한두 개 여유 남기고 끝내도 돼?',
    '디로드 주간은 꼭 해야 돼? 하면 뭐가 좋은데?',
    '내 벤치프레스 기록 보면 요즘 정체기야? 정체면 뭘 바꿔보면 좋을지 연구 근거로 알려줘',
    '스쿼트 마지막 기록이 최고 기록보다 많이 떨어졌는데, 중간에 운동 쉬었던 것 때문일까?',
    '데드리프트 주간 볼륨이 어떻게 변해왔는지 보여주고, 볼륨 더 늘리면 근력에 도움이 될지 알려줘',
    '내 기록에서 운동 제일 오래 쉬었던 때가 언제야? 그 정도 쉬면 근력 많이 빠져?',
    '운동할 때마다 어깨가 찌릿하게 아픈데 회전근개 파열인지 판단해주고 재활 운동 짜줘',
    '다음 달 보디빌딩 대회 나가는데 체지방 빼는 식단이랑 보충제 용량 정해줘',
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def json_safe(value):
    from src.graph.nodes import _json_safe
    return _json_safe(value)


def usage_summary(events):
    return dict(calls=len(events), by_stage=dict(Counter(x['stage'] for x in events)),
                **{key: sum(x['usage'].get(key, 0) or 0 for x in events)
                   for key in ('input_tokens', 'cached_input_tokens', 'output_tokens',
                               'total_tokens', 'estimated_cost_usd')},
                usage_unavailable_calls=sum(not x['usage_available'] for x in events))


class ObservedProvider:
    """Delegates unchanged calls, recording only bounded usage metadata."""

    def __init__(self, delegate, stage, events):
        self.delegate, self.stage, self.events = delegate, stage, events

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def invoke(self, value, **kwargs):
        start = perf_counter()
        event = dict(stage=self.stage, usage={}, usage_available=False)
        try:
            result = self.delegate.invoke(value, **kwargs)
            provenance = getattr(result, 'provenance', {})
            usage = json_safe(getattr(result, 'token_usage', getattr(result, 'usage', None)))
            if usage is None:
                usage = {k: provenance.get(k, 0) for k in ('input_tokens', 'cached_input_tokens',
                         'output_tokens', 'total_tokens', 'estimated_cost_usd')}
            received = bool(getattr(result, 'response_id', None) or provenance.get('response_id'))
            event.update(usage=usage, usage_available=provenance.get('usage_available', received),
                         response_received=received,
                         model=getattr(result, 'model', None) or provenance.get('model'),
                         failed=bool(getattr(result, 'error', None) or getattr(result, 'error_detail', None)))
            return result
        finally:
            event['latency_ms'] = (perf_counter() - start) * 1000
            self.events.append(event)


class ObservedMethod:
    def __init__(self, delegate, method):
        self.delegate, self.method, self.calls = delegate, method, []

    def __getattr__(self, name):
        if name != self.method:
            return getattr(self.delegate, name)

        def call(value):
            result = getattr(self.delegate, name)(value)
            self.calls.append(json_safe(result))
            return result
        return call


def compare(password, output):
    import numpy as np
    from src.api.dependencies import DEFAULT_MODEL_CACHE, DEFAULT_SERVICE_RETRIEVAL_CONFIG
    from src.database.config import DatabaseConfig
    from src.evaluation.retrieval_preparation import MemoryCosineStore
    from src.retrieval.hybrid import FrozenHybridRetriever
    from src.retrieval.runtime_config import load_retrieval_config
    from src.tools.literature import LiteratureTool

    config = load_retrieval_config(DEFAULT_SERVICE_RETRIEVAL_CONFIG)
    frozen = ROOT / 'data/evaluation/retrieval_step3_translation_queries_v2'
    manifest = json.loads((frozen / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'frozen'
    for artifact in manifest['artifacts']:
        assert digest(frozen / artifact['path']) == artifact['sha256']
    questions = []
    for dataset in ('dev', 'dev-ko'):
        rows = [json.loads(line) for line in (frozen / f'{dataset}.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(rows) == 18
        questions.extend((dataset, row['case_id'], row['result']['actual_query']) for row in rows)
    tool = LiteratureTool.from_postgres(password=password, config=DatabaseConfig.from_environment(),
                                       cache_dir=DEFAULT_MODEL_CACHE, retrieval_config=config)
    try:
        operating = tool._retriever
        assets, encoder, store = tool._assets, operating._encoder, operating._vector_store
        ids = [x['chunk_id'] for x in assets.chunks]
        vectors = encoder.encode([x['text'] for x in assets.chunks])
        memory = MemoryCosineStore(ids, vectors)
        offline = FrozenHybridRetriever(assets=assets, encoder=encoder, vector_store=memory)
        with store._connection.cursor() as cursor:
            cursor.execute('SELECT chunk_id, text_sha256, embedding::text FROM retrieval.chunk_embeddings WHERE embedding_run_id=%s',
                           (config.embedding_run_id,))
            stored = {row[0]: (str(row[1]), np.array(json.loads(row[2]), dtype=np.float32)) for row in cursor.fetchall()}
            cursor.execute('SELECT corpus_version, chunk_count FROM literature.corpus_runs ORDER BY corpus_version')
            corpus_runs = cursor.fetchall()
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='retrieval'")
            indexes = [row[0] for row in cursor.fetchall()]
        assert set(stored) == set(ids)
        assert all(stored[x['chunk_id']][0] == x['text_sha256'] for x in assets.chunks)
        max_vector_diff = max(float(np.max(np.abs(vectors[i] - stored[chunk_id][1]))) for i, chunk_id in enumerate(ids))
        results = []
        for dataset, case_id, query in questions:
            left = [asdict(x) for x in offline.search(query).hits]
            right = [asdict(x) for x in operating.search(query).hits]
            same = [x['chunk_id'] for x in left] == [x['chunk_id'] for x in right]
            shared = set(x['chunk_id'] for x in left) & set(x['chunk_id'] for x in right)
            lmap, rmap = ({x['chunk_id']: x for x in hits} for hits in (left, right))
            differences = {cid: {key: abs(lmap[cid][key] - rmap[cid][key])
                                 for key in ('dense_score', 'bm25_score', 'rrf_score')
                                 if lmap[cid][key] is not None and rmap[cid][key] is not None} for cid in shared}
            results.append(dict(dataset=dataset, case_id=case_id, query=query, exact_order_match=same,
                                set_match=set(lmap) == set(rmap), offline=left, operating=right,
                                score_abs_differences=differences))
            print(f'{dataset}/{case_id}: exact_order_match={same}', flush=True)
        report = dict(kind='reproducibility_only_no_gold', corpus_version=assets.corpus_version,
                      config_path=DEFAULT_SERVICE_RETRIEVAL_CONFIG.relative_to(ROOT).as_posix(),
                      config_sha256=digest(DEFAULT_SERVICE_RETRIEVAL_CONFIG), config=config.model_dump(mode='json'),
                      frozen_query_manifest_sha256=digest(frozen / 'manifest.json'),
                      encoder=asdict(encoder.metadata), database=store.server_metadata(),
                      corpus_runs=corpus_runs, indexes=indexes, document_vector_max_abs_difference=max_vector_diff,
                      offline_vector_source='Fresh pinned encoder output over unchanged operating corpus, no DB vector reuse',
                      offline_cosine='MemoryCosineStore float64 norm/dot; pgvector float32 vector cosine',
                      cases=results, mismatch_count=sum(not x['exact_order_match'] for x in results),
                      exact_match_count=sum(x['exact_order_match'] for x in results))
        write_json(output / 'comparison.json', report)
    finally:
        tool.close()


def rest(output, *, interpretation_mode='legacy'):
    import uvicorn
    import src.api.dependencies as dependencies
    from src.api.app import create_app
    from scripts.run_api_integration_smoke import CapturingWorkflow

    if os.environ.get('AGENTIC_RAG_RETRIEVAL_CONFIG') or os.environ.get('AGENTIC_RAG_QUESTION_INTERPRETATION'):
        raise RuntimeError('Observation requires unchanged service defaults without environment overrides')
    if interpretation_mode not in ('legacy', 'phase_a'):
        raise ValueError('Unsupported observation interpretation mode')
    events = []
    constructors = {
        'OpenAIRuntimeEvidenceProvider': 'grader', 'OpenAIEvidenceRecoveryProvider': 'recovery',
        'OpenAIFinalAnswerProvider': 'answer', 'OpenAIToolInputProvider': 'resolver',
        'OpenAIQuestionInterpreterProvider': 'interpreter', 'OpenAILiteratureQueryProvider': 'query',
    }
    from contextlib import ExitStack
    with ExitStack() as stack:
        # Only this observation selects the existing mode. The service default
        # and all interpretation/retrieval configuration files stay unchanged.
        stack.enter_context(patch.dict(os.environ, {'AGENTIC_RAG_QUESTION_INTERPRETATION': interpretation_mode}))
        for name, stage in constructors.items():
            constructor = getattr(dependencies, name)

            def factory(*args, _ctor=constructor, _stage=stage, **kwargs):
                actual_stage = 'step2' if kwargs.get('stage') == 'generation' else 'step3' if kwargs.get('stage') == 'translation' else _stage
                return ObservedProvider(_ctor(*args, **kwargs), actual_stage, events)
            stack.enter_context(patch.object(dependencies, name, factory))
        runtime = dependencies.get_workflow()
    expected_class = dependencies.InterpretedWorkflow if interpretation_mode == 'phase_a' else dependencies.AgenticRAGWorkflow
    if not isinstance(runtime._workflow, expected_class):
        dependencies.close_workflow()
        raise RuntimeError('Constructed workflow does not match requested interpretation mode')
    nodes = getattr(runtime._workflow, 'nodes', runtime._workflow)
    grader = ObservedMethod(nodes.runtime_grader, 'grade')
    recovery = ObservedMethod(nodes.recovery_agent, 'generate')
    nodes.runtime_grader, nodes.recovery_agent = grader, recovery
    literature = ObservedMethod(nodes.literature_tool, 'execute')
    nodes.literature_tool = literature
    nodes.tool_executor._tools['search_literature'] = literature
    captured = CapturingWorkflow(runtime)
    app = create_app()
    app.dependency_overrides[dependencies.get_workflow] = lambda: captured
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8012, log_level='warning', access_log=False))
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    deadline = perf_counter() + 30
    while not server.started:
        if not thread.is_alive() or perf_counter() > deadline:
            raise RuntimeError('REST server did not start')
        sleep(0.1)
    results = dict(kind='AI-authored qualitative user-style smoke questions; no Gold; not benchmark or generalization evaluation',
                   started_at_utc=datetime.now(timezone.utc).isoformat(), requests_per_question=1,
                   default_interpretation='legacy', service_config_sha256=digest(dependencies.DEFAULT_SERVICE_RETRIEVAL_CONFIG),
                   interpretation_mode=interpretation_mode, workflow_class=type(runtime._workflow).__name__,
                   corpus_version=runtime._literature_tool.provenance['corpus_version'], cases=[])
    try:
        for i, question in enumerate(QUESTIONS, 1):
            offsets = (len(events), len(grader.calls), len(recovery.calls), len(literature.calls), len(captured._states))
            started = perf_counter()
            request = Request('http://127.0.0.1:8012/query', data=json.dumps({'question': question}, ensure_ascii=False).encode('utf-8'),
                              headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with urlopen(request, timeout=900) as response:
                    status, payload = response.status, json.load(response)
            except HTTPError as error:
                status, payload = error.code, json.load(error)
            except Exception as error:
                # No second request, even when transport outcome is uncertain.
                status, payload = None, {'transport_error_type': type(error).__name__}
            elapsed = (perf_counter() - started) * 1000
            state = captured.latest_state() if len(captured._states) > offsets[4] else {}
            calls = events[offsets[0]:]
            result = dict(number=i, question=question, http_status=status, response=payload, latency_ms=elapsed,
                          route=state.get('route'), step2=(state.get('first_query_selection') or {}).get('step2'),
                          step3=(state.get('first_query_selection') or {}).get('step3'),
                          initial_query=state.get('initial_query'), tool_results=state.get('tool_results', []),
                          interpretation_status=state.get('interpretation_status'),
                          question_interpretation=state.get('question_interpretation'),
                          interpretation_trace=state.get('interpretation_trace'),
                          literature_calls=literature.calls[offsets[3]:], grader_calls=grader.calls[offsets[1]:],
                          recovery_calls=recovery.calls[offsets[2]:], retry_count=state.get('retry_count', 0),
                          terminal=state.get('final_status'), query_history=state.get('query_history', []),
                          llm_events=calls, usage=usage_summary(calls),
                          errors=[{'stage': x.get('stage'), 'code': x.get('code')} for x in state.get('errors', [])])
            results['cases'].append(result)
            results['total_usage'] = usage_summary(events)
            write_json(output / 'rest.json', results)
            print(f'Q{i}: HTTP {status}, terminal={result["terminal"]}, LLM={len(calls)}', flush=True)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        dependencies.close_workflow()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('compare', 'rest'), required=True)
    parser.add_argument('--secrets-json', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interpretation-mode', choices=('legacy', 'phase_a'), default='legacy',
                        help='Select the existing mode for this REST observation only; service default is unchanged')
    args = parser.parse_args()
    if args.stage != 'rest' and args.interpretation_mode != 'legacy':
        parser.error('--interpretation-mode only applies to REST observations')
    args.output.mkdir(parents=True, exist_ok=False)
    secrets = json.loads(args.secrets_json.read_text(encoding='utf-8-sig'))
    for key in ('PGPASSWORD', 'OPENAI_API_KEY'):
        if key in secrets:
            os.environ[key] = secrets[key]
    try:
        if args.stage == 'compare':
            compare(os.environ['PGPASSWORD'], args.output)
        else:
            rest(args.output, interpretation_mode=args.interpretation_mode)
    finally:
        for key in ('PGPASSWORD', 'OPENAI_API_KEY'):
            os.environ.pop(key, None)


if __name__ == '__main__':
    main()
