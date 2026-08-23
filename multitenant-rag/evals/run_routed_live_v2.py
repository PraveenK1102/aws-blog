"""rag-routed-langgraph-v2-offline — LIVE compound-path run over the 18 V2-compound cases.

Hard budget enforcement: cumulative counters are checked BEFORE every provider or
retrieval operation. If a call would breach an approved bound the run raises
BudgetExceeded and stops without making it.

Approved bounds (architect, 2026-08-23):
  compound cases            = 18
  router V2 calls           = 0      (persisted verdicts injected)
  NVIDIA 20B decomposition  <= 18
  NVIDIA 20B final gen      <= 18
  NVIDIA 20B total          <= 36
  Bedrock Titan embeddings  <= 54    (the AWS-billable bounded resource)
  Qdrant logical branches   <= 54
  Qdrant physical q_points  <= 108
  Groq                      = 0
  NVIDIA 120B               = 0

Retrieval behaviour is untouched: Titan dense embed, dense-only relevance probe,
hybrid RRF query, local BM25 encode — all four per branch, exactly as production.
Each case is persisted immediately; completed cases are never regenerated.
"""
import json, os, sys, time, hashlib, subprocess, warnings; warnings.filterwarnings("ignore")

import nvidia_harness as H
import nvidia_provider as nv
import app as prod
import decomp_graph as D1
import router_v2 as R2
import routed_graph_v2 as G

HERE=os.path.dirname(os.path.abspath(__file__)); OUTD=os.path.join(HERE,"output")
OUT="/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
CKPT=os.path.join(OUTD,"routed_live_v2_cases.jsonl")
EXPERIMENT="rag-routed-langgraph-v2-offline"

BOUNDS={"nvidia_decompose":18,"nvidia_final_gen":18,"nvidia_total":36,
        "titan_embed":54,"qdrant_logical_branches":54,"qdrant_physical":108,
        "groq":0,"nvidia_120b":0,"router_v2":0}
CTR={k:0 for k in ["nvidia_decompose","nvidia_final_gen","nvidia_total","titan_embed",
                   "qdrant_logical_branches","qdrant_dense_probe","qdrant_hybrid_rrf",
                   "qdrant_physical","bm25_encode","groq","nvidia_120b","router_v2"]}

class BudgetExceeded(RuntimeError): pass

def spend(kind, n=1, also=()):
    """Check BEFORE the call; raise rather than breach."""
    for k in (kind,)+tuple(also):
        lim=BOUNDS.get(k)
        if lim is not None and CTR[k]+n > lim:
            raise BudgetExceeded(f"{k} would reach {CTR[k]+n} > approved {lim}")
    for k in (kind,)+tuple(also): CTR[k]+=n

H.install_groq_guard()
G.assert_frozen_router()

# ---------------- instrument the production retrieval primitives ----------------
_real_embed_dense = prod._embed_dense
_real_embed_sparse = prod._embed_sparse
_real_get_client = prod._get_qdrant_client

def counted_embed_dense(text):
    spend("titan_embed")                     # AWS-billable — bounded
    return _real_embed_dense(text)

def counted_embed_sparse(text):
    CTR["bm25_encode"]+=1                    # local, no bound
    return _real_embed_sparse(text)

class CountingQdrant:
    """Delegates everything; meters query_points and classifies the two call shapes."""
    def __init__(self, inner): self._inner=inner
    def query_points(self, *a, **k):
        kind = "qdrant_hybrid_rrf" if k.get("prefetch") is not None else "qdrant_dense_probe"
        spend("qdrant_physical")
        CTR[kind]+=1
        return self._inner.query_points(*a, **k)
    def __getattr__(self, n): return getattr(self._inner, n)

_client_cache={}
def counted_get_client():
    if "c" not in _client_cache: _client_cache["c"]=CountingQdrant(_real_get_client())
    return _client_cache["c"]

_real_nv_chat = nv.chat
_stage={"tag":None}
def counted_chat(model, msgs, **k):
    if model == H.JUDGE_MODEL: spend("nvidia_120b")          # bound 0 -> always raises
    tag=_stage["tag"]
    if tag=="decompose": spend("nvidia_decompose", also=("nvidia_total",))
    elif tag=="final_gen": spend("nvidia_final_gen", also=("nvidia_total",))
    else: spend("nvidia_total")
    return _real_nv_chat(model, msgs, **k)

