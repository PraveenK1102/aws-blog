"""Dependency injection for the routed graph.

The graph needs production retrieval, prompt building, citation and tenant
helpers that live in `app.py`. Importing `app` from here would be circular
(`app` imports `rag`), and it would also make the graph untestable without
boto3/Qdrant. So the endpoint constructs a `RagDeps` from its own module-level
functions and hands it to the graph.

Consequences, both deliberate:
  * the graph reuses the EXISTING production functions (§11) rather than an
    experimental copy of retrieval; and
  * every node is unit-testable against fakes with no AWS or network.
"""
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RagDeps:
    # --- retrieval (existing production functions) ---
    hybrid_search: Callable[..., Any]           # (question, tenant_id, dense_vec=) -> (points, top_dense)
    hybrid_search_multi: Callable[..., Any]     # (question, tenant_ids, dense_vec=) -> (points, top_dense)
    embed_dense: Callable[[str], list]          # Titan query embedding
    # --- semantic cache (existing production module) ---
    semcache_lookup: Callable[..., Any]         # (tenant_id, dense) -> hit|None
    semcache_store: Callable[..., Any]          # (tenant_id, question, dense, answer, citations)
    # --- context / citation (existing production functions) ---
    llm_context: Callable[[Any], list]
    dedupe_citations: Callable[[Any], list[dict]]
    dedupe_citations_attributed: Callable[[Any], list[dict]]
    context_est_tokens: Callable[[Any], int]
    # --- prompt builders (existing production functions, §22) ---
    build_system_prompt: Callable[..., str]         # (tenant, results) -> str
    build_group_system_prompt: Callable[[Any], str] # (results) -> str
    get_tenant: Callable[[str], dict | None]
    # --- tuning constants owned by app.py ---
    retrieval_floor: float
    max_llm_context_chunks: int
