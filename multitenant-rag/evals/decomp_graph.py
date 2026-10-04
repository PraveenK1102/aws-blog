"""LangGraph agentic query decomposition / parallel multi-branch RAG (OFFLINE research).

    START -> analyze_question -> (conditional)
                                  ├─ simple   -> normal_retrieve -> normal_answer -> END
                                  └─ compound -> decompose -> [Send fan-out]
                                                              retrieve_branch xN
                                                              -> merge_evidence (defer, fan-in)
                                                              -> final_answer -> END

Real LangGraph primitives: typed shared state with reducers, nodes, conditional
routing, `Send` fan-out, deferred fan-in, explicit END.

NOT sequential multi-hop: the branches are INDEPENDENT and run in parallel. The
topology can be extended to sequential multi-hop later.

Cost shape for a compound case: 1 analyzer call + 1 final generation call
(NO per-branch generation). Final context stays <= MAX_LLM_CONTEXT_CHUNKS (5).
Groq calls: 0 (guarded). LangChain is NOT used as an application abstraction —
langchain-core is present only as a LangGraph transitive dependency.
"""
from __future__ import annotations

import json
import operator
import os
import re
import threading
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import nvidia_harness as H
import nvidia_provider as nv
import app as prod

ANALYZER_MODEL = os.environ.get("DECOMP_ANALYZER_MODEL", H.APP_MODEL)   # NVIDIA 20B
GEN_MODEL = os.environ.get("DECOMP_GEN_MODEL", H.APP_MODEL)             # NVIDIA 20B
MAX_BRANCH_CONCURRENCY = int(os.environ.get("DECOMP_BRANCH_CONCURRENCY", "2"))
ANALYZER_TIMEOUT = int(os.environ.get("DECOMP_ANALYZER_TIMEOUT", "120"))
GEN_TIMEOUT = int(os.environ.get("DECOMP_GEN_TIMEOUT", "150"))
_retrieval_gate = threading.Semaphore(MAX_BRANCH_CONCURRENCY)


# ---------------------------------------------------------------- state
class DecompState(TypedDict, total=False):
    case_id: str
    original_question: str
    route: str                       # single | multi | group
    target: str | None               # profile name(s) / group name — MUST be declared:
                                     # LangGraph strips keys absent from the state schema,
                                     # which would silently empty the tenant scope.
    scope_tenant_ids: list[str]      # resolved allowed tenants — never widened
    scope_single_tenant: str | None

    is_compound: bool
    subquestions: list[str]
    analysis_raw: str

    branch_results: Annotated[list[dict], operator.add]   # fan-in reducer
    merged_context: list[Any]
    merged_context_map: list[dict]
    citations: list[Any]
    final_answer: str

    node_latencies: Annotated[dict, lambda a, b: {**a, **b}]
    tokens: Annotated[dict, lambda a, b: {**a, **b}]
    errors: Annotated[list[str], operator.add]
    branch_evidence_missing: bool


# ---------------------------------------------------------------- helpers
def _scope(route: str, target: str | None):
    """Resolve the SAME tenant scope the deterministic app would use. Never widened."""
    if route == "multi":
        tids = [H._NAME2TID.get(n.strip()) for n in (target or "").split(",")]
        return [t for t in tids if t], None
    if route == "group":
        from common import groups as groupstore
        gid = H._GRP2ID.get((target or "").strip())
        return (groupstore.member_tenant_ids(gid) if gid else []), None
    tid = H._NAME2TID.get((target or "").strip())
    return ([tid] if tid else []), tid


def _retrieve(question: str, route: str, tenant_ids: list[str], single: str | None):
    """EXISTING production hybrid retrieval. Bounded concurrency."""
    with _retrieval_gate:
        if route == "single":
            return prod._hybrid_search(question, single)
        return prod._hybrid_search_multi(question, tenant_ids)


ANALYZER_SYS = (
    "You analyse a reader's question about a set of blog posts and decide whether it contains "
    "MORE THAN ONE independent information need.\n"
    "Reply with ONLY JSON, no prose:\n"
    '{"is_compound": true, "subquestions": ["...", "..."]}\n'
    'or {"is_compound": false, "subquestions": []}\n'
    "Rules: mark is_compound true only when the question asks for two or three things that could each "
    "be answered separately. Produce 2-3 atomic subquestions, each ONE information need. Preserve the "
    "original names, entities, identifiers and time constraints exactly. Do not change the intent, do "
    "not add facts, and together they must cover the whole original question. "
    "A single question with one need is NOT compound."
)


