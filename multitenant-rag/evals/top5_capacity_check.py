"""3-case NVIDIA GPT-OSS-20B realistic top-5 capacity check.

Application generation only — **NO judge call**, **NO Groq call** (actively guarded).
Reuses production retrieval, production prompt builders and the production
`_llm_context()` cap helper. No manual text truncation anywhere.

Bounded: explicit 120 s timeout per request, at most one retry
(NVIDIA_MAX_ATTEMPTS=2), concurrency 1, existing 6 s pacing.
"""
import json, os, sys, time, warnings; warnings.filterwarnings("ignore")

os.environ.setdefault("NVIDIA_MAX_ATTEMPTS", "2")      # 1 initial + 1 bounded retry

import nvidia_harness as H
import nvidia_provider as nv
import app as prod

MANIFEST, OUT = sys.argv[1], sys.argv[2]
TIMEOUT_S = int(os.environ.get("CAPACITY_TIMEOUT", "120"))

H.install_groq_guard()
H.load_seed_map(MANIFEST)
corpus = {c["case_id"]: c for c in json.load(open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus60.json")))["cases"]}

# Deterministic selection by ROUTE + TITLE only (never by prior score):
#   A single-profile factual, B multi/cross-document, C group compound/conflicting.
SELECTED = ["case-024", "case-004", "case-022"]

print("=== configuration ===")
print(f"  application_provider = nvidia   application_model = {H.APP_MODEL}")
print(f"  judge calls this task = 0 (capacity check only)")
print(f"  groq_calls_expected = 0")
print(f"  MAX_LLM_CONTEXT_CHUNKS = {prod.MAX_LLM_CONTEXT_CHUNKS}  TOP_K = {prod.TOP_K}  floor = {prod.RETRIEVAL_FLOOR}")
print(f"  timeout = {TIMEOUT_S}s  max_attempts = {nv.MAX_ATTEMPTS}  min_interval = {nv.MIN_INTERVAL}s\n")


def build(case):
    """Production retrieval + production prompt + production context cap."""
    route, target, q = case["route"], case["target"], case["question"]
    t0 = time.time()
    if route == "multi":
        tids = [H._NAME2TID.get(n.strip()) for n in (target or "").split(",")]
        results, top = prod._hybrid_search_multi(q, [t for t in tids if t])
        ctx = prod._llm_context(results)
        system = prod._build_group_system_prompt(ctx)
    elif route == "group":
        from common import groups as groupstore
        gid = H._GRP2ID.get((target or "").strip())
        results, top = prod._hybrid_search_multi(q, groupstore.member_tenant_ids(gid) if gid else [])
        ctx = prod._llm_context(results)
        system = prod._build_group_system_prompt(ctx)
    else:
        tid = H._NAME2TID.get((target or "").strip())
        tenant = prod._get_tenant(tid)
        results, top = prod._hybrid_search(q, tid)
        ctx = prod._llm_context(results)
        system = prod._build_system_prompt(tenant, ctx)
    return {
        "retrieval_latency_ms": round((time.time() - t0) * 1000, 1),
        "retrieval_candidate_count": len(results),
        "llm_context_chunk_count": len(ctx),
        "distinct_sources_in_context": len({(getattr(c, "payload", {}) or {}).get("post_id") for c in ctx}),
        "estimated_context_tokens": prod._context_est_tokens(ctx),
        "estimated_total_prompt_tokens": int((len(system) + len(q)) / 4),
        "top_dense": round(top, 4),
        "has_chat_history": False,          # no chat_id is passed in this harness
        "system": system, "question": q,
    }


rows = []
for cid in SELECTED:
    case = corpus[cid]
    b = build(case)
    print(f"--- {cid} [{case['route']}] {case['title'][:50]} ---")
    print(f"    candidates={b['retrieval_candidate_count']} -> llm_context={b['llm_context_chunk_count']} "
          f"(distinct posts={b['distinct_sources_in_context']}) "
          f"ctx_tok~{b['estimated_context_tokens']} total_prompt_tok~{b['estimated_total_prompt_tokens']} "
          f"top_dense={b['top_dense']} history={b['has_chat_history']}", flush=True)
    assert b["llm_context_chunk_count"] <= prod.MAX_LLM_CONTEXT_CHUNKS, "context cap not active!"

    rec = {"case_id": cid, "route": case["route"], "title": case["title"],
           **{k: v for k, v in b.items() if k not in ("system", "question")}}
    t0 = time.time()
    try:
        r = nv.chat(H.APP_MODEL,
                    [{"role": "system", "content": b["system"]},
                     {"role": "user", "content": b["question"]}],
                    max_tokens=H.APP_MAX_TOKENS, timeout=TIMEOUT_S)
        rec.update({"status": "ok", "http": 200, "timeout": False,
                    "actual_input_tokens": r["input_tokens"], "actual_output_tokens": r["output_tokens"],
                    "generation_latency_ms": r["latency_ms"], "retry_count": r["retry_count"],
                    "rate_limited_429": r["rate_limited"], "finish_reason": r["finish_reason"],
                    "valid_answer": bool((r["content"] or "").strip()),
                    "answer_sample": (r["content"] or "")[:300]})
        print(f"    -> OK {r['latency_ms']}ms in/out={r['input_tokens']}/{r['output_tokens']} "
              f"finish={r['finish_reason']} valid={rec['valid_answer']}", flush=True)
    except Exception as e:
        kind = type(e).__name__
        timed_out = "timeout" in str(e).lower() or kind in ("NvidiaError",) and "timeout" in str(e).lower()
        rec.update({"status": "failed", "error_type": kind, "error": str(e)[:160],
                    "timeout": timed_out, "rate_limited_429": "429" in str(e),
                    "generation_latency_ms": round((time.time() - t0) * 1000, 1),
                    "valid_answer": False})
        print(f"    -> FAILED {kind}: {str(e)[:120]} after {rec['generation_latency_ms']}ms", flush=True)
    rows.append(rec)
    json.dump({"selected": SELECTED, "cases": rows, "nvidia_stats": nv.STATS},
              open(OUT, "w"), indent=2, ensure_ascii=False)   # checkpoint after EVERY case

ok = [r for r in rows if r.get("valid_answer")]
print(f"\n=== VERDICT: {len(ok)}/3 succeeded -> "
      f"{'PASS' if len(ok) == 3 else 'FAIL'} ===")
print("nvidia stats:", json.dumps(nv.STATS))
