"""Ground-truth audit + ZERO-COST rescore of the already-persisted router-v2 predictions.

No provider is imported. No NVIDIA/Groq/judge/retrieval/LangGraph call is possible here.
"""
import json, os, re, csv, hashlib, unicodedata, sys, subprocess

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
POLICY="independent-retrieval-needs-v1"
LABELS=("simple","compound","ambiguous")

import router_gt_annotations as ANN

def normalize(q):
    q=unicodedata.normalize("NFKC", q or "").strip()
    return re.sub(r"\s+", " ", q)

# ---------- stage 1: build manifest from question text + annotations ----------
prev=json.load(open(os.path.join(OUTD,"router_v2_manifest.json")))
GEN=set(prev["ground_truth_compound"]+prev["ground_truth_simple"])
EXCLUDED=set(prev["excluded_global_search"])
corpus={c["case_id"]:c for c in json.load(open(os.path.join(HERE,"corpus60.json")))["cases"]}

recs=[]
for cid in sorted(GEN):
    q=corpus[cid]["question"]; n=normalize(q)
    lab,cnt,needs,rule,reason=ANN.A[cid]
    recs.append({"case_id":cid,"question":q,"normalized_question":n,
      "normalized_question_hash":hashlib.sha256(n.encode()).hexdigest()[:16],
      "ground_truth":lab,"independent_retrieval_need_count":cnt,
      "atomic_information_needs":needs,"decision_rule":rule,"label_reason":reason})

# duplicate groups by normalized hash
by_hash={}
for r in recs: by_hash.setdefault(r["normalized_question_hash"],[]).append(r["case_id"])
dupes={h:sorted(v) for h,v in by_hash.items() if len(v)>1}
for i,(h,members) in enumerate(sorted(dupes.items()),1):
    for r in recs:
        if r["normalized_question_hash"]==h: r["duplicate_group_id"]=f"dup-{i:02d}"

# ---------- stage 2: VALIDATION — stop on any failure ----------
errs=[]
if len(recs)!=52: errs.append(f"expected 52 generative cases, got {len(recs)}")
if len({r['case_id'] for r in recs})!=len(recs): errs.append("duplicate case_id")
if GEN & EXCLUDED: errs.append("global-search case present")
if len(EXCLUDED)!=8: errs.append(f"expected 8 excluded global cases, got {len(EXCLUDED)}")
for r in recs:
    if r["ground_truth"] not in LABELS: errs.append(f"{r['case_id']}: bad label")
    if r["ground_truth"]=="compound" and r["independent_retrieval_need_count"]<2:
        errs.append(f"{r['case_id']}: compound with need_count<2")
    if r["ground_truth"]=="simple" and r["independent_retrieval_need_count"]!=1:
        errs.append(f"{r['case_id']}: simple with need_count!=1")
    if len(r["atomic_information_needs"])<1: errs.append(f"{r['case_id']}: no atomic needs")
# THE invariant that made the old manifest invalid
for h,members in dupes.items():
    labs={r["ground_truth"] for r in recs if r["normalized_question_hash"]==h}
    if len(labs)!=1:
        errs.append(f"DUPLICATE LABEL CONFLICT {members}: {labs}")
if errs:
    print("VALIDATION FAILED — not rescoring:"); [print(f"  - {e}") for e in errs]; sys.exit(1)
print(f"validation: OK  ({len(recs)} cases, {len(dupes)} duplicate groups, no label conflicts)")

# ---------- stage 3: freeze ----------
dist={l:sum(1 for r in recs if r["ground_truth"]==l) for l in LABELS}
payload=json.dumps(recs,sort_keys=True,ensure_ascii=False).encode()
MSHA=hashlib.sha256(payload).hexdigest()
meta={"annotation_policy_version":POLICY,"manifest_sha256":MSHA,
 "case_count":len(recs),"unique_question_count":len(by_hash),
 "duplicate_question_groups":{f"dup-{i:02d}":m for i,(h,m) in enumerate(sorted(dupes.items()),1)},
 "duplicate_group_count":len(dupes),
 "simple_count":dist["simple"],"compound_count":dist["compound"],"ambiguous_count":dist["ambiguous"],
 "excluded_global_search":sorted(EXCLUDED),
 "annotation_inputs":"question text only; expected answers, router predictions, router reason codes, "
                     "baseline/judge scores and the previous crude labels were NOT inputs",
 "route_used_for_labels":False,
 "supersedes":{"manifest_fingerprint":prev["manifest_fingerprint"],
               "policy":"case-018/020/022 compound, all other 49 simple (assumed, not adjudicated)"}}
