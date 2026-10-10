"""Freeze Phase A query choices before retrieval; no Gold or retrieval execution."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from src.graph.first_literature_query import select_phase_a_query, select_deterministic_query
from src.graph.literature_scope import derive_literature_subquestion
from src.routing.contracts import RouterInput
from src.routing.interpretation_router import InterpretationRouter
from src.interpretation.exercise_catalog import catalog_sha256


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new_json(path: Path, value: Any) -> None:
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


class SnapshotExerciseRepository:
    """Exact stored canonical labels only; the snapshot precedes all interpretation."""
    def __init__(self, names: tuple[str, ...]):
        if not names or len(set(names)) != len(names) or any(not isinstance(n, str) or not n.strip() for n in names):
            raise ValueError('Invalid canonical catalog snapshot')
        self.names = tuple(sorted(names))

    def list_canonical_exercises(self) -> tuple[str, ...]:
        return self.names

    def resolve_canonical_exercise(self, name: str) -> str | None:
        matches = [item for item in self.names if item.casefold() == name.casefold()]
        return matches[0] if len(matches) == 1 else None


def prepare_query_record(question: str, result: Any, legacy_router: Any) -> dict[str, Any]:
    """This function intentionally has no case ID, dataset label or Gold parameter."""
    interpretation = result.interpretation
    route = InterpretationRouter().route(interpretation).to_dict() if interpretation is not None else {'task_type': 'unknown'}
    provider = result.provider
    raw = provider.raw_interpretation if provider is not None else None
    subquestion = interpretation.literature_subquestion if interpretation is not None else (raw or {}).get('literature_subquestion')
    phase_a = select_phase_a_query(question, route, validation_status=result.execution_status, literature_subquestion=subquestion)
    legacy_route = legacy_router.route(RouterInput(question)).to_dict()
    deterministic = select_deterministic_query(question, legacy_route)
    derived = derive_literature_subquestion(question, legacy_route)
    return {
        'original_question': question,
        'runtime_task_type': route['task_type'],
        'phase_a_literature_subquestion': subquestion,
        'phase_a_validation_status': 'prevalidated_phase_a' if result.execution_status == 'ready' else 'validation_failed',
        'phase_a_execution_status': result.execution_status,
        'phase_a_validation_reasons': [item.model_dump(mode='json') for item in interpretation.unresolved_fields] if interpretation is not None else [],
        'phase_a_error_code': result.error_code,
        'phase_a': phase_a.to_dict(),
        'deterministic_runtime_task_type': legacy_route['task_type'],
        'deterministic_derive_result': derived,
        'deterministic': deterministic.to_dict(),
        'subquestion_differs_from_deterministic': subquestion != derived,
        'actual_query_differs_from_deterministic': phase_a.query != deterministic.query,
        'interpretation_response': result.model_dump(mode='json'),
    }


def isolation_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    phase_status = Counter(r['phase_a_validation_status'] for r in records)
    isolation = Counter(r['phase_a']['isolation_status'] for r in records)
    deterministic = Counter(r['deterministic']['isolation_status'] for r in records)
    return {
        'case_count': len(records),
        'phase_a_validation_success': phase_status['prevalidated_phase_a'],
        'phase_a_validation_failure': phase_status['validation_failed'],
        'phase_a_subquestion_equals_original': sum(r['phase_a_literature_subquestion'] == r['original_question'] for r in records),
        'phase_a_isolated': isolation['isolated'],
        'phase_a_original_already_suitable': isolation['original_already_suitable'],
        'fallback_original_count': sum(r['phase_a']['fallback'] for r in records),
        'fallback_reasons': dict(Counter(r['phase_a']['fallback_reason'] for r in records if r['phase_a']['fallback'])),
        'phase_a_isolation_statuses': dict(isolation),
        'deterministic_isolated': deterministic['isolated'],
        'deterministic_failure': deterministic['safe_fallback'],
        'deterministic_original_already_suitable': deterministic['original_already_suitable'],
        'deterministic_selection_success': deterministic['isolated'] + deterministic['original_already_suitable'],
        'deterministic_fallback_reasons': dict(Counter(r['deterministic']['fallback_reason'] for r in records if r['deterministic']['fallback'])),
        'phase_a_deterministic_query_difference_count': sum(r['actual_query_differs_from_deterministic'] for r in records),
        'api_requests': sum((r['interpretation_response'].get('provider') or {}).get('api_requests', 0) for r in records),
    }


def review_markdown(dataset: str, records: list[dict[str, Any]]) -> str:
    def cell(value):
        if value is None:
            return '—'
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    lines = [f'# {dataset} 원문 → Step 2 검색어 검토표', '',
             '주 조건은 Phase A이다. deterministic 결과는 참고이며 검색 결과로 선택하지 않는다.',
             '평가 분류는 표시용이며 검색어 선택은 실제 Phase A/Router 판정을 사용한다.', '',
             '| case ID | 평가 분류(표시용) / Phase A 실행 판정 | 원문 | Phase A 문헌 질문 | Phase A 검증 | deterministic derive | 실제 Step 2 query | query_source / isolation_status | fallback | 이유 |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for row in records:
        query = row['phase_a']
        values = [row['case_id'], row['display_task_type']+' / '+row['runtime_task_type'], row['original_question'],
                  row['phase_a_literature_subquestion'], row['phase_a_validation_status']+' ('+row['phase_a_execution_status']+')',
                  row['deterministic_derive_result'], query['query'], query['query_source']+' / '+query['isolation_status'],
                  '예' if query['fallback'] else '아니오', query['fallback_reason']]
        lines.append('| '+' | '.join(cell(value) for value in values)+' |')
    return '\n'.join(lines)+'\n'


def verify_frozen_rules(folder: Path, root: Path) -> dict[str, Any]:
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'frozen':
        raise ValueError('Query rules are not frozen')
    for item in manifest['sources']:
        if sha256(root / item['path']) != item['sha256']:
            raise ValueError('Query rule or interpretation source changed: '+item['path'])
    if sha256(folder / 'rules.json') != manifest['rules_sha256']:
        raise ValueError('Rule list changed')
    return manifest


def freeze_query_preparation(*, root: Path, output: Path, rules_folder: Path, datasets: dict[str, list[dict[str, str]]],
                             interpreter: Any, legacy_router: Any, model_config: dict[str, Any], input_hashes: dict[str, str]) -> dict[str, Any]:
    """Exactly one interpreter invocation per question. Completed rows survive interruption."""
    if output.exists():
        raise FileExistsError('Query output already exists; no overwrite or repeat interpretation')
    if set(datasets) != {'dev', 'dev-ko'}:
        raise ValueError('Only dev and dev-ko preparation is supported')
    for questions in datasets.values():
        if not questions or len({q['case_id'] for q in questions}) != len(questions):
            raise ValueError('Empty dataset or duplicate case IDs')
        if any(not q['question'].strip() for q in questions):
            raise ValueError('Empty question')
    verify_frozen_rules(rules_folder, root)
    catalog = interpreter.exercise_catalog.names()
    output.mkdir(parents=True)
    write_new_json(output / 'canonical_catalog.json', {'names': catalog, 'sha256': catalog_sha256(catalog), 'source': 'training.exercises read-only snapshot'})
    write_new_json(output / 'model_config.json', model_config)
    summaries = {}
    for dataset, questions in datasets.items():
        records = []
        with (output / (dataset+'.jsonl')).open('x', encoding='utf-8') as handle:
            for question in questions:
                verify_frozen_rules(rules_folder, root)
                result = interpreter.interpret(question['question'])
                record = prepare_query_record(question['question'], result, legacy_router)
                # IDs and labels are bookkeeping only, added after runtime selection.
                record.update(case_id=question['case_id'], display_task_type=question['display_task_type'],
                              model=(result.provider.model if result.provider is not None else None),
                              configured_model=model_config['model'], prompt_version=model_config['version'],
                              prompt_sha256=sha256(root / model_config['prompt_path']),
                              config_sha256=sha256(root / 'config/question_interpreter.json'),
                              decoding_config={k: model_config[k] for k in ('temperature','reasoning_effort','verbosity','max_output_tokens','max_retries','timeout_seconds')})
                handle.write(json.dumps(record, ensure_ascii=False)+'\n')
                handle.flush()
                records.append(record)
                print(f'{dataset} {question["case_id"]}: {record["phase_a_validation_status"]} / {record["phase_a"]["isolation_status"]}', flush=True)
        summaries[dataset] = isolation_summary(records)
        with (output / (dataset+'_REVIEW.md')).open('x', encoding='utf-8') as handle:
            handle.write(review_markdown(dataset, records))
    verify_frozen_rules(rules_folder, root)
    write_new_json(output / 'summary.json', summaries)
    manifest = {'version': output.name, 'status': 'frozen', 'review_status': 'awaiting_user_query_review',
                'frozen_at_utc': datetime.now(timezone.utc).isoformat(), 'primary_condition': 'phase_a',
                'reference_condition': 'deterministic', 'interpreter_calls_per_question': 1,
                'retrieval_executed': False, 'gold_used': False, 'rules_manifest_sha256': sha256(rules_folder / 'manifest.json'),
                'input_sha256': input_hashes,
                'artifacts': [{'path': path.name, 'sha256': sha256(path)} for path in sorted(output.iterdir()) if path.is_file()]}
    write_new_json(output / 'manifest.json', manifest)
    return manifest


def load_reviewed_queries(folder: Path, approval_path: Path, *, dataset: str) -> list[dict[str, Any]]:
    """No interpreter/provider path exists here; approval is tied to exact frozen bytes."""
    if dataset not in ('dev', 'dev-ko'):
        raise ValueError('Only dev and dev-ko are supported')
    approval = json.loads(approval_path.read_text(encoding='utf-8'))
    if approval.get('actor') != 'user' or approval.get('decision') != 'approved' or not approval.get('evidence_ref') or approval.get('manifest_sha256') != sha256(folder / 'manifest.json'):
        raise ValueError('User query review approval missing or mismatched')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] != 'frozen' or manifest['primary_condition'] != 'phase_a':
        raise ValueError('Invalid Phase A query freeze')
    for artifact in manifest['artifacts']:
        path = folder / artifact['path']
        if path.resolve().parent != folder.resolve() or sha256(path) != artifact['sha256']:
            raise ValueError('Frozen query artifact changed')
    return [json.loads(line) for line in (folder / (dataset+'.jsonl')).read_text(encoding='utf-8').splitlines() if line]
