"""Exports for compound-router-v2-high-recall-holdout-v1. No provider calls."""
import json, os, csv
HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
BASE="compound-router-v2-high-recall-holdout"
M=json.load(open(os.path.join(OUTD,"v2_holdout_metrics.json")))
fp=json.load(open(os.path.join(OUTD,"v2_holdout_fingerprint.json")))
rows=[json.loads(l) for l in open(os.path.join(OUTD,"v2_holdout_results.jsonl"))]
rows=[r for r in rows if r["fingerprint_hash"]==fp["fingerprint_hash"]]

with open(f"{OUT}/{BASE}-results.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f)
    w.writerow(["holdout_case_id","question","category","ground_truth","reference_need_count",
                "predicted_compound","outcome","information_need_count","information_needs",
                "reason_code","parse_ok","latency_ms","input_tokens","output_tokens","provider_status"])
    for r in sorted(rows,key=lambda x:x["id"]):
        gt=r["ground_truth"]; p=r["predicted_compound"]
        o="TP" if (gt=="compound" and p) else "FN" if gt=="compound" else "FP" if p else "TN"
        w.writerow([r["id"],r["question"],r["category"],gt,r["reference_need_count"],p,o,
                    r["information_need_count"]," | ".join(r["information_needs"] or []),
                    r["reason_code"],r["parse_ok"],r["latency_ms"],r["input_tokens"],
                    r["output_tokens"],r["provider_status"]])

with open(f"{OUT}/{BASE}-metrics.csv","w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["metric","value","threshold","pass"])
    T=M["acceptance"]["thresholds"]; C=M["acceptance"]["core_results"]
    for k,mk,tk in [("compound_recall","compound_recall","recall"),
                    ("simple_specificity","simple_specificity","specificity"),
                    ("compound_precision","compound_precision","precision")]:
        w.writerow([k,M[mk],T[tk],C[tk]])
    for k in ["compound_f1","routing_accuracy","false_positive_decomposition_rate",
              "false_negative_compound_rate","TP","FP","TN","FN","scored","parse_ok"]:
        w.writerow([k,M[k],"",""])
    for k,g in M["acceptance"]["category_guards"].items():
        w.writerow([f"guard_{k}",g["actual"],g["required"],g["pass"]])
    w.writerow(["verdict",M["acceptance"]["verdict"],"",""])
    for k in ["mean","p50","p95","max"]: w.writerow([f"latency_{k}_ms",M["latency_ms"][k],"",""])
    for k,v in M["tokens"].items(): w.writerow([f"tokens_{k}",v,"",""])
    for k,v in M["provider"].items(): w.writerow([k,v,"",""])
    w.writerow(["holdout_sha256",M["holdout_sha256"],"",""])
    w.writerow(["router_prompt_sha",M["router_prompt_sha"],"",""])
    for c,o in sorted(M["by_category"].items()):
        w.writerow([f"category_{c}_{o['metric']}",o["score"],"",f"{o['correct']}/{o['n']}"])

print(f"wrote {BASE}-results.csv ({len(rows)} rows) and {BASE}-metrics.csv")
print(f"verdict={M['acceptance']['verdict']}  recall={M['compound_recall']} "
      f"specificity={M['simple_specificity']} precision={M['compound_precision']}")
