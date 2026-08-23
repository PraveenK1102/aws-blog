"""rag-routed-langgraph-v2-offline — routed RAG graph: frozen Router V2 + validated decomposition.

    START -> route_question  (FROZEN Router V2)
               ├─ simple   -> normal_retrieve -> normal_answer -> END
               └─ compound -> decompose ─┬─ (>=2 subquestions) -> [Send fan-out]
                                         │        retrieve_branch xN  (parallel, Semaphore(2))
                                         │        -> merge_evidence (defer=True fan-in)
                                         │        -> final_answer -> END
                                         └─ (unusable decomposition) -> normal_retrieve -> ...

Real LangGraph primitives: typed state with reducers, conditional edges, dynamic
`Send` fan-out, deferred fan-in, bounded concurrency, explicit END.

FIDELITY BY REUSE, NOT COPY. The decomposition analyzer prompt, its parser, the
scope resolver, the retrieval wrapper, the coverage-aware merge, and both answer
nodes are IMPORTED from the frozen `decomp_graph` (v1) and called unmodified.
Copy-pasting them would let them drift; importing guarantees the validated
behaviour is byte-identical. `decomp_graph` is never mutated.

Routing is the ONLY thing that changed from v1: v1 used its own ad-hoc analyzer
for the simple/compound decision; v2 uses the frozen Router V2 that passed the
40-case untouched holdout (recall 1.000, specificity 0.950).

Router V2's `information_needs` are carried as DIAGNOSTIC METADATA ONLY. They are
never used as retrieval queries — V2's needs are validated for routing, not as
retrieval-query generation, so the frozen v1 decomposition node still produces the
subquestions that drive the branches.

Cost shape per compound case: 1 router call (or replayed) + 1 decomposition call
+ N branch retrievals + 1 final generation. Final context <= 5 chunks.
Groq calls: 0. LangChain is NOT an application abstraction here — langchain-core
is only a LangGraph transitive dependency.
"""
from __future__ import annotations

import hashlib
import operator
import os
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

# NOTE: nvidia_harness must be imported FIRST — it is what puts lambdas/ask on
# sys.path, so `app` is unimportable before it. Alphabetical ordering breaks this.
import nvidia_harness as H         # noqa: E402  (path side effect, must precede `app`)
import nvidia_provider as nv       # noqa: E402
import app as prod                 # noqa: E402
import decomp_graph as D1          # FROZEN v1 — imported read-only, never mutated
import router_v2 as R2             # FROZEN router — imported read-only, never mutated

EXPERIMENT = "rag-routed-langgraph-v2-offline"
# frozen identities this graph refuses to run against anything else
FROZEN_ROUTER_PROMPT_SHA = "763d12cd82245285"
FROZEN_ROUTER_FINGERPRINT = "cc6dfde4be4dd586"
MAX_BRANCH_CONCURRENCY = D1.MAX_BRANCH_CONCURRENCY          # Semaphore(2), shared with v1


def router_identity() -> dict:
    """Live identity of the frozen router, for pre-flight assertion."""
    return {"prompt_sha": hashlib.sha256(R2.ROUTER_SYS.encode()).hexdigest()[:16],
            "model": R2.ROUTER_MODEL, "max_tokens": R2.ROUTER_MAX_TOKENS,
            "reason_codes": list(R2.REASON_CODES)}


def assert_frozen_router() -> None:
    ident = router_identity()
    if ident["prompt_sha"] != FROZEN_ROUTER_PROMPT_SHA:
        raise RuntimeError(f"frozen Router V2 prompt changed: {ident['prompt_sha']} "
                           f"!= {FROZEN_ROUTER_PROMPT_SHA}")
    if ident["model"] != H.APP_MODEL:
        raise RuntimeError(f"router model changed: {ident['model']}")


# ---------------------------------------------------------------- state
class RoutedState(TypedDict, total=False):
    case_id: str
    original_question: str
    route: str                        # single | multi | group  (retrieval scope kind)
    target: str | None                # profile name(s) / group name — MUST be declared:
                                      # LangGraph strips keys absent from the schema, which
                                      # silently emptied the tenant scope in the v1 bug.
    scope_tenant_ids: list[str]       # resolved allowed tenants — never widened
    scope_single_tenant: str | None

    # --- routing (frozen Router V2) ---
    injected_router_result: dict | None   # replay seam: harness may supply a persisted
                                          # V2 verdict. The NODE never reads a file, so the
                                          # graph still supports genuine live routing.
    router_source: str                    # "live" | "replayed"
    needs_decomposition: bool
    router_reason_code: str | None
    router_information_needs: list[str]   # DIAGNOSTIC ONLY — never a retrieval query
    router_parse_ok: bool

    # --- decomposition (frozen v1 behaviour) ---
    is_compound: bool
    subquestions: list[str]
    decomposition_unusable: bool

    branch_results: Annotated[list[dict], operator.add]   # fan-in reducer
    merged_context: list[Any]
    merged_context_map: list[dict]
    citations: list[Any]
    final_answer: str
    answer_path: str                      # "simple" | "compound"

    node_latencies: Annotated[dict, lambda a, b: {**a, **b}]
    tokens: Annotated[dict, lambda a, b: {**a, **b}]
    errors: Annotated[list[str], operator.add]
    branch_evidence_missing: bool


