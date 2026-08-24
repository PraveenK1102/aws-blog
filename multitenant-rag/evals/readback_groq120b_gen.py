"""Mandatory LangSmith read-back for groq120b-routed-generation-validation-v1.
Proves the traces are queryable server-side, not merely emitted locally."""
import csv, json, os, time, warnings
warnings.filterwarnings("ignore")
import boto3
from langsmith import Client

OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
OUTD=os.path.join(os.path.dirname(os.path.abspath(__file__)),"output")
PROJECT="multitenant-rag-dev-groq-observability-v1"
EXPERIMENT="groq120b-routed-generation-validation-v1"
FP=json.load(open(os.path.join(OUTD,"groq120b_gen_stats.json")))["fingerprint"]["fingerprint"]

api=json.loads(boto3.client("secretsmanager",region_name="ap-south-1")
    .get_secret_value(SecretId="multitenant/langsmith")["SecretString"])["api_key"]
LS=Client(api_key=api)

# Roots carry experiment/fingerprint metadata. Child spans do NOT (they inherit
# context, not metadata), so they must be fetched by parent — filtering the whole
# project list on experiment metadata silently drops every child span.
roots=[]
for attempt in range(6):
    # No `limit` -> the generator paginates internally (API caps each page at 100).
    roots=[r for r in LS.list_runs(project_name=PROJECT, is_root=True)
           if r.name=="generation_validation_request"
           and (r.extra or {}).get("metadata",{}).get("fingerprint")==FP]
    if len(roots)>=18: break
    print(f"  attempt {attempt+1}: {len(roots)} roots visible — waiting for ingestion")
    time.sleep(12)

children=[]
for r in roots:
    try:
        children += list(LS.list_runs(project_name=PROJECT, parent_run_id=r.id))
    except Exception as e:
        print(f"  child fetch failed for {r.id}: {type(e).__name__}")
runs=roots+children

gens=[r for r in runs if r.name=="generation"]
# children carry the experiment tag only via inheritance; fetch by parent to be exact
by_parent={}
for r in runs: by_parent.setdefault(str(r.parent_run_id) if r.parent_run_id else None,[]).append(r)
print(f"read-back: total runs matched={len(runs)}  roots={len(roots)}  generation spans={len(gens)}")

rows=[]
for r in sorted(roots,key=lambda x:(x.extra or {}).get("metadata",{}).get("case_id","")):
    md=(r.extra or {}).get("metadata",{}) or {}
    kids=by_parent.get(str(r.id),[])
    gk=next((k for k in kids if k.name=="generation"),None)
    gmd=((gk.extra or {}).get("metadata",{}) or {}) if gk else {}
    rows.append({"case_id":md.get("case_id"),"run_id":str(r.id),
        "project":PROJECT,"experiment":EXPERIMENT,"fingerprint":FP,
        "root_name":r.name,"child_span_count":len(kids),
        "generation_span_present":gk is not None,
        "generation_run_id":str(gk.id) if gk else "",
        "status":r.status,"error":r.error or "",
        "server_start":str(r.start_time),"server_end":str(r.end_time),
        "server_duration_ms":(round((r.end_time-r.start_time).total_seconds()*1000,1)
                              if r.end_time and r.start_time else ""),
        "model":md.get("model"),"provider":md.get("provider"),
        "context_count":md.get("context_count"),
        "context_sha256_prefix":md.get("context_sha256"),
        "routed_answer_path":md.get("routed_answer_path"),
        "adjudicated_ground_truth":md.get("adjudicated_ground_truth"),
        "deliberate_pacing_s":md.get("deliberate_pacing_s"),
        "root_wall_latency_ms":md.get("wall_latency_ms"),
        "provider_latency_ms":gmd.get("provider_latency_ms"),
        "input_tokens":gmd.get("input_tokens"),"output_tokens":gmd.get("output_tokens"),
        "retry_count":gmd.get("retry_count"),
        "rl_remaining_tokens":gmd.get("rl_x-ratelimit-remaining-tokens"),
        "rl_limit_tokens":gmd.get("rl_x-ratelimit-limit-tokens")})

with open(f"{OUT}/groq120b-generation-langsmith-export.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

# span-level metrics
sm=[]
for r in sorted(runs,key=lambda x:(x.name,str(x.start_time))):
    md=(r.extra or {}).get("metadata",{}) or {}
    sm.append({"span_name":r.name,"run_type":r.run_type,"run_id":str(r.id),
        "parent_run_id":str(r.parent_run_id) if r.parent_run_id else "",
        "case_id":md.get("case_id",""),"status":r.status,"error":r.error or "",
        "server_duration_ms":(round((r.end_time-r.start_time).total_seconds()*1000,1)
                              if r.end_time and r.start_time else ""),
        "recorded_wall_latency_ms":md.get("wall_latency_ms",""),
        "provider_latency_ms":md.get("provider_latency_ms",""),
        "input_tokens":md.get("input_tokens",""),"output_tokens":md.get("output_tokens",""),
        "total_tokens":md.get("total_tokens",""),"model":md.get("model",""),
        "finish_reason":md.get("finish_reason",""),"retry_count":md.get("retry_count","")})
with open(f"{OUT}/groq120b-generation-span-metrics.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=list(sm[0].keys())); w.writeheader(); w.writerows(sm)

complete=sum(1 for x in rows if x["generation_span_present"] and x["status"]=="success")
errs=[x["case_id"] for x in rows if x["error"]]
print(f"\ntrace completeness: {complete}/{len(rows)} roots have a generation child AND status=success")
print(f"errors: {errs or 'none'}")
print(f"distinct span names: {sorted(set(x['span_name'] for x in sm))}")
print(f"cases in export: {len(set(x['case_id'] for x in rows))}")
json.dump({"roots":len(roots),"generation_spans":len(gens),"total_runs":len(runs),
           "complete":complete,"errors":errs},
          open(os.path.join(OUTD,"groq120b_readback.json"),"w"),indent=2)