json.dump(meta,open(os.path.join(OUTD,"router_gt_v2_meta.json"),"w"),indent=2)
with open(f"{OUT}/compound-router-groundtruth-v2.jsonl","w",encoding="utf-8") as f:
    for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
gcols=["case_id","question","normalized_question_hash","duplicate_group_id","ground_truth",
       "independent_retrieval_need_count","atomic_information_needs","decision_rule","label_reason"]
with open(f"{OUT}/compound-router-groundtruth-v2.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=gcols,extrasaction="ignore"); w.writeheader()
    for r in recs:
        row=dict(r); row["atomic_information_needs"]=" | ".join(r["atomic_information_needs"])
        row.setdefault("duplicate_group_id",""); w.writerow(row)
print(f"manifest FROZEN  sha256={MSHA[:32]}...")
print(f"  simple={dist['simple']} compound={dist['compound']} ambiguous={dist['ambiguous']}  unique_questions={len(by_hash)}")
print(f"  duplicate groups: {meta['duplicate_question_groups']}")

# ---------- stage 4: ZERO-COST rescore of persisted predictions ----------
assert "nvidia_provider" not in sys.modules and "router_v2" not in sys.modules, "provider must not be loaded"
fp=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))["fingerprint_hash"]
raw=[json.loads(l) for l in open(os.path.join(OUTD,"router_v2_results.jsonl"))]
preds={r["case_id"]:r for r in raw if r["fingerprint_hash"]==fp}
assert len(preds)==52, f"expected 52 persisted predictions, got {len(preds)}"
GT={r["case_id"]:r for r in recs}

scored=[r for r in recs if r["ground_truth"]!="ambiguous"]
TP=[r["case_id"] for r in scored if r["ground_truth"]=="compound" and preds[r["case_id"]]["predicted_compound"] is True]
FN=[r["case_id"] for r in scored if r["ground_truth"]=="compound" and preds[r["case_id"]]["predicted_compound"] is False]
FP_=[r["case_id"] for r in scored if r["ground_truth"]=="simple" and preds[r["case_id"]]["predicted_compound"] is True]
TN=[r["case_id"] for r in scored if r["ground_truth"]=="simple" and preds[r["case_id"]]["predicted_compound"] is False]
assert len(TP)+len(FN)+len(FP_)+len(TN)==len(scored), "join must cover every non-ambiguous case exactly once"

def dv(a,b): return round(a/b,4) if b else None
prec=dv(len(TP),len(TP)+len(FP_)); rec=dv(len(TP),len(TP)+len(FN))
f1=round(2*prec*rec/(prec+rec),4) if (prec and rec) else 0.0
spec=dv(len(TN),len(TN)+len(FP_)); acc=dv(len(TP)+len(TN),len(scored))
fpr=dv(len(FP_),len(FP_)+len(TN)); fnr=dv(len(FN),len(FN)+len(TP))

OLD={"positives":3,"negatives":49,"ambiguous":0,"precision":0.1667,"recall":1.0,
     "specificity":0.6939,"accuracy":0.7115,"TP":3,"FP":15,"TN":34,"FN":0}
TH={"recall":0.90,"specificity":0.90,"precision":0.80}
passed=(rec>=TH["recall"] and spec>=TH["specificity"] and prec>=TH["precision"])
verdict=("PROMISING — REQUIRES UNSEEN HOLDOUT VALIDATION" if passed else "NOT ACCEPTED")

# original-15-FP disposition (derived AFTER annotation; does not change labels)
ORIG_FP=["case-002","case-003","case-004","case-005","case-007","case-008","case-009","case-023",
         "case-025","case-030","case-041","case-046","case-047","case-056","case-059"]
disp={"A_label_was_wrong":[],"B_router_really_wrong":[],"C_ambiguous":[]}
for cid in ORIG_FP:
    g=GT[cid]["ground_truth"]
    disp["C_ambiguous" if g=="ambiguous" else ("A_label_was_wrong" if g=="compound" else "B_router_really_wrong")].append(cid)

