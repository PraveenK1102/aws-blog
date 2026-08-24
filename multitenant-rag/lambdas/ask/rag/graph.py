"""The production routed-RAG graph.

    START
      -> resolve_scope        (validate the ALREADY-AUTHORIZED scope; fail closed)
      -> load_history         (normalise prior turns)
      -> fold_followup        (short follow-up -> folded retrieval query)
      -> semantic_cache_check (eligible single-turn only; a HIT spends no router call)
           |- hit  -> END
           `- miss -> route_question        (FROZEN Router V2, Groq 20B)
                        |- simple   -> normal_retrieve --------------.
                        `- compound -> decompose                      |
                                         |- unusable -> normal_retrieve
                                         `- usable   -> [Send fan-out]
                                                          retrieve_branch xN
                                                          (parallel, Semaphore(2))
                                                       -> merge_evidence (defer=True)
                                                                          |
                        .-----------------------------------------------`
                        v
                     build_context  ->  generate  ->  finalize  -> END

BOUNDARY DECISIONS (§10 asks these be documented)
-------------------------------------------------
IN the graph:  scope validation, history normalisation, follow-up folding,
               semantic-cache probe, routing, decomposition, retrieval, merge,
               context+prompt construction, generation, citation construction.

OUTSIDE the graph (owned by the HTTP layer, per §6):
  * JWT validation, request validation, tenant authorization, client init;
  * NDJSON/SSE response assembly;
  * semantic-cache WRITE, chat persistence (`save_chat`) and the DynamoDB usage
    row. These are deliberately outside so a persistence failure cannot
    invalidate an answer the user already received.

Injection: `deps` (production functions) and `budget` (per-request bounds) are
passed through LangGraph's `configurable`, so the graph object itself is
compiled ONCE per container and holds no request state.
"""
import functools
import time

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from common.logger import get_logger

from . import generation, merge, prompts, retrieval_nodes, router
from . import decomposition as decomp
from .budget import BudgetExceeded, LIMITS
from .scope import Scope, ScopeError
from .state import RoutedState

log = get_logger("rag.graph")


def _cfg(config):
    """Pull the injected per-request objects out of LangGraph's config."""
    c = (config or {}).get("configurable") or {}
    return c["deps"], c["budget"], c.get("tracer_span")


# ---------------------------------------------------------------- nodes
def resolve_scope(state, config=None):
    """The scope was resolved and AUTHORIZED by the HTTP layer. This node only
    validates it and fails closed — it can never widen anything."""
    scope = state.get("scope")
    if not isinstance(scope, Scope):
        raise ScopeError("routed graph started without a resolved Scope")
    return {"node_latencies": {"resolve_scope_ms": 0.0}}


def load_history(state, config=None):
    """Normalise prior turns. Bounded to the SAME last-8 window as the existing
    production path — conversation memory is NOT expanded (§24)."""
    hist = []
    for m in (state.get("history") or [])[-8:]:
        role = m.get("role")
        content = m.get("content") or m.get("text") or ""
        if role in ("user", "assistant") and content:
            hist.append({"role": role, "content": content})
    return {"history": hist}


def fold_followup(state, config=None):
    """Short follow-ups ('yes', 'tell me more') carry no retrievable content, so
    the previous user turn is folded in for RETRIEVAL only. Identical rule to the
    existing production path: <= 4 words and a prior user turn exists.

    History never touches scope — only the retrieval query text.
    """
    question = state["question"]
    rq = question
    prev_user = next((m["content"] for m in reversed(state.get("history") or [])
                      if m["role"] == "user"), None)
    if prev_user and len(question.split()) <= 4:
        rq = f"{prev_user} {question}"
    return {"retrieval_query": rq}


def semantic_cache_check(state, config=None):
    """Eligible single-turn probe. A HIT short-circuits BEFORE any Groq call.

    Eligibility mirrors the existing production code exactly:
      * single-profile route only (the group route is cross-tenant and uncached);
      * no chat history (a cached answer ignores conversation context);
      * the cache is per-tenant filtered inside `semcache.lookup`.
    """
    deps, budget, _ = _cfg(config)
    scope: Scope = state["scope"]
    eligible = (scope.kind == "single") and not state.get("history")
    if not eligible:
        return {"cache_eligible": False, "cache_hit": False, "query_dense": None}

    t0 = time.monotonic()
    try:
        budget.spend_semcache_embedding()
        dense = deps.embed_dense(state["question"])
    except Exception as e:
        # Cache/embedding failure must never fail the request — fall through to
        # a normal routed query, which will embed internally.
        log.warning("cache probe embed failed", error_type=type(e).__name__)
        return {"cache_eligible": True, "cache_hit": False, "query_dense": None,
                "errors": [f"semcache_embed:{type(e).__name__}"]}

    hit = None
    try:
        budget.spend("semcache_queries")
        hit = deps.semcache_lookup(scope.single_tenant, dense)
    except Exception as e:
        log.warning("cache lookup failed", error_type=type(e).__name__)

    if hit:
        return {"cache_eligible": True, "cache_hit": True,
                "query_dense": dense,
                "cached_answer": hit.get("answer", ""),
                "cached_citations": list(hit.get("citations") or []),
                "answer_path": "cache", "result_type": "cache_hit",
                "final_answer": hit.get("answer", ""),
                "citations": list(hit.get("citations") or []),
                "node_latencies": {
                    "semantic_cache_ms": round((time.monotonic() - t0) * 1000, 1)}}
    return {"cache_eligible": True, "cache_hit": False, "query_dense": dense,
            "node_latencies": {
                "semantic_cache_ms": round((time.monotonic() - t0) * 1000, 1)}}


