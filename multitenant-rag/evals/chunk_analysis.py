"""PHASE 2-8: read-only chunk analysis of the indexed Qdrant corpus.

ZERO LLM calls. ZERO embedding calls. ZERO mutations (scroll only).
Token estimate uses the SAME heuristic as the production chunker (chars/4);
a real tokenizer estimate is added only if one is ALREADY installed.
"""
import json, os, statistics as st, sys, collections, warnings; warnings.filterwarnings("ignore")
import boto3
from qdrant_client import QdrantClient

OUT = sys.argv[1]
MANIFEST = sys.argv[2]
CHUNK_MAX_TOKENS = 500          # from ingest_worker/chunker.py default
CHUNK_OVERLAP_TOKENS = 50

# optional: real tokenizer ONLY if already present (no new install)
_tok = None
_tok_name = None
try:
    import tiktoken
    _tok = tiktoken.get_encoding("cl100k_base"); _tok_name = "tiktoken/cl100k_base"
except Exception:
    try:
        from tokenizers import Tokenizer  # ships with fastembed
        _tok = None                        # no suitable pretrained vocab guaranteed offline
    except Exception:
        pass

qd = json.loads(boto3.client("secretsmanager", region_name="ap-south-1")
                .get_secret_value(SecretId="multitenant/qdrant")["SecretString"])
qc = QdrantClient(url=qd["url"], api_key=qd["api_key"])
seed_tenants = {t["tenant_id"] for t in json.load(open(MANIFEST))["tenants"]}

# ---- read-only scroll of the whole collection ----
points, offset, n = [], None, 0
while True:
    batch, offset = qc.scroll(collection_name="multitenant_chunks", limit=256,
                              offset=offset, with_payload=True, with_vectors=False)
    points.extend(batch); n += len(batch)
    if offset is None:
        break
print(f"scrolled {n} points (read-only, no mutation, no embeddings)")

def pct(v, q):
    if not v: return None
    s = sorted(v); k = (len(s)-1)*q/100; lo, hi = int(k), min(int(k)+1, len(s)-1)
    return round(s[lo] + (s[hi]-s[lo])*(k-lo), 1)

def stats(v):
    if not v: return None
    return {"N": len(v), "mean": round(st.mean(v), 1), "p50": pct(v, 50), "p75": pct(v, 75),
            "p90": pct(v, 90), "p95": pct(v, 95), "p99": pct(v, 99),
            "max": round(max(v), 1), "min": round(min(v), 1)}

rows = []
for p in points:
    pl = p.payload or {}
    txt = pl.get("chunk_text") or ""
    tid = pl.get("tenant_id", "")
    rows.append({
        "tenant_id": tid, "post_id": pl.get("post_id"), "title": pl.get("title"),
        "chars": len(txt), "words": len(txt.split()),
        "est_tokens_chunker": len(txt) / 4.0,                     # production heuristic
        "est_tokens_tokenizer": (len(_tok.encode(txt)) if _tok else None),
        "is_seed": tid in seed_tenants,
    })

seed = [r for r in rows if r["is_seed"]]
legacy = [r for r in rows if not r["is_seed"]]

def block(rs, label):
    ch = [r["chars"] for r in rs]; wd = [r["words"] for r in rs]
    tk = [r["est_tokens_chunker"] for r in rs]
    tkr = [r["est_tokens_tokenizer"] for r in rs if r["est_tokens_tokenizer"] is not None]
    d = {"label": label, "chunks": len(rs), "characters": stats(ch), "words": stats(wd),
         "est_tokens_chunker": stats(tk)}
    if tkr: d["est_tokens_tokenizer"] = stats(tkr)
    return d

res = {
    "token_estimation": {"production_heuristic": "chars/4 (ingest_worker/chunker.py)",
                         "tokenizer_used": _tok_name or "none installed — not added for this measurement"},
    "chunker_config": {"max_tokens": CHUNK_MAX_TOKENS, "overlap_tokens": CHUNK_OVERLAP_TOKENS,
                       "max_chars": CHUNK_MAX_TOKENS*4, "overlap_chars": CHUNK_OVERLAP_TOKENS*4},
    "corpus": {"total_points": len(rows), "seed_points": len(seed), "legacy_points": len(legacy)},
    "all": block(rows, "all points"),
    "seed": block(seed, "seed-20260822 (evaluation corpus)"),
    "legacy": block(legacy, "legacy/original") if legacy else None,
}

# ---- PHASE 4 buckets (seed corpus) ----
buckets = [(0,100),(101,200),(201,300),(301,400),(401,500),(501,10**9)]
tk = [r["est_tokens_chunker"] for r in seed]
res["token_buckets_seed"] = []
for lo, hi in buckets:
    c = sum(1 for t in tk if lo <= t <= hi)
    res["token_buckets_seed"].append({"bucket": f"{lo}-{hi if hi<10**9 else '∞'}",
                                      "count": c, "pct": round(c/len(tk)*100, 1) if tk else 0})

# ---- PHASE 5 near-limit ----
res["near_max_seed"] = {}
for thr in (400, 450, 475, 500):
    c = sum(1 for t in tk if t >= thr)
    res["near_max_seed"][f">={thr}"] = {"count": c, "pct": round(c/len(tk)*100, 1) if tk else 0}

# ---- PHASE 6 per-post + per-tenant ----
per_post = collections.Counter(r["post_id"] for r in seed)
res["chunks_per_post_seed"] = stats(list(per_post.values()))
res["posts_seed"] = len(per_post)
res["per_tenant_seed"] = {
    t: {"chunks": sum(1 for r in seed if r["tenant_id"] == t),
        "posts": len({r["post_id"] for r in seed if r["tenant_id"] == t}),
        "mean_tokens": round(st.mean([r["est_tokens_chunker"] for r in seed if r["tenant_id"] == t]), 1)}
    for t in sorted(seed_tenants)}

# ---- PHASE 7 THEORETICAL context budgets ----
s = res["seed"]["est_tokens_chunker"]
res["theoretical_context_tokens"] = [
    {"chunks": k, "mean_based": round(s["mean"]*k), "p50_based": round(s["p50"]*k),
     "p95_based": round(s["p95"]*k), "worst_case_max": round(s["max"]*k)}
    for k in (5, 10, 15)]

json.dump(res, open(OUT, "w"), indent=2)
print(json.dumps({k: res[k] for k in ("corpus","chunker_config","token_estimation")}, indent=2))
print("\nSEED chunk est_tokens:", json.dumps(res["seed"]["est_tokens_chunker"], indent=2))
print("\nbuckets:", json.dumps(res["token_buckets_seed"], indent=2))
print("near-max:", json.dumps(res["near_max_seed"], indent=2))
print("chunks/post:", json.dumps(res["chunks_per_post_seed"], indent=2), "over", res["posts_seed"], "posts")
print("theoretical:", json.dumps(res["theoretical_context_tokens"], indent=2))
