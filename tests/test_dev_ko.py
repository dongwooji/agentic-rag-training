from copy import deepcopy
import json

import pytest

from src.evaluation.dev_ko import freeze_dev_ko, load_frozen_dev_ko, validate_draft
from src.retrieval.hybrid import sha256_file


@pytest.fixture
def review(tmp_path):
    source = {'dataset_version': 'synthetic_v1', 'cases': [{'id': 'a', 'category': 'hybrid', 'question': 'Squat 2017-01-02 e1RM RIR2 RPE8 1RM은?', 'gold': {'secret': 'never copied'}}]}
    draft = {'dataset_version': 'synthetic_ko_v1', 'status': 'awaiting_user_review', 'source_dataset': 'synthetic_v1', 'translation_rules': {'preserved_abbreviations': ['RIR', 'RPE', '1RM', 'e1RM']}, 'cases': [{'id': 'a', 'question': '스쿼트 2017-01-02 e1RM RIR2 RPE8 1RM은?'}]}
    source_path, draft_path, approval_path = [tmp_path / name for name in ('source.json', 'draft.json', 'approval.json')]
    source_path.write_text(json.dumps(source), encoding='utf-8')
    draft_path.write_text(json.dumps(draft), encoding='utf-8')
    (tmp_path / 'source.manifest.json').write_text(json.dumps({'status': 'frozen', 'dataset_sha256': sha256_file(source_path)}), encoding='utf-8')
    approval_path.write_text(json.dumps({'actor': 'user', 'decision': 'approved', 'draft_sha256': sha256_file(draft_path), 'evidence_ref': 'synthetic test approval'}), encoding='utf-8')
    return source, draft, source_path, draft_path, approval_path, tmp_path / 'frozen'


def test_freeze_requires_exact_approval_and_links_original_gold(review):
    source, draft, source_path, draft_path, approval_path, output = review
    manifest = freeze_dev_ko(draft_path, source_path, approval_path, output)
    assert manifest['source_dataset_sha256'] == sha256_file(source_path)
    assert load_frozen_dev_ko(output, source_path) == [('a', draft['cases'][0]['question'])]
    assert 'secret' not in (output / 'questions.json').read_text(encoding='utf-8')
    assert 'gold' not in json.loads((output / 'questions.json').read_text(encoding='utf-8'))['cases'][0]
    with pytest.raises(FileExistsError):
        freeze_dev_ko(draft_path, source_path, approval_path, output)


@pytest.mark.parametrize('change', ['reject', 'wrong_hash', 'non_user', 'no_evidence'])
def test_freeze_rejects_missing_or_stale_approval(review, change):
    _, _, source_path, draft_path, approval_path, output = review
    approval = json.loads(approval_path.read_text(encoding='utf-8'))
    if change == 'reject': approval['decision'] = 'pending'
    elif change == 'wrong_hash': approval['draft_sha256'] = '0'*64
    elif change == 'non_user': approval['actor'] = 'agent'
    else: approval.pop('evidence_ref')
    approval_path.write_text(json.dumps(approval), encoding='utf-8')
    with pytest.raises(ValueError, match='Explicit user approval'):
        freeze_dev_ko(draft_path, source_path, approval_path, output)
    assert not output.exists()


@pytest.mark.parametrize('change', ['english', 'number', 'abbreviation', 'case_missing', 'gold'])
def test_draft_validation_rejects_semantic_invariant_changes(review, change):
    source, draft, *_ = review
    draft = deepcopy(draft)
    if change == 'english': draft['cases'][0]['question'] += ' failure'
    elif change == 'number': draft['cases'][0]['question'] = draft['cases'][0]['question'].replace('2017', '2018')
    elif change == 'abbreviation': draft['cases'][0]['question'] = draft['cases'][0]['question'].replace('e1RM', '1RM')
    elif change == 'case_missing': draft['cases'] = []
    else: draft['cases'][0]['gold'] = {}
    with pytest.raises(ValueError):
        validate_draft(draft, source)


@pytest.mark.parametrize('name', ['questions.json', 'mapping.json', 'translation_rules.json', 'user_approval.json', 'approved_draft.json'])
def test_frozen_artifact_tampering_rejected(review, name):
    _, _, source_path, draft_path, approval_path, output = review
    freeze_dev_ko(draft_path, source_path, approval_path, output)
    with (output / name).open('a', encoding='utf-8') as handle: handle.write(' ')
    with pytest.raises(ValueError, match='hash mismatch'):
        load_frozen_dev_ko(output, source_path)


def test_unfrozen_dev_ko_is_rejected_before_model_or_output(review, monkeypatch):
    from types import SimpleNamespace
    from scripts import run_retrieval_v2_preparation as cli

    source, _, source_path, _, _, output = review
    root = source_path.parent
    dev = root / 'data/evaluation/eval_dataset_v1.json'
    dev.parent.mkdir(parents=True)
    dev.write_text(json.dumps(source), encoding='utf-8')
    manifest = dev.with_name('eval_dataset_v1.manifest.json')
    manifest.write_text(json.dumps({'dataset_sha256': sha256_file(dev)}), encoding='utf-8')
    assets = SimpleNamespace(phase7_reproducibility={'inputs': {'eval_dataset_sha256': sha256_file(dev), 'eval_manifest_sha256': sha256_file(manifest), 'literature_bearing_case_count': 1}})
    monkeypatch.setattr(cli, 'ROOT', root)
    monkeypatch.setattr(cli, 'load_frozen_literature_assets', lambda *a, **kw: assets)
    def forbidden_encoder(**kwargs):
        raise AssertionError('Model must not execute before freeze')
    monkeypatch.setattr(cli, 'MiniLMEncoder', forbidden_encoder)
    destination = root / 'reports/experiments/test'
    monkeypatch.setattr(cli.sys, 'argv', ['runner', '--dataset', 'dev-ko', '--dev-ko-dir', str(output), '--output', str(destination)])
    with pytest.raises(FileNotFoundError):
        cli.main()
    assert not destination.exists()


def test_questions_cannot_change_after_approval_even_if_artifact_hash_is_updated(review):
    _, _, source_path, draft_path, approval_path, output = review
    freeze_dev_ko(draft_path, source_path, approval_path, output)
    questions_path = output / 'questions.json'
    payload = json.loads(questions_path.read_text(encoding='utf-8'))
    payload['cases'][0]['question'] += ' 추가 설명을 해줘.'
    questions_path.write_text(json.dumps(payload), encoding='utf-8')
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for a in manifest['artifacts']:
        if a['path'] == 'questions.json': a['sha256'] = sha256_file(questions_path)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='user-approved draft'):
        load_frozen_dev_ko(output, source_path)
