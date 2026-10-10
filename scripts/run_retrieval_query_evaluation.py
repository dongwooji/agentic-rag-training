"""Approved frozen query evaluation on dev/dev-ko only, without LLM or DB."""

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.evaluation.literature_query_freeze import load_reviewed_conditions, sha256, write_new_json
from src.evaluation.query_retrieval import cumulative_decisions
from src.evaluation.retrieval_preparation import (
    MemoryCosineStore, evaluate_rankings, rankings_equal_with_tolerance,
)
from src.retrieval.runtime_config import load_retrieval_config
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.retrieval.bm25 import BM25Index

CONFIGS = dict(H1='retrieval_h1_v1', step2='retrieval_step2_generation_v1',
               step3a='retrieval_step3a_translation_v1', step3b='retrieval_step3b_translation_v1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--translation', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--h1-reference', type=Path, required=True)
    parser.add_argument('--rankings-run', type=Path, help='Resume metrics from an interrupted same-query ranking run')
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / 'reports/experiments') or args.output.exists():
        parser.error('Use a new versioned output within reports/experiments')
    # Approval and both complete freeze chains are checked BEFORE model/assets.
    queries = {d: load_reviewed_conditions(args.generation, args.translation, args.approval, dataset=d)
               for d in ('dev', 'dev-ko')}
    configs = {name: load_retrieval_config(ROOT / 'config' / (version + '.json'))
               for name, version in CONFIGS.items()}
    for config in configs.values():
        normalized = config.model_dump()
        normalized.update(config_version=configs['H1'].config_version, query_mode='original_question')
        if normalized != configs['H1'].model_dump():
            raise ValueError('Non-query retrieval configuration changed')
    references = {}
    for dataset, suffix in (('dev', 'h1_dev'), ('dev-ko', 'h1_dev_ko')):
        folder = args.h1_reference / suffix
        references[dataset] = {n: json.loads((folder / (n + '.json')).read_text(encoding='utf-8'))
                               for n in ('result', 'rankings', 'source_rankings')}
        if (not references[dataset]['result']['passed'] or
                references[dataset]['result']['config_sha256'] != sha256(ROOT / 'config/retrieval_h1_v1.json')):
            raise ValueError('Invalid historical H1 reference')
    dev_path = ROOT / 'data/evaluation/eval_dataset_v1.json'
    if any(r['result']['dev_sha256'] != sha256(dev_path) for r in references.values()):
        raise ValueError('Frozen dev changed')
    if references['dev-ko']['result']['dev_ko_manifest_sha256'] != sha256(
            ROOT / 'data/evaluation/dev_ko_v1/manifest.json'):
        raise ValueError('Frozen dev-ko changed')
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    assets = load_frozen_literature_assets(ROOT, config=configs['H1'])
    if any(r['result']['corpus_sha256'] != assets.corpus_chunks_sha256 for r in references.values()):
        raise ValueError('Reference corpus differs')
    encoder = MiniLMEncoder(model_id=configs['H1'].dense.model_id,
                            model_revision=configs['H1'].dense.model_revision,
                            batch_size=configs['H1'].dense.batch_size,
                            cache_dir=ROOT / 'data/models/huggingface', device='cpu')
    vectors = None if args.rankings_run else encoder.encode([c['text'] for c in assets.chunks])
    args.output.mkdir(parents=True)
    rankings, sources = {}, {}
    if args.rankings_run:
        if not args.rankings_run.resolve().is_relative_to(ROOT / 'reports/experiments'):
            parser.error('Ranking input must stay in reports/experiments')
        saved_queries = json.loads((args.rankings_run / 'queries.json').read_text(encoding='utf-8'))
        if saved_queries != json.loads(json.dumps(queries)):
            raise ValueError('Saved ranking run used different query inputs')
        rankings = json.loads((args.rankings_run / 'rankings.json').read_text(encoding='utf-8'))
        sources = json.loads((args.rankings_run / 'source_rankings.json').read_text(encoding='utf-8'))
        if set(rankings) != set(CONFIGS) or set(sources) != set(CONFIGS):
            raise ValueError('Ranking conditions differ')
    for condition, config in (() if args.rankings_run else configs.items()):
        rankings[condition], sources[condition] = {}, {}
        for dataset, rows in queries.items():
            store = MemoryCosineStore([c['chunk_id'] for c in assets.chunks], vectors)
            retriever = FrozenHybridRetriever(assets=replace(assets, retrieval_config=config),
                                              encoder=encoder, vector_store=store)
            bm25 = BM25Index(assets.chunks, k1=config.bm25.k1, b=config.bm25.b)
            ranked, lexical = {}, {}
            for row in rows:
                dense_query, bm25_query = row[condition]
                ranked[row['case_id']] = [asdict(h) for h in retriever.search(
                    dense_query, bm25_query=bm25_query, top_k=config.rrf.top_k).hits]
                lexical[row['case_id']] = [asdict(h) for h in bm25.search(
                    bm25_query, top_k=config.bm25.source_depth, score_policy=config.bm25.score_policy).hits]
            rankings[condition][dataset] = ranked
            sources[condition][dataset] = dict(dense={r['case_id']: hits for r, hits in
                                                      zip(rows, store.searches, strict=True)}, bm25=lexical)
            print(f'{condition} {dataset}: ranked {len(rows)} cases', flush=True)
    # All rankings are persisted before reading Gold and computing metrics.
    write_new_json(args.output / 'rankings.json', rankings)
    write_new_json(args.output / 'source_rankings.json', sources)
    write_new_json(args.output / 'queries.json', queries)
    cases = [c for c in json.loads(dev_path.read_text(encoding='utf-8'))['cases']
             if c['category'] in ('literature_only', 'hybrid')]
    metrics = {condition: {d: evaluate_rankings(cases, ranked) for d, ranked in data.items()}
               for condition, data in rankings.items()}
    h1_checks = {d: (
        rankings_equal_with_tolerance(ref['rankings'], rankings['H1'][d]) and
        all(rankings_equal_with_tolerance(ref['source_rankings'][name], sources['H1'][d][name])
            for name in ('dense', 'bm25')) and ref['result']['metrics'] == metrics['H1'][d])
        for d, ref in references.items()}
    if not all(h1_checks.values()):
        raise RuntimeError('H1 reproduction failed; no adoption decision issued')
    grouped = {condition: {d: {category: evaluate_rankings(
        [c for c in cases if c['category'] == category],
        {c['id']: ranked[c['id']] for c in cases if c['category'] == category})
        for category in ('hybrid', 'literature_only')} for d, ranked in data.items()}
        for condition, data in rankings.items()}
    literature_unchanged = {d: all(rankings_equal_with_tolerance(
        {c['id']: rankings['H1'][d][c['id']]}, {c['id']: rankings['step2'][d][c['id']]})
        for c in cases if c['category'] == 'literature_only') for d in queries}
    if not all(literature_unchanged.values()):
        raise RuntimeError('Step 2 changed literature-only results')
    decision = cumulative_decisions(metrics)
    source_metrics = {condition: {d: {name: evaluate_rankings(cases, ranked)
        for name, ranked in data.items()} for d, data in datasets.items()}
        for condition, datasets in sources.items()}
    result = dict(version=args.output.name, corpus='literature_corpus_v1',
                  corpus_sha256=assets.corpus_chunks_sha256, encoder=asdict(encoder.metadata),
                  metrics=metrics, grouped_metrics=grouped, source_metrics=source_metrics,
                  h1_reproduction=h1_checks, step2_literature_unchanged=literature_unchanged,
                  **decision, llm_calls=0, database_used=False, held_out_used=False,
                  backend='transient_memory_exact_cosine',
                  manifest_sha256={s: sha256(p / 'manifest.json') for s, p in
                                   (('generation', args.generation), ('translation', args.translation))},
                  approval_sha256=sha256(args.approval),
                  resumed_ranking_sha256=({p:sha256(args.rankings_run / p) for p in
                                          ('queries.json','rankings.json','source_rankings.json')}
                                         if args.rankings_run else None),
                  config_sha256={c: sha256(ROOT / 'config' / (v + '.json')) for c, v in CONFIGS.items()},
                  source_sha256={p: sha256(ROOT / p) for p in (
                      'scripts/run_retrieval_query_evaluation.py', 'src/evaluation/query_retrieval.py',
                      'src/retrieval/literature_query.py', 'src/retrieval/hybrid.py',
                      'src/retrieval/dense.py', 'src/retrieval/bm25.py', 'src/retrieval/rrf.py')})
    write_new_json(args.output / 'result.json', result)
    write_new_json(args.output / 'manifest.json', dict(status='frozen', version=args.output.name,
        artifacts=[dict(path=p.name, sha256=sha256(p)) for p in sorted(args.output.iterdir())]))
    print(json.dumps(dict(selected=decision['selected_condition'],
        metrics={c:{d:m['macro'] for d,m in data.items()} for c,data in metrics.items()})))


if __name__ == '__main__':
    main()
