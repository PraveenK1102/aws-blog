"""groq-routed-rag-observability-v1 — production-provider run with deep LangSmith spans.

Phases: A (smoke) | B (router equivalence) | C (decomposition equivalence)

Provider swap ONLY. The frozen Router V2 prompt/parser and the frozen decomposition
prompt/parser are imported and reused byte-identical; just the transport changes to
Groq. Nothing in router_v2.py / decomp_graph.py / routed_graph_v2.py is modified.

  Router V2      -> Groq openai/gpt-oss-20b
  Decomposition  -> Groq openai/gpt-oss-20b
  Final generation -> Groq openai/gpt-oss-120b

LangSmith project: multitenant-rag-dev-groq-observability-v1  (NEVER prod)
Only real work gets a span; no synthetic durations.
"""
import contextvars, hashlib, json, os, sys, time, warnings
warnings.filterwarnings("ignore")

import boto3
import groq_provider_obs as GQ

STAGE = sys.argv[1] if len(sys.argv) > 1 else "A"
HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, "output")
OUT = "/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
PROJECT = "multitenant-rag-dev-groq-observability-v1"
EXPERIMENT = "groq-routed-rag-observability-v1"

# ---------------- LangSmith (dev project only) ----------------
from langsmith import Client
from langsmith.run_trees import RunTree

_ls_key = json.loads(boto3.client("secretsmanager", region_name="ap-south-1")
    .get_secret_value(SecretId="multitenant/langsmith")["SecretString"])["api_key"]
LS = Client(api_key=_ls_key)

_cur = contextvars.ContextVar("cur_span", default=None)

class Span:
    """Real-duration LangSmith span. Only wraps actual work."""
    def __init__(self, name, run_type="chain", metadata=None, parent=None):
        self.name, self.run_type = name, run_type
        self.metadata = dict(metadata or {})
        self._parent = parent if parent is not None else _cur.get()
        self.rt = None; self.t0 = None; self._token = None
    def __enter__(self):
        self.t0 = time.monotonic()
        try:
            if self._parent is None:
                self.rt = RunTree(name=self.name, run_type=self.run_type,
                                  project_name=PROJECT, client=LS,
                                  inputs={}, extra={"metadata": self.metadata})
            else:
                self.rt = self._parent.rt.create_child(
                    name=self.name, run_type=self.run_type,
                    inputs={}, extra={"metadata": self.metadata})
            self.rt.post()
        except Exception:
            self.rt = None
        self._token = _cur.set(self)
        return self
    def set(self, **md):
        self.metadata.update({k: v for k, v in md.items() if v is not None})
        return self
    def __exit__(self, et, ev, tb):
        self.local_ms = round((time.monotonic() - self.t0) * 1000, 1)
        self.metadata["local_duration_ms"] = self.local_ms
        try:
            if self.rt is not None:
                if et is not None:
                    self.rt.error = et.__name__
                self.rt.extra = {"metadata": self.metadata}
                self.rt.end(outputs={"ok": et is None})
                self.rt.patch()
        except Exception:
            pass
        if self._token is not None:
            _cur.reset(self._token)
        return False

def flush():
    try: LS.flush()
    except Exception: pass

# ---------------- frozen modules (imported, never modified) ----------------
import nvidia_harness as H
import nvidia_provider as nv
import app as prod
import decomp_graph as D1
import router_v2 as R2
import routed_graph_v2 as G

FP = {"experiment": EXPERIMENT, "project": PROJECT,
      "router_prompt_sha": G.router_identity()["prompt_sha"],
      "router_model": GQ.MODEL_20B, "decomposition_model": GQ.MODEL_20B,
      "generation_model": GQ.MODEL_120B, "provider": "groq",
      "max_llm_context_chunks": prod.MAX_LLM_CONTEXT_CHUNKS,
      "top_k": prod.TOP_K, "retrieval_floor": prod.RETRIEVAL_FLOOR,
      "groq_ceiling": GQ.HARD_CEILING}
FP["fingerprint"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]

# ---------------- provider swap: frozen prompts, Groq transport ----------------
_stage = contextvars.ContextVar("llm_stage", default="unknown")

