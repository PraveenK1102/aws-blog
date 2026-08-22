"""Build NVIDIA-20B-EVALUATION.md + safe exports from the checkpoint. Read-only."""
import csv, json, os, re, statistics as st, sys, collections, datetime

CKPT, CORPUS, PRODJSON, OUTDIR, REPORT = sys.argv[1:6]
recs = [json.loads(l) for l in open(CKPT, encoding="utf-8") if l.strip()]
by_case = {r["case_id"]: r for r in recs}                 # later record wins
cases = json.load(open(CORPUS))["cases"]
corpus = {c["case_id"]: c for c in cases}
prod = json.load(open(PRODJSON)) if os.path.exists(PRODJSON) else {}
os.makedirs(OUTDIR, exist_ok=True)
stages = [json.load(open(os.path.join(OUTDIR, f))) for f in sorted(os.listdir(OUTDIR))
          if f.startswith("nvidia20b_stage_")]
FP = stages[-1]["fingerprint"] if stages else {}

def pct(v, q):
    if not v: return None
    s = sorted(v); k = (len(s)-1)*q/100; lo, hi = int(k), min(int(k)+1, len(s)-1)
    return round(s[lo] + (s[hi]-s[lo])*(k-lo), 1)

def lat(v):
    v = [x for x in v if isinstance(x, (int, float))]
    if not v: return None
    return {"N": len(v), "mean": round(st.mean(v), 1), "p50": pct(v, 50),
            "p95": pct(v, 95), "p99": pct(v, 99), "max": round(max(v), 1)}

def scenario(case):
    """Category derived from the EXISTING suite title (not invented post-hoc)."""
    t = (case.get("title") or "").lower()
    if case["route"] == "global": return "global retrieval"
    for pat, name in [
        (r"nonexistent|fake identifier|premise trap|negative|out-of-scope|scope isolation|refus", "negative / scope isolation"),
        (r"temporal|current|old vs|update|correction|status", "temporal / latest-state"),
        (r"compound", "compound multi-part"),
        (r"conflict|disagree|disambigu", "conflicting / disambiguation"),
        (r"cross-user|cross-doc|timeline|lifecycle|evolution", "cross-document synthesis"),
        (r"exact|identifier|numbers|definition", "exact fact / identifier"),
    ]:
        if re.search(pat, t): return name
    return {"single": "single-profile factual", "multi": "multi-profile",
            "group": "group / cross-tenant"}.get(case["route"], "other")

rows = []
for cid, case in sorted(corpus.items()):
    r = by_case.get(cid)
    if not r:
        rows.append({"case_id": cid, "route": case["route"], "status": "NOT_RUN",
                     "question": case["question"], "expected_answer": case["expected_answer"],
                     "scenario": scenario(case)})
        continue
    s = r["result"]
    rows.append({
        "case_id": cid, "route": case["route"], "target": case.get("target"),
        "scenario": scenario(case), "question": case["question"],
        "expected_answer": case["expected_answer"],
        "generated_answer": s.get("generated_answer"),
        "retrieved_contexts": s.get("retrieved_contexts") or [],
        "citations": s.get("citations") or [],
        "request_success": s.get("request_success"),
        "answer_correctness": s.get("correctness_score"),
        "answer_correctness_reason": s.get("correctness_reason"),
        "answer_completeness": s.get("completeness_score"),
        "answer_completeness_reason": s.get("completeness_reason"),
        "answer_groundedness": s.get("groundedness_score"),
        "answer_groundedness_reason": s.get("groundedness_reason"),
        "retrieval_hit_at_k": "NOT_SUPPORTED", "citation_correctness": "NOT_SUPPORTED",
        "status": s.get("status"), "error_type": s.get("error_type"),
        "judge_status": s.get("judge_status"),
        "application_provider": "nvidia", "application_model": s.get("application_model"),
        "judge_provider": "nvidia", "judge_model": FP.get("judge_model"),
        "llm_used": s.get("llm_used"),
        "app_generation_latency_ms": s.get("app_generation_latency_ms"),
        "judge_latency_ms": s.get("judge_latency_ms"),
        "retrieval_latency_ms": s.get("retrieval_latency_ms"),
        "app_input_tokens": s.get("app_input_tokens"), "app_output_tokens": s.get("app_output_tokens"),
        "judge_input_tokens": s.get("judge_input_tokens"), "judge_output_tokens": s.get("judge_output_tokens"),
        "top_dense": s.get("top_dense"),
        "app_retry_count": s.get("app_retry_count"), "judge_retry_count": s.get("judge_retry_count"),
    })

