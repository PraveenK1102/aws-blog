"""Build NVIDIA-20B-TOP5-QUALITY-BASELINE.md + 4 safe exports. Read-only, no LLM calls."""
import csv, json, os, re, statistics as st, sys, collections, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, "output")
REPORT = sys.argv[1]
DEST = sys.argv[2]                    # where the 4 export files go
FP = json.load(open(os.path.join(OUTD, "top5_fingerprint.json")))

def load(p):
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            try: r = json.loads(line)
            except Exception: continue
            if r.get("fingerprint_hash") == FP["fingerprint_hash"]: out[r["case_id"]] = r
    return out

apps = load(os.path.join(OUTD, "top5_app.jsonl"))
judges = load(os.path.join(OUTD, "top5_judge.jsonl"))
corpus = {c["case_id"]: c for c in json.load(open(os.path.join(HERE, "corpus60.json")))["cases"]}

def scenario(c):
    """Category from EXISTING suite titles/routes (not invented post-hoc)."""
    t = (c.get("title") or "").lower()
    if c["route"] == "global": return "global retrieval"
    for pat, name in [
        (r"nonexistent|fake identifier|premise trap|negative|out-of-scope|scope isolation", "negative / scope isolation"),
        (r"compound", "compound multi-part"),
        (r"conflict|disagree|disambigu", "conflicting / disambiguation"),
        (r"temporal|current|old vs|update|correction|status", "temporal / latest-state"),
        (r"cross-user|cross-doc|timeline|lifecycle|evolution", "cross-document synthesis"),
        (r"exact|identifier|numbers|definition", "exact fact / identifier"),
    ]:
        if re.search(pat, t): return name
    return {"single": "single-profile factual", "multi": "multi-profile",
            "group": "group / cross-tenant"}.get(c["route"], "other")

TOKEN_RE = re.compile(r"\b(?:[A-Z]{1,4}-?\d{1,4}[A-Za-z]?|[A-Z][a-z]+(?:\s[A-Z][a-z]+)?|\d{1,4}(?:\.\d+)?)\b")
STOP = {"The","A","An","No","Yes","It","He","She","They","Not","Only","For","In","On","At","Do","Use"}

def distinctive(expected):
    toks = {t.strip() for t in TOKEN_RE.findall(expected or "")}
    return {t for t in toks if t and t not in STOP and len(t) > 2}

def diag_6_10(rec, expected):
    """Deterministic: is expected evidence absent from top5 but present in candidates 6-10?"""
    sig = rec.get("signals") or {}
    c610 = sig.get("candidates_6_10") or []
    if not c610: return "N/A (no candidates beyond top5)"
    top5 = " ".join(rec.get("retrieved_contexts") or []).lower()
    rest = " ".join((x.get("snippet") or "") + " " + (x.get("title") or "") for x in c610).lower()
    toks = distinctive(expected)
    if not toks: return "UNCLEAR (no distinctive tokens in reference)"
    missing = [t for t in toks if t.lower() not in top5]
    found_later = [t for t in missing if t.lower() in rest]
    if found_later: return f"YES — {len(found_later)}/{len(toks)} reference token(s) absent from top5 but present in 6–10 (e.g. {', '.join(sorted(found_later)[:4])})"
    if missing: return f"NO — {len(missing)} reference token(s) absent from top5 AND from 6–10 snippets"
    return "NO — all reference tokens already present in top5"

