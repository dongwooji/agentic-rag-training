"""Sequential immutable query preparation, without retrieval or Gold imports."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median

from src.retrieval.literature_query import (
    generate_korean, translate_english, validate_translation, TRANSLATION_VALIDATOR_VERSION,
)

SOURCE_PATHS = (
    'config/literature_query_generation_v1.md', 'config/literature_query_translation_v1.md',
    'config/literature_query_model_v1.json', 'src/retrieval/literature_query.py',
    'src/retrieval/query_provider.py', 'src/evaluation/literature_query_freeze.py',
    'scripts/prepare_literature_queries.py',
    'config/retrieval_h1_v1.json', 'config/retrieval_step2_generation_v1.json',
    'config/retrieval_step3a_translation_v1.json', 'config/retrieval_step3b_translation_v1.json',
    'src/retrieval/runtime_config.py', 'src/retrieval/hybrid.py', 'src/tools/literature.py',
    'src/graph/initial_literature_tool.py', 'src/graph/nodes.py', 'src/graph/state.py',
    'src/graph/workflow.py', 'src/graph/interpreted_workflow.py', 'src/api/dependencies.py',
    'scripts/run_retrieval_v2_preparation.py',
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new_json(path: Path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def source_hashes(root: Path):
    return {path: sha256(root / path) for path in SOURCE_PATHS}


def verify_prompt_approval(root: Path, approval: Path):
    record = json.loads(approval.read_text(encoding='utf-8'))
    if (record.get('actor') != 'user' or record.get('decision') != 'approved'
            or not record.get('evidence_ref') or record.get('sources') != source_hashes(root)):
        raise ValueError('Explicit user prompt approval is missing or source hashes changed')


def read_frozen(folder: Path, *, stage: str | None = None):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status') != 'frozen' or (stage and manifest.get('stage') != stage):
        raise ValueError('Invalid frozen query manifest')
    # Require both question files to be integrity-checked, never unlisted bytes.
    artifacts = {item['path']: item['sha256'] for item in manifest['artifacts']}
    if not {'dev.jsonl', 'dev-ko.jsonl'}.issubset(artifacts):
        raise ValueError('Query manifest must cover both development datasets')
    for path, expected in artifacts.items():
        target = folder / path
        if not target.resolve().is_relative_to(folder.resolve()) or sha256(target) != expected:
            raise ValueError('Frozen query artifact changed')
    rows = {dataset: [json.loads(line) for line in (folder / (dataset+'.jsonl')).read_text(encoding='utf-8').splitlines()]
            for dataset in ('dev', 'dev-ko')}
    for records in rows.values():
        if not records or len({row['case_id'] for row in records}) != len(records):
            raise ValueError('Missing or duplicate query cases')
    return rows


def usage_summary(records):
    traces = [record['result']['provenance'] for record in records]
    latency = sorted(trace.get('latency_ms', 0) for trace in traces)
    result = {field: sum(trace.get(field, 0) for trace in traces) for field in (
        'api_requests', 'failed_calls', 'input_tokens', 'cached_input_tokens', 'output_tokens',
        'total_tokens', 'estimated_cost_usd', 'latency_ms')}
    result.update(cases=len(records), validation_failed=sum(row['result']['validation_status']=='failed' for row in records),
                  fallback_count=sum(row['result']['fallback'] for row in records),
                  fallback_reasons=dict(Counter(reason for row in records for reason in row['result']['fallback_reasons'])),
                  usage_unavailable_calls=sum(t.get('api_requests',0) for t in traces if not t.get('usage_available',False)),
                  latency_median_ms=median(latency), latency_p95_ms=latency[min(len(latency)-1, int(len(latency)*0.95))])
    return result


def review_markdown(dataset, records, stage):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    lines = [f'# {dataset}: {stage} 검토', '',
             '검색 전 사용자 검토용. 평가 분류는 표시용이며 생성기에 전달하지 않는다.', '',
             '| case ID | 표시 분류 | 원문 | has_personal_context | 한국어 생성문 | Step 2 실제 검색어 | 영어 생성문 | 영어 실제 검색어 | 보완 여부 / 이유 |',
             '|---|---|---|---|---|---|---|---|---|']
    for row in records:
        ko = row['step2'] if stage == 'translation' else row['result']
        en = row['result'] if stage == 'translation' else None
        values = [row['case_id'], row['display_task_type'], row['original_question'], ko['has_personal_context'],
                  ko['generated_query'], ko['actual_query'], en['generated_query'] if en else '',
                  en['actual_query'] if en else '',
                  f"Step2={ko['fallback']}:{ko['fallback_reasons']}; Step3={en['fallback'] if en else ''}:{en['fallback_reasons'] if en else ''}"]
        lines.append('| '+' | '.join(map(cell, values))+' |')
    return '\n'.join(lines)+'\n'


def freeze_stage(*, output: Path, stage: str, datasets, provider, input_manifest_sha256: str,
                 sources: dict[str,str], verify_sources=lambda: None):
    """Write-once rows survive interruption; incomplete outputs are never re-run."""
    if stage not in ('generation', 'translation') or set(datasets) != {'dev', 'dev-ko'}:
        raise ValueError('Only generation/translation of dev and dev-ko are allowed')
    if output.exists():
        raise FileExistsError('Use a new version; never overwrite or repeat calls')
    for rows in datasets.values():
        if not rows or len({r['case_id'] for r in rows}) != len(rows):
            raise ValueError('Empty dataset or duplicate case IDs')
    verify_sources()
    output.mkdir(parents=True)
    summaries = {}
    for dataset, inputs in datasets.items():
        records = []
        with (output / (dataset+'.jsonl')).open('x', encoding='utf-8') as handle:
            for source in inputs:
                verify_sources()
                original = source['original_question']
                text = original if stage == 'generation' else source['result']['actual_query']
                result = generate_korean(provider, text) if stage == 'generation' else translate_english(provider, text)
                # Labels are attached only AFTER the query decision.
                record = dict(case_id=source['case_id'], display_task_type=source['display_task_type'],
                              original_question=original, result=result)
                if stage == 'translation':
                    record['step2'] = source['result']
                handle.write(json.dumps(record, ensure_ascii=False)+'\n')
                handle.flush()
                records.append(record)
        summaries[dataset] = usage_summary(records)
        with (output / (dataset+'_REVIEW.md')).open('x', encoding='utf-8') as handle:
            handle.write(review_markdown(dataset, records, stage))
    verify_sources()
    write_new_json(output / 'summary.json', summaries)
    manifest = dict(version=output.name, stage=stage, status='frozen', review_status='awaiting_user_query_review',
                    frozen_at_utc=datetime.now(timezone.utc).isoformat(), input_manifest_sha256=input_manifest_sha256,
                    sources=sources, llm_calls_per_case=1, retrieval_executed=False, gold_used=False,
                    artifacts=[dict(path=p.name,sha256=sha256(p)) for p in sorted(output.iterdir()) if p.is_file()])
    write_new_json(output / 'manifest.json', manifest)
    return manifest


def load_reviewed_conditions(generation: Path, translation: Path, approval_path: Path, *, dataset: str):
    """Retrieval inputs have no LLM path. Approval is tied to BOTH manifests."""
    if dataset not in ('dev', 'dev-ko'):
        raise ValueError('Only development datasets are supported')
    hashes = {'generation':sha256(generation / 'manifest.json'), 'translation':sha256(translation / 'manifest.json')}
    approval = json.loads(approval_path.read_text(encoding='utf-8'))
    if (approval.get('actor') != 'user' or approval.get('decision') != 'approved'
            or not approval.get('evidence_ref') or approval.get('manifest_sha256') != hashes):
        raise ValueError('User mapping approval missing or mismatched')
    ko = read_frozen(generation, stage='generation')[dataset]
    en = read_frozen(translation, stage='translation')[dataset]
    manifest = json.loads((translation/'manifest.json').read_text(encoding='utf-8'))
    if manifest['input_manifest_sha256'] != hashes['generation']:
        raise ValueError('Translation must derive from this frozen Step 2 manifest')
    if [r['case_id'] for r in ko] != [r['case_id'] for r in en]:
        raise ValueError('Translation cases differ')
    results = []
    for korean, english in zip(ko, en):
        if english['step2'] != korean['result'] or english['result']['input_query'] != korean['result']['actual_query']:
            raise ValueError('Translation input differs from frozen actual Step 2 query')
        original, k, e = korean['original_question'], korean['result']['actual_query'], english['result']['actual_query']
        results.append(dict(case_id=korean['case_id'], H1=(original,original), step2=(k,k), step3a=(k,e), step3b=(e,e)))
    return results


def revalidate_translation_freeze(*, previous: Path, output: Path, validator_source: Path):
    """Revalidate existing model bytes, with no provider, secret or new API call."""
    if output.exists():
        raise FileExistsError('Use a new version; never overwrite a translation freeze')
    datasets = read_frozen(previous, stage='translation')
    old_manifest = json.loads((previous / 'manifest.json').read_text(encoding='utf-8'))
    source_hash = sha256(validator_source)
    output.mkdir(parents=True)
    summaries = {}
    for dataset, records in datasets.items():
        changes = []
        for row in records:
            result = row['result']
            old_status = {key: result[key] for key in
                          ('validation_status', 'actual_query', 'fallback', 'fallback_reasons')}
            # Schema/provider failures remain failures; only successfully
            # parsed string outputs are eligible for offline revalidation.
            query = result.get('generated_query')
            if isinstance(query, str):
                query = query.strip()
                reasons = validate_translation(result['input_query'], query) if query else ['empty_query']
                result.update(actual_query=result['input_query'] if reasons else query,
                              query_source='step2' if reasons else 'translation',
                              validation_status='failed' if reasons else 'passed',
                              fallback=bool(reasons), fallback_reasons=reasons)
            row['revalidation'] = dict(validator_version=TRANSLATION_VALIDATOR_VERSION,
                                       previous=old_status, additional_llm_calls=0)
            if old_status['actual_query'] != result['actual_query']:
                changes.append(row['case_id'])
        with (output / (dataset + '.jsonl')).open('x', encoding='utf-8') as handle:
            for row in records:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        with (output / (dataset + '_REVIEW.md')).open('x', encoding='utf-8') as handle:
            handle.write(review_markdown(dataset, records, 'translation'))
        summaries[dataset] = dict(cases=len(records), actual_query_changed=changes,
                                  validation_failed=sum(r['result']['fallback'] for r in records),
                                  additional_llm_calls=0, additional_tokens=0, additional_cost_usd=0)
    if sha256(validator_source) != source_hash:
        raise ValueError('Validator changed during revalidation')
    write_new_json(output / 'summary.json', summaries)
    manifest = dict(version=output.name, stage='translation', status='frozen',
                    review_status='user_authorized_revalidation',
                    frozen_at_utc=datetime.now(timezone.utc).isoformat(),
                    input_manifest_sha256=old_manifest['input_manifest_sha256'],
                    previous_translation_manifest_sha256=sha256(previous / 'manifest.json'),
                    validator_version=TRANSLATION_VALIDATOR_VERSION,
                    validator_sha256=source_hash, llm_calls_per_case=0,
                    provenance_usage='historical original calls; not charged again',
                    retrieval_executed=False, gold_used=False,
                    artifacts=[dict(path=p.name, sha256=sha256(p)) for p in sorted(output.iterdir())])
    write_new_json(output / 'manifest.json', manifest)
    return manifest