def groq_router_classify(question: str) -> dict:
    """Frozen Router V2 prompt + parser; Groq 20B transport."""
    with Span("router_v2", "llm", {"provider": "groq", "model": GQ.MODEL_20B,
                                   "stage": "router"}) as s:
        r = GQ.chat(GQ.MODEL_20B, R2.build_messages(question),
                    max_tokens=R2.ROUTER_MAX_TOKENS, temperature=0.0)
        out = R2.parse_router_output(r["content"])
        s.set(input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
              total_tokens=r["total_tokens"], latency_ms=r["latency_ms"],
              retry_count=r["retry_count"], provider_status=r["provider_status"],
              route_decision=("compound" if out.get("needs_decomposition") else "simple"),
              router_reason_code=out.get("reason_code"), parse_ok=out.get("parse_ok"),
              **{f"rl_{k}": v for k, v in (r.get("rate_limit_headers") or {}).items()})
        out.update({k: r[k] for k in ("input_tokens", "output_tokens", "total_tokens",
                                      "latency_ms", "retry_count")})
        out["provider_status"] = r["provider_status"]
        return out

_real_nv_chat = nv.chat
def dispatch_chat(model, messages, **k):
    """Route the frozen graph's LLM calls to Groq by stage. NVIDIA is never called."""
    st = _stage.get()
    if st == "decompose":
        gm, name = GQ.MODEL_20B, "decomposition"
    else:
        gm, name = GQ.MODEL_120B, "generation"
    with Span(name, "llm", {"provider": "groq", "model": gm, "stage": st}) as s:
        r = GQ.chat(gm, messages, max_tokens=k.get("max_tokens", 900), temperature=0.0)
        s.set(input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
              total_tokens=r["total_tokens"], latency_ms=r["latency_ms"],
              retry_count=r["retry_count"], provider_status=r["provider_status"],
              finish_reason=r["finish_reason"],
              **{f"rl_{k2}": v for k2, v in (r.get("rate_limit_headers") or {}).items()})
        return {"content": r["content"], "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"], "latency_ms": r["latency_ms"]}

# ---------------- retrieval instrumentation (real work only) ----------------
_real_embed_dense, _real_embed_sparse = prod._embed_dense, prod._embed_sparse
_real_get_client = prod._get_qdrant_client
CTR = {"titan": 0, "bm25": 0, "dense_probe": 0, "hybrid": 0, "logical_branches": 0}

def traced_embed_dense(text):
    CTR["titan"] += 1
    with Span("titan_embedding", "embedding",
              {"provider": "bedrock", "model": prod.TITAN_MODEL_ID}) as s:
        v = _real_embed_dense(text)
        s.set(dims=len(v)); return v

def traced_embed_sparse(text):
    CTR["bm25"] += 1
    with Span("bm25_encode", "embedding", {"provider": "local-fastembed"}) as s:
        v = _real_embed_sparse(text)
        s.set(nnz=len(v.get("indices") or [])); return v

class TracedQdrant:
    def __init__(self, inner): self._inner = inner
    def query_points(self, *a, **k):
        hybrid = k.get("prefetch") is not None
        CTR["hybrid" if hybrid else "dense_probe"] += 1
        with Span("qdrant_hybrid_rrf" if hybrid else "qdrant_dense_probe", "retriever",
                  {"collection": k.get("collection_name"), "limit": k.get("limit")}) as s:
            r = self._inner.query_points(*a, **k)
            n = len(getattr(r, "points", []) or [])
            s.set(result_count=n,
                  top_score=(round(r.points[0].score, 6) if n else None))
            return r
    def __getattr__(self, n): return getattr(self._inner, n)

_cc = {}
def traced_get_client():
    if "c" not in _cc: _cc["c"] = TracedQdrant(_real_get_client())
    return _cc["c"]

_real_retrieve = D1._retrieve
def traced_retrieve(question, route, tenant_ids, single):
    CTR["logical_branches"] += 1
    with Span("retrieval_branch", "chain",
              {"scope_type": route, "scope_tenant_count": len(tenant_ids or [])}) as s:
        cands, top = _real_retrieve(question, route, tenant_ids, single)
        s.set(retrieval_candidate_count=len(cands), top_dense_similarity=round(top, 6),
              relevance_floor=prod.RETRIEVAL_FLOOR,
              relevance_floor_passed=bool(top >= prod.RETRIEVAL_FLOOR))
        return cands, top

_real_merge = D1.merge_evidence
def traced_merge(state):
    with Span("merge_evidence", "chain") as s:
        out = _real_merge(state)
        s.set(final_context_count=len(out.get("merged_context") or []),
              branch_count=len([b for b in state["branch_results"] if "eligible" in b]))
        return out

_real_decompose, _real_final, _real_normal = G.decompose, G.final_answer, G.normal_answer
def staged_decompose(state):
    t = _stage.set("decompose")
    try: return _real_decompose(state)
    finally: _stage.reset(t)
