"""Shared fakes for the routed-RAG production tests.

No AWS, no Qdrant, no Groq, no network. Every provider call is substituted, so
the whole suite runs offline and deterministically.
"""
from rag import decomposition as D
from rag import generation as GEN
from rag import provider as PV
from rag import router as R
from rag.deps import RagDeps


class FakePoint:
    """Stands in for a Qdrant ScoredPoint (payload + score)."""

    def __init__(self, post_id, title, text, score=0.9, tenant_id="t1", header=""):
        self.payload = {"post_id": post_id, "title": title, "chunk_text": text,
                        "tenant_id": tenant_id, "header_path": header,
                        "user_id": f"u-{tenant_id}"}
        self.score = score


def points(n, prefix="p", tenant_id="t1", score=0.9):
    return [FakePoint(f"{prefix}{i}", f"Title {prefix}{i}", f"body {prefix}{i}",
                      score=score - i * 0.01, tenant_id=tenant_id) for i in range(n)]


def make_deps(**over) -> RagDeps:
    """Production-shaped deps whose retrieval/prompt/citation behaviour mirrors
    app.py's real functions closely enough to test the graph's contracts."""
    calls = over.pop("_calls", None)

    def _hs(q, tid, dense_vec=None):
        if calls is not None:
            calls.append(("hybrid_search", q, tid))
        return points(3), 0.8

    def _hsm(q, tids, dense_vec=None):
        if calls is not None:
            calls.append(("hybrid_search_multi", q, tuple(tids)))
        return points(3, tenant_id=(tids[0] if tids else "t1")), 0.8

    base = dict(
        hybrid_search=_hs,
        hybrid_search_multi=_hsm,
        embed_dense=lambda t: [0.01] * 1024,
        semcache_lookup=lambda tid, d: None,
        semcache_store=lambda *a, **k: None,
        # real app.py semantics: cap only, no reordering, no padding
        llm_context=lambda r: list(r)[:5],
        dedupe_citations=lambda r: _dedupe(r, attributed=False),
        dedupe_citations_attributed=lambda r: _dedupe(r, attributed=True),
        context_est_tokens=lambda r: int(sum(
            len((getattr(x, "payload", {}) or {}).get("chunk_text") or "")
            for x in r) / 4),
        build_system_prompt=lambda tenant, res: f"SINGLE:{tenant['display_name']}:{len(res)}",
        build_group_system_prompt=lambda res: f"GROUP:{len(res)}",
        get_tenant=lambda tid: {"tenant_id": tid, "display_name": "Dave",
                                "domain": "gardening"},
        retrieval_floor=0.15,
        max_llm_context_chunks=5,
    )
    base.update(over)
    return RagDeps(**base)


def _dedupe(results, attributed):
    best = {}
    for r in results:
        pid = r.payload.get("post_id")
        if not pid:
            continue
        score = round(r.score, 4)
        if pid not in best or score > best[pid]["score"]:
            row = {"post_id": pid, "title": r.payload.get("title", ""),
                   "score": score}
            if attributed:
                row.update({"writer": "W", "tenant_id": r.payload.get("tenant_id", ""),
                            "user_id": r.payload.get("user_id", "")})
            else:
                row["header_path"] = r.payload.get("header_path", "")
            best[pid] = row
    return sorted(best.values(), key=lambda c: c["score"], reverse=True)


# --------------------------------------------------------------- fake provider
ROUTER_COMPOUND = ('{"needs_decomposition": true, '
                   '"information_needs": ["need one", "need two"], '
                   '"reason_code": "multiple_independent_retrieval_needs"}')
ROUTER_SIMPLE = ('{"needs_decomposition": false, '
                 '"information_needs": ["one need"], '
                 '"reason_code": "single_retrieval_need"}')
DECOMP_TWO = '{"is_compound": true, "subquestions": ["sub one", "sub two"]}'
DECOMP_THREE = ('{"is_compound": true, '
                '"subquestions": ["sub one", "sub two", "sub three"]}')
DECOMP_UNUSABLE = '{"is_compound": false, "subquestions": []}'


class FakeProvider:
    """Records every logical call and returns scripted content per kind.

    Install with `install()`; the graph modules hold their own references to
    `provider.chat`, so all three must be patched.
    """

    def __init__(self, router=ROUTER_SIMPLE, decomposition=DECOMP_TWO,
                 generation="THE ANSWER", raises=None, finish_reason="stop"):
        self.scripted = {"router": router, "decomposition": decomposition,
                         "generation": generation}
        self.raises = raises or {}
        self.finish_reason = finish_reason
        self.calls = []
        self._saved = {}

    def chat(self, model, messages, *, budget, kind, max_tokens, ceiling_ms,
             temperature=0.0):
        # Exercise the REAL budget/deadline gates before answering.
        budget.spend_groq(kind)
        budget.timeout_for(kind, ceiling_ms)
        self.calls.append({"kind": kind, "model": model,
                           "messages": messages, "max_tokens": max_tokens})
        if kind in self.raises:
            raise self.raises[kind]
        content = self.scripted[kind]
        budget.record_tokens(11, 7)
        return {"content": content, "finish_reason": self.finish_reason,
                "input_tokens": 11, "output_tokens": 7, "latency_ms": 1.0}

    def kinds(self):
        return [c["kind"] for c in self.calls]

    def install(self):
        for mod in (PV, R, D, GEN):
            self._saved[mod] = getattr(mod, "chat")
            mod.chat = self.chat
        return self

    def restore(self):
        for mod, fn in self._saved.items():
            mod.chat = fn
        self._saved = {}

    def __enter__(self):
        return self.install()

    def __exit__(self, *a):
        self.restore()
        return False
