"""Frozen-pool A/B CPU reranking, then development-only coverage diagnosis."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.evaluation.literature_query_freeze import load_reviewed_conditions, sha256, write_new_json
from src.evaluation.dense_length import verify_frozen_folder
from src.evaluation.retrieval_preparation import evaluate_rankings, rankings_equal_with_tolerance, metric_changes
from src.evaluation.child_depth import combined_metrics
from src.evaluation.reranker import (fused_candidate_ids, select_reranker, diagnose_missing_groups,
                                    pool_evidence_upper_bound, summarize_token_audit, latency_summary)
from src.retrieval.hybrid import load_frozen_literature_assets
from src.retrieval.runtime_config import load_retrieval_config
from src.retrieval.reranker import load_reranker_config, MiniLMReranker
from src.retrieval.rrf import reciprocal_rank_fusion


DATASETS = ('dev','dev-ko')
SOURCE_FILES = (
    'config/reranker_minilm_v1.json','config/retrieval_step3b_translation_v1.json',
    'config/retrieval_step5_parent_d50_v1.json','docs/decisions/RETRIEVAL_V2_RERANKER.md',
    'scripts/run_retrieval_reranker.py','src/retrieval/reranker.py','src/evaluation/reranker.py',
    'src/evaluation/retrieval_metrics.py','src/evaluation/retrieval_preparation.py',
    'src/evaluation/child_depth.py','src/evaluation/dense_length.py','src/evaluation/literature_query_freeze.py',
    'src/retrieval/rrf.py','src/retrieval/runtime_config.py','src/retrieval/hybrid.py')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify_rrf(dense, bm25, stored):
    fresh = reciprocal_rank_fusion([h['chunk_id'] for h in dense],[h['chunk_id'] for h in bm25]).hits
    if len(fresh) != len(stored):
        raise ValueError('Historical RRF result count differs')
    for new,old in zip(fresh,stored,strict=True):
        if (new.chunk_id != old['chunk_id'] or new.dense_rank != old['dense_rank']
                or new.bm25_rank != old['bm25_rank'] or not math.isclose(
                    new.score,old['rrf_score'],abs_tol=1e-12,rel_tol=0)):
            raise ValueError('Historical same-pool RRF reproduction failed')


def load_inputs(root):
    generation = root/'data/evaluation/retrieval_step2_generation_queries_v1'
    translation = root/'data/evaluation/retrieval_step3_translation_queries_v2'
    approval = root/'reports/experiments/retrieval_v2_step23_query_revalidation_v2/approval.json'
    queries = {d:load_reviewed_conditions(generation,translation,approval,dataset=d) for d in DATASETS}
    if any(len(rows) != 18 for rows in queries.values()) or any(
            row['step3b'][0] != row['step3b'][1] for rows in queries.values() for row in rows):
        raise ValueError('Expected paired 18+18 frozen same-language queries')
    old23 = root/'reports/experiments/retrieval_v2_step23_evaluation_v2'
    old45 = root/'reports/experiments/retrieval_v2_step45_evaluation_v1'
    verify_frozen_folder(old23)
    verify_frozen_folder(old45)
    base_path = root/'config/retrieval_step3b_translation_v1.json'
    depth_path = root/'config/retrieval_step5_parent_d50_v1.json'
    base,depth = load_retrieval_config(base_path),load_retrieval_config(depth_path)
    normalized = depth.model_dump()
    normalized.update(schema_version=base.schema_version,config_version=base.config_version)
    normalized['dense']['source_depth'] = normalized['bm25']['source_depth'] = 10
    if (normalized != base.model_dump() or base.dense.source_depth != 10 or depth.dense.source_depth != 50
            or depth.bm25.source_depth != 50 or base.bm25.score_policy != 'positive_only'):
        raise ValueError('Expected unchanged parent Step 3b apart from source depth')
    old_base = read_json(old23/'result.json')
    old_depth = read_json(old45/'result.json')
    if (old_base['config_sha256']['step3b'] != sha256(base_path)
            or read_json(old45/'protocol.json')['sources']['config/retrieval_step5_parent_d50_v1.json'] != sha256(depth_path)
            or old_depth['step5_dense_unit'] != 'parent'):
        raise ValueError('Historical config or representation differs')
    assets = load_frozen_literature_assets(root,config=base)
    if old_base['corpus_sha256'] != assets.corpus_chunks_sha256 or old_depth['corpus_sha256'] != assets.corpus_chunks_sha256:
        raise ValueError('Historical corpus differs')
    dev = root/'data/evaluation/eval_dataset_v1.json'
    if sha256(dev) != read_json(root/'data/evaluation/eval_dataset_v1.manifest.json')['dataset_sha256']:
        raise ValueError('Frozen development dataset changed')
    sources = dict(A=read_json(old23/'source_rankings.json')['step3b'],
                   B=read_json(old45/'step5_d50/source_rankings.json'))
    rankings = dict(rrf_A=read_json(old23/'rankings.json')['step3b'],
                    rrf_B=read_json(old45/'step5_d50/rankings.json'))
    for d,rows in queries.items():
        expected = {row['case_id'] for row in rows}
        if old_depth['queries'][d] != [dict(case_id=r['case_id'],dense=r['step3b'][0],bm25=r['step3b'][1]) for r in rows]:
            raise ValueError('Historical English query differs')
        if not rankings_equal_with_tolerance(rankings['rrf_A'][d],read_json(old45/'step3b/rankings.json')[d]):
            raise ValueError('Historical Step 3b rankings disagree')
        for condition,limit in (('A',10),('B',50)):
            source = sources[condition][d]
            if any(set(source[k]) != expected for k in ('dense','bm25')) or set(rankings['rrf_'+condition][d]) != expected:
                raise ValueError('Frozen candidate case set differs')
            for cid in expected:
                dense,bm25 = source['dense'][cid],source['bm25'][cid]
                if len(dense) != limit or len(bm25)>limit or any(h['score']<=0 for h in bm25):
                    raise ValueError('Frozen candidate depth / BM25 policy differs')
                if any(not math.isfinite(h['score']) or h['chunk_id'] not in assets.chunks_by_id for h in dense+bm25):
                    raise ValueError('Invalid frozen candidate')
                verify_rrf(dense,bm25,rankings['rrf_'+condition][d][cid])
        for kind in ('dense','bm25'):
            if not rankings_equal_with_tolerance(sources['A'][d][kind],{
                    cid:hits[:10] for cid,hits in sources['B'][d][kind].items()}):
                raise ValueError('Depth 10 candidates are not the frozen depth 50 prefix')
    return dict(queries=queries,sources=sources,rankings=rankings,assets=assets,dev=dev,
        reference_metrics=dict(rrf_A=old_base['metrics']['step3b'],rrf_B=old_depth['metrics']['step5_d50']),
        input_hashes=dict(generation=sha256(generation/'manifest.json'),translation=sha256(translation/'manifest.json'),
                         approval=sha256(approval),step23=sha256(old23/'manifest.json'),
                         step45=sha256(old45/'manifest.json'),dev=sha256(dev),corpus=assets.corpus_chunks_sha256))


def rank_conditions(prepared, reranker, output):
    """No Gold, case labels or retrieval feedback enter the scoring stage."""
    results = {name:{d:{} for d in DATASETS} for name in ('A','B')}
    pools = {name:{d:{} for d in DATASETS} for name in ('A','B')}
    for d,rows in prepared['queries'].items():
        for row in rows:
            for name in ('A','B'):
                cid = row['case_id']
                source = prepared['sources'][name][d]
                ids = fused_candidate_ids(source['dense'][cid],source['bm25'][cid])
                parents = [dict(chunk_id=chunk_id,text=prepared['assets'].chunks_by_id[chunk_id]['text']) for chunk_id in ids]
                results[name][d][cid] = reranker.rerank(row['step3b'][0],parents)
                pools[name][d][cid] = ids
            print(f'{d} {row["case_id"]}: A/B CPU scoring complete',flush=True)
    for name in ('A','B'):
        folder = output/name
        folder.mkdir()
        write_new_json(folder/'rankings.json',{d:{cid:r['hits'] for cid,r in rows.items()} for d,rows in results[name].items()})
        write_new_json(folder/'scores_latency_tokens.json',results[name])
        write_new_json(folder/'candidate_pools.json',pools[name])
    return results,pools


def read_gold_cases(path):
    # This function is called only after BOTH full conditions are written.
    return [c for c in read_json(path)['cases'] if c['category'] in ('hybrid','literature_only')]


def run_experiment(root, output, *, reranker_factory=MiniLMReranker):
    output = output.resolve()
    if output.exists() or not output.is_relative_to((root/'reports/experiments').resolve()):
        raise ValueError('Use a new versioned output under reports/experiments')
    prepared = load_inputs(root)
    config = load_reranker_config(root/'config/reranker_minilm_v1.json')
    sources = {p:sha256(root/p) for p in SOURCE_FILES}
    output.mkdir(parents=True)
    protocol = dict(version=output.name, frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        baseline='step3b',baseline_is_posthoc=True,conditions=dict(A='Dense10+BM25_10',B='Dense50+BM25_50'),
        criteria=['combined CE@10 nondecrease','per-case EGR improved > worsened','mean CPU latency <= 3000ms'],
        tiebreak=['combined CE@10','combined EGR@10','A'],metric_atol=1e-12,
        model=config.model_dump(),datasets=list(DATASETS),sources=sources,input_sha256=prepared['input_hashes'],
        candidate_reuse=True,all_rankings_before_gold=True,service_default='legacy/H0',held_out_used=False)
    write_new_json(output/'protocol.json',protocol)
    for p in sources:
        target = output/'sources'/p
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((root/p).read_bytes())
    def verify_sources():
        if any(sha256(root/p) != digest for p,digest in sources.items()):
            raise ValueError('Predeclared sources changed during evaluation')
    verify_sources()
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    reranker = reranker_factory(config,cache_dir=root/'data/models/huggingface')
    reranker.warm_up()
    results,pools = rank_conditions(prepared,reranker,output)
    verify_sources()
    rankings = dict(prepared['rankings'])
    rankings.update({name:{d:{cid:r['hits'] for cid,r in rows.items()} for d,rows in results[name].items()} for name in ('A','B')})
    # Gold first enters here, after scores and rankings are persisted for A/B.
    cases = read_gold_cases(prepared['dev'])
    if {c['id'] for c in cases} != {r['case_id'] for r in prepared['queries']['dev']}:
        raise ValueError('Development Gold cases differ from frozen queries')
    metrics = {name:{d:evaluate_rankings(cases,hits) for d,hits in ranked.items()} for name,ranked in rankings.items()}
    if any(metrics[name] != expected for name,expected in prepared['reference_metrics'].items()):
        raise ValueError('Historical same-pool metrics differ')
    latencies = {name:{d+':'+cid:r['latency_ms'] for d,rows in results[name].items() for cid,r in rows.items()} for name in ('A','B')}
    selection = select_reranker(metrics['rrf_A'],{n:metrics[n] for n in ('A','B')},latencies)
    diagnosis = {d:diagnose_missing_groups(cases,
        {cid:[h['chunk_id'] for h in hits] for cid,hits in rankings['rrf_A'][d].items()},pools['A'][d],pools['B'][d]) for d in DATASETS}
    diagnosis['combined'] = dict(case_count=sum(diagnosis[d]['case_count'] for d in DATASETS),
        missing_case_count=sum(diagnosis[d]['missing_case_count'] for d in DATASETS),
        missing_group_count=sum(diagnosis[d]['missing_group_count'] for d in DATASETS),
        categories={k:{field:sum(diagnosis[d]['categories'][k][field] for d in DATASETS)
                       for field in ('case_count','group_count')} for k in ('a','b','c')},
        b_inclusive_group_count=sum(diagnosis[d]['b_inclusive_group_count'] for d in DATASETS))
    bounds = {name:{d:pool_evidence_upper_bound(cases,pool) for d,pool in pools[name].items()} for name in ('A','B')}
    for name in bounds:
        bounds[name]['combined'] = dict(case_count=sum(bounds[name][d]['case_count'] for d in DATASETS),macro={
            field:sum(bounds[name][d]['macro'][field]*bounds[name][d]['case_count'] for d in DATASETS)/sum(bounds[name][d]['case_count'] for d in DATASETS)
            for field in ('evidence_group_recall','complete_evidence')})
    grouped = {name:{d:{category:evaluate_rankings([c for c in cases if c['category']==category],
        {c['id']:hits[c['id']] for c in cases if c['category']==category}) for category in ('hybrid','literature_only')}
        for d,hits in ranked.items()} for name,ranked in rankings.items()}
    tokens = {name:dict(combined=summarize_token_audit([r for rows in results[name].values() for r in rows.values()]),
        datasets={d:summarize_token_audit(list(rows.values())) for d,rows in results[name].items()}) for name in ('A','B')}
    dataset_latency = {name:{d:latency_summary(r['latency_ms'] for r in rows.values()) for d,rows in results[name].items()} for name in ('A','B')}
    verify_sources()
    write_new_json(output/'result.json',dict(version=output.name,metrics=metrics,grouped_metrics=grouped,
        combined_metrics={name:combined_metrics(m) for name,m in metrics.items()},diagnosis=diagnosis,candidate_upper_bounds=bounds,
        same_pool_comparisons={name:dict(reference='rrf_'+name,combined=metric_changes(combined_metrics(metrics['rrf_'+name]),combined_metrics(metrics[name])),
            datasets={d:metric_changes(metrics['rrf_'+name][d],metrics[name][d]) for d in DATASETS}) for name in ('A','B')},
        **selection,latency_by_dataset=dataset_latency,token_audit=tokens,model=reranker.metadata,
        protocol_sha256=sha256(output/'protocol.json'),input_sha256=prepared['input_hashes'],
        service_default='legacy/H0',llm_calls=0,api_tokens=0,api_cost_usd=0,database_used=False,held_out_used=False,
        query_reused=True,embedding_reused=True,source_candidates_reused=True))
    write_new_json(output/'manifest.json',dict(status='frozen',version=output.name,
        artifacts=[dict(path=p.relative_to(output).as_posix(),sha256=sha256(p)) for p in sorted(output.rglob('*')) if p.is_file()]))
    print(json.dumps(dict(selected=selection['selected_condition'],eligible=selection['eligible'])),flush=True)
    return read_json(output/'result.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    run_experiment(ROOT,args.output)


if __name__ == '__main__':
    main()
