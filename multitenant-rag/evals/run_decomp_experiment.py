"""rag-agentic-decomposition-nvidia20b-v1 — 6-case offline LangGraph experiment.

Durable per-case checkpoint. Groq calls: 0. Judge: NVIDIA 120B, one structured call
per final answer, judged against the FINAL merged context only.
"""
import json, os, sys, time, hashlib, subprocess, warnings; warnings.filterwarnings("ignore")
os.environ.setdefault("NVIDIA_MAX_ATTEMPTS", "3")

import nvidia_harness as H
import nvidia_provider as nv
import decomp_graph as D
import app as prod

MANIFEST = sys.argv[1]
STAGE = sys.argv[2] if len(sys.argv) > 2 else "all"   # graph | judge | all
HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, "output")
CKPT = os.path.join(OUTD, "decomp_cases.jsonl")
JCKPT = os.path.join(OUTD, "decomp_judge.jsonl")
EXP = "rag-agentic-decomposition-nvidia20b-v1"
JUDGE_TIMEOUT = int(os.environ.get("DECOMP_JUDGE_TIMEOUT", "150"))

H.install_groq_guard()
H.load_seed_map(MANIFEST)
sel = json.load(open(os.path.join(OUTD, "decomp_selection.json")))
CORPUS = {c["case_id"]: c for c in json.load(open(os.path.join(HERE, "corpus60.json")))["cases"]}
CASES = sel["compound"] + sel["controls"]
BASE = sel["baseline"]

from importlib.metadata import version
FP = {"experiment": EXP, "langgraph_version": version("langgraph"),
      "repo_head": subprocess.run(["git","rev-parse","HEAD"],cwd=HERE,capture_output=True,text=True).stdout.strip(),
      "deployed_ask_sha": "d5af30e", "analyzer_model": D.ANALYZER_MODEL, "gen_model": D.GEN_MODEL,
      "judge_model": H.JUDGE_MODEL, "max_llm_context_chunks": prod.MAX_LLM_CONTEXT_CHUNKS,
      "retrieval": "titan+bm25+rrf", "top_k": prod.TOP_K, "retrieval_floor": prod.RETRIEVAL_FLOOR,
      "branch_concurrency": D.MAX_BRANCH_CONCURRENCY}
FP["fingerprint_hash"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]
json.dump(FP, open(os.path.join(OUTD, "decomp_fingerprint.json"), "w"), indent=2)

print(f"=== {EXP} ===")
print(f"  langgraph={FP['langgraph_version']} analyzer={D.ANALYZER_MODEL} gen={D.GEN_MODEL} judge={H.JUDGE_MODEL}")
print(f"  groq_calls_expected=0 | MAX_LLM_CONTEXT_CHUNKS={prod.MAX_LLM_CONTEXT_CHUNKS} | branch_concurrency={D.MAX_BRANCH_CONCURRENCY}")
print(f"  fingerprint={FP['fingerprint_hash']}")
print(f"  cases: compound={sel['compound']} controls={sel['controls']}\n")

done = {}
if os.path.exists(CKPT):
    for l in open(CKPT):
        try:
            r = json.loads(l)
            if r.get("fingerprint_hash") == FP["fingerprint_hash"]: done[r["case_id"]] = r
        except Exception: pass