ran = [r for r in rows if r.get("status") not in (None, "NOT_RUN")]
gen = [r for r in ran if r.get("llm_used")]
scored = [r for r in ran if isinstance(r.get("answer_correctness"), float)]

def dist(key):
    v = [r[key] for r in scored if isinstance(r.get(key), float)]
    if not v: return None
    n = len(v)
    return {"N": n, "mean": round(st.mean(v), 3),
            "full_pct": round(sum(1 for x in v if x == 1.0)/n*100, 1),
            "partial_pct": round(sum(1 for x in v if x == 0.5)/n*100, 1),
            "fail_pct": round(sum(1 for x in v if x == 0.0)/n*100, 1),
            "full": sum(1 for x in v if x == 1.0), "partial": sum(1 for x in v if x == 0.5),
            "fail": sum(1 for x in v if x == 0.0)}

Q = {"correctness": dist("answer_correctness"), "completeness": dist("answer_completeness"),
     "groundedness": dist("answer_groundedness")}
statuses = collections.Counter(r.get("status") for r in ran)
succ = [r["request_success"] for r in ran if r.get("request_success") is not None]

def group_stats(keyfn):
    out = {}
    for r in scored:
        out.setdefault(keyfn(r), []).append(r)
    res = {}
    for k, v in sorted(out.items()):
        res[k] = {"n": len(v),
                  "correctness": round(st.mean([x["answer_correctness"] for x in v]), 3),
                  "completeness": round(st.mean([x["answer_completeness"] for x in v]), 3),
                  "groundedness": round(st.mean([x["answer_groundedness"] for x in v]), 3)}
    return res

by_route = group_stats(lambda r: r["route"])
by_scen = group_stats(lambda r: r["scenario"])

failures = [r for r in scored if min(r["answer_correctness"], r["answer_completeness"],
                                     r["answer_groundedness"]) < 1.0]
provider_fail = [r for r in ran if r.get("status") != "completed"]

APP_LAT = lat([r.get("app_generation_latency_ms") for r in ran])
JUDGE_LAT = lat([r.get("judge_latency_ms") for r in ran])
RETR_LAT = lat([r.get("retrieval_latency_ms") for r in ran])
def toks(a, b):
    i = [r.get(a) for r in ran if isinstance(r.get(a), int)]
    o = [r.get(b) for r in ran if isinstance(r.get(b), int)]
    return {"N": len(i), "in_mean": round(st.mean(i), 1) if i else None,
            "out_mean": round(st.mean(o), 1) if o else None,
            "in_total": sum(i), "out_total": sum(o), "total": sum(i)+sum(o)}
APP_TOK = toks("app_input_tokens", "app_output_tokens")
JUDGE_TOK = toks("judge_input_tokens", "judge_output_tokens")
STATS = {}
for s in stages:
    for k, v in s.get("stats", {}).items():
        STATS[k] = round(STATS.get(k, 0) + v, 2)

# ---------------- exports ----------------
FULL = ["case_id","route","scenario","target","question","expected_answer","generated_answer",
        "retrieved_contexts","citations","request_success","answer_correctness",
        "answer_correctness_reason","answer_completeness","answer_completeness_reason",
        "answer_groundedness","answer_groundedness_reason","retrieval_hit_at_k",
        "citation_correctness","status","error_type","judge_status","application_provider",
        "application_model","judge_provider","judge_model","llm_used",
        "app_generation_latency_ms","judge_latency_ms","retrieval_latency_ms",
        "app_input_tokens","app_output_tokens","judge_input_tokens","judge_output_tokens",
        "top_dense","app_retry_count","judge_retry_count"]