def cache_edge(state) -> str:
    return "hit" if state.get("cache_hit") else "miss"


def route_question(state, config=None):
    _, budget, _ = _cfg(config)
    return router.route_question(state, budget)


def decompose(state, config=None):
    _, budget, _ = _cfg(config)
    return decomp.decompose(state, budget)


def normal_retrieve(state, config=None):
    deps, budget, _ = _cfg(config)
    return retrieval_nodes.normal_retrieve(state, deps, budget)


def retrieve_branch(payload, config=None):
    deps, budget, _ = _cfg(config)
    return retrieval_nodes.retrieve_branch(payload, deps, budget)


def merge_evidence(state, config=None):
    deps, _, _ = _cfg(config)
    return merge.merge_evidence(state, deps)


def decompose_edge(state):
    """Combined fallback + dynamic Send fan-out.

    MUST return a node name OR a list of `Send` objects — never a plain label
    routed through a path map. Returning a label makes LangGraph deliver the
    whole state to `retrieve_branch`, which then has no `branch`/`subquestion`
    payload (this exact bug produced KeyError: 'branch' in the offline v2 run).
    """
    if state.get("decomposition_unusable"):
        return "normal_retrieve"
    payloads = retrieval_nodes.fan_out_payloads(state)
    if len(payloads) < 2:                       # defensive: never a lone branch
        return "normal_retrieve"
    return [Send("retrieve_branch", p) for p in payloads]


def build_context(state, config=None):
    deps, _, _ = _cfg(config)
    out = generation.build_context(state, deps)
    ctx = state.get("merged_context") or []
    # Hard architectural bound, asserted rather than assumed.
    if len(ctx) > LIMITS["final_context_chunks"]:
        raise BudgetExceeded("final_context_chunks", LIMITS["final_context_chunks"])
    out["node_latencies"] = {"build_context_ms": 0.0}
    return out


def generate(state, config=None):
    _, budget, _ = _cfg(config)
    return generation.generate(state, budget, history=state.get("history"))


def finalize(state, config=None):
    """Citations from the SAME capped context the model received, plus the
    refusal rule that suppresses citations when the model declined."""
    deps, budget, _ = _cfg(config)
    answer = state.get("final_answer") or ""
    rtype = state.get("result_type") or "answered"

    if rtype in ("provider_unavailable", "generation_error", "empty_context"):
        return {"citations": [], "result_type": rtype,
                "node_latencies": {"finalize_ms": 0.0}}

    low = answer.lower()
    refused = ("hasn't written about" in low
               or "no one in this selection has written about" in low)
    citations = [] if refused else generation.build_citations(state, deps)
    return {"citations": citations,
            "result_type": "refused" if refused else "answered",
            "node_latencies": {"finalize_ms": 0.0}}


# ---------------------------------------------------------------- graph
@functools.lru_cache(maxsize=1)
def build_graph():
    """Compile once per container. Holds no request state."""
    prompts.assert_frozen()          # fail closed on frozen-prompt drift

    g = StateGraph(RoutedState)
    g.add_node("resolve_scope", resolve_scope)
    g.add_node("load_history", load_history)
    g.add_node("fold_followup", fold_followup)
    g.add_node("semantic_cache_check", semantic_cache_check)
    g.add_node("route_question", route_question)
    g.add_node("normal_retrieve", normal_retrieve)
    g.add_node("decompose", decompose)
    g.add_node("retrieve_branch", retrieve_branch)
    g.add_node("merge_evidence", merge_evidence, defer=True)   # fan-in barrier
    g.add_node("build_context", build_context)
    g.add_node("generate", generate)
    g.add_node("finalize", finalize)

    g.add_edge(START, "resolve_scope")
    g.add_edge("resolve_scope", "load_history")
    g.add_edge("load_history", "fold_followup")
    g.add_edge("fold_followup", "semantic_cache_check")
    g.add_conditional_edges("semantic_cache_check", cache_edge,
                            {"hit": END, "miss": "route_question"})
    g.add_conditional_edges("route_question", router.router_edge,
                            {"simple": "normal_retrieve", "compound": "decompose"})
    # list form (NOT a path map) so returned Send objects fan out correctly
    g.add_conditional_edges("decompose", decompose_edge,
                            ["retrieve_branch", "normal_retrieve"])
    g.add_edge("retrieve_branch", "merge_evidence")
    g.add_edge("normal_retrieve", "build_context")
    g.add_edge("merge_evidence", "build_context")
    g.add_edge("build_context", "generate")
    g.add_edge("generate", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def run(*, request_id: str, question: str, scope: Scope, history: list[dict],
        deps, budget, recursion_limit: int = 25) -> dict:
    """Execute one routed request. Returns the final state."""
    graph = build_graph()
    initial: dict = {"request_id": request_id, "question": question,
                     "retrieval_query": question, "scope": scope,
                     "history": list(history or []), "branch_results": [],
                     "node_latencies": {}, "tokens": {}, "errors": []}
    return graph.invoke(initial, config={
        "configurable": {"deps": deps, "budget": budget},
        "recursion_limit": recursion_limit,
    })