rows = []
for cid, c in sorted(corpus.items()):
    a = apps.get(cid); j = judges.get(cid) or {}
    sig = (a or {}).get("signals") or {}
    rows.append({
        "case_id": cid, "route": c["route"], "scenario": scenario(c), "target": c.get("target"),
        "title": c["title"], "question": c["question"], "expected_answer": c["expected_answer"],
        "generated_answer": (a or {}).get("generated_answer"),
        "retrieved_contexts": (a or {}).get("retrieved_contexts") or [],
        "citations": (a or {}).get("citations") or [],
        "llm_used": (a or {}).get("llm_used"), "app_status": (a or {}).get("status", "NOT_RUN"),
        "app_error_type": (a or {}).get("error_type"),
        "application_model": (a or {}).get("application_model"),
        "judge_status": j.get("judge_status"),
        "answer_correctness": j.get("correctness_score"),
        "answer_correctness_reason": j.get("correctness_reason"),
        "answer_completeness": j.get("completeness_score"),
        "answer_completeness_reason": j.get("completeness_reason"),
        "answer_groundedness": j.get("groundedness_score"),
        "answer_groundedness_reason": j.get("groundedness_reason"),
        "app_input_tokens": (a or {}).get("app_input_tokens"),
        "app_output_tokens": (a or {}).get("app_output_tokens"),
        "app_generation_latency_ms": (a or {}).get("app_generation_latency_ms"),
        "retrieval_latency_ms": (a or {}).get("retrieval_latency_ms"),
        "judge_input_tokens": j.get("judge_input_tokens"), "judge_output_tokens": j.get("judge_output_tokens"),
        "judge_latency_ms": j.get("judge_latency_ms"),
        "top_dense": (a or {}).get("top_dense"),
        "request_success": (1 if (a or {}).get("status") == "completed" and
                            (a or {}).get("generated_answer") and
                            "[Error while generating response]" not in ((a or {}).get("generated_answer") or "")
                            else 0) if a else None,
        **{k: sig.get(k) for k in ("retrieval_candidate_count","llm_context_chunk_count",
            "llm_context_estimated_tokens","distinct_posts_top5","distinct_tenants_top5",
            "distinct_posts_candidates","distinct_tenants_candidates","score_top1","score_top5",
            "score_top10","gap_top1_top5","gap_top5_top10","max_chunks_from_one_post_top5",
            "only_one_source_post_top5","is_refusal")},
    })

gen = [r for r in rows if r["llm_used"]]
glob = [r for r in rows if r["route"] == "global"]
noLLM = [r for r in rows if r["llm_used"] is False and r["route"] != "global"]
scored = [r for r in gen if isinstance(r["answer_correctness"], float)]

def dist(key):
    v = [r[key] for r in scored if isinstance(r.get(key), float)]
    if not v: return None
    n = len(v)
    return {"N": n, "mean": round(st.mean(v), 3),
            "full": sum(1 for x in v if x == 1.0), "partial": sum(1 for x in v if x == 0.5),
            "fail": sum(1 for x in v if x == 0.0),
            "full_pct": round(sum(1 for x in v if x == 1.0)/n*100, 1),
            "partial_pct": round(sum(1 for x in v if x == 0.5)/n*100, 1),
            "fail_pct": round(sum(1 for x in v if x == 0.0)/n*100, 1)}

Q = {k: dist(f"answer_{k}") for k in ("correctness", "completeness", "groundedness")}
def lat(v):
    v = [x for x in v if isinstance(x, (int, float))]
    if not v: return None
    s = sorted(v)
    def p(q):
        k = (len(s)-1)*q/100; lo, hi = int(k), min(int(k)+1, len(s)-1)
        return round(s[lo]+(s[hi]-s[lo])*(k-lo), 1)
    return {"N": len(v), "mean": round(st.mean(v),1), "p50": p(50), "p95": p(95), "p99": p(99), "max": round(max(v),1)}

APP_LAT = lat([r["app_generation_latency_ms"] for r in gen])
JUDGE_LAT = lat([r["judge_latency_ms"] for r in rows])
def toks(a,b,src):
    i=[r[a] for r in src if isinstance(r.get(a),int)]; o=[r[b] for r in src if isinstance(r.get(b),int)]
    return {"N":len(i),"in_mean":round(st.mean(i),1) if i else None,"out_mean":round(st.mean(o),1) if o else None,
            "in_total":sum(i),"out_total":sum(o),"total":sum(i)+sum(o)}
APP_TOK = toks("app_input_tokens","app_output_tokens",gen)
JUDGE_TOK = toks("judge_input_tokens","judge_output_tokens",rows)

def group_by(fn):
    b = collections.defaultdict(list)
    for r in scored: b[fn(r)].append(r)
    return {k: {"n": len(v),
                "corr": round(st.mean([x["answer_correctness"] for x in v]),3),
                "comp": round(st.mean([x["answer_completeness"] for x in v]),3),
                "grnd": round(st.mean([x["answer_groundedness"] for x in v]),3),
                "refusals": sum(1 for x in v if x.get("is_refusal"))}
            for k, v in sorted(b.items())}
by_route = group_by(lambda r: r["route"]); by_scen = group_by(lambda r: r["scenario"])

failures = [r for r in scored if min(r["answer_correctness"], r["answer_completeness"], r["answer_groundedness"]) < 1.0]
def classify(r):
    tags = []
    if r.get("is_refusal"): tags.append("refusal despite retrieved evidence" if (r.get("top_dense") or 0) >= 0.15 else "refusal (retrieval below floor)")
    if r.get("only_one_source_post_top5") and r["route"] in ("multi","group"): tags.append("cross-document source-diversity problem (top5 = 1 post)")
    if r["answer_groundedness"] < 1.0: tags.append("unsupported/weakly-supported claim vs top5 context")
    if r["answer_completeness"] < 1.0 and r["answer_correctness"] == 1.0: tags.append("partial answer — missing expected detail")
    if r["answer_correctness"] < 1.0 and not r.get("is_refusal"): tags.append("factual mismatch vs reference")
    return tags or ["unclassified"]