def parse_analysis(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("no JSON in analyzer output")
    d = json.loads(m.group(0))
    is_c = bool(d.get("is_compound"))
    subs = [str(s).strip() for s in (d.get("subquestions") or []) if str(s).strip()]
    if is_c and len(subs) < 2:
        is_c = False; subs = []                     # unusable decomposition -> treat as simple
    if is_c:
        subs = subs[:3]                             # cap at 3 atomic subquestions
    return {"is_compound": is_c, "subquestions": subs if is_c else []}


# ---------------------------------------------------------------- nodes
def analyze_question(state: DecompState) -> dict:
    """Runtime compound detection. Sees ONLY the user question — never the expected
    answer, dataset scenario label, or any baseline score."""
    t0 = time.time()
    tids, single = _scope(state["route"], state.get("target"))
    out = {"scope_tenant_ids": tids, "scope_single_tenant": single}
    try:
        r = nv.chat(ANALYZER_MODEL,
                    [{"role": "system", "content": ANALYZER_SYS},
                     {"role": "user", "content": state["original_question"]}],
                    max_tokens=500, timeout=ANALYZER_TIMEOUT)
        a = parse_analysis(r["content"])
        out.update(a)
        out["analysis_raw"] = (r["content"] or "")[:500]
        out["tokens"] = {"analyzer_in": r["input_tokens"], "analyzer_out": r["output_tokens"]}
        out["node_latencies"] = {"analyze_question_ms": r["latency_ms"]}
    except Exception as e:
        out.update({"is_compound": False, "subquestions": [],
                    "errors": [f"analyzer:{type(e).__name__}"],
                    "node_latencies": {"analyze_question_ms": round((time.time()-t0)*1000, 1)}})
    return out


def route_question(state: DecompState) -> str:
    return "compound" if state.get("is_compound") and len(state.get("subquestions") or []) >= 2 else "simple"


# ---- simple path: preserves current deterministic top-5 semantics ----
def normal_retrieve(state: DecompState) -> dict:
    t0 = time.time()
    cands, top = _retrieve(state["original_question"], state["route"],
                           state["scope_tenant_ids"], state.get("scope_single_tenant"))
    ctx = prod._llm_context(cands) if (cands and top >= prod.RETRIEVAL_FLOOR) else []
    return {"merged_context": ctx,
            "merged_context_map": [{"chunk": i, "subquestion_index": None} for i in range(len(ctx))],
            "branch_results": [{"branch": 0, "subquestion": state["original_question"],
                                "candidate_count": len(cands), "selected": len(ctx),
                                "top_dense": round(top, 4), "evidence_missing": not ctx}],
            "branch_evidence_missing": not ctx,
            "node_latencies": {"normal_retrieve_ms": round((time.time()-t0)*1000, 1)}}


# ---- compound path ----
def decompose(state: DecompState) -> dict:
    """Subquestions already produced by the analyzer; this node records the plan."""
    return {"node_latencies": {"decompose_ms": 0.0}}


def fan_out(state: DecompState):
    """LangGraph Send fan-out: one INDEPENDENT retrieval branch per subquestion."""
    return [Send("retrieve_branch",
                 {"case_id": state["case_id"], "branch": i, "subquestion": sq,
                  "route": state["route"], "scope_tenant_ids": state["scope_tenant_ids"],
                  "scope_single_tenant": state.get("scope_single_tenant")})
            for i, sq in enumerate(state["subquestions"])]


def retrieve_branch(payload: dict) -> dict:
    """Runs once per Send. Retrieval only — NO per-branch LLM call."""
    t0 = time.time()
    try:
        cands, top = _retrieve(payload["subquestion"], payload["route"],
                               payload["scope_tenant_ids"], payload.get("scope_single_tenant"))
        eligible = list(cands) if top >= prod.RETRIEVAL_FLOOR else []
        return {"branch_results": [{
            "branch": payload["branch"], "subquestion": payload["subquestion"],
            "candidate_count": len(cands), "top_dense": round(top, 4),
            "eligible": eligible, "evidence_missing": not eligible,
            "latency_ms": round((time.time()-t0)*1000, 1)}]}
    except Exception as e:
        return {"branch_results": [{"branch": payload["branch"], "subquestion": payload["subquestion"],
                                    "candidate_count": 0, "eligible": [], "evidence_missing": True,
                                    "error": type(e).__name__,
                                    "latency_ms": round((time.time()-t0)*1000, 1)}],
                "errors": [f"branch{payload['branch']}:{type(e).__name__}"]}


def merge_evidence(state: DecompState) -> dict:
    """Coverage-aware fan-in: dedupe, one best chunk per branch, then round-robin,
    capped at MAX_LLM_CONTEXT_CHUNKS. Never compares RRF scores across branches."""
    t0 = time.time()
    branches = sorted([b for b in state["branch_results"] if "eligible" in b],
                      key=lambda b: b["branch"])
    pools = [list(b["eligible"]) for b in branches]
    cap = prod.MAX_LLM_CONTEXT_CHUNKS
    seen, merged, cmap = set(), [], []

    def key(c):
        p = getattr(c, "payload", {}) or {}
        return (p.get("post_id"), (p.get("chunk_text") or "")[:80])

    # pass 1 — guarantee coverage: best available chunk from EACH branch
    for bi, pool in enumerate(pools):
        for c in pool:
            if key(c) not in seen:
                seen.add(key(c)); merged.append(c)
                cmap.append({"subquestion_index": branches[bi]["branch"], "rank_in_branch": pool.index(c)})
                break
        if len(merged) >= cap:
            break
    # pass 2 — round-robin the next ranked result from each branch
    depth = 1
    while len(merged) < cap and any(len(p) > depth for p in pools):
        for bi, pool in enumerate(pools):
            if len(merged) >= cap:
                break
            for c in pool[depth:]:
                if key(c) not in seen:
                    seen.add(key(c)); merged.append(c)
                    cmap.append({"subquestion_index": branches[bi]["branch"], "rank_in_branch": pool.index(c)})
                    break
        depth += 1
    return {"merged_context": merged[:cap], "merged_context_map": cmap[:cap],
            "branch_evidence_missing": any(b.get("evidence_missing") for b in branches),
            "node_latencies": {"merge_evidence_ms": round((time.time()-t0)*1000, 1)}}


GEN_SYS_COMPOUND = (
    "You answer a reader's question using ONLY the evidence excerpts provided.\n"
    "The question has several parts. You MUST address EVERY part.\n"
    "The excerpts are grouped by the sub-question they were retrieved for.\n"
    "If the evidence for one part is missing, say plainly that it is not covered by the available "
    "posts — never silently skip a part and never invent facts.\n"
    "Cite sources by post title in normal prose. Do not output your reasoning steps."
)


def _blocks(state):
    out, cmap = [], state.get("merged_context_map") or []
    subs = state.get("subquestions") or []
    for i, c in enumerate(state["merged_context"]):
        p = getattr(c, "payload", {}) or {}
        si = (cmap[i].get("subquestion_index") if i < len(cmap) else None)
        label = f"[for sub-question {si+1}] " if isinstance(si, int) and subs else ""
        out.append(f"{label}[Source: {p.get('title','')}]\n{p.get('chunk_text','')}")
    return "\n\n---\n\n".join(out)


def _generate(state: DecompState, system: str, user: str, tag: str) -> dict:
    try:
        r = nv.chat(GEN_MODEL, [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                    max_tokens=H.APP_MAX_TOKENS, timeout=GEN_TIMEOUT)
        cites = (prod._dedupe_citations_attributed(state["merged_context"])
                 if state["route"] in ("multi", "group") else prod._dedupe_citations(state["merged_context"]))
        return {"final_answer": (r["content"] or "").strip() or H.FALLBACK_TEXT,
                "citations": [c.get("title") if isinstance(c, dict) else str(c) for c in cites],
                "tokens": {"gen_in": r["input_tokens"], "gen_out": r["output_tokens"]},
                "node_latencies": {f"{tag}_ms": r["latency_ms"]}}
    except Exception as e:
        return {"final_answer": H.FALLBACK_TEXT, "citations": [],
                "errors": [f"{tag}:{type(e).__name__}"], "node_latencies": {f"{tag}_ms": None}}


def final_answer(state: DecompState) -> dict:
    """ONE generation call for the whole compound question."""
    if not state.get("merged_context"):
        return {"final_answer": "The available posts do not cover this question.", "citations": []}
    subs = "\n".join(f"{i+1}. {s}" for i, s in enumerate(state.get("subquestions") or []))
    user = (f"QUESTION:\n{state['original_question']}\n\nSUB-QUESTIONS TO COVER:\n{subs}\n\n"
            f"EVIDENCE:\n{_blocks(state)}")
    return _generate(state, GEN_SYS_COMPOUND, user, "final_answer")


def normal_answer(state: DecompState) -> dict:
    """Simple path — keeps existing production prompt semantics."""
    if not state.get("merged_context"):
        return {"final_answer": "No relevant content was found for this question.", "citations": []}
    if state["route"] in ("multi", "group"):
        system = prod._build_group_system_prompt(state["merged_context"])
    else:
        tenant = prod._get_tenant(state["scope_single_tenant"]) or {"display_name": "the author", "domain": ""}
        system = prod._build_system_prompt(tenant, state["merged_context"])
    return _generate(state, system, state["original_question"], "normal_answer")


# ---------------------------------------------------------------- graph
def build_graph():
    g = StateGraph(DecompState)
    g.add_node("analyze_question", analyze_question)
    g.add_node("normal_retrieve", normal_retrieve)
    g.add_node("normal_answer", normal_answer)
    g.add_node("decompose", decompose)
    g.add_node("retrieve_branch", retrieve_branch)
    g.add_node("merge_evidence", merge_evidence, defer=True)     # fan-in barrier
    g.add_node("final_answer", final_answer)

    g.add_edge(START, "analyze_question")
    g.add_conditional_edges("analyze_question", route_question,
                            {"simple": "normal_retrieve", "compound": "decompose"})
    g.add_edge("normal_retrieve", "normal_answer")
    g.add_edge("normal_answer", END)
    g.add_conditional_edges("decompose", fan_out, ["retrieve_branch"])
    g.add_edge("retrieve_branch", "merge_evidence")
    g.add_edge("merge_evidence", "final_answer")
    g.add_edge("final_answer", END)
    return g.compile()