def staged_final(state):
    t = _stage.set("final_answer")
    try:
        with Span("build_context", "chain",
                  {"final_context_count": len(state.get("merged_context") or []),
                   "estimated_context_tokens": prod._context_est_tokens(state.get("merged_context") or [])}):
            pass
        return _real_final(state)
    finally: _stage.reset(t)
def staged_normal(state):
    t = _stage.set("normal_answer")
    try:
        with Span("build_context", "chain",
                  {"final_context_count": len(state.get("merged_context") or []),
                   "estimated_context_tokens": prod._context_est_tokens(state.get("merged_context") or [])}):
            pass
        return _real_normal(state)
    finally: _stage.reset(t)

def install():
    prod._embed_dense = traced_embed_dense
    prod._embed_sparse = traced_embed_sparse
    prod._get_qdrant_client = traced_get_client
    D1._retrieve = traced_retrieve
    D1.merge_evidence = traced_merge
    nv.chat = dispatch_chat
    R2.classify = groq_router_classify
    G.decompose = staged_decompose
    G.final_answer = staged_final
    G.normal_answer = staged_normal


# ================= inputs =================
gt = {json.loads(l)["case_id"]: json.loads(l)
      for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl", encoding="utf-8")}
CORPUS = {c["case_id"]: c for c in json.load(open(os.path.join(HERE, "corpus60.json")))["cases"]}
v2fp = json.load(open(os.path.join(OUTD, "router_v2_fingerprint.json")))
nvidia_routes = {}
for l in open(os.path.join(OUTD, "router_v2_results.jsonl")):
    r = json.loads(l)
    if r.get("fingerprint_hash") == v2fp["fingerprint_hash"]:
        nvidia_routes[r["case_id"]] = r
GEN52 = sorted(nvidia_routes)
COMPOUND18 = sorted(c for c, r in nvidia_routes.items() if r["predicted_compound"] is True)

PHASE_A_SIMPLE = ["case-001", "case-014", "case-021"]
PHASE_A_COMPOUND = ["case-018", "case-020", "case-022"]

def ckpt(stage, row):
    p = os.path.join(OUTD, f"groq_obs_{stage}.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())

def done_ids(stage):
    p = os.path.join(OUTD, f"groq_obs_{stage}.jsonl")
    if not os.path.exists(p): return set()
    out = set()
    for l in open(p):
        try:
            d = json.loads(l)
            if d.get("fingerprint") == FP["fingerprint"]: out.add(d["case_id"])
        except Exception: pass
    return out

print(f"=== {EXPERIMENT} [phase {STAGE}] ===")
print(f"  project     : {PROJECT}")
print(f"  fingerprint : {FP['fingerprint']}")
print(f"  router={GQ.MODEL_20B}  decomp={GQ.MODEL_20B}  generation={GQ.MODEL_120B}")
print(f"  groq ceiling: {GQ.HARD_CEILING}   remaining: {GQ.remaining_budget()}")
print(f"  concurrency : 1\n")

install()
H.load_seed_map(os.path.join(OUT, "SEED-MANIFEST.json"))
halted = None

# ================= PHASE A — instrumentation smoke, real graph =================
if STAGE == "A":
    graph = G.build_graph()
    cases = PHASE_A_SIMPLE + PHASE_A_COMPOUND
    skip = done_ids("A")
    for cid in cases:
        if cid in skip:
            print(f"  {cid} already done — skipping"); continue
        c = CORPUS[cid]
        before = dict(CTR); gbefore = GQ.STATS["physical_requests"]
        t0 = time.monotonic()
        try:
            with Span("routed_request", "chain",
                      {"experiment": EXPERIMENT, "fingerprint": FP["fingerprint"],
                       "request_id": f"{EXPERIMENT}:{cid}", "case_id": cid,
                       "provider": "groq", "scope_type": c["route"],
                       "adjudicated_ground_truth": gt[cid]["ground_truth"]}) as root:
                with Span("resolve_scope", "chain", {"scope_type": c["route"]}) as s:
                    tids, single = D1._scope(c["route"], c.get("target"))
                    s.set(scope_tenant_count=len(tids))
                st = graph.invoke({"case_id": cid, "original_question": c["question"],
                                   "route": c["route"], "target": c.get("target")})
                ctx = st.get("merged_context") or []
                root.set(route_decision=("compound" if st.get("needs_decomposition") else "simple"),
                         router_reason_code=st.get("router_reason_code"),
                         decomposition_used=bool(st.get("subquestions")),
                         decomposition_fallback=bool(st.get("decomposition_unusable")),
                         subquestion_count=len(st.get("subquestions") or []),
                         branch_count=len([b for b in st.get("branch_results", []) if "branch" in b]),
                         final_context_count=len(ctx),
                         estimated_context_tokens=prod._context_est_tokens(ctx),
                         citation_count=len(st.get("citations") or []),
                         answer_path=st.get("answer_path"))
        except (GQ.GroqBudgetExceeded, GQ.GroqRateLimited) as e:
            halted = f"{cid}: {type(e).__name__}: {e}"; print(f"\n*** HALT: {halted}"); break
        except Exception as e:
            print(f"  [ERR ] {cid} {type(e).__name__}: {str(e)[:120]}"); continue
        ms = round((time.monotonic() - t0) * 1000, 1)
        row = {"case_id": cid, "fingerprint": FP["fingerprint"], "phase": "A",
               "route": c["route"], "adjudicated": gt[cid]["ground_truth"],
               "question": c["question"], "expected_answer": c["expected_answer"],
               "route_decision": ("compound" if st.get("needs_decomposition") else "simple"),
               "router_reason_code": st.get("router_reason_code"),
               "subquestions": st.get("subquestions") or [],
               "decomposition_fallback": bool(st.get("decomposition_unusable")),
               "branch_count": len([b for b in st.get("branch_results", []) if "branch" in b]),
               "final_context_count": len(st.get("merged_context") or []),
               "estimated_context_tokens": prod._context_est_tokens(st.get("merged_context") or []),
               "citation_count": len(st.get("citations") or []),
               "answer_path": st.get("answer_path"),
               "generated_answer": st.get("final_answer"),
               "end_to_end_ms": ms,
               "counters_delta": {k: CTR[k] - before[k] for k in CTR if CTR[k] != before[k]},
               "groq_physical_delta": GQ.STATS["physical_requests"] - gbefore}
        ckpt("A", row)
        print(f"  [ok  ] {cid} {row['route_decision']:<8} branches={row['branch_count']} "
              f"ctx={row['final_context_count']} cites={row['citation_count']} "
              f"{ms}ms  groq+{row['groq_physical_delta']}  budget_left={GQ.remaining_budget()}", flush=True)

# ================= PHASE B — router provider equivalence, 52 cases =================
elif STAGE == "B":
    skip = done_ids("B")
    for cid in GEN52:
        if cid in skip: continue
        if GQ.remaining_budget() < 1:
            halted = "router budget exhausted"; break
        c = CORPUS[cid]
        try:
            with Span("routed_request", "chain",
                      {"experiment": EXPERIMENT, "fingerprint": FP["fingerprint"],
                       "request_id": f"{EXPERIMENT}:B:{cid}", "case_id": cid,
                       "phase": "B_router_only", "provider": "groq"}) as root:
                res = groq_router_classify(c["question"])
                root.set(route_decision=("compound" if res.get("needs_decomposition") else "simple"),
                         router_reason_code=res.get("reason_code"))
        except (GQ.GroqBudgetExceeded, GQ.GroqRateLimited) as e:
            halted = f"{cid}: {type(e).__name__}: {e}"; print(f"\n*** HALT: {halted}"); break
        except Exception as e:
            print(f"  [ERR ] {cid} {type(e).__name__}: {str(e)[:100]}"); continue
        nv_pred = nvidia_routes[cid]["predicted_compound"]
        row = {"case_id": cid, "fingerprint": FP["fingerprint"], "phase": "B",
               "question": c["question"], "adjudicated": gt[cid]["ground_truth"],
               "decision_rule": gt[cid]["decision_rule"],
               "groq_predicted_compound": res.get("needs_decomposition"),
               "nvidia_predicted_compound": nv_pred,
               "agree_with_nvidia": res.get("needs_decomposition") == nv_pred,
               "groq_reason_code": res.get("reason_code"),
               "nvidia_reason_code": nvidia_routes[cid]["reason_code"],
               "information_needs": res.get("information_needs"),
               "parse_ok": res.get("parse_ok"), "parse_error": res.get("parse_error"),
               "latency_ms": res.get("latency_ms"), "input_tokens": res.get("input_tokens"),
               "output_tokens": res.get("output_tokens"), "total_tokens": res.get("total_tokens"),
               "retry_count": res.get("retry_count"),
               "rate_limit_headers": GQ.STATS.get("last_rate_limit_headers")}
        ckpt("B", row)
        mark = "ok " if row["agree_with_nvidia"] else "DIFF"
        print(f"  [{mark}] {cid} groq={str(res.get('needs_decomposition')):<5} "
              f"nvidia={str(nv_pred):<5} gt={gt[cid]['ground_truth']:<9} "
              f"{res.get('latency_ms')}ms  left={GQ.remaining_budget()}", flush=True)

# ================= PHASE C — decomposition provider equivalence, 18 cases =================
elif STAGE == "C":
    nvidia_decomp = {}
    for l in open(os.path.join(OUTD, "routed_live_v2_cases.jsonl")):
        d = json.loads(l); nvidia_decomp[d["case_id"]] = d
    skip = done_ids("C")
    for cid in COMPOUND18:
        if cid in skip: continue
        if GQ.remaining_budget() < 1:
            halted = "decomposition budget exhausted"; break
        c = CORPUS[cid]
        try:
            with Span("routed_request", "chain",
                      {"experiment": EXPERIMENT, "fingerprint": FP["fingerprint"],
                       "request_id": f"{EXPERIMENT}:C:{cid}", "case_id": cid,
                       "phase": "C_decomposition_only", "provider": "groq"}) as root:
                t = _stage.set("decompose")
                try:
                    r = dispatch_chat(D1.ANALYZER_MODEL,
                        [{"role": "system", "content": D1.ANALYZER_SYS},
                         {"role": "user", "content": f"Question: {c['question']}"}],
                        max_tokens=700)
                finally: _stage.reset(t)
                try: parsed = D1.parse_analysis(r["content"])
                except Exception as pe: parsed = {"is_compound": False, "subquestions": [],
                                                  "_err": type(pe).__name__}
                root.set(subquestion_count=len(parsed["subquestions"]),
                         decomposition_fallback=len(parsed["subquestions"]) < 2)
        except (GQ.GroqBudgetExceeded, GQ.GroqRateLimited) as e:
            halted = f"{cid}: {type(e).__name__}: {e}"; print(f"\n*** HALT: {halted}"); break
        except Exception as e:
            print(f"  [ERR ] {cid} {type(e).__name__}: {str(e)[:100]}"); continue
        nvd = nvidia_decomp.get(cid, {})
        row = {"case_id": cid, "fingerprint": FP["fingerprint"], "phase": "C",
               "question": c["question"], "adjudicated": gt[cid]["ground_truth"],
               "groq_subquestions": parsed["subquestions"],
               "groq_subquestion_count": len(parsed["subquestions"]),
               "groq_usable": len(parsed["subquestions"]) >= 2,
               "groq_fallback": len(parsed["subquestions"]) < 2,
               "nvidia_subquestions": nvd.get("subquestions") or [],
               "nvidia_subquestion_count": nvd.get("subquestion_count"),
               "nvidia_fallback": nvd.get("decomposition_unusable"),
               "parse_error": parsed.get("_err"),
               "latency_ms": r["latency_ms"], "input_tokens": r["input_tokens"],
               "output_tokens": r["output_tokens"],
               "rate_limit_headers": GQ.STATS.get("last_rate_limit_headers")}
        ckpt("C", row)
        print(f"  [ok  ] {cid} groq_subs={row['groq_subquestion_count']} "
              f"nvidia_subs={row['nvidia_subquestion_count']} "
              f"{r['latency_ms']}ms  left={GQ.remaining_budget()}", flush=True)
else:
    print("phase must be A|B|C"); sys.exit(2)

flush()
json.dump({"stats": GQ.STATS, "retrieval_counters": CTR, "fingerprint": FP,
           "halted": halted},
          open(os.path.join(OUTD, f"groq_obs_{STAGE}_stats.json"), "w"), indent=2, default=str)
print(f"\n=== GROQ USAGE (phase {STAGE}) ===")
for k in ["logical_calls", "physical_requests", "successes", "http_429", "http_5xx",
          "timeouts", "retries", "backoff_seconds", "in_tokens", "out_tokens"]:
    print(f"  {k}: {GQ.STATS[k]}")
print(f"  by model: {json.dumps(GQ.STATS['by_model'])}")
print(f"  last rate-limit headers: {json.dumps(GQ.STATS['last_rate_limit_headers'])}")
print(f"  remaining task budget: {GQ.remaining_budget()} / {GQ.HARD_CEILING}")
print(f"  retrieval counters: {json.dumps(CTR)}")
if halted:
    print(f"\n*** HALTED: {halted} ***"); sys.exit(5)
