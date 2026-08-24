"""groq120b-routed-generation-validation-v1 — GENERATION ONLY on all 18 routed contexts.

No retrieval, no router, no decomposition, no LangGraph, no Titan, no Qdrant, no
NVIDIA, no RAGAS/DeepEval. Reads the exact persisted routed final contexts and
re-generates with Groq openai/gpt-oss-120b.

Bounds: 18 logical calls, 22 physical HTTP requests. Checked BEFORE each request.

TPM-aware pacing: before each call we consult the last observed
x-ratelimit-remaining-tokens / x-ratelimit-reset-tokens and, if headroom is
insufficient for the next request's estimated cost, wait for the token window.
That deliberate wait is recorded SEPARATELY and is never counted as inference
latency.
"""
import contextvars, hashlib, json, os, re, sys, time, warnings
warnings.filterwarnings("ignore")
import boto3
import groq_provider_obs as GQ

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
PROJECT="multitenant-rag-dev-groq-observability-v1"
EXPERIMENT="groq120b-routed-generation-validation-v1"
CKPT=os.path.join(OUTD,"groq120b_gen_validation.jsonl")
MAX_LOGICAL=18
GQ.HARD_CEILING=22                 # physical ceiling for THIS task
GQ.MIN_INTERVAL=0.0                # pacing is header-driven below, not fixed
GQ.MAX_ATTEMPTS=2                  # §7: max 2 physical attempts per logical call

from langsmith import Client
from langsmith.run_trees import RunTree
_ls=json.loads(boto3.client("secretsmanager",region_name="ap-south-1")
    .get_secret_value(SecretId="multitenant/langsmith")["SecretString"])["api_key"]
LS=Client(api_key=_ls)
_cur=contextvars.ContextVar("cur",default=None)

class Span:
    def __init__(self,name,run_type="chain",metadata=None):
        self.name,self.run_type,self.metadata=name,run_type,dict(metadata or {})
        self._parent=_cur.get(); self.rt=None; self._tok=None
    def __enter__(self):
        self.t0=time.monotonic()
        try:
            if self._parent is None:
                self.rt=RunTree(name=self.name,run_type=self.run_type,project_name=PROJECT,
                                client=LS,inputs={},extra={"metadata":self.metadata})
            else:
                self.rt=self._parent.rt.create_child(name=self.name,run_type=self.run_type,
                                inputs={},extra={"metadata":self.metadata})
            self.rt.post()
        except Exception: self.rt=None
        self._tok=_cur.set(self); return self
    def set(self,**md):
        self.metadata.update({k:v for k,v in md.items() if v is not None}); return self
    def __exit__(self,et,ev,tb):
        self.metadata["wall_latency_ms"]=round((time.monotonic()-self.t0)*1000,1)
        try:
            if self.rt is not None:
                if et is not None: self.rt.error=et.__name__
                self.rt.extra={"metadata":self.metadata}
                self.rt.end(outputs={"ok":et is None}); self.rt.patch()
        except Exception: pass
        if self._tok is not None: _cur.reset(self._tok)
        return False

import nvidia_harness as H
import app as prod
import decomp_graph as D1

FP={"experiment":EXPERIMENT,"project":PROJECT,"provider":"groq","model":GQ.MODEL_120B,
    "temperature":0.0,"max_tokens":H.APP_MAX_TOKENS,
    "max_llm_context_chunks":prod.MAX_LLM_CONTEXT_CHUNKS,
    "generation_only":True,"retrieval_performed":False,"router_performed":False,
    "decomposition_performed":False,"logical_ceiling":MAX_LOGICAL,
    "physical_ceiling":GQ.HARD_CEILING}
FP["fingerprint"]=hashlib.sha256(json.dumps(FP,sort_keys=True).encode()).hexdigest()[:16]

