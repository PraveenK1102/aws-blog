"""router-v4 metrics + exports (dev and, if run, holdout). No provider calls."""
import json, os, csv, statistics as st

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
FP=json.load(open(os.path.join(OUTD,"router_v4_fingerprint.json")))

def load(stage):
    p=os.path.join(OUTD,f"router_v4_{stage}_results.jsonl")
    if not os.path.exists(p): return []
    out={}
    for l in open(p):
        r=json.loads(l)
        if r.get("fingerprint_hash")==FP["fingerprint_hash"]: out[r["id"]]=r
    return list(out.values())

def score(recs):
    s=[r for r in recs if r["ground_truth"]!="ambiguous"]
    TP=[r for r in s if r["ground_truth"]=="compound" and r["predicted_compound"] is True]
    FN=[r for r in s if r["ground_truth"]=="compound" and r["predicted_compound"] is False]
    FP_=[r for r in s if r["ground_truth"]=="simple" and r["predicted_compound"] is True]
    TN=[r for r in s if r["ground_truth"]=="simple" and r["predicted_compound"] is False]
    d=lambda a,b: round(a/b,4) if b else None
    prec=d(len(TP),len(TP)+len(FP_)); rec=d(len(TP),len(TP)+len(FN))
    f1=round(2*prec*rec/(prec+rec),4) if (prec and rec) else 0.0
    # NOTE: when unscorable > 0 these rates are computed over the parseable subset ONLY
    # and are NOT a valid gate result for the full denominator.
    lat=[r["latency_ms"] for r in s if r.get("latency_ms")]
    def pct(v,p):
        if not v: return None
        q=sorted(v); k=(len(q)-1)*p/100; lo,hi=int(k),min(int(k)+1,len(q)-1)
        return round(q[lo]+(q[hi]-q[lo])*(k-lo),1)
    ti=[r["input_tokens"] for r in s if r.get("input_tokens")]
    to=[r["output_tokens"] for r in s if r.get("output_tokens")]
    UNSCORABLE=[r for r in s if r["predicted_compound"] is None]
    return {"scored":len(s),"unscorable":len(UNSCORABLE),
      "unscorable_cases":[{"id":r["id"],"parse_error":r["parse_error"]} for r in UNSCORABLE],
      "confusion_matrix_covers_all":len(TP)+len(FN)+len(FP_)+len(TN)==len(s),
      "parseable_scored":len(s)-len(UNSCORABLE),
      "TP":len(TP),"FP":len(FP_),"TN":len(TN),"FN":len(FN),
      "TP_cases":sorted(r["id"] for r in TP),"FP_cases":sorted(r["id"] for r in FP_),
      "FN_cases":sorted(r["id"] for r in FN),
      "compound_precision":prec,"compound_recall":rec,"compound_f1":f1,
      "simple_specificity":d(len(TN),len(TN)+len(FP_)),
      "routing_accuracy":d(len(TP)+len(TN),len(s)-len(UNSCORABLE)),
      "routing_accuracy_full_denominator":d(len(TP)+len(TN),len(s)),
      "false_positive_decomposition_rate":d(len(FP_),len(FP_)+len(TN)),
      "false_negative_compound_rate":d(len(FN),len(FN)+len(TP)),
      "parse_ok":sum(1 for r in s if r.get("parse_ok")),
      "parse_errors":[{"id":r["id"],"error":r["parse_error"]} for r in s if not r.get("parse_ok")],
      "latency_ms":{"mean":round(st.mean(lat),1) if lat else None,"p50":pct(lat,50),
                    "p95":pct(lat,95),"max":max(lat) if lat else None},
      "tokens":{"input_mean":round(st.mean(ti),1) if ti else None,
                "output_mean":round(st.mean(to),1) if to else None,
                "input_total":sum(ti),"output_total":sum(to)},
      "reason_code_distribution":{c:sum(1 for r in s if r.get("reason_code")==c)
                                  for c in sorted({r.get("reason_code") for r in s}-{None})},
      "evidence_unit_count_distribution":{str(n):sum(1 for r in s if r["evidence_unit_count"]==n)
                                  for n in sorted({r["evidence_unit_count"] for r in s})}}

