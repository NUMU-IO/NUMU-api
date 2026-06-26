"""NUMU-knowledge RAG: embeddings + two-layer pgvector-style retrieval.

Layer A = shared NUMU platform docs (no merchant data, not tenant-scoped).
Layer B = per-tenant knowledge (RLS). A query retrieves Layer A + only the
caller's Layer B (Constitution VIII).
"""
