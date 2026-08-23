"""router-v3 runner. Stages: dev (52 adjudicated) | holdout (40 frozen unseen).

Concurrency 1. One NVIDIA 20B call per question. Groq 0. NVIDIA 120B 0.
No retrieval, no generation, no judging, no LangGraph.
Durable checkpoint after every classification. Prompt/schema/temperature/model
are IDENTICAL across both stages — the fingerprint proves it.
"""
import json, os, sys, hashlib, subprocess, warnings; warnings.filterwarnings("ignore")

import nvidia_harness as H
import nvidia_provider as nv
import router_v3 as R

STAGE = sys.argv[1] if len(sys.argv) > 1 else "dev"
HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, "output")
OUT = "/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
CKPT = os.path.join(OUTD, f"router_v3_{STAGE}_results.jsonl")
GATE = os.path.join(OUTD, "router_v3_dev_gate.json")
THRESH = {"recall": 0.90, "specificity": 0.90, "precision": 0.80}

H.install_groq_guard()

FP = {"experiment": "compound-router-v3", "stage_independent": True,
      "router_model": R.ROUTER_MODEL, "temperature": R.ROUTER_TEMPERATURE,
      "structured_output_version": R.STRUCTURED_OUTPUT_VERSION,
      "prompt_sha": hashlib.sha256(R.ROUTER_SYS.encode()).hexdigest()[:16],
      "reason_codes": list(R.REASON_CODES), "max_retrieval_queries": R.MAX_RETRIEVAL_QUERIES,
      "concurrency": 1, "timeout_s": R.ROUTER_TIMEOUT, "max_tokens": R.ROUTER_MAX_TOKENS,
      "repo_head": subprocess.run(["git","rev-parse","HEAD"],cwd=HERE,capture_output=True,text=True).stdout.strip(),
      "retrieval_performed": False, "generation_performed": False,
      "judging_performed": False, "langgraph_used": False}
FP["fingerprint_hash"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]
json.dump(FP, open(os.path.join(OUTD, "router_v3_fingerprint.json"), "w"), indent=2)

