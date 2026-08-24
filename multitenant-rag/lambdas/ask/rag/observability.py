"""LangSmith span tree for a completed routed request.

WHY SPANS ARE EMITTED AFTER THE GRAPH, NOT DURING IT
----------------------------------------------------
Compound branches run in parallel threads. `RunTree.create_child()` is not
documented thread-safe, so creating child runs from inside concurrent nodes
would put an unproven concurrency assumption directly on the request path — for
telemetry. Instead the graph records exact per-node timings in
`state["node_latencies"]` and per-branch facts in `state["branch_results"]`, and
this module replays them into the span hierarchy once the answer is already
produced.

Consequences, both acceptable and deliberate:
  * durations are the REAL measured node durations, not wall-clock guesses;
  * tracing cannot slow, block, or break generation — it runs after it;
  * the hierarchy is exactly the one in §20.

Privacy: every value goes through `common.tracing._clean`'s whitelist. This
module never passes question, answer, prompt, chunk text, sub-question text,
history, tenant ids, group ids or tokens-as-content.
"""
from . import config
from .budget import LIMITS


def _lat(state, key):
    return (state.get("node_latencies") or {}).get(key)


def emit_spans(root_span, state, budget, *, route_type: str) -> None:
    """Build the §20 span tree under an existing root. Never raises."""
    try:
        _emit(root_span, state, budget, route_type)
    except Exception:
        # tracing is best-effort by contract; the answer is already sent
        pass


def _emit(root, state, budget, route_type: str) -> None:
    snap = budget.snapshot()
    toks = state.get("tokens") or {}

    root.set(routed=True, route_type=route_type,
             answer_path=state.get("answer_path"),
             route_decision=("compound" if state.get("needs_decomposition") else "simple"),
             router_reason_code=state.get("router_reason_code"),
             router_parse_ok=state.get("router_parse_ok"),
             router_failed=state.get("router_failed"),
             decomposition_used=state.get("decomposition_used"),
             decomposition_fallback=state.get("decomposition_unusable"),
             decomposition_failed=state.get("decomposition_failed"),
             subquestion_count=len(state.get("subquestions") or []),
             branch_count=state.get("branch_count"),
             successful_branch_count=state.get("successful_branch_count"),
             failed_branch_count=state.get("failed_branch_count"),
             partial_branch_failure=state.get("partial_branch_failure"),
             retrieval_candidate_count=state.get("retrieval_candidate_count"),
             top_dense_similarity=state.get("top_dense"),
             relevance_floor_passed=state.get("relevance_floor_passed"),
             final_context_count=len(state.get("merged_context") or []),
             estimated_context_tokens=state.get("estimated_context_tokens"),
             citation_count=len(state.get("citations") or []),
             result_type=state.get("result_type"),
             cache_hit=bool(state.get("cache_hit")),
             **{k: snap[k] for k in (
                 "request_deadline_ms", "remaining_budget_ms", "deadline_exceeded",
                 "router_calls", "decomposition_calls", "generation_calls",
                 "groq_logical_calls", "retrieval_branches", "titan_embeddings",
                 "qdrant_dense_probes", "qdrant_hybrid_queries",
                 "qdrant_physical_queries", "semcache_embeddings",
                 "rate_limit_events", "retry_count", "total_tokens")})

    # ---- semantic_cache ----
    if state.get("cache_eligible"):
        with root.child("semantic_cache", run_type="retriever") as sc:
            sc.set(cache_hit=bool(state.get("cache_hit")),
                   latency_ms=_lat(state, "semantic_cache_ms"),
                   citation_count=len(state.get("cached_citations") or []))
    if state.get("cache_hit"):
        return                      # a hit spends no router/decomp/generation

    # ---- router_v2 ----
    with root.child("router_v2", run_type="llm") as rs:
        rs.set(provider="groq", model=config.ROUTER_MODEL,
               route_decision=("compound" if state.get("needs_decomposition") else "simple"),
               router_reason_code=state.get("router_reason_code"),
               router_parse_ok=state.get("router_parse_ok"),
               router_failed=state.get("router_failed"),
               input_tokens=toks.get("router_in"), output_tokens=toks.get("router_out"),
               latency_ms=_lat(state, "route_question_ms"))

    # ---- decomposition (conditional) ----
    if _lat(state, "decompose_ms") is not None:
        with root.child("decomposition", run_type="llm") as ds:
            ds.set(provider="groq", model=config.DECOMPOSE_MODEL,
                   decomposition_used=state.get("decomposition_used"),
                   decomposition_fallback=state.get("decomposition_unusable"),
                   decomposition_failed=state.get("decomposition_failed"),
                   subquestion_count=len(state.get("subquestions") or []),
                   input_tokens=toks.get("decompose_in"),
                   output_tokens=toks.get("decompose_out"),
                   latency_ms=_lat(state, "decompose_ms"))

    # ---- retrieval branches ----
    branches = sorted((state.get("branch_results") or []),
                      key=lambda b: b.get("branch", 0))
    for b in branches:
        name = f"retrieval_branch_{b.get('branch', 0)}"
        with root.child(name, run_type="retriever") as bs:
            bs.set(branch=b.get("branch"),
                   candidate_count=b.get("candidate_count"),
                   top_dense_similarity=b.get("top_dense"),
                   evidence_missing=bool(b.get("evidence_missing")),
                   error_type=b.get("error"),
                   latency_ms=b.get("latency_ms"))
            # The four physical operations inside one logical retrieval. Emitted
            # as structure (no per-op timing is measured inside the frozen
            # production retrieval function, so no invented numbers are set).
            for child, rt in (("titan_embedding", "embedding"),
                              ("bm25_encode", "embedding"),
                              ("qdrant_dense_probe", "retriever"),
                              ("qdrant_hybrid_rrf", "retriever")):
                with bs.child(child, run_type=rt):
                    pass

    # ---- merge / context / generation ----
    if _lat(state, "merge_evidence_ms") is not None:
        with root.child("merge_evidence", run_type="chain") as ms:
            ms.set(branch_count=state.get("branch_count"),
                   successful_branch_count=state.get("successful_branch_count"),
                   failed_branch_count=state.get("failed_branch_count"),
                   partial_branch_failure=state.get("partial_branch_failure"),
                   final_context_count=len(state.get("merged_context") or []),
                   latency_ms=_lat(state, "merge_evidence_ms"))

    with root.child("build_context", run_type="chain") as bc:
        bc.set(final_context_count=len(state.get("merged_context") or []),
               estimated_context_tokens=state.get("estimated_context_tokens"),
               max_llm_context_chunks=LIMITS["final_context_chunks"])

    with root.child("groq_generation", run_type="llm") as gs:
        gs.set(provider="groq", model=config.GENERATION_MODEL,
               input_tokens=toks.get("gen_in"), output_tokens=toks.get("gen_out"),
               result_type=state.get("result_type"),
               latency_ms=_lat(state, "generate_ms"))

