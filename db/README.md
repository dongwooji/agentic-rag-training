# Database workflow

## Phase 3 — training log

Use `python scripts/setup_postgres.py` for the first local setup. Use
`python scripts/load_postgres.py --replace` only when intentionally replacing an
existing project load.

Migration `001_training_schema.sql` creates relational training-log objects in
the dedicated `training` schema.

## Phase 4 — literature corpus

After Phase 3 is installed, run:

```powershell
python scripts\load_literature_postgres.py
```

Migration `002_literature_schema.sql` creates the `literature` schema and loads
the frozen `literature_corpus_v1` paper and chunk records. The loader is
transactional, validates artifact hashes and counts, and rejects an accidental
duplicate version load. Use `--replace` only for an intentional rebuild.

Phase 4 stores text and provenance only. Embeddings, the pgvector extension,
and retrieval indexes are deferred until after the Phase 5 evaluation set is
frozen.
