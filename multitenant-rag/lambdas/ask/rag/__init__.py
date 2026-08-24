"""Production routed-RAG package.

Owns the production graph logic that was validated offline as
`rag-routed-langgraph-v2-offline`. NOTHING in this package imports from
`evals/` — the frozen prompts and contracts are carried here as constants and
held to byte parity by `test_frozen_parity.py`.

Import boundary: modules here never import `app` at module scope (that would be
circular). Production functions are injected via `deps.RagDeps`.
"""
