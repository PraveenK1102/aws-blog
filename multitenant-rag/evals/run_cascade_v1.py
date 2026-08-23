"""compound-router-cascade-v1 runner.

Stage A = FROZEN Router V2 (high recall).  Stage B = new strict verifier.
Cascade: V2 simple -> SIMPLE.  V2 compound -> verifier decides.
Stage B cannot create a compound V2 missed; recall is bounded by V2's.

dev     : Stage A predictions are REUSED from the persisted V2 checkpoint (0 V2 calls).
holdout : Stage A runs the frozen Router V2 live, then Stage B where needed.

Concurrency 1, bounded timeouts, durable checkpoint per verifier call.
Groq 0. NVIDIA 120B 0. No retrieval, generation, judging or LangGraph.
"""
import json, os, sys, hashlib, subprocess, warnings; warnings.filterwarnings("ignore")

import nvidia_harness as H
import nvidia_provider as nv
import verifier_v1 as V

STAGE = sys.argv[1] if len(sys.argv) > 1 else "dev"
HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, "output")
OUT = "/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
CKPT = os.path.join(OUTD, f"cascade_v1_{STAGE}_verifier.jsonl")
ACKPT = os.path.join(OUTD, "cascade_v1_holdout_stageA.jsonl")
GATE = os.path.join(OUTD, "cascade_v1_dev_gate.json")
THRESH = {"recall": 0.90, "specificity": 0.90, "precision": 0.80}
HOLDOUT_SHA = "0957b7bf8db137bad6e35f1495f5953e636e87969b5173b0fd9101a1dfd233b8"

H.install_groq_guard()

FP = {"experiment": "compound-router-cascade-v1",
      "stage_a": "frozen router_v2", "stage_b": "verifier_v1",
      "verifier_model": V.VERIFIER_MODEL, "verifier_temperature": V.VERIFIER_TEMPERATURE,
      "verifier_schema": V.STRUCTURED_OUTPUT_VERSION,
      "verifier_prompt_sha": hashlib.sha256(V.VERIFIER_SYS.encode()).hexdigest()[:16],
      "verifier_reason_codes": list(V.REASON_CODES),
      "concurrency": 1, "timeout_s": V.VERIFIER_TIMEOUT, "max_tokens": V.VERIFIER_MAX_TOKENS,
      "repo_head": subprocess.run(["git","rev-parse","HEAD"],cwd=HERE,capture_output=True,text=True).stdout.strip(),
      "retrieval_performed": False, "generation_performed": False,
      "judging_performed": False, "langgraph_used": False}

