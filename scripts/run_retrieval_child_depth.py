"""Step 3b -> child -> depth sweep, frozen queries and combined preregistered criteria."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.evaluation.literature_query_freeze import load_reviewed_conditions, sha256, write_new_json
from src.evaluation.retrieval_preparation import (
    MemoryCosineStore, evaluate_rankings, rankings_equal_with_tolerance,
)
from src.evaluation.child_depth import adoption_decision, combined_metrics
from src.retrieval.runtime_config import load_retrieval_config
from src.retrieval.hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.bm25 import BM25Index
from src.retrieval.child import ParentChildStore, token_count


def verified_json_folder(folder):
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('status')!='frozen':
        raise ValueError('Input must be frozen')
    for item in manifest['artifacts']:
        path=folder/item['path']
        if not path.resolve().is_relative_to(folder.resolve()) or sha256(path)!=item['sha256']:
            raise ValueError('Frozen input artifact changed')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--children',type=Path,default=ROOT/'data/literature/representations/retrieval_child_v1')
    args=parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(ROOT/'reports/experiments'):
        parser.error('Use a new versioned output under reports/experiments')
    if not args.children.resolve().is_relative_to(ROOT/'data/literature/representations'):
        parser.error('Use a separate child representation')
    generation=ROOT/'data/evaluation/retrieval_step2_generation_queries_v1'
    translation=ROOT/'data/evaluation/retrieval_step3_translation_queries_v2'
    approval=ROOT/'reports/experiments/retrieval_v2_step23_query_revalidation_v2/approval.json'
    queries={d:load_reviewed_conditions(generation,translation,approval,dataset=d) for d in ('dev','dev-ko')}
    if any(len(rows)!=18 for rows in queries.values()):
        raise ValueError('Expected the approved paired 18+18 cases')
    historical=ROOT/'reports/experiments/retrieval_v2_step23_evaluation_v2'
    verified_json_folder(historical)
    old=json.loads((historical/'result.json').read_text(encoding='utf-8'))
    old_rankings=json.loads((historical/'rankings.json').read_text(encoding='utf-8'))['step3b']
    old_sources=json.loads((historical/'source_rankings.json').read_text(encoding='utf-8'))['step3b']
    versions=['retrieval_step3b_translation_v1','retrieval_step4_child_v1']+[
        f'retrieval_step5_{kind}_d{depth}_v1' for kind in ('child','parent') for depth in (10,20,50)]
    configs={v:load_retrieval_config(ROOT/'config'/(v+'.json')) for v in versions}
    base=configs['retrieval_step3b_translation_v1']
    if old['config_sha256']['step3b']!=sha256(ROOT/'config/retrieval_step3b_translation_v1.json'):
        raise ValueError('Historical Step 3b config changed')
    for config in configs.values():
        normalized=config.model_dump()
        normalized.update(schema_version=base.schema_version, config_version=base.config_version,
                          embedding_run_id=base.embedding_run_id)
        normalized['dense'].update(unit='parent',source_depth=base.dense.source_depth)
        normalized['dense'].pop('child',None)
        normalized['bm25']['source_depth']=base.bm25.source_depth
        if normalized!=base.model_dump():
            raise ValueError('Undeclared non-representation configuration change')
    child_manifest=verified_json_folder(args.children)
    children=[json.loads(line) for line in (args.children/'children.jsonl').read_text(encoding='utf-8').splitlines()]
    sources={p:sha256(ROOT/p) for p in (
        'docs/decisions/RETRIEVAL_V2_STEP45.md','src/evaluation/child_depth.py',
        'scripts/run_retrieval_child_depth.py','src/retrieval/child.py','src/retrieval/hybrid.py',
        'src/retrieval/runtime_config.py','src/retrieval/dense.py','src/retrieval/bm25.py','src/retrieval/rrf.py')}
    sources.update({'config/'+v+'.json':sha256(ROOT/'config'/(v+'.json')) for v in versions})
    for p in ('src/retrieval/child.py','src/retrieval/dense.py','src/retrieval/runtime_config.py','src/retrieval/hybrid.py'):
        if child_manifest['sources'][p]!=sources[p]:
            raise ValueError('Representation code changed after its freeze')
    args.output.mkdir(parents=True)
    protocol=dict(frozen_at_utc=datetime.now(timezone.utc).isoformat(), baseline='step3b',baseline_is_posthoc=True,
        criteria=['combined CE@10 nondecrease','combined per-case EGR improved > worsened'],
        datasets=['dev','dev-ko'],depth_order=[10,20,50],sources=sources,
        child_manifest_sha256=sha256(args.children/'manifest.json'),
        generation_manifest_sha256=sha256(generation/'manifest.json'),
        translation_manifest_sha256=sha256(translation/'manifest.json'),
        historical_manifest_sha256=sha256(historical/'manifest.json'),approval_sha256=sha256(approval))
    write_new_json(args.output/'protocol.json',protocol)
    for p in sources:
        target=args.output/'sources'/p
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/p).read_bytes())
    def verify_sources():
        if any(sha256(ROOT/p)!=digest for p,digest in sources.items()):
            raise ValueError('Predeclared source changed during evaluation')
    verify_sources()
    os.environ['HF_HUB_OFFLINE']=os.environ['TRANSFORMERS_OFFLINE']='1'
    assets=load_frozen_literature_assets(ROOT,config=base)
    if assets.corpus_chunks_sha256!=child_manifest['parent_corpus_sha256'] or assets.corpus_chunks_sha256!=old['corpus_sha256']:
        raise ValueError('Parent corpus changed')
    dev=ROOT/'data/evaluation/eval_dataset_v1.json'
    manifest=json.loads((ROOT/'data/evaluation/eval_dataset_v1.manifest.json').read_text(encoding='utf-8'))
    if sha256(dev)!=manifest['dataset_sha256'] or sha256(dev)!=assets.phase7_reproducibility['inputs']['eval_dataset_sha256']:
        raise ValueError('Frozen dev changed')
    encoder=MiniLMEncoder(model_id=base.dense.model_id,model_revision=base.dense.model_revision,
                         batch_size=base.dense.batch_size,cache_dir=ROOT/'data/models/huggingface',device='cpu')
    if asdict(encoder.metadata)!=child_manifest['encoder']:
        raise ValueError('Encoder runtime differs from the child embedding freeze')
    import numpy as np
    child_vectors=np.load(args.children/'embeddings.npy',allow_pickle=False)
    if child_vectors.shape!=(len(children),base.dense.embedding_dimension):
        raise ValueError('Child vector dimensions differ')
    if len({c['child_id'] for c in children})!=len(children) or {c['parent_id'] for c in children}!=set(assets.chunks_by_id):
        raise ValueError('Child membership differs from parent corpus')
    for c in children:
        parent=assets.chunks_by_id[c['parent_id']]
        if (parent['text'][c['start_char']:c['end_char']]!=c['text'] or
                token_count(encoder.tokenizer,c['text'])!=c['token_count'] or c['token_count']>110):
            raise ValueError('Child source span/token contract mismatch')
    parent_vectors=encoder.encode([c['text'] for c in assets.chunks])
    all_rankings,all_sources,all_mappings,metrics,groups,decisions={}, {}, {}, {}, {}, {}

    def rank_condition(name,config):
        verify_sources()
        ranked,sourced,mapped={}, {}, {}
        for dataset,rows in queries.items():
            if config.dense.unit=='child':
                raw=MemoryCosineStore([c['child_id'] for c in children],child_vectors)
                store=ParentChildStore(raw,children,set(assets.chunks_by_id),child_depth=config.dense.child.search_depth)
            else:
                store=MemoryCosineStore([c['chunk_id'] for c in assets.chunks],parent_vectors)
            retriever=FrozenHybridRetriever(assets=replace(assets,retrieval_config=config),encoder=encoder,vector_store=store)
            bm25=BM25Index(assets.chunks,k1=config.bm25.k1,b=config.bm25.b)
            hits,lexical={}, {}
            for row in rows:
                q,b=row['step3b']
                hits[row['case_id']]=[asdict(h) for h in retriever.search(q,bm25_query=b,top_k=10).hits]
                lexical[row['case_id']]=[asdict(h) for h in bm25.search(b,top_k=config.bm25.source_depth,
                                                                      score_policy=config.bm25.score_policy).hits]
            ranked[dataset]=hits
            sourced[dataset]=dict(dense={r['case_id']:h for r,h in zip(rows,store.searches,strict=True)},bm25=lexical)
            mapped[dataset]=({r['case_id']:dict(pool=pool,**mapping) for r,pool,mapping in
                              zip(rows,store.child_searches,store.mappings,strict=True)} if config.dense.unit=='child' else None)
        folder=args.output/name
        folder.mkdir()
        write_new_json(folder/'rankings.json',ranked)
        write_new_json(folder/'source_rankings.json',sourced)
        write_new_json(folder/'child_mappings.json',mapped)
        all_rankings[name],all_sources[name],all_mappings[name]=ranked,sourced,mapped
        print(name+': stored 36 rankings before Gold evaluation',flush=True)

    rank_condition('step3b',base)
    reproduction={d:rankings_equal_with_tolerance(old_rankings[d],all_rankings['step3b'][d]) and
                  all(rankings_equal_with_tolerance(old_sources[d][s],all_sources['step3b'][d][s])
                      for s in ('dense','bm25')) for d in queries}
    if not all(reproduction.values()):
        raise RuntimeError('Step 3b rank reproduction failed')
    # Gold is read only at the metric phase, after baseline rankings are written.
    cases=[c for c in json.loads(dev.read_text(encoding='utf-8'))['cases'] if c['category'] in ('hybrid','literature_only')]
    def evaluate_condition(name):
        metrics[name]={d:evaluate_rankings(cases,hits) for d,hits in all_rankings[name].items()}
        groups[name]={d:{category:evaluate_rankings([c for c in cases if c['category']==category],
            {c['id']:hits[c['id']] for c in cases if c['category']==category}) for category in ('hybrid','literature_only')}
            for d,hits in all_rankings[name].items()}
        if combined_metrics(metrics[name])['case_count']!=36:
            raise ValueError('Combined case count differs')
    evaluate_condition('step3b')
    if metrics['step3b']!=old['metrics']['step3b']:
        raise RuntimeError('Step 3b metric reproduction failed')
    rank_condition('step4',configs['retrieval_step4_child_v1'])
    if not all(rankings_equal_with_tolerance(all_sources['step3b'][d]['bm25'],all_sources['step4'][d]['bm25']) for d in queries):
        raise RuntimeError('Step 4 changed BM25 parent retrieval')
    evaluate_condition('step4')
    decisions['step4']=dict(reference='step3b',**adoption_decision(metrics['step3b'],metrics['step4']))
    current='step4' if decisions['step4']['adopted'] else 'step3b'
    kind='child' if decisions['step4']['adopted'] else 'parent'
    for depth in (10,20,50):
        name=f'step5_d{depth}'
        config=configs[f'retrieval_step5_{kind}_d{depth}_v1']
        rank_condition(name,config)
        if kind=='child' and any(all_mappings[name][d][cid]['pool']!=all_mappings['step4'][d][cid]['pool']
                                for d in queries for cid in all_mappings[name][d]):
            raise RuntimeError('Candidate depth changed the fixed child search pool')
        evaluate_condition(name)
        decisions[name]=dict(reference=current,**adoption_decision(metrics[current],metrics[name]))
        if decisions[name]['adopted']:
            current=name
    verify_sources()
    result=dict(version=args.output.name,metrics=metrics,grouped_metrics=groups,
        combined_metrics={k:combined_metrics(v) for k,v in metrics.items()},decisions=decisions,
        selected_condition=current,step5_dense_unit=kind,baseline_is_posthoc=True,
        representation_stats=json.loads((args.children/'stats.json').read_text(encoding='utf-8')),
        step3b_reproduction=reproduction,bm25_parent_unchanged=True,encoder=asdict(encoder.metadata),
        llm_calls=0,database_used=False,held_out_used=False,corpus_sha256=assets.corpus_chunks_sha256,
        protocol_sha256=sha256(args.output/'protocol.json'),service_default='legacy/H0',
        queries={d:[dict(case_id=r['case_id'],dense=r['step3b'][0],bm25=r['step3b'][1]) for r in rows]
                 for d,rows in queries.items()})
    write_new_json(args.output/'result.json',result)
    write_new_json(args.output/'manifest.json',dict(status='frozen',version=args.output.name,
        artifacts=[dict(path=p.relative_to(args.output).as_posix(),sha256=sha256(p))
                   for p in sorted(args.output.rglob('*')) if p.is_file()]))
    print(json.dumps(dict(selected=current,decisions={k:dict(reference=v['reference'],adopted=v['adopted'],checks=v['checks'])
                                                    for k,v in decisions.items()})))


if __name__=='__main__':
    main()
