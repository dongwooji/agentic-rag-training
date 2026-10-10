"""Freeze corpus-v1 child representation and embeddings, without questions or Gold."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.evaluation.literature_query_freeze import sha256, write_new_json
from src.retrieval.runtime_config import load_retrieval_config
from src.retrieval.hybrid import load_frozen_literature_assets
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.child import build_children, REPRESENTATION_VERSION


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(ROOT/'data/literature/representations'):
        parser.error('Use a new child version under data/literature/representations')
    config=load_retrieval_config(ROOT/'config/retrieval_step4_child_v1.json')
    sources={path:sha256(ROOT/path) for path in ('src/retrieval/child.py','src/retrieval/dense.py',
        'src/retrieval/runtime_config.py','src/retrieval/hybrid.py','scripts/build_retrieval_children.py',
        'config/retrieval_step4_child_v1.json','docs/decisions/RETRIEVAL_V2_STEP45.md')}
    assets=load_frozen_literature_assets(ROOT,config=config)
    os.environ['HF_HUB_OFFLINE']=os.environ['TRANSFORMERS_OFFLINE']='1'
    encoder=MiniLMEncoder(model_id=config.dense.model_id,model_revision=config.dense.model_revision,
                         batch_size=config.dense.batch_size,cache_dir=ROOT/'data/models/huggingface',device='cpu')
    rows,stats=build_children(assets.chunks,encoder.tokenizer,max_tokens=config.dense.child.max_tokens)
    args.output.mkdir(parents=True)
    with (args.output/'children.jsonl').open('x',encoding='utf-8',newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    vectors=encoder.encode([row['text'] for row in rows],show_progress=True)
    import numpy as np
    with (args.output/'embeddings.npy').open('xb') as handle:
        np.save(handle,vectors,allow_pickle=False)
    write_new_json(args.output/'stats.json',stats)
    if any(sha256(ROOT/path)!=digest for path,digest in sources.items()):
        raise RuntimeError('Representation sources changed during build')
    # Preserve code bytes alongside hashes, so historical runs survive later changes.
    for path in sources:
        target=args.output/'sources'/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/path).read_bytes())
    write_new_json(args.output/'manifest.json',dict(status='frozen',version=REPRESENTATION_VERSION,
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),parent_corpus_sha256=assets.corpus_chunks_sha256,
        max_tokens=config.dense.child.max_tokens,encoder=asdict(encoder.metadata),sources=sources,
        retrieval_executed=False,gold_used=False,
        artifacts=[dict(path=p.relative_to(args.output).as_posix(),sha256=sha256(p))
                   for p in sorted(args.output.rglob('*')) if p.is_file()]))
    print(json.dumps(dict(stats=stats,manifest_sha256=sha256(args.output/'manifest.json'))))


if __name__=='__main__':
    main()