# ---------------- Stage A ----------------
if STAGE == "dev":
    v2fp = json.load(open(os.path.join(OUTD, "router_v2_fingerprint.json")))
    FP["stage_a_source"] = "persisted"; FP["stage_a_fingerprint"] = v2fp["fingerprint_hash"]
    FP["stage_a_prompt_sha"] = v2fp["prompt_sha"]
    preds = {}
    for l in open(os.path.join(OUTD, "router_v2_results.jsonl")):
        r = json.loads(l)
        if r.get("fingerprint_hash") == v2fp["fingerprint_hash"]: preds[r["case_id"]] = r
    assert len(preds) == 52, f"expected 52 persisted V2 predictions, got {len(preds)}"
    gt = {json.loads(l)["case_id"]: json.loads(l)
          for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl", encoding="utf-8")}
    items = [{"id": c, "question": preds[c]["question"], "gt": gt[c]["ground_truth"],
              "category": gt[c]["decision_rule"],
              "a_compound": preds[c]["predicted_compound"],
              "a_needs": preds[c]["information_needs"],
              "a_code": preds[c]["reason_code"]} for c in sorted(preds)]
elif STAGE == "holdout":
    if not os.path.exists(GATE) or not json.load(open(GATE)).get("passed"):
        print("*** REFUSING to run the holdout: development gate has not passed. ***"); sys.exit(4)
    recs = [json.loads(l) for l in open(f"{OUT}/compound-router-holdout-v1.jsonl", encoding="utf-8")]
    live = hashlib.sha256(json.dumps(recs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if live != HOLDOUT_SHA:
        print(f"*** REFUSING: frozen holdout hash mismatch.\n  expected {HOLDOUT_SHA}\n  actual   {live} ***")
        sys.exit(6)
    import router_v2 as R2                       # frozen Stage A, used unmodified
    FP["stage_a_source"] = "live frozen router_v2"
    FP["stage_a_prompt_sha"] = hashlib.sha256(R2.ROUTER_SYS.encode()).hexdigest()[:16]
    print(f"  frozen holdout verified: sha256={live[:32]}... ({len(recs)} cases)")
    items = [{"id": r["holdout_case_id"], "question": r["question"], "gt": r["ground_truth"],
              "category": r["category"]} for r in recs]
else:
    print("stage must be dev|holdout"); sys.exit(2)

FP["fingerprint_hash"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]
json.dump(FP, open(os.path.join(OUTD, f"cascade_v1_{STAGE}_fingerprint.json"), "w"), indent=2)

print(f"=== compound-router-cascade-v1 [{STAGE}] ===")
print(f"  Stage A: {FP['stage_a_source']}  prompt_sha={FP['stage_a_prompt_sha']}")
print(f"  Stage B: {V.VERIFIER_MODEL} temp={V.VERIFIER_TEMPERATURE} "
      f"prompt_sha={FP['verifier_prompt_sha']} schema={V.STRUCTURED_OUTPUT_VERSION}")
print(f"  fingerprint={FP['fingerprint_hash']}  cases={len(items)}  concurrency=1  groq=0  nvidia_120b=0\n")

a_calls = 0
if STAGE == "holdout":
    adone = {}
    if os.path.exists(ACKPT):
        for l in open(ACKPT):
            r = json.loads(l)
            if r.get("fingerprint_hash") == FP["fingerprint_hash"]: adone[r["id"]] = r
    for it in items:
        if it["id"] in adone:
            it.update(a_compound=adone[it["id"]]["a_compound"], a_needs=adone[it["id"]]["a_needs"],
                      a_code=adone[it["id"]]["a_code"], a_latency=adone[it["id"]]["a_latency"],
                      a_in=adone[it["id"]]["a_in"], a_out=adone[it["id"]]["a_out"]); continue
        res = R2.classify(it["question"]); a_calls += 1
        rec = {"id": it["id"], "fingerprint_hash": FP["fingerprint_hash"],
               "a_compound": res.get("needs_decomposition"), "a_needs": res.get("information_needs"),
               "a_code": res.get("reason_code"), "a_parse_ok": res.get("parse_ok"),
               "a_parse_error": res.get("parse_error"), "a_latency": res.get("latency_ms"),
               "a_in": res.get("input_tokens"), "a_out": res.get("output_tokens"),
               "a_provider_status": res.get("provider_status")}
        with open(ACKPT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
        it.update(a_compound=rec["a_compound"], a_needs=rec["a_needs"], a_code=rec["a_code"],
                  a_latency=rec["a_latency"], a_in=rec["a_in"], a_out=rec["a_out"])
        print(f"  [A] {it['id']} v2={str(rec['a_compound']):<5} needs={len(rec['a_needs'] or [])} "
              f"{rec['a_latency']}ms {rec['a_provider_status']}", flush=True)
        if rec["a_provider_status"] != "ok":
            print("\n  STAGE A PROVIDER DEGRADED — stopping; checkpoint durable."); sys.exit(7)

# ---------------- Stage B: only where Stage A said compound ----------------
bdone = {}
if os.path.exists(CKPT):
    for l in open(CKPT):
        r = json.loads(l)
        if r.get("fingerprint_hash") == FP["fingerprint_hash"]: bdone[r["id"]] = r
candidates = [it for it in items if it.get("a_compound") is True]
print(f"\n  Stage A compound candidates: {len(candidates)}/{len(items)} "
      f"({round(100*len(candidates)/len(items),1)}% require the verifier)")
todo = [it for it in candidates if it["id"] not in bdone]
print(f"  verifier: already done={len(candidates)-len(todo)}  to run={len(todo)}\n")

b_calls = 0
for it in todo:
    res = V.verify(it["question"], it["a_needs"], it["a_code"]); b_calls += 1
    rec = {"id": it["id"], "fingerprint_hash": FP["fingerprint_hash"], "stage": STAGE,
           "question": it["question"], "ground_truth": it["gt"], "category": it["category"],
           "a_needs": it["a_needs"], "a_code": it["a_code"],
           "confirm_compound": res.get("confirm_compound"), "reason_code": res.get("reason_code"),
           "rationale": res.get("rationale"), "parse_ok": res.get("parse_ok"),
           "parse_error": res.get("parse_error"), "latency_ms": res.get("latency_ms"),
           "input_tokens": res.get("input_tokens"), "output_tokens": res.get("output_tokens"),
           "provider_status": res.get("provider_status")}
    with open(CKPT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
    bdone[it["id"]] = rec
    final = "compound" if rec["confirm_compound"] else "simple"
    hit = (rec["confirm_compound"] is True) == (it["gt"] == "compound")
    print(f"  [B] {'ok ' if hit else 'MISS'} {it['id']} gt={it['gt']:<9} verifier={final:<8} "
          f"code={rec['reason_code']} {rec['latency_ms']}ms {rec['provider_status']}"
          f"{'' if rec['parse_ok'] else ' PARSE:'+str(rec['parse_error'])}", flush=True)
    if rec["provider_status"] != "ok":
        print("\n  STAGE B PROVIDER DEGRADED — stopping; checkpoint durable."); break

print(f"\nnvidia stats: {json.dumps(nv.STATS)}")

# ---------------- cascade classification + scoring ----------------
rows = []
for it in items:
    b = bdone.get(it["id"])
    if it.get("a_compound") is True:
        final = None if (b is None or b["confirm_compound"] is None) else b["confirm_compound"]
        path = "A->B"
    else:
        final = False; path = "A only"
    rows.append({**it, "final_compound": final, "path": path,
                 "b_code": (b or {}).get("reason_code"), "b_rationale": (b or {}).get("rationale"),
                 "b_latency": (b or {}).get("latency_ms"), "b_in": (b or {}).get("input_tokens"),
                 "b_out": (b or {}).get("output_tokens"),
                 "b_parse_ok": (b or {}).get("parse_ok"), "b_parse_error": (b or {}).get("parse_error")})
json.dump(rows, open(os.path.join(OUTD, f"cascade_v1_{STAGE}_rows.json"), "w"), indent=2)

scored = [r for r in rows if r["gt"] != "ambiguous"]
unscorable = [r["id"] for r in scored if r["final_compound"] is None]
if unscorable:
    print(f"\n*** {len(unscorable)} case(s) produced no cascade verdict: {unscorable} ***"); sys.exit(3)
TP=[r["id"] for r in scored if r["gt"]=="compound" and r["final_compound"]]
FN=[r["id"] for r in scored if r["gt"]=="compound" and not r["final_compound"]]
FP_=[r["id"] for r in scored if r["gt"]=="simple" and r["final_compound"]]
TN=[r["id"] for r in scored if r["gt"]=="simple" and not r["final_compound"]]
d=lambda a,b: round(a/b,4) if b else None
prec=d(len(TP),len(TP)+len(FP_)); rec_=d(len(TP),len(TP)+len(FN))
f1=round(2*prec*rec_/(prec+rec_),4) if (prec and rec_) else 0.0
spec=d(len(TN),len(TN)+len(FP_)); acc=d(len(TP)+len(TN),len(scored))
print(f"\n=== {STAGE} cascade metrics (scored={len(scored)}) ===")
print(f"  TP={len(TP)} FP={len(FP_)} TN={len(TN)} FN={len(FN)}")
print(f"  precision={prec} recall={rec_} f1={f1} specificity={spec} accuracy={acc}")
print(f"  false positives: {FP_}\n  false negatives: {FN}")
print(f"  calls: stage_A={a_calls} stage_B={b_calls} "
      f"(verifier invoked on {len(candidates)}/{len(items)})")

if STAGE == "dev":
    print("\n  === Stage B decisions on all 16 non-ambiguous V2 compound candidates ===")
    for r in sorted([x for x in rows if x.get("a_compound") and x["gt"]!="ambiguous"],
                    key=lambda x:(x["gt"],x["id"])):
        print(f"    {r['id']} gt={r['gt']:<9} verifier={'CONFIRM' if r['final_compound'] else 'reject ':<8} "
              f"code={r['b_code']}")
    amb=[x for x in rows if x.get("a_compound") and x["gt"]=="ambiguous"]
    if amb:
        print("  ambiguous (path executed, excluded from metrics):")
        for r in amb: print(f"    {r['id']} verifier={'CONFIRM' if r['final_compound'] else 'reject'} code={r['b_code']}")
    passed=(rec_>=THRESH["recall"] and spec>=THRESH["specificity"] and prec>=THRESH["precision"])
    json.dump({"passed":bool(passed),"precision":prec,"recall":rec_,"specificity":spec,
               "accuracy":acc,"f1":f1,"thresholds":THRESH,"TP":len(TP),"FP":len(FP_),
               "TN":len(TN),"FN":len(FN),"FP_cases":FP_,"FN_cases":FN,
               "fingerprint_hash":FP["fingerprint_hash"]}, open(GATE,"w"), indent=2)
    print(f"\n  gate: recall>={THRESH['recall']} {rec_>=THRESH['recall']} | "
          f"specificity>={THRESH['specificity']} {spec>=THRESH['specificity']} | "
          f"precision>={THRESH['precision']} {prec>=THRESH['precision']}")
    if not passed:
        print("\n*** CASCADE V1 DEVELOPMENT FAILURE — holdout NOT run. ***"); sys.exit(5)
    print("\n*** DEVELOPMENT GATE PASSED — cleared to run the frozen holdout ONCE. ***")
