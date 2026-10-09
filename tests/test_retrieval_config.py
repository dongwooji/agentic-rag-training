from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from src.retrieval.bm25 import BM25Index
from src.retrieval.hybrid import (
    FROZEN_CORPUS_CHUNKS_SHA256, FROZEN_CORPUS_MANIFEST_SHA256, FROZEN_PHASE7_MANIFEST_SHA256,
    FrozenHybridRetriever, load_frozen_literature_assets, sha256_file,
)
from src.retrieval.postgres import SearchHit, SearchResponse
from src.retrieval.runtime_config import DEFAULT_RETRIEVAL_CONFIG, RetrievalConfig, load_retrieval_config


def test_default_config_preserves_historical_hashes_and_settings():
    config = load_retrieval_config()
    assert config.validation.corpus_chunks_sha256 == FROZEN_CORPUS_CHUNKS_SHA256 == '640bf8bcf3b6126c1755e27844286e9945743c1cb8b7b983d67590ad27ca0177'
    assert config.validation.corpus_manifest_sha256 == FROZEN_CORPUS_MANIFEST_SHA256 == '84982b1bf7f4226755c8a1335b7a1dd6da0271fc241b00a15212db445133ca66'
    assert config.validation.hybrid_manifest_sha256 == FROZEN_PHASE7_MANIFEST_SHA256 == '015924df97fc29130e81f971aed7b7f1946e9daf7f02c63315995bf21bf92e1f'
    assert config.dense.max_sequence_length == 128
    assert config.dense.source_depth == config.bm25.source_depth == config.rrf.top_k == 10
    assert config.bm25.score_policy == 'retain_zero'


@pytest.mark.parametrize('change', ['gold', 'bad_hash', 'unsupported_query', 'filter_zero', 'bad_depth', 'unknown_nested', 'malformed_revision'])
def test_invalid_config_fails_closed(change):
    data = deepcopy(DEFAULT_RETRIEVAL_CONFIG.model_dump())
    if change == 'gold': data['gold_path'] = 'evaluation.json'
    elif change == 'bad_hash': data['validation']['corpus_chunks_sha256'] = 'bad'
    elif change == 'unsupported_query': data['query_mode'] = 'literature_subquestion'
    elif change == 'filter_zero': data['bm25']['score_policy'] = 'drop_zero'
    elif change == 'bad_depth': data['dense']['source_depth'] = 1
    elif change == 'unknown_nested': data['dense']['labels'] = []
    else: data['dense']['model_revision'] = 'main'
    with pytest.raises(ValidationError):
        RetrievalConfig.model_validate(data)


@pytest.fixture
def synthetic_assets(tmp_path):
    chunks = [{'chunk_id': f'c{i:02}', 'text': 'alpha beta', 'corpus_version': 'synthetic'} for i in range(12)]
    chunks_path = tmp_path / 'data/literature/processed/chunks.jsonl'
    corpus_manifest = tmp_path / 'data/literature/manifests/corpus_v1.json'
    baseline = tmp_path / 'reports/baselines/hybrid_baseline_v1'
    chunks_path.parent.mkdir(parents=True)
    corpus_manifest.parent.mkdir(parents=True)
    baseline.mkdir(parents=True)
    chunks_path.write_text('\n'.join(json.dumps(c) for c in chunks) + '\n', encoding='utf-8')
    corpus_manifest.write_text(json.dumps({'corpus_version': 'synthetic', 'chunk_count': len(chunks)}), encoding='utf-8')
    index = BM25Index(chunks)
    reproduction = {'phase7_version': 'hybrid_baseline_v1', 'inputs': {'corpus_chunks_sha256': sha256_file(chunks_path)}, 'bm25': index.metadata, 'rrf': {'rrf_k': 60, 'weights': {'dense': 1.0, 'bm25': 1.0}, 'source_depth': {'dense': 10, 'bm25': 10}}}
    for name, payload in [('reproducibility.json', reproduction), ('bm25_index_metadata.json', index.metadata)]:
        (baseline / name).write_text(json.dumps(payload), encoding='utf-8')
    manifest = {'status': 'frozen', 'phase7_version': 'hybrid_baseline_v1', 'configuration_status': 'untuned_first_configuration', 'artifacts': [{'path': name, 'sha256': sha256_file(baseline / name)} for name in ['reproducibility.json', 'bm25_index_metadata.json']]}
    (baseline / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    data = DEFAULT_RETRIEVAL_CONFIG.model_dump()
    data['corpus_version'] = 'synthetic'
    data['validation'] = {'corpus_chunks_sha256': sha256_file(chunks_path), 'corpus_manifest_sha256': sha256_file(corpus_manifest), 'hybrid_manifest_sha256': sha256_file(baseline / 'manifest.json'), 'expected_chunk_count': len(chunks)}
    config = RetrievalConfig.model_validate(data)
    return tmp_path, config


@pytest.mark.parametrize('relative_path,message', [
    ('data/literature/processed/chunks.jsonl', 'chunks hash changed'),
    ('data/literature/manifests/corpus_v1.json', 'manifest hash changed'),
    ('reports/baselines/hybrid_baseline_v1/manifest.json', 'manifest hash changed'),
    ('reports/baselines/hybrid_baseline_v1/bm25_index_metadata.json', 'artifact hash changed'),
])
def test_config_driven_hash_validation_rejects_tampering(synthetic_assets, relative_path, message):
    root, config = synthetic_assets
    load_frozen_literature_assets(root, config=config)
    with (root / relative_path).open('a', encoding='utf-8') as handle:
        handle.write(' ')
    with pytest.raises(RuntimeError, match=message):
        load_frozen_literature_assets(root, config=config)


def test_config_values_drive_queries_and_preserve_zero_match(synthetic_assets):
    root, config = synthetic_assets
    assets = load_frozen_literature_assets(root, config=config)
    seen = []
    class Encoder:
        def encode(self, texts, *, show_progress):
            seen.append(texts)
            return np.ones((len(texts), 384))
    class Store:
        def search_exact_cosine(self, query, *, embedding_run_id, top_k):
            seen.append((embedding_run_id, top_k))
            return SearchResponse([SearchHit(f'c{i:02}', .9-i*.01) for i in range(top_k)], .1)
    result = FrozenHybridRetriever(assets=assets, encoder=Encoder(), vector_store=Store()).search('없는질문')
    assert seen == [['없는질문'], (config.embedding_run_id, config.dense.source_depth)]
    assert len(result.hits) == 10
    assert all(h.bm25_score == 0 and h.bm25_rrf_contribution > 0 for h in result.hits)
    altered = config.model_dump()
    altered['rrf']['k'] = 61
    with pytest.raises(RuntimeError, match='config differs'):
        FrozenHybridRetriever(assets=assets, encoder=Encoder(), vector_store=Store(), config=RetrievalConfig.model_validate(altered))


def test_actual_encoder_metadata_must_match_config(synthetic_assets):
    root, config = synthetic_assets
    assets = load_frozen_literature_assets(root, config=config)
    encoder = SimpleNamespace(metadata=SimpleNamespace(**config.dense.model_dump()))
    encoder.metadata.max_sequence_length = 64
    with pytest.raises(RuntimeError, match='max_sequence_length'):
        FrozenHybridRetriever(assets=assets, encoder=encoder, vector_store=object())


def test_modified_config_must_still_match_frozen_metadata(synthetic_assets):
    root, config = synthetic_assets
    data = config.model_dump()
    data['bm25']['k1'] = 1.5
    with pytest.raises(RuntimeError, match='bm25_k1'):
        load_frozen_literature_assets(root, config=RetrievalConfig.model_validate(data))
