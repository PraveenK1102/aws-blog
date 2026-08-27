"""Measure chunk density using the ACTUAL production chunker.

The chunker is IMPORTED from `lambdas/ingest_worker/chunker.py` — the algorithm
is never reimplemented here, so the measurement reflects real production
behaviour including header-aware splitting, paragraph/sentence fallback, the
chars/4 token estimate, the prepended header_path, and the SOFT (not hard) max.

Chunking only: no Titan call, no embedding, no Qdrant write.
"""
import argparse
import collections
import hashlib
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "multitenant-rag", "lambdas", "ingest_worker"))

from chunker import chunk_markdown          # noqa: E402  production module

MAX_TOKENS, OVERLAP_TOKENS = 500, 50        # exactly what ingest_worker uses


def pct(vals, p):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))]


def measure(corpus_dir):
    man = json.load(open(os.path.join(corpus_dir, "manifest.json"), encoding="utf-8"))
    posts = man["posts"]
    per_post, per_user, per_cat = [], collections.Counter(), collections.Counter()
    tok, chars, outliers = [], [], []

    for p in posts:
        body = open(os.path.join(corpus_dir, p["path"]), encoding="utf-8").read()
        chunks = chunk_markdown(body, max_tokens=MAX_TOKENS, overlap_tokens=OVERLAP_TOKENS)
        n = len(chunks)
        per_post.append({"post_id": p["post_id"], "user_id": p["user_id"],
                         "category": p["category"], "size_class": p["size_class"],
                         "words": p["actual_word_count"], "chunks": n})
        per_user[p["user_id"]] += n
        per_cat[p["category"]] += n
        for c in chunks:
            est = c.char_count // 4          # the chunker's own token estimate
            tok.append(est)
            chars.append(c.char_count)
            if est > MAX_TOKENS:
                outliers.append({"post_id": p["post_id"], "est_tokens": est,
                                 "chars": c.char_count,
                                 "header_path": c.header_path[:60]})

    counts = [x["chunks"] for x in per_post]
    dist = collections.Counter(min(c, 6) for c in counts)   # 6 == "6+"
    stats = {
        "chunker": {"module": "lambdas/ingest_worker/chunker.py",
                    "function": "chunk_markdown",
                    "max_tokens": MAX_TOKENS, "overlap_tokens": OVERLAP_TOKENS,
                    "reimplemented": False},
        "posts": len(posts), "total_chunks": sum(counts),
        "chunks_per_post": {"mean": round(st.mean(counts), 3),
                            "p50": st.median(counts), "p95": pct(counts, 95),
                            "min": min(counts), "max": max(counts)},
        "chunk_count_distribution": {f"{k}{'+' if k == 6 else ''}_chunk_posts": dist[k]
                                     for k in sorted(dist)},
        "chunk_tokens_estimated": {"min": min(tok), "mean": round(st.mean(tok), 1),
                                   "p50": st.median(tok), "p95": pct(tok, 95),
                                   "p99": pct(tok, 99), "max": max(tok)},
        "chunk_chars": {"min": min(chars), "mean": round(st.mean(chars), 1),
                        "p95": pct(chars, 95), "max": max(chars)},
        "soft_limit_outliers": {"count": len(outliers),
                                "pct_of_chunks": round(100 * len(outliers) / len(tok), 2),
                                "examples": outliers[:5]},
        "chunks_by_category": dict(per_cat),
        "chunks_by_user": {
            "min": min(per_user.values()), "mean": round(st.mean(per_user.values()), 1),
            "p50": st.median(per_user.values()), "p95": pct(list(per_user.values()), 95),
            "max": max(per_user.values()),
            "top5": sorted(per_user.items(), key=lambda kv: -kv[1])[:5]},
        "linear_projection_100_users": sum(counts) * 4,
        "linear_projection_note": "LINEAR PROJECTION ONLY — later cohorts have equivalent "
                                  "difficulty composition but different content; the real "
                                  "count is only knowable once they are generated.",
    }
    return stats, per_post


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(
        REPO, "rag-stress-corpus", "generated", "cohort-a"))
    a = ap.parse_args()
    stats, per_post = measure(a.corpus)
    with open(os.path.join(a.corpus, "chunk_stats.json"), "w", encoding="utf-8") as fh:
        json.dump({"stats": stats, "per_post": per_post}, fh, indent=2)
    s = stats
    print(f"  chunker: {s['chunker']['module']}::{s['chunker']['function']} "
          f"(max_tokens={MAX_TOKENS}, overlap={OVERLAP_TOKENS}) — imported, not reimplemented")
    print(f"  posts={s['posts']}  TOTAL CHUNKS={s['total_chunks']}")
    print(f"  chunks/post mean={s['chunks_per_post']['mean']} p50={s['chunks_per_post']['p50']} "
          f"p95={s['chunks_per_post']['p95']} max={s['chunks_per_post']['max']}")
    print(f"  distribution: {s['chunk_count_distribution']}")
    t = s["chunk_tokens_estimated"]
    print(f"  est tokens min={t['min']} mean={t['mean']} p50={t['p50']} p95={t['p95']} "
          f"p99={t['p99']} max={t['max']}")
    c = s["chunk_chars"]
    print(f"  chars min={c['min']} mean={c['mean']} p95={c['p95']} max={c['max']}")
    print(f"  soft-limit outliers (>{MAX_TOKENS} est tokens): {s['soft_limit_outliers']['count']} "
          f"({s['soft_limit_outliers']['pct_of_chunks']}%)")
    print(f"  by category: {s['chunks_by_category']}")
    u = s["chunks_by_user"]
    print(f"  by user min={u['min']} mean={u['mean']} p50={u['p50']} p95={u['p95']} max={u['max']}")
    print(f"  LINEAR PROJECTION (x4): {s['linear_projection_100_users']}")
    # §17: the 1,000-1,600 gate is RETIRED by architect decision — higher chunk
    # density is ACCEPTED as a legitimate property of header-rich prose. The
    # range below is INFORMATIONAL ONLY and never fails the run.
    INFO_LO, INFO_HI, tot = 1800, 2500, s["total_chunks"]
    where = ("within" if INFO_LO <= tot <= INFO_HI
             else "below" if tot < INFO_LO else "above")
    print(f"  INFORMATIONAL RANGE [{INFO_LO}-{INFO_HI}]: {tot} ({where}) "
          f"— informational only, NOT a gate")


if __name__ == "__main__":
    main()
