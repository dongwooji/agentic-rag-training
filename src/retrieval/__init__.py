"""Dense literature retrieval components introduced in Phase 6."""

from .baseline import FrozenRetrievalInputs, load_frozen_retrieval_inputs
from .bm25 import BM25Index
from .dense import MiniLMEncoder
from .hybrid import FrozenHybridRetriever, load_frozen_literature_assets
from .postgres import PgVectorStore
from .rrf import reciprocal_rank_fusion

__all__ = [
    "FrozenRetrievalInputs",
    "BM25Index",
    "MiniLMEncoder",
    "FrozenHybridRetriever",
    "PgVectorStore",
    "load_frozen_retrieval_inputs",
    "load_frozen_literature_assets",
    "reciprocal_rank_fusion",
]