graph = D.build_graph()
for cid in (CASES if STAGE in ('graph','all') else []):
    if cid in done:
        print(f"  {cid} already done — skipping"); continue
    c = CORPUS[cid]
    t0 = time.time()
    st = graph.invoke({"case_id": cid, "original_question": c["question"],
                       "route": c["route"], "target": c.get("target")})
    total_ms = round((time.time()-t0)*1000, 1)
    branches = sorted([b for b in st.get("branch_results", []) if "branch" in b], key=lambda b: b["branch"])
    rec = {
        "case_id": cid, "fingerprint_hash": FP["fingerprint_hash"],
        "route": c["route"], "target": c.get("target"), "title": c["title"],
        "scenario_label_for_reporting_only": ("compound multi-part" if cid in sel["compound"] else "control"),
        "original_question": c["question"], "expected_answer": c["expected_answer"],
        "runtime_is_compound": bool(st.get("is_compound")),
        "subquestions": st.get("subquestions") or [],
        "subquestion_count": len(st.get("subquestions") or []),
        "branch_count": len(branches),
        "branch_signals": [{k: v for k, v in b.items() if k != "eligible"} for b in branches],
        "branch_evidence_missing": bool(st.get("branch_evidence_missing")),
        "final_context_chunk_count": len(st.get("merged_context") or []),
        "final_context_estimated_tokens": prod._context_est_tokens(st.get("merged_context") or []),
        "merged_context": H._contexts_from(st.get("merged_context") or []),
        "merged_context_map": st.get("merged_context_map") or [],
        "citations": st.get("citations") or [],
        "generated_answer": st.get("final_answer"),
        "node_latencies": st.get("node_latencies") or {},
        "tokens": st.get("tokens") or {},
        "graph_total_ms": total_ms, "errors": st.get("errors") or [],
        "baseline": BASE.get(cid),
    }
    # ---- persist the GRAPH result BEFORE any judge call (durable stage separation) ----
    with open(CKPT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
    done[cid] = rec
    print(f"  [graph] {cid} [{c['route']}] compound={rec['runtime_is_compound']} "
          f"subs={rec['subquestion_count']} branches={rec['branch_count']} "
          f"ctx={rec['final_context_chunk_count']} ctx_tok={rec['final_context_estimated_tokens']} "
          f"missing_branch={rec['branch_evidence_missing']} ({total_ms}ms)", flush=True)

print(f"\nnvidia stats: {json.dumps(nv.STATS)}")


# ===================== STAGE: JUDGE (separate, resumable) =====================
if STAGE in ("judge", "all"):
    jdone = {}
    if os.path.exists(JCKPT):
        for l in open(JCKPT):
            try:
                r = json.loads(l)
                if r.get("fingerprint_hash") == FP["fingerprint_hash"] and r.get("judge_status") == "scored":
                    jdone[r["case_id"]] = r
            except Exception: pass
    graphed = {}
    for l in open(CKPT):
        try:
            r = json.loads(l)
            if r.get("fingerprint_hash") == FP["fingerprint_hash"]: graphed[r["case_id"]] = r
        except Exception: pass
    todo = [cid for cid in CASES if cid in graphed and cid not in jdone]
    print(f"\n[JUDGE] graphed={len(graphed)} already scored={len(jdone)} to judge={len(todo)}", flush=True)
    _orig_chat = nv.chat
    def _judge_chat(model, msgs, **k):
        k["timeout"] = JUDGE_TIMEOUT
        return _orig_chat(model, msgs, **k)
    for cid in todo:
        g = graphed[cid]
        res = {"generated_answer": g["generated_answer"], "retrieved_contexts": g["merged_context"],
               "status": "completed", "llm_used": True}
        nv.chat = _judge_chat; saved = nv.MAX_ATTEMPTS; nv.MAX_ATTEMPTS = 2
        try:
            j = H.judge(res, g["original_question"], g["expected_answer"])
        finally:
            nv.chat = _orig_chat; nv.MAX_ATTEMPTS = saved
        rec = {"case_id": cid, "fingerprint_hash": FP["fingerprint_hash"],
               "judge_model": H.JUDGE_MODEL, "judge_status": j.get("status")}
        if j.get("status") == "scored":
            for dim, v in j["scores"].items():
                rec[f"new_{dim}"] = v["score"]; rec[f"new_{dim}_reason"] = v["reason"]
            for k in ("judge_input_tokens","judge_output_tokens","judge_latency_ms"): rec[k] = j.get(k)
        else:
            rec["judge_reason"] = j.get("reason")
        with open(JCKPT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
        b = graphed[cid].get("baseline") or {}
        print(f"  [judge] {cid} {rec['judge_status']} | corr {b.get('corr')}->{rec.get('new_correctness')} "
              f"comp {b.get('comp')}->{rec.get('new_completeness')} "
              f"grnd {b.get('grnd')}->{rec.get('new_groundedness')}", flush=True)
    print(f"\nnvidia stats: {json.dumps(nv.STATS)}")
