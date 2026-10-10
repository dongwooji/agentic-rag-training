"""Sentence-bounded children and best-child parent mapping, no evaluation input."""

from collections import Counter
from dataclasses import dataclass
import re
from statistics import mean, median

from .postgres import SearchHit, SearchResponse

REPRESENTATION_VERSION = 'retrieval_child_v1'
ABBREVIATIONS = re.compile(r'(?:\b(?:e\.g|i\.e|et al|Fig|Figs|Eq|Eqs|Dr|Mr|Ms|vs|etc)|\b[A-Z])\.$', re.I)


def sentence_spans(text):
    """Deterministic punctuation boundaries; keep decimals and common abbreviations."""
    start = 0
    for match in re.finditer(r'[.!?]+["\'”’)]*(?=\s|$)', text):
        end = match.end()
        if text[match.start()] == '.' and ABBREVIATIONS.search(text[:end]):
            continue
        if text[start:end].strip():
            yield start, end
            start = end
    if text[start:].strip():
        yield start, len(text)


def token_count(tokenizer, text):
    return len(tokenizer(text, add_special_tokens=True, truncation=False)['input_ids'])


def _trim_span(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end-1].isspace():
        end -= 1
    return start, end


def _split_long_sentence(text, start, end, tokenizer, max_tokens):
    while start < end:
        start, end = _trim_span(text, start, end)
        remaining = text[start:end]
        if token_count(tokenizer, remaining) <= max_tokens:
            yield start, end
            return
        encoded = tokenizer(remaining, add_special_tokens=False, truncation=False,
                            return_offsets_mapping=True)
        budget = max_tokens - tokenizer.num_special_tokens_to_add(pair=False)
        if budget <= 0:
            raise ValueError('Special tokens exhaust the child budget')
        boundaries = sorted({offset[1] for offset in encoded['offset_mapping'][:budget]
                             if offset[1] > 0}, reverse=True)
        cut = next((b for b in boundaries if token_count(tokenizer, remaining[:b]) <= max_tokens), None)
        if cut is None:
            raise ValueError('Cannot split an oversized sentence at a safe token boundary')
        yield start, start + cut
        start += cut


def build_children(parents, tokenizer, *, max_tokens=110):
    if not getattr(tokenizer, 'is_fast', False):
        raise ValueError('Child construction requires a fast tokenizer with offsets')
    if len({p['chunk_id'] for p in parents}) != len(parents):
        raise ValueError('Duplicate parent IDs')
    children = []
    sentence_count = forced_sentences = 0
    for parent in parents:
        text = parent['text']
        local = []
        pending = None
        for start, end in sentence_spans(text):
            start, end = _trim_span(text, start, end)
            sentence_count += 1
            if token_count(tokenizer, text[start:end]) > max_tokens:
                if pending:
                    local.append((*pending, False))
                    pending = None
                forced_sentences += 1
                local.extend((a,b,True) for a,b in _split_long_sentence(text,start,end,tokenizer,max_tokens))
            elif pending and token_count(tokenizer, text[pending[0]:end]) <= max_tokens:
                pending = (pending[0], end)
            else:
                if pending:
                    local.append((*pending, False))
                pending = (start, end)
        if pending:
            local.append((*pending, False))
        if not local:
            raise ValueError('Empty parent text')
        for i,(start,end,forced) in enumerate(local):
            body = text[start:end]
            count = token_count(tokenizer, body)
            if not body.strip() or count > max_tokens:
                raise ValueError('Invalid child token count')
            children.append(dict(child_id=f"{parent['chunk_id']}::{REPRESENTATION_VERSION}:{i:04d}",
                                 parent_id=parent['chunk_id'], text=body, start_char=start, end_char=end,
                                 token_count=count, forced_split=forced))
    counts = sorted(c['token_count'] for c in children)
    forced_children = sum(c['forced_split'] for c in children)
    stats = dict(parent_count=len(parents), child_count=len(children), sentence_count=sentence_count,
                 forced_split_sentence_count=forced_sentences, forced_split_child_count=forced_children,
                 forced_split_sentence_ratio=forced_sentences/sentence_count,
                 forced_split_child_ratio=forced_children/len(children),
                 tokens=dict(min=min(counts), max=max(counts), mean=mean(counts), median=median(counts),
                             p95=counts[min(len(counts)-1, int(len(counts)*.95))],
                             distribution={str(k):v for k,v in sorted(Counter(counts).items())}))
    return children, stats


class ParentChildStore:
    """Search a fixed child pool, collapse to unique parents and cap RRF inputs."""
    search_unit = 'child'

    def __init__(self, store, children, parent_ids, *, child_depth=50):
        self.store, self.child_depth = store, child_depth
        self.children = {c['child_id']:c for c in children}
        if len(self.children) != len(children) or not self.children:
            raise ValueError('Empty or duplicate child IDs')
        if any(c['parent_id'] not in parent_ids for c in children):
            raise ValueError('Child outside frozen parent membership')
        self.searches = []
        self.child_searches = []
        self.mappings = []

    def search_exact_cosine(self, query_embedding, *, embedding_run_id, top_k):
        response = self.store.search_exact_cosine(query_embedding, embedding_run_id=embedding_run_id,
                                                  top_k=min(self.child_depth,len(self.children)))
        seen, selected, mappings = set(), [], []
        for rank, hit in enumerate(response.hits, 1):
            if hit.chunk_id not in self.children:
                raise ValueError('Search returned an unknown child')
            parent = self.children[hit.chunk_id]['parent_id']
            if parent in seen:
                continue
            seen.add(parent)
            if len(selected) < top_k:
                selected.append(SearchHit(parent, hit.score))
                mappings.append(dict(parent_id=parent, best_child_id=hit.chunk_id,
                                     best_child_rank=rank, parent_rank=len(selected), score=hit.score))
        self.child_searches.append([dict(child_id=h.chunk_id,score=h.score) for h in response.hits])
        self.searches.append([dict(chunk_id=h.chunk_id,score=h.score) for h in selected])
        self.mappings.append(dict(selected=mappings, unique_parents_in_child_pool=len(seen),
                                  requested_parent_depth=top_k, actual_parent_count=len(selected)))
        return SearchResponse(selected, response.database_search_ms)
