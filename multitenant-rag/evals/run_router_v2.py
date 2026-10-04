"""compound-router-v2 runner — ROUTING ONLY.

Stages:
  preflight : the original six cases (018/020/022 compound, 001/002/004 control).
              Hard gate: if all six come back compound, router-v2 has reproduced
              v1's "always compound" behaviour and the run STOPS.
  full      : all 52 generative cases (8 global-search cases excluded).

No retrieval, no fan-out, no generation, no judging. Groq calls = 0.
NVIDIA 120B calls = 0. Concurrency = 1. Checkpoint after every classification.
"""
import json, os, sys, time, hashlib, subprocess, warnings; warnings.filterwarnings("ignore")

import nvidia_harness as H
import nvidia_provider as nv
import router_v2 as R

STAGE = sys.argv[1] if len(sys.argv) > 1 else "preflight"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, "output")
CKPT = os.path.join(OUTD, "router_v2_results.jsonl")
EXP = "compound-router-v2"

H.install_groq_guard()                       # any Groq usage now raises
MAN = json.load(open(os.path.join(OUTD, "router_v2_manifest.json")))
CORPUS = {c["case_id"]: c for c in json.load(open(os.path.join(HERE, "corpus60.json")))["cases"]}

POS = set(MAN["ground_truth_compound"])
GEN = MAN["ground_truth_compound"] + MAN["ground_truth_simple"]
PREFLIGHT = MAN["preflight_cases"]
V1_PRED = {c: True for c in PREFLIGHT}       # v1 classified all six compound

from importlib.metadata import version
FP = {"experiment": EXP, "router_model": R.ROUTER_MODEL,
      "manifest_fingerprint": MAN["manifest_fingerprint"],
      "prompt_sha": hashlib.sha256(R.ROUTER_SYS.encode()).hexdigest()[:16],
      "reason_codes": list(R.REASON_CODES), "concurrency": 1,
      "timeout_s": R.ROUTER_TIMEOUT, "max_tokens": R.ROUTER_MAX_TOKENS,
      "repo_head": subprocess.run(["git","rev-parse","HEAD"],cwd=HERE,
                                  capture_output=True,text=True).stdout.strip(),
      "langgraph_used": False, "retrieval_performed": False,
      "generation_performed": False, "judging_performed": False}
FP["fingerprint_hash"] = hashlib.sha256(json.dumps(FP, sort_keys=True).encode()).hexdigest()[:16]
json.dump(FP, open(os.path.join(OUTD, "router_v2_fingerprint.json"), "w"), indent=2)

print(f"=== {EXP} [{STAGE}] ===")
print(f"  router={R.ROUTER_MODEL}  prompt_sha={FP['prompt_sha']}  manifest={MAN['manifest_fingerprint']}")
print(f"  fingerprint={FP['fingerprint_hash']}  concurrency=1  groq=0  nvidia_120b=0")
print(f"  generative={len(GEN)}  positives={len(POS)}  negatives={len(GEN)-len(POS)}\n")

done = {}
if os.path.exists(CKPT):
    for l in open(CKPT):
        try:
            r = json.loads(l)
            if r.get("fingerprint_hash") == FP["fingerprint_hash"]: done[r["case_id"]] = r
        except Exception: pass

cases = PREFLIGHT if STAGE == "preflight" else GEN
todo = [c for c in cases if c not in done]
print(f"  already done={len([c for c in cases if c in done])}  to classify={len(todo)}\n")

for cid in todo:                                     # strictly sequential
    c = CORPUS[cid]
    res = R.classify(c["question"])                  # question ONLY
    rec = {"case_id": cid, "fingerprint_hash": FP["fingerprint_hash"],
           "question": c["question"], "route": c["route"], "title": c["title"],
           "ground_truth_compound": cid in POS,
           "predicted_compound": res.get("needs_decomposition"),
           "reason_code": res.get("reason_code"),
           "information_needs": res.get("information_needs"),
           "information_need_count": len(res.get("information_needs") or []),
           "parse_ok": res.get("parse_ok"), "parse_error": res.get("parse_error"),
           "latency_ms": res.get("latency_ms"),
           "input_tokens": res.get("input_tokens"), "output_tokens": res.get("output_tokens"),
           "provider_status": res.get("provider_status")}
    with open(CKPT, "a", encoding="utf-8") as f:      # durable, per classification
        f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
    done[cid] = rec
    gt = "CMPD" if rec["ground_truth_compound"] else "simple"
    pr = rec["predicted_compound"]
    mark = "ok " if pr == rec["ground_truth_compound"] else "MISS"
    print(f"  [{mark}] {cid} gt={gt:<6} pred={str(pr):<5} code={rec['reason_code']} "
          f"needs={rec['information_need_count']} {rec['latency_ms']}ms {rec['provider_status']}",
          flush=True)
    if rec["provider_status"] != "ok":
        print(f"\n  PROVIDER DEGRADED on {cid} — stopping; checkpoint is durable, resume later.")
        break

print(f"\nnvidia stats: {json.dumps(nv.STATS)}")

# ---------------- preflight gate ----------------
if STAGE == "preflight":
    got = [done[c] for c in PREFLIGHT if c in done]
    if len(got) < len(PREFLIGHT):
        print("\nPREFLIGHT INCOMPLETE — provider degraded. Resume before judging the gate."); sys.exit(2)
    preds = {r["case_id"]: r["predicted_compound"] for r in got}
    print("\n=== SIX-CASE PREFLIGHT: v1 vs v2 ===")
    print(f"{'case':<10} {'ground_truth':<14} {'v1_pred':<9} {'v2_pred':<9} verdict")
    for cid in PREFLIGHT:
        gt = cid in POS; v2 = preds[cid]
        print(f"{cid:<10} {str(gt):<14} {str(V1_PRED[cid]):<9} {str(v2):<9} "
              f"{'ok' if v2 == gt else 'MISS'}")
    unparsed = [c for c in PREFLIGHT if preds[c] is None]
    if unparsed:
        print(f"\n*** PREFLIGHT INDETERMINATE — no verdict for {unparsed}. "
              "An unparsed case is NOT evidence of improvement; the gate cannot be "
              "judged on incomplete predictions. Fix and re-run. ***"); sys.exit(5)
    all_compound = all(preds[c] is True for c in PREFLIGHT)
    kept = all(preds[c] is True for c in PREFLIGHT if c in POS)
    ctrl_fixed = sum(1 for c in PREFLIGHT if c not in POS and preds[c] is False)
    print(f"\n  compounds retained: {sum(1 for c in PREFLIGHT if c in POS and preds[c] is True)}/3")
    print(f"  controls now simple: {ctrl_fixed}/3   (v1 had 0/3)")
    if all_compound:
        print("\n*** PREFLIGHT FAILED — router-v2 classified all six as compound, "
              "equivalent to v1 'always compound'. DO NOT run all 52. ***"); sys.exit(3)
    if not kept:
        print("\n*** PREFLIGHT FAILED — a known compound was lost. ***"); sys.exit(4)
    if ctrl_fixed == 0:
        print("\n*** PREFLIGHT FAILED — not one control was classified simple, so behaviour "
              "is not meaningfully better than v1. DO NOT run all 52. ***"); sys.exit(6)
    print(f"\n*** PREFLIGHT PASSED — {ctrl_fixed}/3 controls now simple (v1: 0/3) and all "
          "three compounds retained. Cleared to run all 52. ***")
