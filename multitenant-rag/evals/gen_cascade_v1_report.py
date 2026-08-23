"""cascade-v1 metrics + exports. No provider calls."""
import json, os, csv, statistics as st
HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"

def load(stage):
    p=os.path.join(OUTD,f"cascade_v1_{stage}_rows.json")
    return json.load(open(p)) if os.path.exists(p) else []

def score(rows):
    s=[r for r in rows if r["gt"]!="ambiguous"]
    TP=[r for r in s if r["gt"]=="compound" and r["final_compound"]]
    FN=[r for r in s if r["gt"]=="compound" and not r["final_compound"]]
    FP=[r for r in s if r["gt"]=="simple" and r["final_compound"]]
    TN=[r for r in s if r["gt"]=="simple" and not r["final_compound"]]
    d=lambda a,b: round(a/b,4) if b else None
    prec=d(len(TP),len(TP)+len(FP)); rec=d(len(TP),len(TP)+len(FN))
    bl=[r["b_latency"] for r in rows if r.get("b_latency")]
    def pct(v,p):
        if not v: return None
        q=sorted(v); k=(len(q)-1)*p/100; lo,hi=int(k),min(int(k)+1,len(q)-1)
        return round(q[lo]+(q[hi]-q[lo])*(k-lo),1)
    bi=[r["b_in"] for r in rows if r.get("b_in")]; bo=[r["b_out"] for r in rows if r.get("b_out")]
    al=[r["a_latency"] for r in rows if r.get("a_latency")]
    cand=[r for r in rows if r.get("a_compound") is True]
    return {"scored":len(s),"TP":len(TP),"FP":len(FP),"TN":len(TN),"FN":len(FN),
      "TP_cases":sorted(r["id"] for r in TP),"FP_cases":sorted(r["id"] for r in FP),
      "FN_cases":sorted(r["id"] for r in FN),
      "compound_precision":prec,"compound_recall":rec,
      "compound_f1":round(2*prec*rec/(prec+rec),4) if (prec and rec) else 0.0,
      "simple_specificity":d(len(TN),len(TN)+len(FP)),
      "routing_accuracy":d(len(TP)+len(TN),len(s)),
      "false_positive_decomposition_rate":d(len(FP),len(FP)+len(TN)),
      "false_negative_compound_rate":d(len(FN),len(FN)+len(TP)),
      "call_efficiency":{"total_cases":len(rows),"stage_a_calls":len(al),
        "stage_b_calls":len(cand),"verifier_invocation_rate":round(len(cand)/len(rows),4),
        "mean_calls_per_question_measured":round((len(al)+len(cand))/len(rows),4),
        "mean_calls_per_question_if_stage_a_live":round((len(rows)+len(cand))/len(rows),4)},
      "stage_b_latency_ms":{"mean":round(st.mean(bl),1) if bl else None,"p50":pct(bl,50),
                            "p95":pct(bl,95),"max":max(bl) if bl else None},
      "stage_b_tokens":{"input_mean":round(st.mean(bi),1) if bi else None,
        "output_mean":round(st.mean(bo),1) if bo else None,
        "input_total":sum(bi),"output_total":sum(bo)},
      "stage_a_latency_ms":{"mean":round(st.mean(al),1) if al else None,"p50":pct(al,50),
                            "p95":pct(al,95),"max":max(al) if al else None},
      "verifier_reason_codes":{c:sum(1 for r in rows if r.get("b_code")==c)
                               for c in sorted({r.get("b_code") for r in rows}-{None})},
      "by_category":cat(rows)}

def cat(rows):
    out={}
    for r in rows:
        if r["gt"]=="ambiguous": continue
        o=out.setdefault(r["category"] or "uncategorised",{"n":0,"gt":r["gt"],"correct":0,"wrong":[]})
        o["n"]+=1
        if bool(r["final_compound"])==(r["gt"]=="compound"): o["correct"]+=1
        else: o["wrong"].append(r["id"])
    for o in out.values():
        o["score"]=round(o["correct"]/o["n"],4) if o["n"] else None
        o["metric"]="recall" if o["gt"]=="compound" else "specificity"
    return out

dev=load("dev"); hold=load("holdout")
M={"experiment":"compound-router-cascade-v1","holdout_executed":bool(hold),
   "provider":{"groq_calls":0,"nvidia_120b_calls":0}}
for k,v in [("development",dev),("holdout",hold)]:
    if v: M[k]=score(v)
if dev:
    g=os.path.join(OUTD,"cascade_v1_dev_gate.json")
    M["development_gate"]=json.load(open(g)) if os.path.exists(g) else None
    M["stage_b_decisions_on_v2_candidates"]=[
      {"id":r["id"],"ground_truth":r["gt"],"verifier":("CONFIRM" if r["final_compound"] else "reject"),
       "reason_code":r["b_code"],"correct":bool(r["final_compound"])==(r["gt"]=="compound")
       if r["gt"]!="ambiguous" else None}
      for r in sorted(dev,key=lambda x:x["id"]) if r.get("a_compound") is True]
M["comparison"]={"v2_adjudicated":{"precision":0.6875,"recall":1.0,"specificity":0.8718,"accuracy":0.9},
  "v3_dev":{"precision":0.9,"recall":0.8182,"specificity":0.9744,"accuracy":0.94},
  "v4_dev_parseable":{"precision":0.75,"recall":0.8182,"specificity":0.9167,"accuracy":0.8936},
  "cascade_v1_dev":{k:M.get("development",{}).get(x) for k,x in
    [("precision","compound_precision"),("recall","compound_recall"),
     ("specificity","simple_specificity"),("accuracy","routing_accuracy")]}}