if STAGE == "dev":
    src = [json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl", encoding="utf-8")]
    items = [{"id": r["case_id"], "question": r["question"], "gt": r["ground_truth"],
              "category": r.get("decision_rule", "")} for r in src]
elif STAGE == "holdout":
    if not os.path.exists(GATE) or not json.load(open(GATE)).get("passed"):
        print("*** REFUSING to run the holdout: development gate has not passed. ***"); sys.exit(4)
    src = [json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl", encoding="utf-8")]
    items = [{"id": r["holdout_case_id"], "question": r["question"], "gt": r["ground_truth"],
              "category": r["category"]} for r in src]
else:
    print("stage must be dev|holdout"); sys.exit(2)

print(f"=== compound-router-v3 [{STAGE}] ===")
print(f"  model={R.ROUTER_MODEL} temp={R.ROUTER_TEMPERATURE} prompt_sha={FP['prompt_sha']}")
print(f"  schema={R.STRUCTURED_OUTPUT_VERSION} fingerprint={FP['fingerprint_hash']}")
print(f"  cases={len(items)}  concurrency=1  groq=0  nvidia_120b=0\n")

done = {}
if os.path.exists(CKPT):
    for l in open(CKPT):
        try:
            r = json.loads(l)
            if r.get("fingerprint_hash") == FP["fingerprint_hash"]: done[r["id"]] = r
        except Exception: pass
todo = [i for i in items if i["id"] not in done]
print(f"  already done={len(items)-len(todo)}  to classify={len(todo)}\n")

for it in todo:                                        # strictly sequential
    res = R.classify(it["question"])                   # question ONLY
    rec = {"id": it["id"], "fingerprint_hash": FP["fingerprint_hash"], "stage": STAGE,
           "question": it["question"], "ground_truth": it["gt"], "category": it["category"],
           "predicted_compound": res.get("needs_decomposition"),
           "retrieval_queries": res.get("retrieval_queries"),
           "retrieval_query_count": len(res.get("retrieval_queries") or []),
           "reason_code": res.get("reason_code"), "rationale": res.get("rationale"),
           "parse_ok": res.get("parse_ok"), "parse_error": res.get("parse_error"),
           "latency_ms": res.get("latency_ms"), "input_tokens": res.get("input_tokens"),
           "output_tokens": res.get("output_tokens"), "provider_status": res.get("provider_status")}
    with open(CKPT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
    done[it["id"]] = rec
    hit = (rec["predicted_compound"] is True) == (it["gt"] == "compound")
    print(f"  [{'ok ' if hit else 'MISS'}] {it['id']} gt={it['gt']:<8} pred={str(rec['predicted_compound']):<5} "
          f"q={rec['retrieval_query_count']} code={rec['reason_code']} {rec['latency_ms']}ms {rec['provider_status']}",
          flush=True)
    if rec["provider_status"] != "ok":
        print("\n  PROVIDER DEGRADED — stopping; checkpoint is durable, resume later."); break

print(f"\nnvidia stats: {json.dumps(nv.STATS)}")

# ---------------- scoring ----------------
scored = [done[i["id"]] for i in items if i["id"] in done and i["gt"] != "ambiguous"]
unparsed = [r["id"] for r in scored if r["predicted_compound"] is None]
if unparsed:
    print(f"\n*** {len(unparsed)} case(s) produced no verdict: {unparsed} — cannot score. ***"); sys.exit(3)
TP=[r["id"] for r in scored if r["ground_truth"]=="compound" and r["predicted_compound"] is True]
FN=[r["id"] for r in scored if r["ground_truth"]=="compound" and r["predicted_compound"] is False]
FP_=[r["id"] for r in scored if r["ground_truth"]=="simple" and r["predicted_compound"] is True]
TN=[r["id"] for r in scored if r["ground_truth"]=="simple" and r["predicted_compound"] is False]
d=lambda a,b: round(a/b,4) if b else None
prec=d(len(TP),len(TP)+len(FP_)); rec_=d(len(TP),len(TP)+len(FN))
f1=round(2*prec*rec_/(prec+rec_),4) if (prec and rec_) else 0.0
spec=d(len(TN),len(TN)+len(FP_)); acc=d(len(TP)+len(TN),len(scored))
print(f"\n=== {STAGE} metrics (scored={len(scored)}) ===")
print(f"  TP={len(TP)} FP={len(FP_)} TN={len(TN)} FN={len(FN)}")
print(f"  precision={prec} recall={rec_} f1={f1} specificity={spec} accuracy={acc}")
print(f"  false positives: {FP_}")
print(f"  false negatives: {FN}")

if STAGE == "dev":
    KNOWN=["case-002","case-003","case-004","case-056","case-059"]
    print(f"\n  the five known v2 false positives under v3:")
    for cid in KNOWN:
        r=done.get(cid)
        if r: print(f"    {cid}: v2=compound(FP) -> v3={'compound (still FP)' if r['predicted_compound'] else 'SIMPLE (fixed)'}"
                    f"  code={r['reason_code']} q={r['retrieval_query_count']}")
    passed=(rec_>=THRESH["recall"] and spec>=THRESH["specificity"] and prec>=THRESH["precision"])
    json.dump({"passed":bool(passed),"precision":prec,"recall":rec_,"specificity":spec,
               "accuracy":acc,"f1":f1,"thresholds":THRESH,"TP":len(TP),"FP":len(FP_),
               "TN":len(TN),"FN":len(FN),"FP_cases":FP_,"FN_cases":FN,
               "fingerprint_hash":FP["fingerprint_hash"]}, open(GATE,"w"), indent=2)
    print(f"\n  gate: recall>={THRESH['recall']} {rec_>=THRESH['recall']} | "
          f"specificity>={THRESH['specificity']} {spec>=THRESH['specificity']} | "
          f"precision>={THRESH['precision']} {prec>=THRESH['precision']}")
    if not passed:
        print("\n*** ROUTER V3 DEVELOPMENT FAILURE — do NOT run the unseen holdout. ***"); sys.exit(5)
    print("\n*** DEVELOPMENT GATE PASSED — cleared to run the frozen unseen holdout ONCE. ***")