for r in failures:
    r["_tags"] = classify(r)
    r["_diag"] = diag_6_10(apps.get(r["case_id"]) or {}, r["expected_answer"]) if r["route"] in ("multi","group") else "n/a (single route)"

# ---------- exports ----------
FULL = ["case_id","route","scenario","target","title","question","expected_answer","generated_answer",
        "retrieved_contexts","citations","request_success","answer_correctness","answer_correctness_reason",
        "answer_completeness","answer_completeness_reason","answer_groundedness","answer_groundedness_reason",
        "app_status","app_error_type","judge_status","application_model","llm_used",
        "app_input_tokens","app_output_tokens","app_generation_latency_ms","retrieval_latency_ms",
        "judge_input_tokens","judge_output_tokens","judge_latency_ms","top_dense"]
SIG = ["case_id","route","scenario","retrieval_candidate_count","llm_context_chunk_count",
       "llm_context_estimated_tokens","distinct_posts_top5","distinct_tenants_top5",
       "distinct_posts_candidates","distinct_tenants_candidates","score_top1","score_top5","score_top10",
       "gap_top1_top5","gap_top5_top10","max_chunks_from_one_post_top5","only_one_source_post_top5",
       "is_refusal","top_dense","answer_correctness","answer_completeness","answer_groundedness"]
MET = ["case_id","route","scenario","request_success","answer_correctness","answer_completeness",
       "answer_groundedness","app_generation_latency_ms","judge_latency_ms","app_input_tokens",
       "app_output_tokens","judge_input_tokens","judge_output_tokens","app_status","judge_status"]
def w(name, cols, flatten=False):
    with open(os.path.join(DEST, name), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); wr.writeheader()
        for r in rows:
            d = dict(r)
            if flatten:
                d["retrieved_contexts"] = " || ".join(r.get("retrieved_contexts") or [])
                d["citations"] = "; ".join(str(x) for x in (r.get("citations") or []))
            wr.writerow(d)
w("rag-model-eval-nvidia20b-top5-v1-full.csv", FULL, flatten=True)
w("rag-model-eval-nvidia20b-top5-v1-metrics.csv", MET)
w("rag-model-eval-nvidia20b-top5-v1-runtime-signals.csv", SIG)
with open(os.path.join(DEST, "rag-model-eval-nvidia20b-top5-v1-full.jsonl"), "w") as f:
    for r in rows: f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False)+"\n")

json.dump({"quality": Q, "by_route": by_route, "by_scenario": by_scen,
           "app_lat": APP_LAT, "judge_lat": JUDGE_LAT, "app_tok": APP_TOK, "judge_tok": JUDGE_TOK,
           "counts": {"total": len(rows), "generative": len(gen), "global": len(glob),
                      "no_llm_generative": len(noLLM), "scored": len(scored),
                      "failures": len(failures)},
           "failures": [{"case_id": r["case_id"], "route": r["route"], "scenario": r["scenario"],
                         "corr": r["answer_correctness"], "comp": r["answer_completeness"],
                         "grnd": r["answer_groundedness"], "tags": r["_tags"], "diag": r["_diag"],
                         "posts5": r["distinct_posts_top5"], "posts_cand": r["distinct_posts_candidates"],
                         "refusal": r["is_refusal"]} for r in failures]},
          open(os.path.join(OUTD, "top5_summary.json"), "w"), indent=2)
print(json.dumps({"counts": {"total": len(rows), "generative": len(gen), "global": len(glob),
                             "no_llm": len(noLLM), "scored": len(scored), "failures": len(failures)},
                  "quality": Q, "by_route": by_route}, indent=2))
print("\nby_scenario:", json.dumps(by_scen, indent=1))
print("\nfailures:", json.dumps([{ "case_id":r["case_id"],"corr":r["answer_correctness"],
    "comp":r["answer_completeness"],"grnd":r["answer_groundedness"],"tags":r["_tags"],"diag":r["_diag"]}
    for r in failures], indent=1)[:3000])
print("\napp_lat:", json.dumps(APP_LAT), "\njudge_lat:", json.dumps(JUDGE_LAT))
print("app_tok:", json.dumps(APP_TOK), "\njudge_tok:", json.dumps(JUDGE_TOK))
