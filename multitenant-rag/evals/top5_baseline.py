"""rag-model-eval-nvidia20b-top5-v1 — two-stage, durable, resumable.

  python top5_baseline.py <manifest> app   [n|all]     # Stage A: NVIDIA 20B generation
  python top5_baseline.py <manifest> judge [n|all]     # Stage B: NVIDIA 120B judging

Stage A persists the generated answer BEFORE any judge call, so a judge stall never
forces regeneration. Stage B reads persisted app records only.

Groq calls: 0 (guarded). Global-search cases never touch the 20B model.
Retrieval/prompt/context policy: production, unchanged (top-5 cap already deployed).
"""
import json, os, re, sys, time, hashlib, subprocess, warnings; warnings.filterwarnings("ignore")

os.environ.setdefault("NVIDIA_MAX_ATTEMPTS", "3")

import nvidia_harness as H
import nvidia_provider as nv
import app as prod

MANIFEST = sys.argv[1]
STAGE = sys.argv[2]
LIMIT = sys.argv[3] if len(sys.argv) > 3 else "all"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, "output")
APP_CKPT = os.path.join(OUTD, "top5_app.jsonl")
JUDGE_CKPT = os.path.join(OUTD, "top5_judge.jsonl")
APP_TIMEOUT = int(os.environ.get("APP_TIMEOUT", "120"))
JUDGE_TIMEOUT = int(os.environ.get("JUDGE_TIMEOUT", "150"))
LOGICAL = "rag-model-eval-nvidia20b-top5-v1"

H.install_groq_guard()
H.load_seed_map(MANIFEST)
CORPUS = {c["case_id"]: c for c in json.load(open(os.path.join(HERE, "corpus60.json")))["cases"]}

# ---- fingerprint: NEW, includes the top-5 context policy ----
_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
FP = {"experiment": LOGICAL, "repo_head": _sha, "deployed_ask_sha": "d5af30e",
      "dataset_id": "d426fe19-3757-4442-b893-99a6cf031b68", "dataset_version": "baseline-v1",
      "application_provider": "nvidia", "application_model": H.APP_MODEL,
      "judge_provider": "nvidia", "judge_model": H.JUDGE_MODEL,
      "embedding_model": "titan-text-v2", "sparse_model": "fastembed-bm25",
      "retrieval": "hybrid-rrf", "top_k": prod.TOP_K,
      "retrieval_floor": prod.RETRIEVAL_FLOOR,
      "max_llm_context_chunks": prod.MAX_LLM_CONTEXT_CHUNKS,
      "chunk_max_tokens": 500, "chunk_overlap_tokens": 50}
FP["fingerprint_hash"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]

REFUSAL_PATTERNS = [
    "hasn't written about", "has not written about",
    "no one in this selection has written about",
    "hasn't published any posts yet",
    "not enough information", "no relevant information",
    "i don't have enough", "cannot answer",
]


def is_refusal(text: str) -> bool:
    t = (text or "").lower()
    return any(p in t for p in REFUSAL_PATTERNS)


def load(path):
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("fingerprint_hash") == FP["fingerprint_hash"] and r.get("case_id"):
                out[r["case_id"]] = r
    return out


