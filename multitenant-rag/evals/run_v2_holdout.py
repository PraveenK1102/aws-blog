"""compound-router-v2-high-recall-holdout-v1 — HOLDOUT-ONLY validation of frozen Router V2.

Candidate: the exact frozen Router V2, unmodified. No verifier, no V3, no V4, no
cascade Stage B, no retrieval, no generation, no judging, no LangGraph.

Acceptance policy was fixed BEFORE execution (see THRESH / CATEGORY_GUARDS below):
  compound recall >= 0.95, simple specificity >= 0.80, compound precision >= 0.80,
  compound_without_and recall >= 0.75, contrast_verification specificity >= 0.75.

One V2 call per question, concurrency 1, durable checkpoint per case. Once the first
classification is written the configuration is immutable.
"""
import json, os, sys, re, hashlib, unicodedata, subprocess, statistics as st
import warnings; warnings.filterwarnings("ignore")

import nvidia_harness as H
import nvidia_provider as nv
import router_v2 as R2                       # frozen candidate, used unmodified

HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, "output")
OUT = "/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
CKPT = os.path.join(OUTD, "v2_holdout_results.jsonl")
HOLDOUT_SHA = "0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8"
THRESH = {"recall": 0.95, "specificity": 0.80, "precision": 0.80}
CATEGORY_GUARDS = {"compound_without_and": 0.75, "contrast_verification": 0.75}

H.install_groq_guard()