def by_category(recs):
    out={}
    for r in recs:
        if r["ground_truth"]=="ambiguous": continue
        o=out.setdefault(r["category"] or "uncategorised",
                         {"n":0,"gt":r["ground_truth"],"correct":0,"wrong_ids":[]})
        o["n"]+=1
        if (r["predicted_compound"] is True)==(r["ground_truth"]=="compound"): o["correct"]+=1
        else: o["wrong_ids"].append(r["id"])
    for o in out.values():
        o["score"]=round(o["correct"]/o["n"],4) if o["n"] else None
        o["metric_name"]="recall" if o["gt"]=="compound" else "specificity"
    return out

dev=load("dev"); hold=load("holdout")
V2={"TP":11,"FP":5,"TN":34,"FN":0,"compound_precision":0.6875,"compound_recall":1.0,
    "compound_f1":0.8148,"simple_specificity":0.8718,"routing_accuracy":0.9}
V3={"TP":9,"FP":1,"TN":38,"FN":2,"compound_precision":0.9,"compound_recall":0.8182,
    "compound_f1":0.8572,"simple_specificity":0.9744,"routing_accuracy":0.94}

M={"experiment":"compound-router-v4","fingerprint_hash":FP["fingerprint_hash"],
   "prompt_sha":FP["prompt_sha"],"model":FP["router_model"],"temperature":FP["temperature"],
   "structured_output_version":FP["structured_output_version"],
   "provider":{"groq_calls":0,"nvidia_120b_calls":0,"nvidia_20b_calls":len(dev)+len(hold)},
   "holdout_executed":bool(hold)}
DIAG=["case-030","case-041","case-002","case-004","case-056","case-059","case-003"]
if dev:
    M["development"]=score(dev); M["development"]["by_category"]=by_category(dev)
    dd={r["id"]:r for r in dev}
    def _diag(r):
        unparsed = r["predicted_compound"] is None
        v4 = "UNPARSED" if unparsed else ("compound" if r["predicted_compound"] else "simple")
        correct = None if unparsed else (r["predicted_compound"] is True)==(r["ground_truth"]=="compound")
        return {"ground_truth":r["ground_truth"],"v4":v4,"units":r["evidence_unit_count"],
                "reason_code":r["reason_code"],"parse_error":r["parse_error"],"correct":correct,
                "status":("UNSCORABLE" if unparsed else ("ok" if correct else "MISS"))}
    M["diagnostic_cases"]={k:_diag(dd[k]) for k in DIAG if k in dd}
    g=os.path.join(OUTD,"router_v4_dev_gate.json")
    M["development_gate"]=json.load(open(g)) if os.path.exists(g) else None
if hold:
    M["holdout"]=score(hold); M["holdout"]["by_category"]=by_category(hold)
    meta=json.load(open(os.path.join(OUTD,"router_holdout_v1_meta.json")))
    M["holdout"]["manifest_sha256"]=meta["manifest_sha256"]
    M["holdout"]["naive_and_detector_baseline"]=meta["naive_and_detector_baseline"]
    h=M["holdout"]; cat=h["by_category"]
    cwa=cat.get("compound_without_and",{}).get("score")
    cv=cat.get("contrast_verification",{}).get("score")
    core=(h["compound_recall"]>=0.90 and h["simple_specificity"]>=0.90
          and h["compound_precision"]>=0.80)
    collapse=((cwa is not None and cwa<0.75) or (cv is not None and cv<0.75))
    M["acceptance"]={"core":{"recall>=0.90":h["compound_recall"]>=0.90,
        "specificity>=0.90":h["simple_specificity"]>=0.90,
        "precision>=0.80":h["compound_precision"]>=0.80},
      "category_guards":{"compound_without_and_recall":cwa,
        "compound_without_and_recall>=0.75":(cwa is None or cwa>=0.75),
        "contrast_verification_specificity":cv,
        "contrast_verification_specificity>=0.75":(cv is None or cv>=0.75)},
      "verdict":("ACCEPTED FOR OFFLINE LANGGRAPH INTEGRATION"
                 if (core and not collapse) else "NOT ACCEPTED")}
json.dump(M,open(os.path.join(OUTD,"router_v4_metrics.json"),"w"),indent=2)

