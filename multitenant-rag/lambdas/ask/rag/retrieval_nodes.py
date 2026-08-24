"""Retrieval nodes — simple path, compound fan-out, and per-branch retrieval.

Retrieval SEMANTICS are frozen and come from the existing production functions
via `RagDeps` (§11): one logical retrieval is
    1 Titan query embedding
  + 1 local BM25 query encoding
  + 1 Qdrant dense top-1 relevance probe   (absolute cosine, gates RETRIEVAL_FLOOR)
  + 1 Qdrant hybrid RRF query              (ranking + citations)
This module adds NO new retriever and changes no prefetch limit or TOP_K.

Bounded concurrency: Semaphore(2), the validated value. Parallel branches all
share one RequestBudget, which is why the budget is lock-guarded.
"""
import threading
import time

from common.logger import get_logger

from . import config
from .budget import BudgetExceeded, DeadlineExceeded
from .scope import Scope, ScopeError

log = get_logger("rag.retrieval")

# One gate per process (matches the validated Semaphore(2)); Lambda serves one
# request at a time per container, so this bounds a single request's branches.
_retrieval_gate = threading.Semaphore(config.MAX_BRANCH_CONCURRENCY)


def _retrieve(deps, question: str, scope: Scope, dense_vec=None):
    """The EXISTING production hybrid retrieval, under bounded concurrency.

    `dense_vec` reuses an already-computed Titan embedding — the existing
    production path does exactly this with the semantic-cache probe vector.
    Passing it means Bedrock is NOT called again for the same text.
    """
    with _retrieval_gate:
        if scope.kind == "single":
            return deps.hybrid_search(question, scope.single_tenant,
                                      dense_vec=dense_vec)
        return deps.hybrid_search_multi(question, list(scope.tenant_ids),
                                        dense_vec=dense_vec)


def _eligible(deps, cands, top_dense: float):
    """Relevance gate: the dense cosine probe, not the RRF fused score."""
    return list(cands) if (cands and top_dense >= deps.retrieval_floor) else []


# ------------------------------------------------------------- simple path
def normal_retrieve(state, deps, budget):
    """One logical retrieval for the whole question. Preserves current semantics."""
    t0 = time.monotonic()
    scope: Scope = state["scope"]

    # Reuse the semantic-cache probe embedding when it covers EXACTLY the text we
    # are about to retrieve on — the same optimisation the existing production
    # path performs. The probe embeds `question`; retrieval uses `retrieval_query`,
    # which differs when a short follow-up was folded in. Cache eligibility already
    # implies no history (so no folding), but the texts are compared rather than
    # assumed, because reusing a vector for different text would silently retrieve
    # against the wrong query.
    reuse = (state.get("query_dense") is not None
             and state.get("retrieval_query") == state.get("question"))
    dense_vec = state.get("query_dense") if reuse else None

    try:
        budget.spend_retrieval(embed=not reuse)
    except BudgetExceeded as e:
        return {"merged_context": [], "merged_context_map": [],
                "answer_path": "simple", "top_dense": 0.0,
                "retrieval_candidate_count": 0, "relevance_floor_passed": False,
                "branch_count": 0, "successful_branch_count": 0,
                "failed_branch_count": 1, "partial_branch_failure": False,
                "errors": [f"normal_retrieve:{e.resource}_exhausted"],
                "node_latencies": {
                    "normal_retrieve_ms": round((time.monotonic() - t0) * 1000, 1)}}
    try:
        cands, top = _retrieve(deps, state["retrieval_query"], scope, dense_vec)
    except Exception as e:
        log.error("normal retrieval failed", error_type=type(e).__name__)
        return {"merged_context": [], "merged_context_map": [],
                "answer_path": "simple", "top_dense": 0.0,
                "retrieval_candidate_count": 0, "relevance_floor_passed": False,
                "branch_count": 1, "successful_branch_count": 0,
                "failed_branch_count": 1, "partial_branch_failure": False,
                "errors": [f"normal_retrieve:{type(e).__name__}"],
                "node_latencies": {
                    "normal_retrieve_ms": round((time.monotonic() - t0) * 1000, 1)}}

    eligible = _eligible(deps, cands, top)
    ctx = deps.llm_context(eligible) if eligible else []
    return {"merged_context": ctx,
            "merged_context_map": [{"subquestion_index": None, "rank_in_branch": i}
                                   for i in range(len(ctx))],
            "answer_path": "simple",
            "top_dense": round(top, 4),
            "retrieval_candidate_count": len(cands),
            "relevance_floor_passed": bool(eligible),
            "branch_count": 1,
            "successful_branch_count": 1,
            "failed_branch_count": 0,
            "partial_branch_failure": False,
            "node_latencies": {
                "normal_retrieve_ms": round((time.monotonic() - t0) * 1000, 1)}}


