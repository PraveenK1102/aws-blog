"""rag-routed-langgraph-v2-offline — Phase A: ZERO-COST artifact replay + call budget.

This is an ARTIFACT REPLAY, not a fresh graph execution. It asks: given the frozen
Router V2 verdicts already on disk, which existing artifact would the routed
architecture have selected for each case?

  V2 simple   -> the existing NVIDIA20B single-query top-5 baseline artifact
  V2 compound -> the existing decomposition-v1 artifact

NO provider calls of any kind: no NVIDIA, no Groq, no Bedrock Titan, no Qdrant.
Enforced structurally — this module imports no provider, harness, graph or app module.
"""
import csv, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, "output")
OUT = "/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
EXPERIMENT = "rag-routed-langgraph-v2-offline"
REPLAY_CASES = ["case-001", "case-002", "case-004", "case-018", "case-020", "case-022"]
MAX_BRANCHES = 3          # frozen v1 caps atomic subquestions at 3
MIN_BRANCHES = 2          # a usable decomposition needs >= 2

# ---------- frozen Router V2 verdicts (persisted; no new router call) ----------
v2fp = json.load(open(os.path.join(OUTD, "router_v2_fingerprint.json")))
routes = {}
for l in open(os.path.join(OUTD, "router_v2_results.jsonl")):
    r = json.loads(l)
    if r.get("fingerprint_hash") == v2fp["fingerprint_hash"]:
        routes[r["case_id"]] = r
assert len(routes) == 52, f"expected 52 persisted V2 verdicts, got {len(routes)}"

gt = {json.loads(l)["case_id"]: json.loads(l)
      for l in open(f"{OUT}/compound-router-groundtruth-v2.jsonl", encoding="utf-8")}
baseline = {json.loads(l)["case_id"]: json.loads(l)
            for l in open(f"{OUT}/rag-model-eval-nvidia20b-top5-v1-full.jsonl", encoding="utf-8")}
decomp = {json.loads(l)["case_id"]: json.loads(l)
          for l in open(os.path.join(OUTD, "decomp_cases.jsonl"))}
judged = {}
for l in open(os.path.join(OUTD, "decomp_judge.jsonl")):
    r = json.loads(l)
    if r.get("judge_status") == "scored": judged[r["case_id"]] = r

print(f"=== {EXPERIMENT} — PHASE A (artifact replay, zero provider cost) ===")
print(f"  frozen V2 verdicts loaded: {len(routes)}  (fingerprint {v2fp['fingerprint_hash']})")
print(f"  baseline artifacts: {len(baseline)}   decomposition-v1 artifacts: {len(decomp)}\n")

# ---------- routing distribution over all 52 ----------
simple = [c for c, r in routes.items() if r["predicted_compound"] is False]
compound = [c for c, r in routes.items() if r["predicted_compound"] is True]
amb = [c for c in routes if gt[c]["ground_truth"] == "ambiguous"]
amb_comp = [c for c in amb if routes[c]["predicted_compound"] is True]
amb_simp = [c for c in amb if routes[c]["predicted_compound"] is False]
print("=== FULL 52 ROUTING DISTRIBUTION (from persisted V2, no new calls) ===")
print(f"  V2 simple   : {len(simple)}")
print(f"  V2 compound : {len(compound)}")
print(f"  ambiguous ground truth: {len(amb)}  -> routed compound {len(amb_comp)} / simple {len(amb_simp)}")
by_gt = {}
for c, r in routes.items():
    k = (gt[c]["ground_truth"], "compound" if r["predicted_compound"] else "simple")
    by_gt[k] = by_gt.get(k, 0) + 1
print(f"  cross-tab (ground_truth, routed): {dict(sorted(by_gt.items()))}")

