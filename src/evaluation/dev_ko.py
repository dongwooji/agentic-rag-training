"""User-reviewed Korean dev questions; original Gold remains external and frozen."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from src.retrieval.hybrid import sha256_file


ABBREVIATIONS = ('RIR', 'RPE', '1RM', 'e1RM')
NUMBER = re.compile(r'\d+(?:,\d{3})*(?:\.\d+)?')
LATIN_TOKEN = re.compile(r'[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*')
ABBREVIATION_TOKEN = re.compile(r'(?<![A-Za-z0-9])(?:e1RM|1RM|RIR|RPE)(?:\d+)?(?![A-Za-z0-9])')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def eligible_cases(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in source['cases'] if c['category'] in ('literature_only', 'hybrid')]


def validate_draft(draft: dict[str, Any], source: dict[str, Any]) -> None:
    if draft.get('status') != 'awaiting_user_review':
        raise ValueError('Draft must await user review')
    if draft.get('source_dataset') != source['dataset_version']:
        raise ValueError('Source version mismatch')
    original = {c['id']: c for c in eligible_cases(source)}
    cases = draft['cases']
    if len(cases) != len(original) or {c['id'] for c in cases} != set(original):
        raise ValueError('Draft must map every eligible source case exactly once')
    if draft['translation_rules']['preserved_abbreviations'] != list(ABBREVIATIONS):
        raise ValueError('Abbreviation policy changed')
    for case in cases:
        if set(case) != {'id', 'question'}:
            raise ValueError('Draft questions must not contain Gold or labels')
        question = case['question']
        before = original[case['id']]['question']
        if not isinstance(question, str) or not re.search('[가-힣]', question):
            raise ValueError('Korean question required')
        for token in LATIN_TOKEN.findall(question):
            if not any(re.fullmatch(re.escape(a) + r'\d*', token) for a in ABBREVIATIONS):
                raise ValueError(f'Unexpected English term in {case["id"]}: {token}')
        if NUMBER.findall(question) != NUMBER.findall(before):
            raise ValueError(f'Numbers or dates changed: {case["id"]}')
        if ABBREVIATION_TOKEN.findall(question) != ABBREVIATION_TOKEN.findall(before):
            raise ValueError(f'Abbreviations changed: {case["id"]}')


def freeze_dev_ko(draft_path: Path, source_path: Path, approval_path: Path, output: Path) -> dict[str, Any]:
    """Approval must refer to the exact draft bytes that the user reviewed."""
    draft, source, approval = read_json(draft_path), read_json(source_path), read_json(approval_path)
    validate_draft(draft, source)
    draft_hash, source_hash = sha256_file(draft_path), sha256_file(source_path)
    if approval.get('decision') != 'approved' or approval.get('actor') != 'user' or approval.get('draft_sha256') != draft_hash or not approval.get('evidence_ref'):
        raise ValueError('Explicit user approval of the exact draft is required')
    source_manifest = read_json(source_path.with_name(source_path.stem + '.manifest.json'))
    if source_manifest.get('status') != 'frozen' or source_manifest.get('dataset_sha256') != source_hash:
        raise ValueError('Frozen source manifest mismatch')
    filenames = ('questions.json', 'mapping.json', 'translation_rules.json', 'user_approval.json', 'approved_draft.json', 'manifest.json')
    if any((output / name).exists() for name in filenames):
        raise FileExistsError('Refusing to overwrite frozen dev-ko artifacts')
    original = {c['id']: c for c in eligible_cases(source)}
    payloads = {
        'questions.json': {'dataset_version': draft['dataset_version'], 'status': 'frozen', 'cases': draft['cases']},
        'mapping.json': [{'id': c['id'], 'source_id': c['id'], 'original_question': original[c['id']]['question'], 'korean_question': c['question']} for c in draft['cases']],
        'translation_rules.json': draft['translation_rules'],
        'user_approval.json': approval,
    }
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        with (output / name).open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    with (output / 'approved_draft.json').open('xb') as handle:
        handle.write(draft_path.read_bytes())
    manifest = {
        'dataset_version': draft['dataset_version'], 'status': 'frozen',
        'frozen_at_utc': datetime.now(timezone.utc).isoformat(), 'case_count': len(draft['cases']),
        'source_dataset_version': source['dataset_version'], 'source_dataset_sha256': source_hash,
        'draft_sha256': draft_hash, 'gold_policy': 'link original Gold by source case ID; no new Gold',
        'artifacts': [{'path': name, 'sha256': sha256_file(output / name)} for name in (*payloads, 'approved_draft.json')],
    }
    with (output / 'manifest.json').open('x', encoding='utf-8') as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest


def load_frozen_dev_ko(folder: Path, source_path: Path) -> list[tuple[str, str]]:
    manifest = read_json(folder / 'manifest.json')
    if manifest.get('status') != 'frozen' or manifest.get('source_dataset_sha256') != sha256_file(source_path):
        raise ValueError('Frozen dev-ko/source hash mismatch')
    artifacts = manifest.get('artifacts', [])
    if {a['path'] for a in artifacts} != {'questions.json', 'mapping.json', 'translation_rules.json', 'user_approval.json', 'approved_draft.json'} or len(artifacts) != 5:
        raise ValueError('Incomplete dev-ko artifact manifest')
    for artifact in artifacts:
        name = artifact['path']
        if Path(name).name != name or sha256_file(folder / name) != artifact['sha256']:
            raise ValueError('Frozen dev-ko artifact hash mismatch')
    approval = read_json(folder / 'user_approval.json')
    if approval.get('decision') != 'approved' or approval.get('actor') != 'user' or approval.get('draft_sha256') != manifest['draft_sha256'] or not approval.get('evidence_ref'):
        raise ValueError('Missing user approval')
    payload = read_json(folder / 'questions.json')
    source = read_json(source_path)
    draft = read_json(folder / 'approved_draft.json')
    validate_draft(draft, source)
    if sha256_file(folder / 'approved_draft.json') != approval['draft_sha256'] or draft['cases'] != payload['cases'] or draft['translation_rules'] != read_json(folder / 'translation_rules.json') or draft['dataset_version'] != payload['dataset_version']:
        raise ValueError('Frozen questions differ from the user-approved draft')
    original = {c['id']: c for c in eligible_cases(source)}
    expected_mapping = [{'id': c['id'], 'source_id': c['id'], 'original_question': original[c['id']]['question'], 'korean_question': c['question']} for c in draft['cases']]
    if read_json(folder / 'mapping.json') != expected_mapping:
        raise ValueError('Frozen source mapping differs from approved questions')
    if payload.get('status') != 'frozen' or payload.get('dataset_version') != manifest['dataset_version'] or len(payload['cases']) != manifest['case_count']:
        raise ValueError('Frozen dev-ko dataset contract mismatch')
    return [(c['id'], c['question']) for c in payload['cases']]
