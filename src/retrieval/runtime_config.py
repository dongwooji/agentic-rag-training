"""Runtime retrieval identifiers.

``EMBEDDING_RUN_ID`` names the frozen embedding run that the runtime literature
tool reads from pgvector. It is defined here so runtime code does not depend on
the historical Dense baseline runner (``src.retrieval.baseline``), which keeps
its own identical definition for reproducibility.
"""

EMBEDDING_RUN_ID = "dense_multilingual_minilm_l12_v2_literature_corpus_v1"