def append(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush(); os.fsync(f.fileno())


def meta(p, key, default=""):
    return (getattr(p, "payload", {}) or {}).get(key, default)


def signals(candidates, ctx):
    """Runtime instrumentation only — no ranking change, no extra LLM calls."""
    def uniq(seq, k):
        return len({meta(x, k) for x in seq})
    scores = [round(getattr(x, "score", 0.0), 5) for x in candidates]
    posts5 = [meta(x, "post_id") for x in ctx]
    counts = {p: posts5.count(p) for p in set(posts5)}
    s = {
        "retrieval_candidate_count": len(candidates),
        "llm_context_chunk_count": len(ctx),
        "llm_context_estimated_tokens": prod._context_est_tokens(ctx),
        "distinct_posts_top5": uniq(ctx, "post_id"),
        "distinct_tenants_top5": uniq(ctx, "tenant_id"),
        "distinct_posts_candidates": uniq(candidates, "post_id"),
        "distinct_tenants_candidates": uniq(candidates, "tenant_id"),
        "score_top1": scores[0] if scores else None,
        "score_top5": scores[4] if len(scores) >= 5 else None,
        "score_top10": scores[9] if len(scores) >= 10 else None,
        "max_chunks_from_one_post_top5": max(counts.values()) if counts else 0,
        "only_one_source_post_top5": (uniq(ctx, "post_id") == 1) if ctx else False,
        "candidate_scores": scores,
    }
    s["gap_top1_top5"] = (round(s["score_top1"] - s["score_top5"], 5)
                          if s["score_top1"] is not None and s["score_top5"] is not None else None)
    s["gap_top5_top10"] = (round(s["score_top5"] - s["score_top10"], 5)
                           if s["score_top5"] is not None and s["score_top10"] is not None else None)
    # compact record of candidates 6-10 for the later top5-vs-6..10 diagnostic (no model exposure)
    s["candidates_6_10"] = [
        {"pos": i + 1, "post_id": meta(x, "post_id"), "title": meta(x, "title"),
         "tenant_id": meta(x, "tenant_id"), "score": round(getattr(x, "score", 0.0), 5),
         "snippet": (meta(x, "chunk_text") or "")[:700]}
        for i, x in enumerate(candidates) if i >= len(ctx)]
    return s


# =====================================================================
# STAGE A — application generation (NVIDIA 20B), persisted immediately
# =====================================================================
def stage_app():
    done = load(APP_CKPT)
    todo = [c for cid, c in sorted(CORPUS.items()) if cid not in done]
    if LIMIT != "all":
        todo = todo[:int(LIMIT)]
    print(f"[STAGE A] app records: {len(done)} done | running {len(todo)}", flush=True)
    for case in todo:
        cid, route, target, q = case["case_id"], case["route"], case["target"], case["question"]
        rec = {"case_id": cid, "fingerprint_hash": FP["fingerprint_hash"], "route": route,
               "target": target, "title": case["title"], "question": q,
               "expected_answer": case["expected_answer"], "stage": "app"}
        t0 = time.time()
        try:
            if route == "global":
                dense = prod._embed_dense(q)
                res = prod._get_qdrant_client().query_points(
                    collection_name=prod.COLLECTION_NAME, query=dense, using="dense",
                    limit=10, with_payload=True)
                best = {}
                for p in res.points:
                    pid = meta(p, "post_id")
                    if not pid: continue
                    sc = round(p.score, 4)
                    if pid not in best or sc > best[pid]["score"]:
                        best[pid] = {"title": meta(p, "title"),
                                     "writer": prod._tenant_name(meta(p, "tenant_id")), "score": sc}
                ranked = sorted(best.values(), key=lambda c: c["score"], reverse=True)
                rec.update({"llm_used": False, "application_model": "retrieval-only (LLM-free)",
                            "status": "completed", "results_top": ranked[:10],
                            "citations": [f'{r["title"]} — {r["writer"]} ({r["score"]})' for r in ranked],
                            "retrieved_contexts": [], "generated_answer": None,
                            "retrieval_latency_ms": round((time.time()-t0)*1000, 1),
                            "signals": {"retrieval_candidate_count": len(res.points),
                                        "distinct_posts_candidates": len(best)}})
                append(APP_CKPT, rec); done[cid] = rec
                print(f"  {cid} [global] LLM-free, {len(ranked)} ranked posts", flush=True)
                continue

            # ---- generative routes: production retrieval + production top-5 cap ----
            if route == "multi":
                tids = [H._NAME2TID.get(n.strip()) for n in (target or "").split(",")]
                cands, top = prod._hybrid_search_multi(q, [t for t in tids if t])
            elif route == "group":
                from common import groups as groupstore
                gid = H._GRP2ID.get((target or "").strip())
                cands, top = prod._hybrid_search_multi(q, groupstore.member_tenant_ids(gid) if gid else [])
            else:
                tid = H._NAME2TID.get((target or "").strip())
                tenant = prod._get_tenant(tid)
                cands, top = prod._hybrid_search(q, tid)
            ctx = prod._llm_context(cands)
            assert len(ctx) <= prod.MAX_LLM_CONTEXT_CHUNKS
            rec["retrieval_latency_ms"] = round((time.time()-t0)*1000, 1)
            rec["top_dense"] = round(top, 4)
            rec["signals"] = signals(cands, ctx)
            rec["retrieved_contexts"] = H._contexts_from(ctx)
            rec["llm_used"] = True

            below = (not cands) or top < prod.RETRIEVAL_FLOOR
            if route == "single":
                if below:
                    titles = prod._tenant_post_titles(tid)
                    if not titles:
                        rec.update({"status": "completed", "application_model": "no-LLM (empty corpus)",
                                    "generated_answer": f"{tenant['display_name']} hasn't published any posts yet.",
                                    "citations": [], "llm_used": False})
                        append(APP_CKPT, rec); done[cid] = rec; continue
                    system = prod._build_profile_prompt(tenant, titles)
                    model = prod.GROQ_MODEL_SMALL  # name only; NVIDIA model used below
                    cites = []
                    rec["path"] = "overview_decline (title-only prompt, 0 chunks)"
                else:
                    system = prod._build_system_prompt(tenant, ctx); cites = prod._dedupe_citations(ctx)
                    rec["path"] = "answer"
            else:
                if below:
                    rec.update({"status": "completed", "application_model": "no-LLM (below floor)",
                                "generated_answer": "No one in this selection has written about that yet.",
                                "citations": [], "llm_used": False})
                    append(APP_CKPT, rec); done[cid] = rec
                    print(f"  {cid} [{route}] below floor -> no LLM call", flush=True)
                    continue
                system = prod._build_group_system_prompt(ctx)
                cites = prod._dedupe_citations_attributed(ctx)
                rec["path"] = "answer"

            rec["citations"] = [c.get("title") if isinstance(c, dict) else str(c) for c in cites]
            rec["estimated_total_prompt_tokens"] = int((len(system) + len(q)) / 4)
            rec["application_model"] = H.APP_MODEL
            try:
                r = nv.chat(H.APP_MODEL, [{"role": "system", "content": system},
                                          {"role": "user", "content": q}],
                            max_tokens=H.APP_MAX_TOKENS, timeout=APP_TIMEOUT)
                ans = (r["content"] or "").strip()
                rec.update({"status": "completed" if ans else "generation_error",
                            "generated_answer": ans or H.FALLBACK_TEXT,
                            "app_input_tokens": r["input_tokens"], "app_output_tokens": r["output_tokens"],
                            "app_generation_latency_ms": r["latency_ms"], "app_retry_count": r["retry_count"],
                            "app_rate_limited": r["rate_limited"], "finish_reason": r["finish_reason"]})
                rec["signals"]["is_refusal"] = is_refusal(ans)
            except nv.CircuitOpen as e:
                print(f"  !! CIRCUIT OPEN: {e} — stopping Stage A safely", flush=True)
                rec.update({"status": "application_rate_limit", "error_type": "CircuitOpen",
                            "generated_answer": H.FALLBACK_TEXT})
                append(APP_CKPT, rec); break
            except Exception as e:
                kind = type(e).__name__
                rec.update({"status": "application_rate_limit" if "429" in str(e) else "application_provider_error",
                            "error_type": kind, "error": str(e)[:150],
                            "generated_answer": H.FALLBACK_TEXT})
            append(APP_CKPT, rec); done[cid] = rec
            print(f"  {cid} [{route}] {rec['status']} lat={rec.get('app_generation_latency_ms')}ms "
                  f"in/out={rec.get('app_input_tokens')}/{rec.get('app_output_tokens')} "
                  f"ctx={rec['signals']['llm_context_chunk_count']} "
                  f"posts5={rec['signals']['distinct_posts_top5']} refusal={rec['signals'].get('is_refusal')}",
                  flush=True)
        except nv.CircuitOpen:
            break
        except Exception as e:
            rec.update({"status": "retrieval_error", "error_type": type(e).__name__, "error": str(e)[:150]})
            append(APP_CKPT, rec)
            print(f"  {cid} RETRIEVAL_ERROR {type(e).__name__}", flush=True)
    print(f"[STAGE A] complete. nvidia stats: {json.dumps(nv.STATS)}", flush=True)


# =====================================================================
# STAGE B — judging (NVIDIA 120B) from persisted app records only
# =====================================================================
def stage_judge():
    apps = load(APP_CKPT); judged = load(JUDGE_CKPT)
    eligible = [r for cid, r in sorted(apps.items())
                if r.get("llm_used") and r.get("status") == "completed" and cid not in judged]
    if LIMIT != "all":
        eligible = eligible[:int(LIMIT)]
    print(f"[STAGE B] app records: {len(apps)} | judged: {len(judged)} | to judge now: {len(eligible)}", flush=True)
    for a in eligible:
        cid = a["case_id"]
        res = {"generated_answer": a["generated_answer"], "retrieved_contexts": a["retrieved_contexts"],
               "status": "completed", "llm_used": True}
        try:
            _orig = nv.chat
            def chat_t(model, msgs, **k):
                k.setdefault("timeout", JUDGE_TIMEOUT); return _orig(model, msgs, **k)
            nv.chat = chat_t
            j = H.judge(res, a["question"], a["expected_answer"])
            nv.chat = _orig
        except nv.CircuitOpen as e:
            nv.chat = _orig
            print(f"  !! CIRCUIT OPEN: {e} — stopping Stage B safely (app results preserved)", flush=True)
            break
        rec = {"case_id": cid, "fingerprint_hash": FP["fingerprint_hash"], "stage": "judge",
               "judge_model": H.JUDGE_MODEL, "judge_status": j.get("status")}
        if j.get("status") == "scored":
            for dim, v in j["scores"].items():
                rec[f"{dim}_score"] = v["score"]; rec[f"{dim}_reason"] = v["reason"]
            for k in ("judge_input_tokens", "judge_output_tokens", "judge_latency_ms",
                      "judge_retry_count", "judge_rate_limited"):
                rec[k] = j.get(k)
        else:
            rec["judge_reason"] = j.get("reason")
        append(JUDGE_CKPT, rec); judged[cid] = rec
        print(f"  {cid} judge={rec['judge_status']} corr={rec.get('correctness_score')} "
              f"comp={rec.get('completeness_score')} grnd={rec.get('groundedness_score')} "
              f"lat={rec.get('judge_latency_ms')}ms", flush=True)
    print(f"[STAGE B] complete. nvidia stats: {json.dumps(nv.STATS)}", flush=True)


print(f"=== {LOGICAL} | stage={STAGE} ===")
print(f"  application: nvidia {H.APP_MODEL} | judge: nvidia {H.JUDGE_MODEL} | groq_calls_expected=0")
print(f"  MAX_LLM_CONTEXT_CHUNKS={prod.MAX_LLM_CONTEXT_CHUNKS} TOP_K={prod.TOP_K} floor={prod.RETRIEVAL_FLOOR}")
print(f"  fingerprint={FP['fingerprint_hash']} repo_head={FP['repo_head'][:7]} deployed_ask={FP['deployed_ask_sha']}")
json.dump(FP, open(os.path.join(OUTD, "top5_fingerprint.json"), "w"), indent=2) if os.path.isdir(OUTD) else None
if STAGE == "app":
    stage_app()
elif STAGE == "judge":
    stage_judge()
else:
    print("unknown stage"); sys.exit(2)