# ---------------------------------------------------------------- nodes
def resolve_scope(state: RoutedState) -> dict:
    """Resolve tenant scope with the FROZEN v1 resolver. Never widened downstream."""
    tids, single = D1._scope(state["route"], state.get("target"))
    return {"scope_tenant_ids": tids, "scope_single_tenant": single}


def route_question(state: RoutedState) -> dict:
    """FROZEN Router V2. Live by default; the harness may inject a persisted verdict."""
    t0 = time.time()
    inj = state.get("injected_router_result")
    if inj is not None:
        return {"needs_decomposition": bool(inj.get("predicted_compound")),
                "router_reason_code": inj.get("reason_code"),
                "router_information_needs": list(inj.get("information_needs") or []),
                "router_parse_ok": bool(inj.get("parse_ok", True)),
                "router_source": "replayed",
                "node_latencies": {"route_question_ms": 0.0}}
    assert_frozen_router()
    res = R2.classify(state["original_question"])          # question ONLY
    nd = res.get("needs_decomposition")
    out = {"needs_decomposition": bool(nd),
           "router_reason_code": res.get("reason_code"),
           "router_information_needs": list(res.get("information_needs") or []),
           "router_parse_ok": bool(res.get("parse_ok")),
           "router_source": "live",
           "tokens": {"router_in": res.get("input_tokens"), "router_out": res.get("output_tokens")},
           "node_latencies": {"route_question_ms": res.get("latency_ms")}}
    if nd is None:                                          # unparseable -> safest is the simple path
        out["needs_decomposition"] = False
        out["errors"] = [f"router:{res.get('parse_error')}"]
    return out


def router_edge(state: RoutedState) -> str:
    return "compound" if state.get("needs_decomposition") else "simple"


def decompose(state: RoutedState) -> dict:
    """FROZEN v1 decomposition: v1's analyzer prompt and parser, unmodified.

    Router V2's information_needs are deliberately NOT used as subquestions."""
    t0 = time.time()
    try:
        r = nv.chat(D1.ANALYZER_MODEL,
                    [{"role": "system", "content": D1.ANALYZER_SYS},
                     {"role": "user", "content": f"Question: {state['original_question']}"}],
                    max_tokens=700, timeout=D1.ANALYZER_TIMEOUT)
        parsed = D1.parse_analysis(r["content"] or "")
        subs = parsed["subquestions"]
        return {"is_compound": bool(subs), "subquestions": subs,
                "decomposition_unusable": len(subs) < 2,
                "tokens": {"decompose_in": r["input_tokens"], "decompose_out": r["output_tokens"]},
                "node_latencies": {"decompose_ms": r["latency_ms"]}}
    except Exception as e:
        return {"is_compound": False, "subquestions": [], "decomposition_unusable": True,
                "errors": [f"decompose:{type(e).__name__}"],
                "node_latencies": {"decompose_ms": round((time.time()-t0)*1000, 1)}}


def decompose_edge(state: RoutedState):
    """Combined fallback + dynamic Send fan-out.

    MUST return either a node name or a list of `Send` objects — NOT a plain label
    routed through a path map. Returning a label makes LangGraph deliver the whole
    state to `retrieve_branch`, which then has no `branch`/`subquestion` payload
    (caught by test_routed_graph_v2 as KeyError: 'branch').

    A decomposition that cannot yield >=2 atomic subquestions falls back to the
    simple path rather than fanning out a degenerate single branch."""
    if state.get("decomposition_unusable"):
        return "normal_retrieve"
    return D1.fan_out(state)                 # list[Send] — one branch per subquestion


def normal_retrieve(state: RoutedState) -> dict:
    out = D1.normal_retrieve(state)                        # frozen v1 node, unmodified
    out["answer_path"] = "simple"
    return out


def normal_answer(state: RoutedState) -> dict:
    return D1.normal_answer(state)                         # frozen v1 node, unmodified


def final_answer(state: RoutedState) -> dict:
    out = D1.final_answer(state)                           # frozen v1 node, unmodified
    out["answer_path"] = "compound"
    return out


# ---------------------------------------------------------------- graph
def build_graph():
    g = StateGraph(RoutedState)
    g.add_node("resolve_scope", resolve_scope)
    g.add_node("route_question", route_question)
    g.add_node("normal_retrieve", normal_retrieve)
    g.add_node("normal_answer", normal_answer)
    g.add_node("decompose", decompose)
    g.add_node("retrieve_branch", D1.retrieve_branch)                  # frozen v1 node
    g.add_node("merge_evidence", D1.merge_evidence, defer=True)        # frozen v1 fan-in
    g.add_node("final_answer", final_answer)

    g.add_edge(START, "resolve_scope")
    g.add_edge("resolve_scope", "route_question")
    g.add_conditional_edges("route_question", router_edge,
                            {"simple": "normal_retrieve", "compound": "decompose"})
    g.add_edge("normal_retrieve", "normal_answer")
    g.add_edge("normal_answer", END)
    # list form (not a path map) so returned `Send` objects fan out correctly
    g.add_conditional_edges("decompose", decompose_edge,
                            ["retrieve_branch", "normal_retrieve"])
    g.add_edge("retrieve_branch", "merge_evidence")
    g.add_edge("merge_evidence", "final_answer")
    g.add_edge("final_answer", END)
    return g.compile()


def fan_out(state: RoutedState):
    """Exposed for tests: the Send fan-out used by the compound branch."""
    return D1.fan_out(state)
