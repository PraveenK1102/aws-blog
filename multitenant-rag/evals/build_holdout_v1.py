"""Build, validate and FREEZE compound-router-holdout-v1. No provider is imported."""
import json, os, re, csv, hashlib, unicodedata, sys
import holdout_v1_cases as HC

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
POLICY="independent-retrieval-needs-v1"

def norm(q): return re.sub(r"\s+"," ",unicodedata.normalize("NFKC",q or "").strip())
def toks(q): return set(re.findall(r"[a-z0-9-]+", norm(q).lower()))

recs=[]
for i,(q,gt,cnt,cat,refq,reason) in enumerate(HC.C,1):
    n=norm(q)
    recs.append({"holdout_case_id":f"hold-{i:03d}","question":q,"normalized_question":n,
      "normalized_question_hash":hashlib.sha256(n.encode()).hexdigest()[:16],
      "ground_truth":gt,"independent_retrieval_need_count":cnt,
      "reference_retrieval_queries":refq,"category":cat,"annotation_reason":reason})

dev=[json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")]
devnorm={r["normalized_question"] for r in dev}
devhash={r["normalized_question_hash"] for r in dev}

# ---------------- quality gates ----------------
errs=[]
if len(recs)!=40: errs.append(f"expected 40 cases, got {len(recs)}")
dist={l:sum(1 for r in recs if r["ground_truth"]==l) for l in ("simple","compound")}
if dist["simple"]!=20 or dist["compound"]!=20: errs.append(f"unbalanced: {dist}")
if any(r["ground_truth"]=="ambiguous" for r in recs): errs.append("AMBIGUOUS not allowed in holdout")
for r in recs:
    if r["ground_truth"] not in ("simple","compound"): errs.append(f"{r['holdout_case_id']}: bad label")
    if r["ground_truth"]=="compound" and r["independent_retrieval_need_count"]<2:
        errs.append(f"{r['holdout_case_id']}: compound need_count<2")
    if r["ground_truth"]=="simple" and r["independent_retrieval_need_count"]!=1:
        errs.append(f"{r['holdout_case_id']}: simple need_count!=1")
    if len(r["reference_retrieval_queries"])!=r["independent_retrieval_need_count"]:
        errs.append(f"{r['holdout_case_id']}: reference queries != need count")
    if not r["annotation_reason"].strip(): errs.append(f"{r['holdout_case_id']}: no reason")
# no internal duplicates
seen={}
for r in recs: seen.setdefault(r["normalized_question_hash"],[]).append(r["holdout_case_id"])
for h,m in seen.items():
    if len(m)>1: errs.append(f"internal duplicate {m}")
# no exact overlap with the development set
for r in recs:
    if r["normalized_question"] in devnorm or r["normalized_question_hash"] in devhash:
        errs.append(f"{r['holdout_case_id']}: EXACT OVERLAP with dev set")
# near-duplicate check (Jaccard) against dev set
near=[]
for r in recs:
    a=toks(r["question"]); best=(0.0,None)
    for d in dev:
        b=toks(d["question"]); j=len(a&b)/len(a|b) if (a|b) else 0
        if j>best[0]: best=(j,d["case_id"])
    if best[0]>=0.60: errs.append(f"{r['holdout_case_id']}: near-duplicate of {best[1]} (J={best[0]:.2f})")
    elif best[0]>=0.40: near.append((r["holdout_case_id"],best[1],round(best[0],2)))
if errs:
    print("HOLDOUT VALIDATION FAILED — not freezing:"); [print(f"  - {e}") for e in errs]; sys.exit(1)
print(f"validation: OK  (40 cases, 20/20, no internal duplicates, no dev overlap)")
if near:
    print(f"  highest same-domain similarities (below the 0.60 reject bar, reported for transparency):")
    for a,b,j in sorted(near,key=lambda x:-x[2])[:6]: print(f"    {a} ~ {b}  J={j}")

# ---------------- anti-cue counts ----------------
has_and=lambda q: " and " in q.lower()
cue={"simple_with_and":sum(1 for r in recs if r["ground_truth"]=="simple" and has_and(r["question"])),
     "compound_without_and":sum(1 for r in recs if r["ground_truth"]=="compound" and not has_and(r["question"])),
     "compound_with_and":sum(1 for r in recs if r["ground_truth"]=="compound" and has_and(r["question"])),
     "simple_without_and":sum(1 for r in recs if r["ground_truth"]=="simple" and not has_and(r["question"])),
     "single_entity_multi_attribute":sum(1 for r in recs if r["category"]=="single_entity_multi_attribute"),
     "contrast_verification":sum(1 for r in recs if r["category"]=="contrast_verification"),
     "multi_entity_compound":sum(1 for r in recs if r["category"]=="different_entity")}
catdist={}
for r in recs: catdist[r["category"]]=catdist.get(r["category"],0)+1
# a naive and-detector must NOT solve this holdout
nt=cue["compound_with_and"]; nf=cue["simple_with_and"]
nfn=cue["compound_without_and"]; ntn=cue["simple_without_and"]
naive={"precision":round(nt/(nt+nf),4) if nt+nf else None,
       "recall":round(nt/(nt+nfn),4) if nt+nfn else None,
       "specificity":round(ntn/(ntn+nf),4) if ntn+nf else None,
       "accuracy":round((nt+ntn)/len(recs),4)}
if naive["accuracy"] is not None and naive["accuracy"]>=0.90:
    print(f"  WARNING: a naive and-detector scores accuracy {naive['accuracy']} — holdout is cue-separable")

payload=json.dumps(recs,sort_keys=True,ensure_ascii=False).encode()
MSHA=hashlib.sha256(payload).hexdigest()
meta={"holdout":"compound-router-holdout-v1","annotation_policy_version":POLICY,
 "manifest_sha256":MSHA,"case_count":len(recs),
 "simple_count":dist["simple"],"compound_count":dist["compound"],"ambiguous_count":0,
 "category_distribution":catdist,"cue_counts":cue,
 "naive_and_detector_baseline":naive,
 "authored_before_any_router_v3_call":True,
 "dev_set_exact_overlap":0,"near_duplicate_reject_threshold":0.60,
 "frozen":"labels fixed before any router-v3 call; must not be altered after seeing output"}
json.dump(meta,open(os.path.join(OUTD,"router_holdout_v1_meta.json"),"w"),indent=2)
with open(f"{OUT}/compound-router-holdout-v1.jsonl","w",encoding="utf-8") as f:
    for r in recs: f.write(json.dumps(r,ensure_ascii=False)+"\n")
cols=["holdout_case_id","question","normalized_question_hash","ground_truth",
      "independent_retrieval_need_count","reference_retrieval_queries","category","annotation_reason"]
with open(f"{OUT}/compound-router-holdout-v1.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=cols,extrasaction="ignore"); w.writeheader()
    for r in recs:
        row=dict(r); row["reference_retrieval_queries"]=" | ".join(r["reference_retrieval_queries"]); w.writerow(row)
print(f"\nHOLDOUT FROZEN  sha256={MSHA[:32]}...")
print(f"  simple={dist['simple']} compound={dist['compound']} ambiguous=0")
print(f"  categories: {catdist}")
print(f"  cue counts: {cue}")
print(f"  naive and-detector on this holdout: {naive}")
