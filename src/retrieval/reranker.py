"""Pinned CPU parent reranker; no evaluation data or evidence decisions."""

from importlib import metadata
import hashlib
import json
import math
from pathlib import Path
import platform
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RerankerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, protected_namespaces=())
    schema_version: Literal[1]
    config_version: Literal['reranker_minilm_v1']
    requested_model_id: Literal['cross-encoder/ms-marco-MiniLM-L-6-v2']
    model_id: Literal['cross-encoder/ms-marco-MiniLM-L6-v2']
    model_revision: Literal['233902d25c440f23af6f7d6e94d2946bac0bee0a']
    max_length: Literal[512]
    batch_size: Literal[16]
    cpu_threads: Literal[4]
    device: Literal['cpu']
    precision: Literal['float32']
    truncation: Literal['only_second']
    score: Literal['raw_logit']
    tie_breaker: Literal['score_desc_rrf_rank_chunk_id']
    top_k: Literal[10]


def load_reranker_config(path):
    return RerankerConfig.model_validate_json(Path(path).read_text(encoding='utf-8'))


def rerank_parents(query, parents, *, tokenizer, score_pairs, max_length=512, top_k=10):
    """Parents arrive in RRF order; IDs remain bookkeeping, never model input."""
    started = perf_counter()
    if not isinstance(query, str) or not query.strip():
        raise ValueError('Reranker query must be nonempty')
    if type(top_k) is not int or top_k <= 0 or type(max_length) is not int or max_length <= 0:
        raise ValueError('Invalid reranker length or top-k')
    ids = [p['chunk_id'] for p in parents]
    if not ids or any(not isinstance(cid, str) or not cid for cid in ids) or len(ids) != len(set(ids)):
        raise ValueError('Reranker requires nonempty unique parent IDs')
    if any(not isinstance(p.get('text'), str) or not p['text'].strip() for p in parents):
        raise ValueError('Parent text must be nonempty')
    query_tokens = len(tokenizer.encode(query, add_special_tokens=False))
    special_tokens = tokenizer.num_special_tokens_to_add(pair=True)
    budget = max_length-query_tokens-special_tokens
    if budget <= 0:
        raise ValueError('Query exhausts reranker input length; query truncation is forbidden')
    audits = []
    for parent in parents:
        count = len(tokenizer.encode(parent['text'], add_special_tokens=False))
        audits.append(dict(chunk_id=parent['chunk_id'], query_tokens=query_tokens,
            parent_tokens=count, special_tokens=special_tokens, pair_tokens=count+query_tokens+special_tokens,
            retained_parent_tokens=min(count,budget), truncated=count>budget))
    # Full original strings enter the scorer. Only model tokenization truncates.
    pairs = [(query,p['text']) for p in parents]
    scores = list(score_pairs(pairs))
    if len(scores) != len(parents):
        raise ValueError('Reranker score count differs from candidate count')
    scores = [float(score) for score in scores]
    if not all(math.isfinite(score) for score in scores):
        raise ValueError('Reranker scores must be finite')
    hits = [dict(chunk_id=cid,score=score,rrf_rank=i+1)
            for i,(cid,score) in enumerate(zip(ids,scores,strict=True))]
    hits.sort(key=lambda h:(-h['score'],h['rrf_rank'],h['chunk_id']))
    return dict(hits=hits[:top_k], all_scores=hits, token_audit=audits,
                candidate_count=len(parents), latency_ms=(perf_counter()-started)*1000)


class MiniLMReranker:
    """One exact model, local CPU inference, passage-only truncation."""
    def __init__(self, config, *, cache_dir):
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch

        self.config = config
        self._torch = torch
        torch.set_num_threads(config.cpu_threads)
        started = perf_counter()
        snapshot = Path(snapshot_download(repo_id=config.model_id, revision=config.model_revision,
            cache_dir=str(Path(cache_dir).resolve()), allow_patterns=[
                'config.json','model.safetensors','tokenizer.json','tokenizer_config.json',
                'special_tokens_map.json','vocab.txt']))
        self.tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(snapshot, local_files_only=True)
        self._model.to(device='cpu', dtype=torch.float32)
        self._model.eval()
        if self._model.config.num_labels != 1 or self._model.config.max_position_embeddings < config.max_length:
            raise ValueError('Pinned reranker model contract differs')
        self.metadata = dict(config=config.model_dump(), model_load_ms=(perf_counter()-started)*1000,
            files={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(snapshot.iterdir()) if p.is_file()},
            torch_version=torch.__version__, transformers_version=metadata.version('transformers'),
            huggingface_hub_version=metadata.version('huggingface_hub'), python_version=platform.python_version(),
            platform=platform.platform(), processor=platform.processor(),
            cpu_threads=torch.get_num_threads(), cpu_interop_threads=torch.get_num_interop_threads())

    def _score_pairs(self, pairs):
        scores = []
        for offset in range(0,len(pairs),self.config.batch_size):
            batch = pairs[offset:offset+self.config.batch_size]
            features = self.tokenizer([p[0] for p in batch],[p[1] for p in batch],
                padding=True, truncation=self.config.truncation, max_length=self.config.max_length,
                return_tensors='pt')
            with self._torch.inference_mode():
                logits = self._model(**features).logits
            if tuple(logits.shape) != (len(batch),1):
                raise ValueError('Reranker must produce one logit per pair')
            scores.extend(logits[:,0].detach().cpu().tolist())
        return scores

    def rerank(self, query, parents):
        return rerank_parents(query,parents,tokenizer=self.tokenizer,score_pairs=self._score_pairs,
                              max_length=self.config.max_length,top_k=self.config.top_k)

    def warm_up(self):
        """Fixed synthetic warm-up, no development question or score reuse."""
        result = self.rerank('What color is the fictional object?',[
            dict(chunk_id='synthetic_warmup',text='The fictional object is blue.')])
        self.metadata['synthetic_warmup_ms'] = result['latency_ms']
