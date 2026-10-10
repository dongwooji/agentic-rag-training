"""Freeze new 256/512 parent embeddings without reading questions or Gold."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.evaluation.literature_query_freeze import sha256, write_new_json
from src.evaluation.dense_length import truncation_statistics
from src.retrieval.child import token_count
from src.retrieval.dense import MiniLMEncoder
from src.retrieval.hybrid import load_frozen_literature_assets
from src.retrieval.runtime_config import load_retrieval_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--length', type=int, choices=(256, 512), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(ROOT/'data/literature/representations'):
        parser.error('Use a new parent embedding version under data/literature/representations')
    config_path = f'config/retrieval_parent_length{args.length}_v1.json'
    config = load_retrieval_config(ROOT/config_path)
    sources = {p: sha256(ROOT/p) for p in (
        config_path, 'src/retrieval/dense.py', 'src/retrieval/runtime_config.py',
        'src/retrieval/hybrid.py', 'src/retrieval/child.py', 'src/evaluation/dense_length.py',
        'scripts/build_retrieval_parent_lengths.py', 'docs/decisions/RETRIEVAL_V2_CHILD_CORRECTION_LENGTH.md')}
    assets = load_frozen_literature_assets(ROOT, config=config)
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    encoder = MiniLMEncoder(model_id=config.dense.model_id, model_revision=config.dense.model_revision,
        batch_size=config.dense.batch_size, cache_dir=ROOT/'data/models/huggingface',
        device='cpu', max_seq_length=args.length)
    lengths = [token_count(encoder.tokenizer, c['text']) for c in assets.chunks]
    stats = truncation_statistics(lengths, args.length)
    args.output.mkdir(parents=True)
    write_new_json(args.output/'parent_tokens.json', [dict(parent_id=c['chunk_id'], token_count=n,
        truncated=n>args.length, retained_tokens=min(n,args.length)) for c,n in zip(assets.chunks,lengths,strict=True)])
    started = perf_counter()
    vectors = encoder.encode([c['text'] for c in assets.chunks], show_progress=True)
    embedding_seconds = perf_counter()-started
    import numpy as np
    with (args.output/'embeddings.npy').open('xb') as handle:
        np.save(handle, vectors, allow_pickle=False)
    write_new_json(args.output/'stats.json', stats)
    if any(sha256(ROOT/p) != digest for p,digest in sources.items()):
        raise ValueError('Representation sources changed during build')
    for p in sources:
        target = args.output/'sources'/p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/p).read_bytes())
    write_new_json(args.output/'manifest.json', dict(status='frozen', version=args.output.name,
        frozen_at_utc=datetime.now(timezone.utc).isoformat(), encoder=asdict(encoder.metadata),
        embedding_run_id=config.embedding_run_id, config_sha256=sha256(ROOT/config_path),
        parent_corpus_sha256=assets.corpus_chunks_sha256, sources=sources,
        embedding_seconds=embedding_seconds, retrieval_executed=False, gold_used=False,
        artifacts=[dict(path=p.relative_to(args.output).as_posix(),sha256=sha256(p))
                   for p in sorted(args.output.rglob('*')) if p.is_file()]))
    print(json.dumps(dict(stats=stats, manifest_sha256=sha256(args.output/'manifest.json'))),flush=True)


if __name__ == '__main__':
    main()
