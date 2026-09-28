-- Run as a PostgreSQL administrator in the project database.
-- This is deliberately separate from the Phase 3/4 migration chain so those
-- phases remain runnable on PostgreSQL installations without pgvector.
CREATE EXTENSION IF NOT EXISTS vector;

