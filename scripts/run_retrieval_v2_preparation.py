"""Reproduce H0 or compare H1 on frozen dev/dev-ko; no held-out or DB writes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.retrieval_preparation import MemoryCosineStore, rank_questions, evaluate_rankings, compare_baseline, compare_source, rankings_equal_with_tolerance, compare_h1_to_h0
from src.evaluation.dev_ko import load_frozen_dev_ko
from src.retrieval.bm25 import BM25Index
from src.retrieval.runtime_config import DEFAULT_CONFIG_PATH, load_retrieval_config
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'data/models/huggingface')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument('--dataset', choices=('dev', 'dev-ko'), default='dev')
    parser.add_argument('--dev-ko-dir', type=Path, default=ROOT / 'data/evaluation/dev_ko_v1')
    parser.add_argument('--reference-run', type=Path, help='Same-dataset H0 directory; reproduce H0 or measure H1 changes')
    args = parser.parse_args()
    args.output = args.output.resolve()
    if not args.output.is_relative_to(ROOT / 'reports/experiments'):
        parser.error('New output must stay within reports/experiments')
    if args.output.exists():
        parser.error('Output must be a new versioned directory')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    config = load_retrieval_config(args.config)
    if config.query_mode != 'original_question':
        parser.error('Step 2/3 requires frozen query mapping and user approval; this runner cannot run generated-query retrieval')
    if config.setting == 'H1' and not args.reference_run:
        parser.error('H1 requires a same-dataset H0 reference run')
    if args.reference_run:
        reference = json.loads((args.reference_run / 'result.json').read_text(encoding='utf-8'))
        if reference['dataset'] != args.dataset or reference['config']['setting'] != 'H0' or reference['config_sha256'] != sha256_file(DEFAULT_CONFIG_PATH):
            parser.error('Reference must use the original H0 config and same dataset')
    assets = load_frozen_literature_assets(ROOT, config=config)
    dev_path = ROOT / 'data/evaluation/eval_dataset_v1.json'
    manifest = json.loads((ROOT / 'data/evaluation/eval_dataset_v1.manifest.json').read_text(encoding='utf-8'))
    frozen_inputs = assets.phase7_reproducibility['inputs']
    if sha256_file(dev_path) != manifest['dataset_sha256'] or manifest['dataset_sha256'] != frozen_inputs['eval_dataset_sha256'] or sha256_file(ROOT / 'data/evaluation/eval_dataset_v1.manifest.json') != frozen_inputs['eval_manifest_sha256']:
        raise RuntimeError('Frozen dev hash mismatch')
    cases = [c for c in json.loads(dev_path.read_text(encoding='utf-8'))['cases'] if c['category'] in ('literature_only', 'hybrid')]
    if len(cases) != frozen_inputs['literature_bearing_case_count']:
        raise RuntimeError('Frozen dev eligible case count mismatch')
    questions = [(c['id'], c['question']) for c in cases] if args.dataset == 'dev' else load_frozen_dev_ko(args.dev_ko_dir, dev_path)
    encoder = MiniLMEncoder(model_id=config.dense.model_id, model_revision=config.dense.model_revision, batch_size=config.dense.batch_size, cache_dir=args.cache_dir, device='cpu')
    vectors = encoder.encode([chunk['text'] for chunk in assets.chunks])
    store = MemoryCosineStore([c['chunk_id'] for c in assets.chunks], vectors)
    retriever = FrozenHybridRetriever(assets=assets, encoder=encoder, vector_store=store)
    rankings = rank_questions(retriever, questions)
    dense_rankings = {case_id: hits for (case_id, _), hits in zip(questions, store.searches, strict=True)}
    bm25_index = BM25Index(assets.chunks, k1=config.bm25.k1, b=config.bm25.b)
    bm25_rankings = {case_id: [asdict(h) for h in bm25_index.search(question, top_k=config.bm25.source_depth, score_policy=config.bm25.score_policy).hits] for case_id, question in questions}
    args.output.mkdir(parents=True)
    # Persist the rankings before evaluating them against Gold.
    (args.output / 'rankings.json').write_text(json.dumps(rankings, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'source_rankings.json').write_text(json.dumps({'dense': dense_rankings, 'bm25': bm25_rankings}, indent=2), encoding='utf-8')
    metrics = evaluate_rankings(cases, rankings)
    reproduce_h0 = config.setting == 'H0' and args.dataset == 'dev'
    comparison = compare_baseline(rankings, metrics, ROOT / 'reports/baselines/hybrid_baseline_v1') if reproduce_h0 else None
    source_comparison = {'dense': compare_source(dense_rankings, ROOT / 'reports/baselines/dense_baseline_v1/retrieval_results.jsonl'), 'bm25': compare_source(bm25_rankings, ROOT / 'reports/baselines/hybrid_baseline_v1/bm25_results.jsonl')} if reproduce_h0 else None
    passed = not reproduce_h0 or (comparison['passed'] and all(c['passed'] for c in source_comparison.values()))
    refactor_comparison = None
    if args.reference_run and config.setting == 'H0':
        before = json.loads((args.reference_run / 'rankings.json').read_text(encoding='utf-8'))
        before_sources = json.loads((args.reference_run / 'source_rankings.json').read_text(encoding='utf-8'))
        before_result = json.loads((args.reference_run / 'result.json').read_text(encoding='utf-8'))
        refactor_comparison = {'rankings_equal_with_score_tolerance': rankings_equal_with_tolerance(before, rankings), 'source_rankings_equal_with_score_tolerance': all(rankings_equal_with_tolerance(before_sources[name], hits) for name, hits in {'dense': dense_rankings, 'bm25': bm25_rankings}.items()), 'metrics_exact': before_result['metrics'] == metrics, 'inputs_match': before_result['corpus_sha256'] == assets.corpus_chunks_sha256 and before_result['dev_sha256'] == sha256_file(dev_path)}
        passed = passed and before_result['passed'] and all(refactor_comparison.values())
    source_metrics = {name: evaluate_rankings(cases, hits) for name, hits in {'dense': dense_rankings, 'bm25': bm25_rankings}.items()}
    result = {'version': 'retrieval_v2_step1_v1', 'dataset': args.dataset, 'corpus': assets.corpus_version, 'corpus_sha256': assets.corpus_chunks_sha256, 'dev_sha256': sha256_file(dev_path), 'config': config.model_dump(), 'config_sha256': sha256_file(args.config), 'encoder': asdict(encoder.metadata), 'backend': 'transient_memory_exact_cosine', 'database_used': False, 'llm_used': False, 'metrics': metrics, 'source_metrics': source_metrics, 'comparison': comparison, 'source_comparison': source_comparison, 'refactor_comparison': refactor_comparison, 'passed': passed}
    result['source_sha256'] = {name: sha256_file(ROOT / name) for name in ['scripts/run_retrieval_v2_preparation.py', 'src/evaluation/retrieval_preparation.py', 'src/retrieval/hybrid.py', 'src/retrieval/runtime_config.py', 'src/retrieval/dense.py', 'src/retrieval/bm25.py', 'src/retrieval/rrf.py']}
    if args.dataset == 'dev-ko':
        result['dev_ko_manifest_sha256'] = sha256_file(args.dev_ko_dir / 'manifest.json')
    if config.setting == 'H1':
        result['h0_comparison'] = compare_h1_to_h0(result, rankings, {'dense': dense_rankings, 'bm25': bm25_rankings}, args.reference_run)
        result['reference_sha256'] = {name: sha256_file(args.reference_run / name) for name in ('result.json', 'rankings.json', 'source_rankings.json')}
        passed = result['passed'] = result['h0_comparison']['passed']
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': passed, 'case_count': len(cases), 'metrics': metrics['macro']}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
