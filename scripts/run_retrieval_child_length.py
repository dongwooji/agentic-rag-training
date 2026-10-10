"""Independent child correction / 256 / 512 comparisons against frozen Step 3b."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.evaluation.literature_query_freeze import load_reviewed_conditions, sha256, write_new_json
from src.evaluation.retrieval_preparation import MemoryCosineStore, evaluate_rankings, rankings_equal_with_tolerance
from src.evaluation.dense_length import verify_frozen_folder, truncation_statistics, select_condition, combined_metrics
from src.retrieval.runtime_config import load_retrieval_config
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.child import token_count
from src.retrieval.child_quota import ParentQuotaChildStore
from src.retrieval.bm25 import BM25Index

VERSIONS = dict(step3b='retrieval_step3b_translation_v1',
                child_corrected='retrieval_step4_child_corrected_v2',
                parent256='retrieval_parent_length256_v1', parent512='retrieval_parent_length512_v1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(ROOT/'reports/experiments'):
        parser.error('Use a new versioned output under reports/experiments')
    generation = ROOT/'data/evaluation/retrieval_step2_generation_queries_v1'
    translation = ROOT/'data/evaluation/retrieval_step3_translation_queries_v2'
    approval = ROOT/'reports/experiments/retrieval_v2_step23_query_revalidation_v2/approval.json'
    queries = {d: load_reviewed_conditions(generation,translation,approval,dataset=d) for d in ('dev','dev-ko')}
    if any(len(rows) != 18 for rows in queries.values()):
        raise ValueError('Expected the approved paired 18+18 cases')
    historical = ROOT/'reports/experiments/retrieval_v2_step23_evaluation_v2'
    verify_frozen_folder(historical)
    old = json.loads((historical/'result.json').read_text(encoding='utf-8'))
    old_rankings = json.loads((historical/'rankings.json').read_text(encoding='utf-8'))['step3b']
    old_sources = json.loads((historical/'source_rankings.json').read_text(encoding='utf-8'))['step3b']
    configs = {name: load_retrieval_config(ROOT/'config'/(version+'.json')) for name,version in VERSIONS.items()}
    base = configs['step3b']
    if old['config_sha256']['step3b'] != sha256(ROOT/'config/retrieval_step3b_translation_v1.json'):
        raise ValueError('Historical Step 3b config changed')
    for config in configs.values():
        normalized = config.model_dump()
        normalized.update(schema_version=base.schema_version,config_version=base.config_version,
                          embedding_run_id=base.embedding_run_id)
        normalized['dense'].update(unit='parent',max_sequence_length=base.dense.max_sequence_length)
        normalized['dense'].pop('child',None)
        if normalized != base.model_dump():
            raise ValueError('Undeclared change beyond representation/input length')
    representations = dict(child_corrected=ROOT/'data/literature/representations/retrieval_child_v1',
        parent256=ROOT/'data/literature/representations/retrieval_parent_length256_v2',
        parent512=ROOT/'data/literature/representations/retrieval_parent_length512_v1')
    manifests = {name: verify_frozen_folder(path) for name,path in representations.items()}
    sources = {p: sha256(ROOT/p) for p in (
        'docs/decisions/RETRIEVAL_V2_CHILD_CORRECTION_LENGTH.md',
        'scripts/run_retrieval_child_length.py', 'src/evaluation/dense_length.py',
        'src/evaluation/child_depth.py','src/evaluation/retrieval_preparation.py',
        'src/evaluation/retrieval_metrics.py','src/retrieval/child_quota.py','src/retrieval/child.py',
        'src/retrieval/dense.py','src/retrieval/runtime_config.py','src/retrieval/hybrid.py',
        'src/retrieval/bm25.py','src/retrieval/rrf.py')}
    sources.update({'config/'+v+'.json':sha256(ROOT/'config'/(v+'.json')) for v in VERSIONS.values()})
    for name in ('parent256','parent512'):
        for p,digest in manifests[name]['sources'].items():
            if sha256(ROOT/p) != digest:
                raise ValueError('Length embedding sources changed after freeze')
    args.output.mkdir(parents=True)
    protocol = dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(),baseline='step3b',
        baseline_is_posthoc=True,step4_is_implementation_correction=True,comparison='independent',
        conditions=list(VERSIONS)[1:],criteria=['combined CE@10 nondecrease','per-case EGR improved > worsened'],
        tiebreak=['combined CE@10','combined EGR@10','parent512 > parent256 > child_corrected'],
        metric_atol=1e-12,sources=sources, datasets=['dev','dev-ko'],
        representation_manifest_sha256={name:sha256(path/'manifest.json') for name,path in representations.items()},
        historical_manifest_sha256=sha256(historical/'manifest.json'),
        generation_manifest_sha256=sha256(generation/'manifest.json'),
        translation_manifest_sha256=sha256(translation/'manifest.json'),approval_sha256=sha256(approval))
    write_new_json(args.output/'protocol.json',protocol)
    for p in sources:
        target = args.output/'sources'/p
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/p).read_bytes())
    def verify_sources():
        if any(sha256(ROOT/p) != digest for p,digest in sources.items()):
            raise ValueError('Predeclared source changed during evaluation')
    verify_sources()
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    assets = load_frozen_literature_assets(ROOT,config=base)
    if assets.corpus_chunks_sha256 != old['corpus_sha256'] or any(
            m['parent_corpus_sha256'] != assets.corpus_chunks_sha256 for m in manifests.values()):
        raise ValueError('Parent corpus changed')
    dev = ROOT/'data/evaluation/eval_dataset_v1.json'
    dev_manifest = json.loads((ROOT/'data/evaluation/eval_dataset_v1.manifest.json').read_text(encoding='utf-8'))
    if sha256(dev) != dev_manifest['dataset_sha256'] or sha256(dev) != assets.phase7_reproducibility['inputs']['eval_dataset_sha256']:
        raise ValueError('Frozen dev changed')
    import numpy as np
    children = [json.loads(line) for line in (representations['child_corrected']/'children.jsonl').read_text(encoding='utf-8').splitlines()]
    child_vectors = np.load(representations['child_corrected']/'embeddings.npy',allow_pickle=False)
    encoders, vectors, truncation = {}, {}, {}
    for name in VERSIONS:
        config = configs[name]
        if name == 'child_corrected':
            encoder = encoders['step3b']
        else:
            encoder = MiniLMEncoder(model_id=config.dense.model_id,model_revision=config.dense.model_revision,
                batch_size=config.dense.batch_size,cache_dir=ROOT/'data/models/huggingface',device='cpu',
                **({'max_seq_length':config.dense.max_sequence_length} if name != 'step3b' else {}))
        encoders[name] = encoder
        if name == 'child_corrected':
            if asdict(encoder.metadata) != manifests[name]['encoder']:
                raise ValueError('Child encoder runtime differs from frozen representation')
            if child_vectors.shape != (len(children),config.dense.embedding_dimension):
                raise ValueError('Child vector shape differs')
            if len({c['child_id'] for c in children}) != len(children) or {c['parent_id'] for c in children} != set(assets.chunks_by_id):
                raise ValueError('Child parent membership differs')
            for c in children:
                if (assets.chunks_by_id[c['parent_id']]['text'][c['start_char']:c['end_char']] != c['text']
                        or token_count(encoder.tokenizer,c['text']) != c['token_count'] or c['token_count'] > 110):
                    raise ValueError('Child source span/token contract mismatch')
            vectors[name] = child_vectors
            continue
        lengths = [token_count(encoder.tokenizer,c['text']) for c in assets.chunks]
        truncation[name] = truncation_statistics(lengths,config.dense.max_sequence_length)
        if name == 'step3b':
            vectors[name] = encoder.encode([c['text'] for c in assets.chunks])
        else:
            if asdict(encoder.metadata) != manifests[name]['encoder'] or manifests[name]['embedding_run_id'] != config.embedding_run_id:
                raise ValueError('Length embedding encoder/run mismatch')
            tokens = json.loads((representations[name]/'parent_tokens.json').read_text(encoding='utf-8'))
            if [(p['parent_id'],p['token_count']) for p in tokens] != [(c['chunk_id'],n) for c,n in zip(assets.chunks,lengths,strict=True)]:
                raise ValueError('Length embedding parent order/token mismatch')
            if truncation[name] != json.loads((representations[name]/'stats.json').read_text(encoding='utf-8')):
                raise ValueError('Length coverage audit differs')
            vectors[name] = np.load(representations[name]/'embeddings.npy',allow_pickle=False)
            if vectors[name].shape != (len(assets.chunks),config.dense.embedding_dimension):
                raise ValueError('Parent vector shape differs')
    rankings, sources_ranked, mappings = {}, {}, {}
    for name,config in configs.items():
        verify_sources()
        ranked,sourced,mapped = {}, {}, {}
        for dataset,rows in queries.items():
            if name == 'child_corrected':
                raw = MemoryCosineStore([c['child_id'] for c in children],vectors[name])
                store = ParentQuotaChildStore(raw,children,set(assets.chunks_by_id),child_depth=50)
            else:
                store = MemoryCosineStore([c['chunk_id'] for c in assets.chunks],vectors[name])
            retriever = FrozenHybridRetriever(assets=replace(assets,retrieval_config=config),encoder=encoders[name],vector_store=store)
            bm25 = BM25Index(assets.chunks,k1=config.bm25.k1,b=config.bm25.b)
            hits,lexical,latencies = {}, {}, {}
            for row in rows:
                q,b = row['step3b']
                response = retriever.search(q,bm25_query=b,top_k=10)
                hits[row['case_id']] = [asdict(h) for h in response.hits]
                latencies[row['case_id']] = response.latency_ms
                lexical[row['case_id']] = [asdict(h) for h in bm25.search(b,top_k=10,score_policy='positive_only').hits]
            ranked[dataset] = hits
            sourced[dataset] = dict(dense={r['case_id']:h for r,h in zip(rows,store.searches,strict=True)},bm25=lexical,latency_ms=latencies)
            mapped[dataset] = ({r['case_id']:dict(pool=pool,**mapping) for r,pool,mapping in
                zip(rows,store.child_searches,store.mappings,strict=True)} if name == 'child_corrected' else None)
            if name == 'child_corrected' and any(m['actual_parent_count'] != 10 for m in mapped[dataset].values()):
                raise ValueError('Corrected child failed to fill the frozen corpus parent quota')
        condition_dir = args.output/name
        condition_dir.mkdir()
        write_new_json(condition_dir/'rankings.json',ranked)
        write_new_json(condition_dir/'source_rankings.json',sourced)
        write_new_json(condition_dir/'child_mappings.json',mapped)
        rankings[name],sources_ranked[name],mappings[name] = ranked,sourced,mapped
        print(f'{name}: stored 36 rankings before Gold evaluation',flush=True)
    reproduction = {d:rankings_equal_with_tolerance(old_rankings[d],rankings['step3b'][d]) and all(
        rankings_equal_with_tolerance(old_sources[d][s],sources_ranked['step3b'][d][s]) for s in ('dense','bm25')) for d in queries}
    if not all(reproduction.values()):
        raise ValueError('Step 3b reproduction failed')
    if any(not rankings_equal_with_tolerance(sources_ranked['step3b'][d]['bm25'],sources_ranked[name][d]['bm25'])
           for name in VERSIONS for d in queries):
        raise ValueError('Independent condition changed BM25')
    # All four conditions are ranked before Gold is read at all.
    cases = [c for c in json.loads(dev.read_text(encoding='utf-8'))['cases'] if c['category'] in ('hybrid','literature_only')]
    metrics = {name:{d:evaluate_rankings(cases,hits) for d,hits in ranked.items()} for name,ranked in rankings.items()}
    if metrics['step3b'] != old['metrics']['step3b'] or any(combined_metrics(m)['case_count'] != 36 for m in metrics.values()):
        raise ValueError('Baseline metrics or case count differ')
    grouped = {name:{d:{category:evaluate_rankings([c for c in cases if c['category']==category],
        {c['id']:hits[c['id']] for c in cases if c['category']==category}) for category in ('hybrid','literature_only')}
        for d,hits in ranked.items()} for name,ranked in rankings.items()}
    selection = select_condition(metrics['step3b'],{name:metrics[name] for name in VERSIONS if name != 'step3b'})
    verify_sources()
    write_new_json(args.output/'result.json',dict(version=args.output.name,metrics=metrics,grouped_metrics=grouped,
        combined_metrics={name:combined_metrics(m) for name,m in metrics.items()},**selection,
        truncation=truncation,representation_stats=json.loads((representations['child_corrected']/'stats.json').read_text(encoding='utf-8')),
        step3b_reproduction=reproduction,bm25_parent_unchanged=True,step4_is_implementation_correction=True,
        encoder={name:asdict(e.metadata) for name,e in encoders.items()},protocol_sha256=sha256(args.output/'protocol.json'),
        corpus_sha256=assets.corpus_chunks_sha256,service_default='legacy/H0',llm_calls=0,database_used=False,held_out_used=False,
        queries={d:[dict(case_id=r['case_id'],dense=r['step3b'][0],bm25=r['step3b'][1]) for r in rows] for d,rows in queries.items()}))
    write_new_json(args.output/'manifest.json',dict(status='frozen',version=args.output.name,
        artifacts=[dict(path=p.relative_to(args.output).as_posix(),sha256=sha256(p)) for p in sorted(args.output.rglob('*')) if p.is_file()]))
    print(json.dumps(dict(selected=selection['selected_condition'],eligible=selection['eligible'])))


if __name__ == '__main__':
    main()