# ---------- integrity gate: assert BEFORE any provider call ----------
recs = [json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl", encoding="utf-8")]
live = hashlib.sha256(json.dumps(recs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
norm = lambda q: re.sub(r"\s+", " ", unicodedata.normalize("NFKC", q).strip())
errs = []
if live != HOLDOUT_SHA: errs.append(f"hash mismatch: expected {HOLDOUT_SHA} got {live}")
if len(recs) != 40: errs.append(f"expected 40 cases, got {len(recs)}")
if sum(1 for r in recs if r["ground_truth"] == "simple") != 20: errs.append("simple != 20")
if sum(1 for r in recs if r["ground_truth"] == "compound") != 20: errs.append("compound != 20")
if any(r["ground_truth"] == "ambiguous" for r in recs): errs.append("ambiguous present")
if len({r["holdout_case_id"] for r in recs}) != 40: errs.append("duplicate case ids")
if len({norm(r["question"]) for r in recs}) != 40: errs.append("duplicate normalized questions")
if errs:
    print("*** DECISION REQUIRED — HOLDOUT INTEGRITY FAILURE ***")
    [print("   -", e) for e in errs]; sys.exit(1)

# ---------- frozen V2 identity gate ----------
v2fp = json.load(open(os.path.join(OUTD, "router_v2_fingerprint.json")))
live_prompt = hashlib.sha256(R2.ROUTER_SYS.encode()).hexdigest()[:16]
idc = [("prompt_sha", live_prompt, v2fp["prompt_sha"]),
       ("model", R2.ROUTER_MODEL, v2fp["router_model"]),
       ("max_tokens", R2.ROUTER_MAX_TOKENS, v2fp["max_tokens"]),
       ("reason_codes", list(R2.REASON_CODES), v2fp["reason_codes"])]
bad = [n for n, l, s in idc if l != s]
if bad:
    print(f"*** REFUSING: frozen V2 identity mismatch on {bad} ***"); sys.exit(2)

FP = {"experiment": "compound-router-v2-high-recall-holdout-v1",
      "candidate": "frozen router_v2 (unmodified)",
      "router_model": R2.ROUTER_MODEL, "temperature": 0.0,
      "router_prompt_sha": live_prompt, "router_reason_codes": list(R2.REASON_CODES),
      "holdout_sha256": live, "acceptance_thresholds": THRESH,
      "category_guards": CATEGORY_GUARDS,
      "concurrency": 1, "timeout_s": R2.ROUTER_TIMEOUT, "max_tokens": R2.ROUTER_MAX_TOKENS,
      "repo_head": subprocess.run(["git","rev-parse","HEAD"],cwd=HERE,capture_output=True,text=True).stdout.strip(),
      "retrieval_performed": False, "generation_performed": False,
      "judging_performed": False, "langgraph_used": False,
      "verifier_used": False, "v3_used": False, "v4_used": False}
FP["fingerprint_hash"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]
json.dump(FP, open(os.path.join(OUTD, "v2_holdout_fingerprint.json"), "w"), indent=2)

print("=== compound-router-v2-high-recall-holdout-v1 ===")
print(f"  holdout sha256 verified : {live[:32]}...  (40 cases, 20/20, 0 ambiguous)")
print(f"  frozen V2 verified      : prompt_sha={live_prompt} model={R2.ROUTER_MODEL} temp=0.0")
print(f"  acceptance (fixed pre-run): recall>={THRESH['recall']} specificity>={THRESH['specificity']} "
      f"precision>={THRESH['precision']}")
print(f"  category guards          : {CATEGORY_GUARDS}")
print(f"  fingerprint={FP['fingerprint_hash']}  concurrency=1  groq=0  nvidia_120b=0\n")

done = {}
if os.path.exists(CKPT):
    for l in open(CKPT):
        r = json.loads(l)
        if r.get("fingerprint_hash") == FP["fingerprint_hash"]: done[r["id"]] = r
todo = [r for r in recs if r["holdout_case_id"] not in done]
print(f"  already done={len(recs)-len(todo)}  to classify={len(todo)}\n")

for c in todo:
    res = R2.classify(c["question"])                     # question ONLY
    rec = {"id": c["holdout_case_id"], "fingerprint_hash": FP["fingerprint_hash"],
           "question": c["question"], "ground_truth": c["ground_truth"],
           "category": c["category"],
           "reference_need_count": c["independent_retrieval_need_count"],
           "predicted_compound": res.get("needs_decomposition"),
           "information_needs": res.get("information_needs"),
           "information_need_count": len(res.get("information_needs") or []),
           "reason_code": res.get("reason_code"),
           "parse_ok": res.get("parse_ok"), "parse_error": res.get("parse_error"),
           "latency_ms": res.get("latency_ms"), "input_tokens": res.get("input_tokens"),
           "output_tokens": res.get("output_tokens"), "provider_status": res.get("provider_status")}
    with open(CKPT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
    done[rec["id"]] = rec
    hit = (rec["predicted_compound"] is True) == (c["ground_truth"] == "compound")
    print(f"  [{'ok ' if hit else 'MISS'}] {rec['id']} gt={c['ground_truth']:<8} "
          f"pred={str(rec['predicted_compound']):<5} needs={rec['information_need_count']} "
          f"code={rec['reason_code']} [{c['category']}] {rec['latency_ms']}ms {rec['provider_status']}"
          f"{'' if rec['parse_ok'] else ' PARSE:'+str(rec['parse_error'])}", flush=True)
    if rec["provider_status"] != "ok":
        print("\n  PROVIDER DEGRADED — stopping; checkpoint is durable, resume later."); break

print(f"\nnvidia stats: {json.dumps(nv.STATS)}")

rows = [done[r["holdout_case_id"]] for r in recs if r["holdout_case_id"] in done]
unscorable = [r["id"] for r in rows if r["predicted_compound"] is None]
if len(rows) < 40 or unscorable:
    print(f"\n*** INCOMPLETE: classified={len(rows)}/40 unscorable={unscorable} — cannot score. ***")
    sys.exit(3)

TP=[r for r in rows if r["ground_truth"]=="compound" and r["predicted_compound"]]
FN=[r for r in rows if r["ground_truth"]=="compound" and not r["predicted_compound"]]
FP_=[r for r in rows if r["ground_truth"]=="simple" and r["predicted_compound"]]
TN=[r for r in rows if r["ground_truth"]=="simple" and not r["predicted_compound"]]
d=lambda a,b: round(a/b,4) if b else None
prec=d(len(TP),len(TP)+len(FP_)); rec_=d(len(TP),len(TP)+len(FN))
f1=round(2*prec*rec_/(prec+rec_),4) if (prec and rec_) else 0.0
spec=d(len(TN),len(TN)+len(FP_)); acc=d(len(TP)+len(TN),len(rows))

cats={}
for r in rows:
    o=cats.setdefault(r["category"],{"n":0,"gt":r["ground_truth"],"correct":0,"wrong":[]})
    o["n"]+=1
    if (r["predicted_compound"] is True)==(r["ground_truth"]=="compound"): o["correct"]+=1
    else: o["wrong"].append(r["id"])
for o in cats.values():
    o["score"]=round(o["correct"]/o["n"],4); o["metric"]="recall" if o["gt"]=="compound" else "specificity"

lat=[r["latency_ms"] for r in rows if r["latency_ms"]]
def pct(v,p):
    q=sorted(v); k=(len(q)-1)*p/100; lo,hi=int(k),min(int(k)+1,len(q)-1)
    return round(q[lo]+(q[hi]-q[lo])*(k-lo),1)
ti=[r["input_tokens"] for r in rows if r["input_tokens"]]; to=[r["output_tokens"] for r in rows if r["output_tokens"]]

core={"recall":rec_>=THRESH["recall"],"specificity":spec>=THRESH["specificity"],
      "precision":prec>=THRESH["precision"]}
guards={k:(cats.get(k,{}).get("score") is None or cats[k]["score"]>=v)
        for k,v in CATEGORY_GUARDS.items()}
verdict = "PASS" if (all(core.values()) and all(guards.values())) else "FAIL"

M={"experiment":FP["experiment"],"fingerprint_hash":FP["fingerprint_hash"],
   "holdout_sha256":live,"router_prompt_sha":live_prompt,
   "scored":len(rows),"TP":len(TP),"FP":len(FP_),"TN":len(TN),"FN":len(FN),
   "compound_precision":prec,"compound_recall":rec_,"compound_f1":f1,
   "simple_specificity":spec,"routing_accuracy":acc,
   "false_positive_decomposition_rate":d(len(FP_),len(FP_)+len(TN)),
   "false_negative_compound_rate":d(len(FN),len(FN)+len(TP)),
   "parse_ok":sum(1 for r in rows if r["parse_ok"]),
   "by_category":cats,
   "false_positives":[{"id":r["id"],"question":r["question"],"category":r["category"],
                       "reason_code":r["reason_code"],"information_needs":r["information_needs"]} for r in FP_],
   "false_negatives":[{"id":r["id"],"question":r["question"],"category":r["category"],
                       "reason_code":r["reason_code"],"information_needs":r["information_needs"],
                       "reference_need_count":r["reference_need_count"]} for r in FN],
   "latency_ms":{"mean":round(st.mean(lat),1),"p50":pct(lat,50),"p95":pct(lat,95),"max":max(lat)},
   "tokens":{"input_mean":round(st.mean(ti),1),"output_mean":round(st.mean(to),1),
             "input_total":sum(ti),"output_total":sum(to)},
   "reason_code_distribution":{c:sum(1 for r in rows if r["reason_code"]==c)
                               for c in sorted({r["reason_code"] for r in rows}-{None})},
   "acceptance":{"thresholds":THRESH,"core_results":core,
                 "category_guards":{k:{"required":v,"actual":cats.get(k,{}).get("score"),
                                       "pass":guards[k]} for k,v in CATEGORY_GUARDS.items()},
                 "verdict":verdict},
   "provider":{"nvidia_20b_requests":nv.STATS.get("requests"),
               "nvidia_20b_successes":nv.STATS.get("successes"),
               "nvidia_20b_timeouts":nv.STATS.get("timeouts"),
               "nvidia_20b_429":nv.STATS.get("http_429"),
               "nvidia_120b_calls":0,"groq_calls":0}}
json.dump(M,open(os.path.join(OUTD,"v2_holdout_metrics.json"),"w"),indent=2)

print(f"\n=== HOLDOUT METRICS (40 cases, 20 simple / 20 compound) ===")
print(f"  TP={len(TP)} FP={len(FP_)} TN={len(TN)} FN={len(FN)}   parse_ok={M['parse_ok']}/40")
print(f"  precision={prec} recall={rec_} f1={f1} specificity={spec} accuracy={acc}")
print(f"  fp_rate={M['false_positive_decomposition_rate']} fn_rate={M['false_negative_compound_rate']}")
print(f"  latency {M['latency_ms']}\n  tokens {M['tokens']}")
print(f"  reason codes {M['reason_code_distribution']}")
print("\n  by category:")
for c,o in sorted(cats.items(), key=lambda x:(x[1]["gt"],x[0])):
    print(f"    {c:<32} {o['metric']:<12} {o['correct']}/{o['n']} = {o['score']}"
          + (f"  miss={o['wrong']}" if o["wrong"] else ""))
print(f"\n  core thresholds: {core}")
print(f"  category guards: {json.dumps(M['acceptance']['category_guards'])}")
print(f"\n*** ROUTER V2 HIGH-RECALL HOLDOUT = {verdict} ***")
if verdict=="PASS":
    print("*** ROUTER V2 ACCEPTED FOR OFFLINE LANGGRAPH INTEGRATION (not production) ***")