# ---------- six-case replay ----------
print(f"\n=== SIX-CASE ARTIFACT REPLAY ===")
rows = []
for cid in REPLAY_CASES:
    r = routes[cid]
    is_comp = r["predicted_compound"] is True
    src = "decomposition-v1 artifact" if is_comp else "nvidia20b top-5 baseline artifact"
    art = decomp.get(cid) if is_comp else baseline.get(cid)
    if art is None:
        print(f"  {cid}: MISSING {src}"); continue
    if is_comp:
        answer = art.get("generated_answer"); ctx_n = art.get("final_context_chunk_count")
        subs = art.get("subquestions") or []; branches = art.get("branch_count")
        ctx_tok = art.get("final_context_estimated_tokens"); cites = art.get("citations") or []
    else:
        answer = art.get("generated_answer"); ctx = art.get("retrieved_contexts") or []
        ctx_n = len(ctx); subs = []; branches = 1
        ctx_tok = int(sum(len(c or "") for c in ctx) / 4); cites = art.get("citations") or []
    j = judged.get(cid)
    rows.append({"case_id": cid, "route_kind": gt[cid]["decision_rule"],
                 "adjudicated_ground_truth": gt[cid]["ground_truth"],
                 "v2_predicted_compound": is_comp,
                 "v2_reason_code": r["reason_code"],
                 "v2_information_needs_diagnostic_only": " | ".join(r["information_needs"] or []),
                 "selected_path": "compound" if is_comp else "simple",
                 "artifact_source": src,
                 "subquestions": " | ".join(subs), "branch_count": branches,
                 "final_context_chunks": ctx_n, "final_context_est_tokens": ctx_tok,
                 "citation_count": len(cites),
                 "answer_excerpt": (answer or "")[:240].replace("\n", " "),
                 "judge_correctness": (j or {}).get("new_correctness"),
                 "judge_completeness": (j or {}).get("new_completeness"),
                 "judge_groundedness": (j or {}).get("new_groundedness"),
                 "judge_status": (j or {}).get("judge_status", "pending" if is_comp else "baseline_score_reused")})
    print(f"  {cid}: V2={'compound' if is_comp else 'simple':<9} -> {src}")
    print(f"        subs={len(subs)} branches={branches} ctx={ctx_n} chunks ({ctx_tok} est tok) "
          f"cites={len(cites)} judge={rows[-1]['judge_status']}")

with open(f"{OUT}/rag-routed-langgraph-v2-replay.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

# ---------- prospective live call budget ----------
n_comp, n_simp = len(compound), len(simple)
budget = [
 ("v2_simple_cases", n_simp, n_simp, "routed to the existing top-5 path"),
 ("v2_compound_cases", n_comp, n_comp, "routed to decomposition + fan-out"),
 ("router_v2_calls", 0, 0, "persisted verdicts reused; no new router call needed"),
 ("nvidia20b_decomposition_calls", n_comp, n_comp, "1 per compound case"),
 ("nvidia20b_final_generation_calls", n_comp, n_comp, "1 per compound case (no per-branch generation)"),
 ("nvidia20b_total_calls", 2*n_comp, 2*n_comp, "decomposition + final generation"),
 ("titan_embedding_calls", n_comp*MIN_BRANCHES, n_comp*MAX_BRANCHES,
  f"1 dense query embedding per branch; {MIN_BRANCHES} min .. {MAX_BRANCHES} max branches — BEDROCK, PAID"),
 ("qdrant_branch_searches", n_comp*MIN_BRANCHES, n_comp*MAX_BRANCHES, "1 hybrid RRF query per branch"),
 ("bm25_sparse_encodes", n_comp*MIN_BRANCHES, n_comp*MAX_BRANCHES, "local fastembed, no external cost"),
 ("simple_path_new_retrievals", 0, 0, "baseline artifacts reused for V2-simple cases"),
 ("simple_path_new_generations", 0, 0, "baseline artifacts reused for V2-simple cases"),
 ("groq_calls", 0, 0, "guarded to zero"),
 ("nvidia120b_judge_calls", 0, 0, "not probed in this task"),
]
with open(f"{OUT}/rag-routed-langgraph-v2-call-budget.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["item", "min", "max", "note"]); w.writerows(budget)

print(f"\n=== PROSPECTIVE LIVE 52-CASE CALL BUDGET (NOT executed) ===")
for k, lo, hi, note in budget:
    rng = f"{lo}" if lo == hi else f"{lo}..{hi}"
    print(f"  {k:<36} {rng:<10} {note}")
print(f"\n  *** USER ACTION REQUIRED — APPROVE LIVE BEDROCK RETRIEVAL BATCH ***")
print(f"  Titan embedding calls needed: {n_comp*MIN_BRANCHES}..{n_comp*MAX_BRANCHES} (AWS-billable)")
print(f"\n  provider calls made by this Phase A run: NVIDIA 0 | Groq 0 | Titan 0 | Qdrant 0")