# wrap the graph's decomposition + generation so calls are attributed to a stage
_real_decompose = G.decompose
def staged_decompose(state):
    _stage["tag"]="decompose"
    try: return _real_decompose(state)
    finally: _stage["tag"]=None
_real_final = G.final_answer
def staged_final(state):
    _stage["tag"]="final_gen"
    try: return _real_final(state)
    finally: _stage["tag"]=None

_real_retrieve = D1._retrieve
def counted_retrieve(question, route, tenant_ids, single):
    spend("qdrant_logical_branches")
    return _real_retrieve(question, route, tenant_ids, single)

prod._embed_dense = counted_embed_dense
prod._embed_sparse = counted_embed_sparse
prod._get_qdrant_client = counted_get_client
nv.chat = counted_chat
D1._retrieve = counted_retrieve
G.decompose = staged_decompose
G.final_answer = staged_final

# ---------------- inputs ----------------
v2fp=json.load(open(os.path.join(OUTD,"router_v2_fingerprint.json")))
routes={}
for l in open(os.path.join(OUTD,"router_v2_results.jsonl")):
    r=json.loads(l)
    if r.get("fingerprint_hash")==v2fp["fingerprint_hash"]: routes[r["case_id"]]=r
assert len(routes)==52
COMPOUND=sorted(c for c,r in routes.items() if r["predicted_compound"] is True)
assert len(COMPOUND)==18, f"expected 18 compound cases, got {len(COMPOUND)}"
gt={json.loads(l)["case_id"]:json.loads(l) for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl",encoding="utf-8")}
CORPUS={c["case_id"]:c for c in json.load(open(os.path.join(HERE,"corpus60.json")))["cases"]}
H.load_seed_map(os.path.join(OUT,"SEED-MANIFEST.json"))

FP={"experiment":EXPERIMENT,"stage":"live_compound",
    "router_source":"persisted frozen V2 (0 router calls)",
    "router_prompt_sha":G.router_identity()["prompt_sha"],
    "stage_a_fingerprint":v2fp["fingerprint_hash"],
    "decomposition":"frozen decomp_graph v1 analyzer, unmodified",
    "app_model":H.APP_MODEL,"max_llm_context_chunks":prod.MAX_LLM_CONTEXT_CHUNKS,
    "top_k":prod.TOP_K,"retrieval_floor":prod.RETRIEVAL_FLOOR,
    "branch_concurrency":D1.MAX_BRANCH_CONCURRENCY,"bounds":BOUNDS,
    "repo_head":subprocess.run(["git","rev-parse","HEAD"],cwd=HERE,capture_output=True,text=True).stdout.strip()}
FP["fingerprint_hash"]=hashlib.sha256(json.dumps(FP,sort_keys=True).encode()).hexdigest()[:16]
json.dump(FP,open(os.path.join(OUTD,"routed_live_v2_fingerprint.json"),"w"),indent=2)

print(f"=== {EXPERIMENT} — LIVE COMPOUND RUN ===")
print(f"  cases={len(COMPOUND)}  fingerprint={FP['fingerprint_hash']}")
print(f"  router calls=0 (persisted {v2fp['fingerprint_hash']})  app model={H.APP_MODEL}")
print(f"  bounds: {BOUNDS}\n")

done={}
if os.path.exists(CKPT):
    for l in open(CKPT):
        try:
            r=json.loads(l)
            if r.get("fingerprint_hash")==FP["fingerprint_hash"]: done[r["case_id"]]=r
        except Exception: pass
todo=[c for c in COMPOUND if c not in done]
print(f"  already complete={len(done)}  to run={len(todo)}\n")

graph=G.build_graph()
halted=None
for cid in todo:
    c=CORPUS[cid]; t0=time.time()
    snap=dict(CTR)
    try:
        st=graph.invoke({"case_id":cid,"original_question":c["question"],
                         "route":c["route"],"target":c.get("target"),
                         "injected_router_result":{
                             "predicted_compound":True,
                             "reason_code":routes[cid]["reason_code"],
                             "information_needs":routes[cid]["information_needs"],
                             "parse_ok":routes[cid]["parse_ok"]}})
    except BudgetExceeded as e:
        halted=f"{cid}: {e}"; print(f"\n*** DECISION REQUIRED — APPROVED LIVE BUDGET WOULD BE EXCEEDED ***")
        print(f"    {halted}\n    no further call issued."); break
    except Exception as e:
        print(f"  [ERR ] {cid} {type(e).__name__}: {str(e)[:140]}")
        continue
    branches=sorted([b for b in st.get("branch_results",[]) if "branch" in b], key=lambda b:b["branch"])
    ctx=st.get("merged_context") or []
    rec={"case_id":cid,"fingerprint_hash":FP["fingerprint_hash"],
         "route":c["route"],"target":c.get("target"),"title":c["title"],
         "adjudicated_ground_truth":gt[cid]["ground_truth"],
         "decision_rule":gt[cid]["decision_rule"],
         "question":c["question"],"expected_answer":c["expected_answer"],
         "v2_reason_code":routes[cid]["reason_code"],
         "v2_information_needs_diagnostic_only":routes[cid]["information_needs"],
         "router_source":st.get("router_source"),
         "subquestions":st.get("subquestions") or [],
         "subquestion_count":len(st.get("subquestions") or []),
         "decomposition_unusable":bool(st.get("decomposition_unusable")),
         "answer_path":st.get("answer_path"),
         "branch_count":len(branches),
         "branch_signals":[{k:v for k,v in b.items() if k!="eligible"} for b in branches],
         "empty_retrieval_branches":[b["branch"] for b in branches if b.get("evidence_missing")],
         "branch_evidence_missing":bool(st.get("branch_evidence_missing")),
         "final_context_chunk_count":len(ctx),
         "final_context_estimated_tokens":prod._context_est_tokens(ctx),
         "merged_context":H._contexts_from(ctx),
         "merged_context_map":st.get("merged_context_map") or [],
         "citations":st.get("citations") or [],
         "citation_count":len(st.get("citations") or []),
         "generated_answer":st.get("final_answer"),
         "node_latencies":st.get("node_latencies") or {},
         "tokens":st.get("tokens") or {},
         "errors":st.get("errors") or [],
         "graph_total_ms":round((time.time()-t0)*1000,1),
         "counters_delta":{k:CTR[k]-snap[k] for k in CTR if CTR[k]!=snap[k]}}
    with open(CKPT,"a",encoding="utf-8") as f:
        f.write(json.dumps(rec,ensure_ascii=False)+"\n"); f.flush(); os.fsync(f.fileno())
    done[cid]=rec
    print(f"  [ok  ] {cid} [{c['route']}] subs={rec['subquestion_count']} branches={rec['branch_count']} "
          f"ctx={rec['final_context_chunk_count']} tok={rec['final_context_estimated_tokens']} "
          f"cites={rec['citation_count']} empty={rec['empty_retrieval_branches']} "
          f"({rec['graph_total_ms']}ms)  Δ{rec['counters_delta']}", flush=True)

json.dump(CTR,open(os.path.join(OUTD,"routed_live_v2_counters.json"),"w"),indent=2)
print(f"\n=== ACTUAL PROVIDER / RETRIEVAL COUNTS ===")
for k in ["nvidia_decompose","nvidia_final_gen","nvidia_total","titan_embed",
          "qdrant_logical_branches","qdrant_dense_probe","qdrant_hybrid_rrf","qdrant_physical",
          "bm25_encode","router_v2","groq","nvidia_120b"]:
    lim=BOUNDS.get(k)
    print(f"  {k:<26} {CTR[k]:<5} limit={lim if lim is not None else 'n/a':<5} "
          f"{'OK' if (lim is None or CTR[k]<=lim) else 'BREACH'}")
print(f"\nnvidia provider stats: {json.dumps(nv.STATS)}")
print(f"cases complete: {len(done)}/18")
if halted: print(f"HALTED: {halted}"); sys.exit(5)
