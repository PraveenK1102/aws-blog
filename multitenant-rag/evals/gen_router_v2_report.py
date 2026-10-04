"""compound-router-v2 — metrics, exports and error analysis. No model calls."""
import json, os, csv, statistics as st

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
EXP="compound-router-v2"
MAN=json.load(open(os.path.join(OUTD,"router_v2_manifest.json")))
FP=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))
CORPUS={c["case_id"]:c for c in json.load(open(os.path.join(HERE,"corpus60.json")))["cases"]}

rows={}
for l in open(os.path.join(OUTD,"router_v2_results.jsonl")):
    r=json.loads(l)
    if r.get("fingerprint_hash")==FP["fingerprint_hash"]: rows[r["case_id"]]=r
ORDER=MAN["ground_truth_compound"]+MAN["ground_truth_simple"]
recs=[rows[c] for c in ORDER if c in rows]

TP=[r for r in recs if r["ground_truth_compound"] and r["predicted_compound"] is True]
FP_=[r for r in recs if not r["ground_truth_compound"] and r["predicted_compound"] is True]
TN=[r for r in recs if not r["ground_truth_compound"] and r["predicted_compound"] is False]
FN=[r for r in recs if r["ground_truth_compound"] and r["predicted_compound"] is False]
UNS=[r for r in recs if r["predicted_compound"] is None]

def d(a,b): return (a/b) if b else None
prec=d(len(TP),len(TP)+len(FP_)); rec=d(len(TP),len(TP)+len(FN))
f1=(2*prec*rec/(prec+rec)) if (prec and rec) else (0.0 if (prec is not None and rec is not None) else None)
spec=d(len(TN),len(TN)+len(FP_)); acc=d(len(TP)+len(TN),len(recs))
fpr=d(len(FP_),len(FP_)+len(TN)); fnr=d(len(FN),len(FN)+len(TP))

lat=[r["latency_ms"] for r in recs if r.get("latency_ms")]
def pct(v,p):
    if not v: return None
    s=sorted(v); k=(len(s)-1)*p/100
    lo,hi=int(k),min(int(k)+1,len(s)-1)
    return round(s[lo]+(s[hi]-s[lo])*(k-lo),1)
tin=[r["input_tokens"] for r in recs if r.get("input_tokens")]
tout=[r["output_tokens"] for r in recs if r.get("output_tokens")]

M={"experiment":EXP,"fingerprint_hash":FP["fingerprint_hash"],
 "manifest_fingerprint":MAN["manifest_fingerprint"],"prompt_sha":FP["prompt_sha"],
 "cases_classified":len(recs),"positives":len([r for r in recs if r["ground_truth_compound"]]),
 "negatives":len([r for r in recs if not r["ground_truth_compound"]]),
 "true_positives":len(TP),"false_positives":len(FP_),"true_negatives":len(TN),
 "false_negatives":len(FN),"unscored_provider_error":len(UNS),
 "compound_precision":prec,"compound_recall":rec,"compound_f1":f1,
 "simple_specificity":spec,"routing_accuracy":acc,
 "false_positive_decomposition_rate":fpr,"false_negative_compound_rate":fnr,
 "parse_ok_count":len([r for r in recs if r.get("parse_ok")]),
 "parse_failures":[{"case_id":r["case_id"],"parse_error":r.get("parse_error")}
                   for r in recs if not r.get("parse_ok")],
 "latency_ms":{"mean":round(st.mean(lat),1) if lat else None,"p50":pct(lat,50),
               "p95":pct(lat,95),"max":max(lat) if lat else None},
 "tokens":{"input_mean":round(st.mean(tin),1) if tin else None,
           "output_mean":round(st.mean(tout),1) if tout else None,
           "input_total":sum(tin),"output_total":sum(tout)},
 "reason_code_distribution":{},
 "provider":{"nvidia_20b_calls":len(recs),"nvidia_120b_calls":0,"groq_calls":0,
             "errors":len(UNS)},
 "success_target":{"required_recall":"3/3","required_specificity":">=0.90",
   "actual_recall":f"{len(TP)}/{len(TP)+len(FN)}","actual_specificity":spec,
   "verdict":("PASS" if (rec==1.0 and spec is not None and spec>=0.90) else "FAIL")}}
for r in recs:
    k=r.get("reason_code") or "unparsed"
    M["reason_code_distribution"][k]=M["reason_code_distribution"].get(k,0)+1

# v1 comparison: v1 decomposed every control -> unnecessary decompositions avoided
V1_FP_RATE=1.0   # v1 classified 3/3 controls compound
M["v1_comparison"]={
 "v1_behaviour":"all 6 preflight cases compound (3/3 controls over-decomposed)",
 "v1_false_positive_rate_on_controls":V1_FP_RATE,
 "v2_false_positives_of_negatives":f"{len(FP_)}/{len(TN)+len(FP_)}",
 "unnecessary_decompositions_v1_would_cause":len(TN)+len(FP_),
 "unnecessary_decompositions_v2_causes":len(FP_),
 "unnecessary_decompositions_avoided":(len(TN)+len(FP_))-len(FP_),
 "v1_measured_overhead_per_unnecessary_decomposition":{"latency_s":2.6,"tokens":462},
 "estimated_latency_s_avoided":round(((len(TN)+len(FP_))-len(FP_))*2.6,1),
 "estimated_tokens_avoided":((len(TN)+len(FP_))-len(FP_))*462,
 "note":"analyzer-call overhead only; excludes the +(N-1) retrieval operations each unnecessary decomposition would add"}