M={"experiment":"compound-router-v2-rescored","annotation_policy_version":POLICY,
 "manifest_sha256":MSHA,"router_prediction_fingerprint":fp,
 "provider_calls":{"nvidia_20b":0,"nvidia_120b":0,"groq":0,"retrieval":0,"langgraph":0},
 "scored_cases":len(scored),"ambiguous_excluded":dist["ambiguous"],
 "positives":dist["compound"],"negatives":dist["simple"],
 "TP":len(TP),"FP":len(FP_),"TN":len(TN),"FN":len(FN),
 "TP_cases":TP,"FP_cases":FP_,"FN_cases":FN,
 "compound_precision":prec,"compound_recall":rec,"compound_f1":f1,
 "simple_specificity":spec,"routing_accuracy":acc,
 "false_positive_decomposition_rate":fpr,"false_negative_compound_rate":fnr,
 "diagnostic_thresholds":TH,
 "threshold_results":{"recall":rec>=TH["recall"],"specificity":spec>=TH["specificity"],
                      "precision":prec>=TH["precision"]},
 "verdict":verdict,"original_scoring_superseded":OLD,
 "original_15_false_positive_disposition":{k:{"count":len(v),"cases":v} for k,v in disp.items()}}
json.dump(M,open(os.path.join(OUTD,"router_v2_rescored_metrics.json"),"w"),indent=2)

# exports
with open(f"{OUT}/compound-router-v2-rescored.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f)
    w.writerow(["case_id","question","old_ground_truth","adjudicated_ground_truth","duplicate_group_id",
                "predicted_compound","reason_code","outcome_old","outcome_adjudicated",
                "independent_retrieval_need_count","decision_rule","label_reason"])
    oldpos=set(prev["ground_truth_compound"])
    for r in recs:
        cid=r["case_id"]; p=preds[cid]["predicted_compound"]; g=r["ground_truth"]
        o_old="TP" if (cid in oldpos and p) else "FN" if (cid in oldpos and not p) else "FP" if p else "TN"
        o_new=("EXCLUDED_AMBIGUOUS" if g=="ambiguous" else
               "TP" if (g=="compound" and p) else "FN" if g=="compound" else "FP" if p else "TN")
        w.writerow([cid,r["question"],"compound" if cid in oldpos else "simple",g,
                    r.get("duplicate_group_id",""),p,preds[cid]["reason_code"],o_old,o_new,
                    r["independent_retrieval_need_count"],r["decision_rule"],r["label_reason"]])
with open(f"{OUT}/compound-router-v2-rescored-metrics.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["metric","original_labels","adjudicated_labels"])
    for k,o,n in [("positives",OLD["positives"],dist["compound"]),("negatives",OLD["negatives"],dist["simple"]),
      ("ambiguous_excluded",0,dist["ambiguous"]),("scored_cases",52,len(scored)),
      ("TP",OLD["TP"],len(TP)),("FP",OLD["FP"],len(FP_)),("TN",OLD["TN"],len(TN)),("FN",OLD["FN"],len(FN)),
      ("compound_precision",OLD["precision"],prec),("compound_recall",OLD["recall"],rec),
      ("compound_f1",0.2857,f1),("simple_specificity",OLD["specificity"],spec),
      ("routing_accuracy",OLD["accuracy"],acc),("false_positive_decomposition_rate",0.3061,fpr),
      ("false_negative_compound_rate",0.0,fnr),("verdict","FAIL",verdict),
      ("nvidia_20b_calls",58,0),("nvidia_120b_calls",0,0),("groq_calls",0,0)]:
        w.writerow([k,o,n])

print(f"\n=== ZERO-COST RESCORE (provider calls: 0) ===")
print(f"scored={len(scored)}  ambiguous_excluded={dist['ambiguous']}")
print(f"TP={len(TP)} FP={len(FP_)} TN={len(TN)} FN={len(FN)}")
print(f"precision {OLD['precision']} -> {prec}")
print(f"recall      {OLD['recall']} -> {rec}")
print(f"specificity {OLD['specificity']} -> {spec}")
print(f"accuracy    {OLD['accuracy']} -> {acc}")
print(f"f1 0.2857 -> {f1}   fp_rate {0.3061} -> {fpr}   fn_rate 0.0 -> {fnr}")
print(f"thresholds recall>=.90 {rec>=.9} | specificity>=.90 {spec>=.9} | precision>=.80 {prec>=.8}")
print(f"VERDICT: {verdict}")
print(f"\noriginal-15-FP disposition:")
for k,v in disp.items(): print(f"  {k}: {len(v)}  {v}")
print(f"\nremaining false positives after adjudication: {FP_}")