json.dump(M,open(os.path.join(OUTD,"cascade_v1_metrics.json"),"w"),indent=2)

for stage,rows in [("dev",dev),("holdout",hold)]:
    if not rows: continue
    with open(f"{OUT}/compound-router-cascade-v1-{stage}-results.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["id","question","category","ground_truth","stage_a_compound","stage_a_needs",
                    "stage_a_reason_code","path","verifier_confirm","verifier_reason_code",
                    "verifier_rationale","final_compound","outcome","stage_a_latency_ms",
                    "stage_b_latency_ms","stage_b_in_tokens","stage_b_out_tokens"])
        for r in sorted(rows,key=lambda x:x["id"]):
            gt=r["gt"]; fin=r["final_compound"]
            o=("EXCLUDED_AMBIGUOUS" if gt=="ambiguous" else
               "TP" if (gt=="compound" and fin) else "FN" if gt=="compound" else "FP" if fin else "TN")
            vc=("" if r.get("a_compound") is not True else ("CONFIRM" if fin else "reject"))
            w.writerow([r["id"],r["question"],r["category"],gt,r.get("a_compound"),
                        " | ".join(r.get("a_needs") or []),r.get("a_code"),r["path"],vc,
                        r.get("b_code"),r.get("b_rationale"),fin,o,r.get("a_latency"),
                        r.get("b_latency"),r.get("b_in"),r.get("b_out")])

with open(f"{OUT}/compound-router-cascade-v1-metrics.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f)
    w.writerow(["metric","v2_adjudicated","v3_dev","v4_dev","cascade_v1_dev","cascade_v1_holdout"])
    d=M.get("development",{}); h=M.get("holdout",{})
    V2={"TP":11,"FP":5,"TN":34,"FN":0,"compound_precision":0.6875,"compound_recall":1.0,
        "compound_f1":0.8148,"simple_specificity":0.8718,"routing_accuracy":0.9,"scored":50}
    V3={"TP":9,"FP":1,"TN":38,"FN":2,"compound_precision":0.9,"compound_recall":0.8182,
        "compound_f1":0.8572,"simple_specificity":0.9744,"routing_accuracy":0.94,"scored":50}
    V4={"TP":9,"FP":3,"TN":33,"FN":2,"compound_precision":0.75,"compound_recall":0.8182,
        "compound_f1":0.7826,"simple_specificity":0.9167,"routing_accuracy":0.8936,"scored":47}
    for k in ["scored","TP","FP","TN","FN","compound_precision","compound_recall","compound_f1",
              "simple_specificity","routing_accuracy","false_positive_decomposition_rate",
              "false_negative_compound_rate"]:
        w.writerow([k,V2.get(k,""),V3.get(k,""),V4.get(k,""),d.get(k,""),h.get(k,"")])
    ce=d.get("call_efficiency",{})
    for k,lab in [("stage_a_calls","stage_a_calls"),("stage_b_calls","stage_b_calls"),
                  ("verifier_invocation_rate","verifier_invocation_rate"),
                  ("mean_calls_per_question_measured","mean_calls_per_question_measured"),
                  ("mean_calls_per_question_if_stage_a_live","mean_calls_per_question_if_stage_a_live")]:
        w.writerow([lab,"","","",ce.get(k,""),(h.get("call_efficiency") or {}).get(k,"")])
    for k in ["mean","p50","p95","max"]:
        w.writerow([f"stage_b_latency_{k}_ms","","","",(d.get("stage_b_latency_ms") or {}).get(k,""),
                    (h.get("stage_b_latency_ms") or {}).get(k,"")])
    for k in ["input_mean","output_mean","input_total","output_total"]:
        w.writerow([f"stage_b_tokens_{k}","","","",(d.get("stage_b_tokens") or {}).get(k,""),
                    (h.get("stage_b_tokens") or {}).get(k,"")])
    w.writerow(["groq_calls",0,0,0,0,0]); w.writerow(["nvidia_120b_calls",0,0,0,0,0])
    w.writerow(["verdict","NOT ACCEPTED","DEV FAILURE","DEV FAILURE",
                "DEV FAILURE" if not (M.get("development_gate") or {}).get("passed") else "PASS",
                "NOT RUN" if not hold else ""])

def show(tag,S):
    print(f"\n=== {tag} (scored={S['scored']}) ===")
    print(f"  TP={S['TP']} FP={S['FP']} TN={S['TN']} FN={S['FN']}")
    print(f"  precision={S['compound_precision']} recall={S['compound_recall']} f1={S['compound_f1']}")
    print(f"  specificity={S['simple_specificity']} accuracy={S['routing_accuracy']}")
    print(f"  FP={S['FP_cases']}  FN={S['FN_cases']}")
    print(f"  call efficiency: {json.dumps(S['call_efficiency'])}")
    print(f"  stage B latency {S['stage_b_latency_ms']}  tokens {S['stage_b_tokens']}")
    print(f"  verifier codes {S['verifier_reason_codes']}")
    print("  by category:")
    for c,o in sorted(S["by_category"].items()):
        print(f"    {c:<30} {o['metric']:<12} {o['correct']}/{o['n']} = {o['score']}"
              + (f"  miss={o['wrong']}" if o["wrong"] else ""))
if dev: show("CASCADE DEVELOPMENT", M["development"])
if hold: show("CASCADE HOLDOUT", M["holdout"])
else: print("\n  HOLDOUT: NOT RUN")
print(f"\n  comparison: {json.dumps(M['comparison'],indent=2)}")