# ----------------------------------------------------------- compound path
def fan_out_payloads(state) -> list[dict]:
    """Send payloads: one INDEPENDENT retrieval branch per subquestion.

    Each payload carries the EXACT parent scope (`Scope.for_branch()`); the
    receiving node rebuilds it and re-asserts parity, so a widened scope cannot
    survive the fan-out even if the state were tampered with.
    """
    scope: Scope = state["scope"]
    return [{"branch": i, "subquestion": sq,
             "scope_payload": scope.for_branch(),
             "parent_scope_payload": scope.for_branch()}
            for i, sq in enumerate(state["subquestions"])]


def retrieve_branch(payload: dict, deps, budget):
    """Runs once per Send. Retrieval only — no per-branch LLM call."""
    t0 = time.monotonic()
    branch = payload["branch"]
    try:
        scope = Scope.from_payload(payload["scope_payload"])
        parent = Scope.from_payload(payload["parent_scope_payload"])
        parent.assert_parity(scope)          # fail closed on any widening
    except ScopeError as e:
        log.error("branch scope rejected", branch=branch, error_type=type(e).__name__)
        return {"branch_results": [{
            "branch": branch, "subquestion": payload["subquestion"],
            "candidate_count": 0, "eligible": [], "evidence_missing": True,
            "error": "ScopeError",
            "latency_ms": round((time.monotonic() - t0) * 1000, 1)}],
            "errors": [f"branch{branch}:ScopeError"]}

    try:
        budget.spend_retrieval()
    except BudgetExceeded as e:
        return {"branch_results": [{
            "branch": branch, "subquestion": payload["subquestion"],
            "candidate_count": 0, "eligible": [], "evidence_missing": True,
            "error": "BudgetExceeded",
            "latency_ms": round((time.monotonic() - t0) * 1000, 1)}],
            "errors": [f"branch{branch}:{e.resource}_exhausted"]}

    # Do not START a branch that cannot finish inside the deadline.
    try:
        budget.timeout_for(f"branch{branch}", config.QDRANT_TIMEOUT_MS)
    except DeadlineExceeded:
        return {"branch_results": [{
            "branch": branch, "subquestion": payload["subquestion"],
            "candidate_count": 0, "eligible": [], "evidence_missing": True,
            "error": "DeadlineExceeded",
            "latency_ms": round((time.monotonic() - t0) * 1000, 1)}],
            "errors": [f"branch{branch}:DeadlineExceeded"]}

    try:
        cands, top = _retrieve(deps, payload["subquestion"], scope)
        eligible = _eligible(deps, cands, top)
        return {"branch_results": [{
            "branch": branch, "subquestion": payload["subquestion"],
            "candidate_count": len(cands), "top_dense": round(top, 4),
            "eligible": eligible, "evidence_missing": not eligible,
            "latency_ms": round((time.monotonic() - t0) * 1000, 1)}]}
    except Exception as e:
        log.error("branch retrieval failed", branch=branch,
                  error_type=type(e).__name__)
        return {"branch_results": [{
            "branch": branch, "subquestion": payload["subquestion"],
            "candidate_count": 0, "eligible": [], "evidence_missing": True,
            "error": type(e).__name__,
            "latency_ms": round((time.monotonic() - t0) * 1000, 1)}],
            "errors": [f"branch{branch}:{type(e).__name__}"]}
