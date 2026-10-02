"""Runtime retrieval identifiers.

``EMBEDDING_RUN_ID`` names the embedding run that the runtime literature tool
reads from pgvector, and ``CORPUS_VERSION`` names the literature corpus that the
runtime hybrid retriever verifies before loading. The historical Dense baseline
runner that originally defined both is preserved in the
``legacy-pre-retrieval-v2`` Git tag.
"""

EMBEDDING_RUN_ID = "dense_multilingual_minilm_l12_v2_literature_corpus_v1"
CORPUS_VERSION = "literature_corpus_v1"
