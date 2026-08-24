"""Coverage-aware fan-in merge — the validated deterministic merge.

THE PROPERTY THAT MATTERS
-------------------------
RRF fused scores are RANK numbers produced independently per branch search, so
the top hit of every branch scores ~1.0. Sorting the union by score would
therefore be meaningless and would let one branch crowd out the others. This
merge NEVER compares scores across branches. Instead:

  pass 1 — coverage: take the best not-yet-seen chunk from EACH branch, in
           branch order, so every sub-question is represented before any branch
           gets a second chunk.
  pass 2 — round-robin the next ranked chunk from each branch until the cap.

Dedupe key is (post_id, chunk_text[:80]) — the same chunk retrieved by two
branches is one piece of evidence, not two.

The returned list is capped at MAX_LLM_CONTEXT_CHUNKS and is THE SINGLE SOURCE
for both the prompt and the citations (see generation.py), which is what keeps
the LLM-visible == citation-eligible invariant true.
"""
import time

from .budget import LIMITS


def _key(chunk):
    p = getattr(chunk, "payload", {}) or {}
    return (p.get("post_id"), (p.get("chunk_text") or "")[:80])


def merge_evidence(state, deps, budget=None):
    """Fan-in node. Deterministic: same branch results -> same merged context."""
    t0 = time.monotonic()
    branches = sorted([b for b in (state.get("branch_results") or []) if "eligible" in b],
                      key=lambda b: b["branch"])
    pools = [list(b["eligible"]) for b in branches]

    # The context cap is BOTH a product decision (MAX_LLM_CONTEXT_CHUNKS) and an
    # architectural bound (final_context_chunks); honour the tighter of the two.
    cap = min(deps.max_llm_context_chunks, LIMITS["final_context_chunks"])

    seen, merged, cmap = set(), [], []

    # pass 1 — guarantee coverage: best available chunk from EACH branch
    for bi, pool in enumerate(pools):
        for c in pool:
            if _key(c) not in seen:
                seen.add(_key(c))
                merged.append(c)
                cmap.append({"subquestion_index": branches[bi]["branch"],
                             "rank_in_branch": pool.index(c)})
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
                if _key(c) not in seen:
                    seen.add(_key(c))
                    merged.append(c)
                    cmap.append({"subquestion_index": branches[bi]["branch"],
                                 "rank_in_branch": pool.index(c)})
                    break
        depth += 1

    ok = [b for b in branches if not b.get("evidence_missing")]
    failed = [b for b in branches if b.get("error")]
    return {"merged_context": merged[:cap],
            "merged_context_map": cmap[:cap],
            "answer_path": "compound",
            "branch_count": len(branches),
            "successful_branch_count": len(ok),
            "failed_branch_count": len(failed),
            # partial == some branches produced evidence and some did not
            "partial_branch_failure": bool(ok) and len(ok) < len(branches),
            "relevance_floor_passed": bool(merged),
            "retrieval_candidate_count": sum(b.get("candidate_count", 0) for b in branches),
            "top_dense": round(max((b.get("top_dense") or 0.0) for b in branches), 4)
                         if branches else 0.0,
            "node_latencies": {"merge_evidence_ms": round((time.monotonic() - t0) * 1000, 1)}}