for stage,recs in [("dev",dev),("holdout",hold)]:
    if not recs: continue
    with open(f"{OUT}/compound-router-v4-{stage}-results.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["id","question","category","ground_truth","predicted_compound","outcome",
                    "evidence_unit_count","evidence_unit_anchors","evidence_unit_queries",
                    "facts_needed","reason_code","rationale","parse_ok","parse_error",
                    "latency_ms","input_tokens","output_tokens","provider_status"])
        for r in sorted(recs,key=lambda x:x["id"]):
            gt=r["ground_truth"]; p=r["predicted_compound"]
            o=("EXCLUDED_AMBIGUOUS" if gt=="ambiguous" else
               "TP" if (gt=="compound" and p) else "FN" if gt=="compound" else "FP" if p else "TN")
            u=r.get("evidence_units") or []
            w.writerow([r["id"],r["question"],r["category"],gt,p,o,r["evidence_unit_count"],
                        " | ".join(x["anchor"] for x in u),
                        " | ".join(x["retrieval_query"] for x in u),
                        " | ".join("; ".join(x["facts_needed"]) for x in u),
                        r["reason_code"],r["rationale"],r["parse_ok"],r["parse_error"],
                        r["latency_ms"],r["input_tokens"],r["output_tokens"],r["provider_status"]])

with open(f"{OUT}/compound-router-v4-metrics.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["metric","v2_adjudicated_dev","v3_dev","v4_dev","v4_holdout"])
    d=M.get("development",{}); h=M.get("holdout",{})
    for k in ["scored","TP","FP","TN","FN","compound_precision","compound_recall","compound_f1",
              "simple_specificity","routing_accuracy","false_positive_decomposition_rate",
              "false_negative_compound_rate"]:
        w.writerow([k,V2.get(k,50 if k=="scored" else ""),V3.get(k,50 if k=="scored" else ""),
                    d.get(k,""),h.get(k,"")])
    for k in ["mean","p50","p95","max"]:
        w.writerow([f"latency_{k}_ms","",{"mean":4567.9,"p50":2840.9,"p95":13624.2,"max":16010.4}[k],
                    (d.get("latency_ms") or {}).get(k,""),(h.get("latency_ms") or {}).get(k,"")])
    for k in ["input_mean","output_mean","input_total","output_total"]:
        w.writerow([f"tokens_{k}","","",(d.get("tokens") or {}).get(k,""),(h.get("tokens") or {}).get(k,"")])
    w.writerow(["groq_calls",0,0,0,0]); w.writerow(["nvidia_120b_calls",0,0,0,0])
    w.writerow(["nvidia_20b_calls","",52,len(dev),len(hold)])
    w.writerow(["verdict","NOT ACCEPTED","NOT ACCEPTED (dev gate)","",
                M.get("acceptance",{}).get("verdict","HOLDOUT NOT RUN")])

def show(tag,S):
    print(f"\n=== {tag} (scored={S['scored']}) ===")
    print(f"  TP={S['TP']} FP={S['FP']} TN={S['TN']} FN={S['FN']}  parse_ok={S['parse_ok']}/{S['scored']}")
    if S["parse_errors"]: print(f"  parse errors: {S['parse_errors']}")
    print(f"  precision={S['compound_precision']} recall={S['compound_recall']} f1={S['compound_f1']}")
    print(f"  specificity={S['simple_specificity']} accuracy={S['routing_accuracy']}")
    print(f"  fp_rate={S['false_positive_decomposition_rate']} fn_rate={S['false_negative_compound_rate']}")
    print(f"  latency {S['latency_ms']}\n  tokens {S['tokens']}")
    print(f"  unit counts {S['evidence_unit_count_distribution']}\n  codes {S['reason_code_distribution']}")
    print(f"  FP: {S['FP_cases']}\n  FN: {S['FN_cases']}")
    print("  by category:")
    for c,o in sorted(S["by_category"].items()):
        print(f"    {c:<34} {o['metric_name']:<12} {o['correct']}/{o['n']} = {o['score']}"
              + (f"   miss={o['wrong_ids']}" if o["wrong_ids"] else ""))
if dev:
    show("DEVELOPMENT (52 adjudicated, 50 scored)", M["development"])
    print("\n  diagnostic cases:")
    for k,v in M["diagnostic_cases"].items():
        print(f"    {k} gt={v['ground_truth']:<8} v4={v['v4']:<9} units={v['units']} "
              f"code={str(v['reason_code']):<32} {v['status']}")
if hold:
    show("HOLDOUT (40 frozen unseen)", M["holdout"])
    print(f"\n  acceptance: {json.dumps(M['acceptance'],indent=2)}")
else:
    print("\n  HOLDOUT: NOT RUN")