# ---------- rebuild the EXACT live prompt from persisted artifacts ----------
_SPLIT=re.compile(r"^\[(.*?)\]\s(.*)$", re.S)
def title_and_text(ctx: str):
    """Persisted form is f'[{title}] {chunk_text}' (nvidia_harness._contexts_from)."""
    m=_SPLIT.match(ctx)
    return (m.group(1), m.group(2)) if m else ("", ctx)

def blocks(contexts, cmap, subs):
    """Reproduces decomp_graph._blocks byte-for-byte from the persisted strings."""
    out=[]
    for i,c in enumerate(contexts):
        t,txt=title_and_text(c)
        si=(cmap[i].get("subquestion_index") if i<len(cmap) else None)
        label=f"[for sub-question {si+1}] " if isinstance(si,int) and subs else ""
        out.append(f"{label}[Source: {t}]\n{txt}")
    return "\n\n---\n\n".join(out)

rows=[json.loads(l) for l in open(os.path.join(OUTD,"routed_live_v2_cases.jsonl"))]
assert len(rows)==18, len(rows)
gt={json.loads(l)["case_id"]:json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")}

done=set()
if os.path.exists(CKPT):
    for l in open(CKPT):
        try:
            d=json.loads(l)
            if d.get("fingerprint")==FP["fingerprint"]: done.add(d["case_id"])
        except Exception: pass

print(f"=== {EXPERIMENT} ===")
print(f"  project={PROJECT}  fingerprint={FP['fingerprint']}")
print(f"  model={GQ.MODEL_120B}  temperature=0.0  max_tokens={H.APP_MAX_TOKENS}")
print(f"  bounds: {MAX_LOGICAL} logical / {GQ.HARD_CEILING} physical")
print(f"  generation only — Titan 0, Qdrant 0, NVIDIA 0, router 0, decomposition 0\n")

pacing_total=0.0
def tpm_wait(est_tokens: int):
    """Header-driven wait. Recorded separately from inference latency."""
    global pacing_total
    h=GQ.STATS.get("last_rate_limit_headers") or {}
    rem=h.get("x-ratelimit-remaining-tokens"); rst=h.get("x-ratelimit-reset-tokens")
    if rem is None: return 0.0
    try: rem=float(rem)
    except ValueError: return 0.0
    if rem >= est_tokens: return 0.0
    secs=0.0
    if rst:
        m=re.match(r"(?:(\d+)m)?([\d.]+)s", str(rst))
        if m: secs=float(m.group(1) or 0)*60+float(m.group(2))
    secs=min(max(secs,1.0),75.0)+1.0
    print(f"        [pacing] remaining_tokens={rem:.0f} < est {est_tokens} -> waiting {secs:.1f}s", flush=True)
    time.sleep(secs); pacing_total+=secs
    return secs

halted=None
for r in rows:
    cid=r["case_id"]
    if cid in done: print(f"  {cid} already done — skipping"); continue
    if GQ.STATS["logical_calls"] >= MAX_LOGICAL:
        halted="logical ceiling reached"; break
    ctx=r["merged_context"]; cmap=r["merged_context_map"]; subs=r["subquestions"] or []
    ctx_sha=hashlib.sha256(json.dumps(ctx,ensure_ascii=False).encode()).hexdigest()
    est=r["final_context_estimated_tokens"]+400
    if GQ.remaining_budget() < 1:
        halted="physical ceiling reached"; break
    waited=tpm_wait(est)
    sub_txt="\n".join(f"{i+1}. {s}" for i,s in enumerate(subs))
    user=(f"QUESTION:\n{r['question']}\n\n" +
          (f"SUB-QUESTIONS TO COVER:\n{sub_txt}\n\n" if subs else "") +
          f"EVIDENCE:\n{blocks(ctx,cmap,subs)}")
    try:
        with Span("generation_validation_request","chain",
                  {"experiment":EXPERIMENT,"fingerprint":FP["fingerprint"],"case_id":cid,
                   "provider":"groq","model":GQ.MODEL_120B,
                   "context_count":len(ctx),"context_sha256":ctx_sha[:32],
                   "estimated_context_tokens":r["final_context_estimated_tokens"],
                   "adjudicated_ground_truth":gt[cid]["ground_truth"],
                   "routed_answer_path":r["answer_path"],
                   "deliberate_pacing_s":round(waited,1)}) as root:
            with Span("generation","llm",
                      {"provider":"groq","model":GQ.MODEL_120B,
                       "context_count":len(ctx)}) as gs:
                g=GQ.chat(GQ.MODEL_120B,
                          [{"role":"system","content":D1.GEN_SYS_COMPOUND},
                           {"role":"user","content":user}],
                          max_tokens=H.APP_MAX_TOKENS, temperature=0.0)
                gs.set(input_tokens=g["input_tokens"],output_tokens=g["output_tokens"],
                       total_tokens=g["total_tokens"],provider_latency_ms=g["latency_ms"],
                       retry_count=g["retry_count"],physical_requests=g["physical_requests"],
                       provider_status=g["provider_status"],finish_reason=g["finish_reason"],
                       **{f"rl_{k}":v for k,v in (g.get("rate_limit_headers") or {}).items()})
            root.set(provider_latency_ms=g["latency_ms"],input_tokens=g["input_tokens"],
                     output_tokens=g["output_tokens"],total_tokens=g["total_tokens"],
                     retry_count=g["retry_count"])
    except (GQ.GroqBudgetExceeded, GQ.GroqRateLimited) as e:
        halted=f"{cid}: {type(e).__name__}: {e}"; print(f"\n*** HALT: {halted}"); break
    except Exception as e:
        print(f"  [ERR ] {cid} {type(e).__name__}: {str(e)[:120]}"); continue
    rec={"case_id":cid,"fingerprint":FP["fingerprint"],"experiment":EXPERIMENT,
         "adjudicated":gt[cid]["ground_truth"],"routed_answer_path":r["answer_path"],
         "decomposition_fallback":bool(r["decomposition_unusable"]),
         "question":r["question"],"expected_answer":r["expected_answer"],
         "context_count":len(ctx),"context_sha256":ctx_sha,
         "estimated_context_tokens":r["final_context_estimated_tokens"],
         "subquestion_count":len(subs),
         "nvidia20b_answer":r["generated_answer"],
         "groq120b_answer":g["content"],
         "input_tokens":g["input_tokens"],"output_tokens":g["output_tokens"],
         "total_tokens":g["total_tokens"],"provider_latency_ms":g["latency_ms"],
         "retry_count":g["retry_count"],"physical_requests":g["physical_requests"],
         "deliberate_pacing_s":round(waited,1),"provider_status":g["provider_status"],
         "rate_limit_headers":g.get("rate_limit_headers")}
    with open(CKPT,"a",encoding="utf-8") as f:
        f.write(json.dumps(rec,ensure_ascii=False)+"\n"); f.flush(); os.fsync(f.fileno())
    print(f"  [ok  ] {cid} ctx={len(ctx)} in={g['input_tokens']} out={g['output_tokens']} "
          f"{g['latency_ms']}ms retry={g['retry_count']} "
          f"phys={GQ.STATS['physical_requests']}/{GQ.HARD_CEILING}", flush=True)

try: LS.flush()
except Exception: pass
json.dump({"stats":GQ.STATS,"fingerprint":FP,"pacing_total_s":round(pacing_total,1),
           "halted":halted}, open(os.path.join(OUTD,"groq120b_gen_stats.json"),"w"),
          indent=2, default=str)
print(f"\n=== GROQ 120B USAGE ===")
for k in ["logical_calls","physical_requests","successes","http_429","http_5xx","timeouts",
          "retries","backoff_seconds","in_tokens","out_tokens"]:
    print(f"  {k}: {GQ.STATS[k]}")
print(f"  deliberate TPM pacing (separate from latency): {round(pacing_total,1)}s")
print(f"  last headers: {json.dumps(GQ.STATS['last_rate_limit_headers'])}")
if halted: print(f"\n*** HALTED: {halted} ***"); sys.exit(5)