with open(f"{OUTDIR}/rag-model-eval-nvidia20b-v1-full.csv","w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=FULL,extrasaction="ignore"); w.writeheader()
    for r in rows:
        d=dict(r); d["retrieved_contexts"]=" || ".join(r.get("retrieved_contexts") or [])
        d["citations"]="; ".join(str(x) for x in (r.get("citations") or [])); w.writerow(d)
with open(f"{OUTDIR}/rag-model-eval-nvidia20b-v1-full.jsonl","w") as f:
    for r in rows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
M=["case_id","route","scenario","request_success","answer_correctness","answer_completeness",
   "answer_groundedness","app_generation_latency_ms","judge_latency_ms","retrieval_latency_ms",
   "app_input_tokens","app_output_tokens","judge_input_tokens","judge_output_tokens",
   "top_dense","status","error_type"]
with open(f"{OUTDIR}/rag-model-eval-nvidia20b-v1-metrics.csv","w",newline="") as f:
    w=csv.DictWriter(f,fieldnames=M,extrasaction="ignore"); w.writeheader()
    for r in rows: w.writerow({k:r.get(k) for k in M})

# ---------------- report ----------------
L=[];A=L.append
def trow(n,s): return f"| {n} | {s['N']} | {s['mean']} | {s['p50']} | {s['p95']} | {s['p99']} | {s['max']} |" if s else f"| {n} | 0 | — | — | — | — | — |"
A("# NVIDIA GPT-OSS-20B — Offline Model Evaluation v1"); A("")
A(f"_Generated {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}. "
  "Measurement only — production unchanged._"); A("")
A("## 1. Experiment identity")
A(f"- **Logical experiment:** `rag-model-eval-nvidia20b-v1`")
A(f"- **LangSmith shards:** " + ", ".join(f"`{s['experiment']}`" for s in stages))
A(f"- **Dataset:** `multitenant-rag-eval-60-v1` (`{FP.get('dataset_id')}`), version `{FP.get('dataset_version')}`, 60 examples, PRIVATE, synthetic_public")
A(f"- **Git SHA:** `{FP.get('git_sha')}`")
A(f"- **Fingerprint:** `{FP.get('fingerprint_hash')}` — "
  + ", ".join(f"{k}={FP.get(k)}" for k in ("application_model","judge_model","top_k","retrieval_floor","retrieval","embedding_model","sparse_model")))
A("")
A("## 2. Models")
A("| Role | Provider | Model |"); A("|---|---|---|")
A(f"| Application under test | NVIDIA | `{FP.get('application_model')}` |")
A(f"| Judge | NVIDIA | `{FP.get('judge_model')}` |")
A("| Production (UNCHANGED) | Groq | `openai/gpt-oss-120b` |")
A(""); A("**Groq calls during this experiment: 0** (actively guarded — any Groq entry point raises).")
A("")
A("## 3. Routing")
A("| Route | Cases in dataset | Ran | LLM used |"); A("|---|---|---|---|")
mix=collections.Counter(c["route"] for c in cases)
for rt,n in sorted(mix.items()):
    ranr=[r for r in ran if r["route"]==rt]
    A(f"| {rt} | {n} | {len(ranr)} | {sum(1 for r in ranr if r.get('llm_used'))} |")
A(""); A(f"Global search stayed **LLM-free** by design — no NVIDIA generation call was made for it.")
A("")
A("## 4. Reliability")
A(f"- Cases in dataset: **{len(cases)}** · ran: **{len(ran)}** · not run: **{len(cases)-len(ran)}**")
A(f"- request_success: **{round(sum(succ)/len(succ)*100,1) if succ else 'n/a'}%** ({sum(succ)}/{len(succ)})")
A("")
A("| Status | Count |"); A("|---|---|")
for k,v in statuses.most_common(): A(f"| `{k}` | {v} |")
A("")
A(f"- Provider/application failures: **{len(provider_fail)}**")
A(f"- Judge failures (parse/provider/rate-limit): **{sum(1 for r in ran if r.get('judge_status'))}**")
A("")
A("> Provider failure is recorded as a **separate status**, never as answer_correctness = 0. "
  "Reliability and quality denominators are reported independently.")
A("")
for name,key in (("5. Answer correctness","correctness"),("6. Completeness","completeness"),("7. Groundedness","groundedness")):
    A(f"## {name}")
    d=Q[key]
    if not d: A("_no scored cases_"); A(""); continue
    A(f"- **Mean: {d['mean']}** over N={d['N']} scored generative cases")
    A(f"- full (1.0): **{d['full']} ({d['full_pct']}%)** · partial (0.5): **{d['partial']} ({d['partial_pct']}%)** · fail (0.0): **{d['fail']} ({d['fail_pct']}%)**")
    A("")
A("## 8. Retrieval metrics")
A("`retrieval_hit_at_k` = **NOT_SUPPORTED** · `citation_correctness` = **NOT_SUPPORTED** — the 60-case "
  "corpus carries no deterministic expected source/post identifiers (verified: example metadata has "
  "only case_id/route/target/title/source/prior_score). No source truth was fabricated.")
A("")
A("## 9. Category analysis")
A("_Categories derived from the EXISTING suite titles/routes, not invented after seeing results._")
A(""); A("### By route"); A("| Route | N | Correctness | Completeness | Groundedness |"); A("|---|---|---|---|---|")
for k,v in by_route.items(): A(f"| {k} | {v['n']} | {v['correctness']} | {v['completeness']} | {v['groundedness']} |")
A(""); A("### By scenario"); A("| Scenario | N | Correctness | Completeness | Groundedness |"); A("|---|---|---|---|---|")
for k,v in by_scen.items(): A(f"| {k} | {v['n']} | {v['correctness']} | {v['completeness']} | {v['groundedness']} |")
A("")
A("## 10. Failure analysis")
if not failures: A("No scored case fell below 1.0 on any dimension.")
else:
    A("| Case | Route | Scenario | Corr | Comp | Grnd | Diagnostic |"); A("|---|---|---|---|---|---|---|")
    for r in failures:
        why=[]
        if r["answer_groundedness"]<1.0: why.append("unsupported claim vs retrieved context")
        if r["answer_completeness"]<1.0: why.append("missing expected detail")
        if r["answer_correctness"]<1.0: why.append("factual mismatch vs reference")
        if (r.get("top_dense") or 1)<0.15: why.append("retrieval below floor")
        A(f"| {r['case_id']} | {r['route']} | {r['scenario']} | {r['answer_correctness']} | "
          f"{r['answer_completeness']} | {r['answer_groundedness']} | {'; '.join(why)} |")
A("")
if provider_fail:
    A("Provider/infrastructure failures (NOT quality failures): " +
      ", ".join(f"{r['case_id']} ({r.get('status')})" for r in provider_fail)); A("")
A("## 11. NVIDIA 20B application latency (ms)")
A("| Metric | N | Mean | P50 | P95 | P99 | Max |"); A("|---|---|---|---|---|---|---|")
A(trow("nvidia20b_generation_latency_ms", APP_LAT)); A(trow("evaluation retrieval_latency_ms", RETR_LAT)); A("")
A("## 12. NVIDIA 120B judge latency (ms) — offline evaluation overhead")
A("| Metric | N | Mean | P50 | P95 | P99 | Max |"); A("|---|---|---|---|---|---|---|")
A(trow("nvidia120b_judge_latency_ms", JUDGE_LAT)); A("")
A("> Judge latency is **evaluation overhead**, never user-facing RAG latency. Deliberate 6 s pacing "
  f"is excluded from all model latencies (total pacing sleep: {STATS.get('pacing_wait_seconds')}s).")
A("")
A("## 13. Tokens — application vs judge (never combined)")
A("| Source | N | Mean in | Mean out | Total in | Total out | Grand total |"); A("|---|---|---|---|---|---|---|")
A(f"| Application NVIDIA `{FP.get('application_model')}` | {APP_TOK['N']} | {APP_TOK['in_mean']} | {APP_TOK['out_mean']} | {APP_TOK['in_total']} | {APP_TOK['out_total']} | {APP_TOK['total']} |")
A(f"| Judge NVIDIA `{FP.get('judge_model')}` | {JUDGE_TOK['N']} | {JUDGE_TOK['in_mean']} | {JUDGE_TOK['out_mean']} | {JUDGE_TOK['in_total']} | {JUDGE_TOK['out_total']} | {JUDGE_TOK['total']} |")
A("")
A("## 14. Rate-limit / free-tier observations")
A("| Metric | Value |"); A("|---|---|")
for k in ("requests","successes","http_429","http_5xx","timeouts","retries","circuit_breaker_events"):
    A(f"| {k} | {STATS.get(k,0)} |")
A("")
A("- Client-side control only: concurrency 1, 6 s minimum interval, bounded backoff (30/60/120 s + jitter), "
  "3-consecutive-429 circuit breaker. NVIDIA exposes no usable rate-limit headers.")
A("- **No paid upgrade/action was performed.** This does not establish that the service is permanently free.")
A("")
A("## 15. Comparison with existing production observations")
if prod:
    e2e=prod.get("end_to_end",{}); comp=prod.get("components",{})
    A("**PRODUCTION OPERATIONAL DATA** (Groq gpt-oss-120b in Lambda), for context only:")
    A("")
    A("| Flow | N | Mean | P50 | P95 | P99 | Max |"); A("|---|---|---|---|---|---|---|")
    for k,v in e2e.items(): A(trow(k,v))
    A(""); A(f"- Production `groq_generation` mean: **{(comp.get('groq_generation') or {}).get('mean')} ms**")
    A(f"- This experiment's NVIDIA-20B generation mean: **{APP_LAT['mean'] if APP_LAT else 'n/a'} ms**")
A("")
A("> ⚠️ **This is NOT a controlled model-size latency comparison.** Provider (Groq vs NVIDIA) AND "
  "execution environment (Lambda production vs local offline harness) both differ, and the production "
  "figures are additionally inflated by earlier Groq 429 retry waits. A valid model-size comparison "
  "must run NVIDIA 20B vs NVIDIA 120B under the same provider and harness. Quality comparison is also "
  "not strictly like-for-like: the paused Groq baseline never completed, so no Groq quality numbers exist.")
A("")
A("## 16. Limitations")
for x in ("Synthetic 60-case corpus (fictional seed) — absolute scores do not transfer to real users.",
          "Small sample: 60 cases, 52 generative; per-scenario cells are single-digit N.",
          "**Judge is GPT-OSS-120B and the application is GPT-OSS-20B — same model family.** Using the larger model avoids direct self-judging but does NOT eliminate model-family bias.",
          "Single judge, temperature 0, no human adjudication and no multi-judge panel.",
          "Free NVIDIA endpoint: quota, SLA and entitlement are unknown and unobservable (no rate-limit headers). Success here proves API compatibility and evaluation viability, NOT production throughput/SLA/availability.",
          "Production provider differs (Groq) — this experiment does not measure production behaviour.",
          "Retrieval source-truth metrics unsupported (no deterministic expected post ids).",
          "Semantic cache bypassed, so this measures the full RAG path rather than the cache-hit path.",
          "RAGAS and DeepEval remain DEFERRED (not installed); their metrics are not reported.",
          "Group scope is membership-based (union of member tenants' posts), not per-post group tag."):
    A(f"- {x}")
A("")
A("## 17. Conclusion — how well did NVIDIA GPT-OSS-20B perform?")
c_,p_,g_=Q["correctness"],Q["completeness"],Q["groundedness"]
if c_:
    A(f"On {c_['N']} scored generative cases: correctness **{c_['mean']}** ({c_['full_pct']}% fully correct), "
      f"completeness **{p_['mean']}**, groundedness **{g_['mean']}**. "
      f"request_success **{round(sum(succ)/len(succ)*100,1)}%**.")
    A("")
    weak=sorted(by_scen.items(), key=lambda kv: kv[1]["correctness"])[:3]
    A("Weakest scenarios by correctness: " + ", ".join(f"`{k}` {v['correctness']} (n={v['n']})" for k,v in weak) + ".")
A(""); A("**Production was not changed.** This is evidence for the architect's decision, not a recommendation.")
A("")
A("## 18. Recommended next experiment (NOT executed)")
A("- `NVIDIA GPT-OSS-20B vs NVIDIA GPT-OSS-120B` on the hardest 10–15 cases — same provider, retrieval, "
  "prompt, contexts and harness, so model size is the only variable.")
open(REPORT,"w").write("\n".join(L))
print("wrote", REPORT)
print(json.dumps({"ran":len(ran),"scored":len(scored),"quality":Q,"by_route":by_route,
                  "statuses":dict(statuses),"app_lat":APP_LAT,"judge_lat":JUDGE_LAT,
                  "app_tok":APP_TOK,"judge_tok":JUDGE_TOK,"stats":STATS}, indent=2)[:2500])