json.dump(M,open(os.path.join(OUTD,"router_v2_metrics.json"),"w"),indent=2)

# ---------- exports ----------
full=[]
for r in recs:
    c=CORPUS[r["case_id"]]
    full.append({"experiment":EXP,"fingerprint_hash":FP["fingerprint_hash"],
      "case_id":r["case_id"],"route":c["route"],"title":c["title"],
      "question":r["question"],
      "ground_truth_compound":r["ground_truth_compound"],
      "predicted_compound":r["predicted_compound"],
      "correct":r["predicted_compound"]==r["ground_truth_compound"],
      "outcome":("TP" if r in TP else "FP" if r in FP_ else "TN" if r in TN else "FN" if r in FN else "UNSCORED"),
      "reason_code":r["reason_code"],"information_needs":r["information_needs"],
      "information_need_count":r["information_need_count"],
      "parse_ok":r["parse_ok"],"parse_error":r["parse_error"],
      "latency_ms":r["latency_ms"],"input_tokens":r["input_tokens"],
      "output_tokens":r["output_tokens"],"provider_status":r["provider_status"]})
with open(f"{OUT}/{EXP}-full.jsonl","w",encoding="utf-8") as f:
    for r in full: f.write(json.dumps(r,ensure_ascii=False)+"\n")
cols=["case_id","route","question","ground_truth_compound","predicted_compound","correct",
 "outcome","reason_code","information_needs","information_need_count","latency_ms",
 "input_tokens","output_tokens","provider_status"]
with open(f"{OUT}/{EXP}-full.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore"); w.writeheader()
    for r in full:
        row=dict(r); row["information_needs"]=" | ".join(r["information_needs"] or []); w.writerow(row)
with open(f"{OUT}/{EXP}-metrics.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["metric","value"])
    flat=[("cases_classified",M["cases_classified"]),("positives",M["positives"]),
      ("negatives",M["negatives"]),("true_positives",M["true_positives"]),
      ("false_positives",M["false_positives"]),("true_negatives",M["true_negatives"]),
      ("false_negatives",M["false_negatives"]),
      ("compound_precision",M["compound_precision"]),("compound_recall",M["compound_recall"]),
      ("compound_f1",M["compound_f1"]),("simple_specificity",M["simple_specificity"]),
      ("routing_accuracy",M["routing_accuracy"]),
      ("false_positive_decomposition_rate",M["false_positive_decomposition_rate"]),
      ("false_negative_compound_rate",M["false_negative_compound_rate"]),
      ("latency_mean_ms",M["latency_ms"]["mean"]),("latency_p50_ms",M["latency_ms"]["p50"]),
      ("latency_p95_ms",M["latency_ms"]["p95"]),("latency_max_ms",M["latency_ms"]["max"]),
      ("input_tokens_mean",M["tokens"]["input_mean"]),("output_tokens_mean",M["tokens"]["output_mean"]),
      ("input_tokens_total",M["tokens"]["input_total"]),("output_tokens_total",M["tokens"]["output_total"]),
      ("nvidia_20b_calls",M["provider"]["nvidia_20b_calls"]),
      ("nvidia_120b_calls",0),("groq_calls",0),
      ("unnecessary_decompositions_avoided",M["v1_comparison"]["unnecessary_decompositions_avoided"]),
      ("success_target_verdict",M["success_target"]["verdict"])]
    for k,v in flat: w.writerow([k,v])

print(f"cases={len(recs)}  TP={len(TP)} FP={len(FP_)} TN={len(TN)} FN={len(FN)} unscored={len(UNS)}")
print(f"precision={prec} recall={rec} f1={f1} specificity={spec} accuracy={acc}")
print(f"fp_rate={fpr} fn_rate={fnr}")
print(f"latency mean={M['latency_ms']['mean']} p50={M['latency_ms']['p50']} p95={M['latency_ms']['p95']} max={M['latency_ms']['max']}")
print(f"reason codes: {M['reason_code_distribution']}")
print(f"VERDICT = {M['success_target']['verdict']}")
if FP_:
    print("\nFALSE POSITIVES:")
    for r in FP_: print(f"  {r['case_id']} [{CORPUS[r['case_id']]['route']}] code={r['reason_code']} needs={r['information_need_count']}\n     Q: {r['question']}\n     needs: {r['information_needs']}")
if FN:
    print("\nFALSE NEGATIVES:")
    for r in FN: print(f"  {r['case_id']} code={r['reason_code']}\n     Q: {r['question']}")
if TP:
    print("\nTRUE COMPOUND information needs:")
    for r in TP: print(f"  {r['case_id']}: {r['information_needs']}")
