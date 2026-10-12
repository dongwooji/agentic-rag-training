"""Diagnose only frozen held-out ranks. No search/LLM/secret loader is provided."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.evaluation.heldout_diagnosis import build_diagnosis, read_json, sha256, verify_bundle


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT/'reports/diagnostics').resolve()) or args.output.exists():
        parser.error('Use a new diagnosis version under reports/diagnostics')
    ranks=ROOT/'reports/experiments/retrieval_v2_heldout_rankings_v1'
    evaluated=ROOT/'reports/experiments/retrieval_v2_heldout_evaluation_v1'
    configuration=ROOT/'reports/experiments/retrieval_v2_final_configuration_v1'
    for folder in (ranks,evaluated,configuration):
        verify_bundle(folder)
    protocol=read_json(configuration/'protocol.json')
    for ref in protocol['references'].values():
        path=ROOT/ref['path']
        if sha256(path)!=ref['sha256']:
            raise ValueError('PR #10 frozen reference changed')
    old=ROOT/'data/evaluation/heldout_v2_v1'
    current=ROOT/'data/evaluation/heldout_v2_v1_1_final'
    prior=ROOT/'data/evaluation/heldout_v2_v1_1'
    v1=ROOT/'data/literature/manifests/corpus_v1.json'
    v2=ROOT/'data/literature/v2/build_freeze_v1'
    inputs={}
    def tracked(path):
        inputs[path.relative_to(ROOT).as_posix()]=sha256(path)
        return read_json(path)
    def lines(path):
        inputs[path.relative_to(ROOT).as_posix()]=sha256(path)
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    for folder in (old,current,prior):
        m=tracked(folder/'manifest.json')
        for filename in ('evaluation_cases.json','gold_evidence.json'):
            ref=m['files'][filename]
            if sha256(ROOT/ref['path'])!=ref['sha256']:
                raise ValueError('Frozen case/Gold reference changed')
    original_gold=tracked(old/'gold_evidence.json')
    final_gold=tracked(current/'gold_evidence.json')
    prefinal=tracked(prior/'gold_evidence.json')
    corpus1=tracked(v1)
    corpus2=tracked(v2/'build_manifest.json')
    corpus1['chunks']=lines(ROOT/'data/literature/processed/chunks.jsonl')
    corpus2['chunks']=lines(v2/'chunks.jsonl')
    corpus2['papers']=lines(v2/'papers.jsonl')
    vectors=np.load(ranks/'parent_embeddings.npy',mmap_mode='r',allow_pickle=False)
    if vectors.ndim!=2 or vectors.shape[1]!=384:
        raise ValueError('Unexpected frozen parent embedding shape')
    corpus2['embedding_row_count']=vectors.shape[0]
    published=tracked(evaluated/'result.json')
    saved={name:tracked(ranks/(name+'_rankings.json')) for name in ('H0','H1','step2','final_v2')}
    result=build_diagnosis(old_cases=tracked(old/'evaluation_cases.json'),final_cases=tracked(current/'evaluation_cases.json'),
                           old_gold=original_gold,final_gold=final_gold,prefinal_gold=prefinal,rankings=saved,
                           dense10=tracked(ranks/'final_v2_dense.json'),index_ids=tracked(ranks/'parent_chunk_ids.json'),
                           corpus_v1=corpus1,corpus_v2=corpus2,published_metrics=published['metrics'])
    if len(corpus1['chunks'])!=corpus1['chunk_count'] or len(corpus2['chunks'])!=corpus2['chunk_count']:
        raise ValueError('Corpus manifest counts differ')
    result['manifest_required_counts']={'v1':read_json(old/'manifest.json')['summary']['required_group_count'],
                                      'v2_1_final':read_json(current/'manifest.json')['summary']['required_group_count']}
    result['depth50_artifact_inventory']=[ref['path'] for ref in read_json(ranks/'manifest.json')['artifacts']]
    args.output.mkdir(parents=True)
    def write(path,value):
        with path.open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
            f.write('\n')
    write(args.output/'diagnosis.json',result)
    sources={p:sha256(ROOT/p) for p in ('src/evaluation/heldout_diagnosis.py','scripts/diagnose_retrieval_heldout.py',
                                       'src/evaluation/retrieval_metrics.py','src/evaluation/heldout_v2.py')}
    for path,h in inputs.items():
        if sha256(ROOT/path)!=h:
            raise ValueError('Input changed during read-only diagnosis')
    write(args.output/'manifest.json',dict(status='frozen',kind='diagnosis_only',version=args.output.name,
          frozen_at_utc=datetime.now(timezone.utc).isoformat(),inputs=inputs,sources=sources,
          ranking_manifest_sha256=sha256(ranks/'manifest.json'),original_final_result_manifest_sha256=sha256(evaluated/'manifest.json'),
          search_calls=0,llm_calls=0,artifacts=[dict(path='diagnosis.json',sha256=sha256(args.output/'diagnosis.json'))]))
    print(json.dumps(dict(manifest_sha256=sha256(args.output/'manifest.json'),
                         metrics={v:{name:m['macro'] for name,m in conditions.items()} for v,conditions in result['metrics'].items()},
                         integrity=result['integrity'],gold_change={k:v for k,v in result['gold_change'].items() if k!='conditions'},
                         search_calls=0,llm_calls=0),ensure_ascii=False))


if __name__=='__main__':
    main()
