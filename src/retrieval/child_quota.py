"""Corrected child-to-parent quota; historical fixed-pool adapter is preserved."""

from .child import ParentChildStore
from .postgres import SearchHit, SearchResponse


class ParentQuotaChildStore(ParentChildStore):
    parent_selection = 'unique_parent_quota'

    def __init__(self, store, children, parent_ids, *, child_depth=50):
        if type(child_depth) is not int or child_depth <= 0:
            raise ValueError('Child fetch batch must be positive')
        super().__init__(store, children, parent_ids, child_depth=child_depth)

    def search_exact_cosine(self, query_embedding, *, embedding_run_id, top_k):
        if type(top_k) is not int or top_k <= 0:
            raise ValueError('Parent quota must be positive')
        depth = min(self.child_depth, len(self.children))
        previous, depths, latency = [], [], 0.0
        while True:
            response = self.store.search_exact_cosine(
                query_embedding, embedding_run_id=embedding_run_id, top_k=depth)
            pool = list(response.hits)
            ids = [h.chunk_id for h in pool]
            if len(pool) > depth or len(set(ids)) != len(ids):
                raise ValueError('Invalid child result count or duplicate IDs')
            if any(cid not in self.children for cid in ids):
                raise ValueError('Search returned an unknown child')
            if pool[:len(previous)] != previous:
                raise ValueError('Expanded child ranking changed its prefix')
            depths.append(depth)
            latency += response.database_search_ms
            seen, selected, mappings = set(), [], []
            consumed = 0
            for rank, hit in enumerate(pool, 1):
                consumed = rank
                parent = self.children[hit.chunk_id]['parent_id']
                if parent in seen:
                    continue
                seen.add(parent)
                selected.append(SearchHit(parent, hit.score))
                mappings.append(dict(parent_id=parent, best_child_id=hit.chunk_id,
                                     best_child_rank=rank, parent_rank=len(selected), score=hit.score))
                if len(selected) == top_k:
                    break
            exhausted = depth == len(self.children) or len(pool) < depth
            if len(selected) == top_k or exhausted:
                break
            previous = pool
            depth = min(depth + self.child_depth, len(self.children))
        self.searches.append([dict(chunk_id=h.chunk_id, score=h.score) for h in selected])
        self.child_searches.append([dict(child_id=h.chunk_id, score=h.score) for h in pool])
        self.mappings.append(dict(selected=mappings, requested_parent_depth=top_k,
                                  actual_parent_count=len(selected), retrieved_child_count=len(pool),
                                  consumed_child_count=consumed, search_depths=depths,
                                  exhausted=exhausted, quota_filled=len(selected) == top_k))
        return SearchResponse(selected, latency)
