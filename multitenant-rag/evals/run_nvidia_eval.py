"""Run rag-model-eval-nvidia20b-v1 (staged + resumable).

  python run_nvidia_eval.py <SEED-MANIFEST.json> <n_cases|all> <stage-label>

Application : NVIDIA openai/gpt-oss-20b   Judge : NVIDIA openai/gpt-oss-120b
Groq        : ZERO calls (actively guarded)
Resume      : completed cases are read from the checkpoint and never re-spent.
"""
import json, os, sys, warnings; warnings.filterwarnings("ignore")
import boto3
from langsmith import Client

import nvidia_harness as H
import nvidia_provider as nv

MANIFEST = sys.argv[1]
LIMIT = sys.argv[2]
STAGE = sys.argv[3] if len(sys.argv) > 3 else "part-01"

DATASET = "multitenant-rag-eval-60-v1"
DATASET_ID = "d426fe19-3757-4442-b893-99a6cf031b68"
DATASET_VERSION = "baseline-v1"
LOGICAL = "rag-model-eval-nvidia20b-v1"
CKPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output",
                    "nvidia20b_checkpoint.jsonl")

H.install_groq_guard()                     # PHASE 3: fail fast on any Groq use
H.load_seed_map(MANIFEST)
FP = H.fingerprint(DATASET_ID, DATASET_VERSION)

print("=== configuration (safe) ===")
print(f"  application_provider = {FP['application_provider']}")
print(f"  application_model    = {FP['application_model']}")
print(f"  judge_provider       = {FP['judge_provider']}")
print(f"  judge_model          = {FP['judge_model']}")
print(f"  groq_calls_expected  = 0")
print(f"  top_k={FP['top_k']} retrieval_floor={FP['retrieval_floor']} retrieval={FP['retrieval']}")
print(f"  nvidia min_interval  = {nv.MIN_INTERVAL}s, concurrency = 1")
print(f"  fingerprint          = {FP['fingerprint_hash']} (git {FP['git_sha'][:7]})")

key = json.loads(boto3.client("secretsmanager", region_name="ap-south-1")
                 .get_secret_value(SecretId="multitenant/langsmith")["SecretString"])["api_key"]
c = Client(api_key=key)

done = H.load_checkpoint(CKPT, FP["fingerprint_hash"])
examples = sorted(c.list_examples(dataset_id=DATASET_ID),
                  key=lambda e: (e.inputs or {}).get("case_id") or "")
todo = [e for e in examples if (e.inputs or {}).get("case_id") not in done]
print(f"\ncheckpoint: {len(done)} completed | remaining: {len(todo)}")
if LIMIT != "all":
    todo = todo[:int(LIMIT)]
if not todo:
    print("nothing to do — all cases complete for this fingerprint"); raise SystemExit(0)
print(f"this stage will run {len(todo)} case(s): "
      f"{todo[0].inputs['case_id']} .. {todo[-1].inputs['case_id']}")

aborted = {"flag": False}


def target(inputs: dict) -> dict:
    """Application (NVIDIA 20B) + ONE structured judge call (NVIDIA 120B), checkpointed."""
    cid = inputs.get("case_id")
    if cid in done:                                    # resume: never re-spend quota
        return done[cid]["result"]
    if aborted["flag"]:
        return {"status": "skipped_circuit_open", "generated_answer": "",
                "retrieved_contexts": [], "citations": [], "llm_used": False}
    try:
        res = H.run_case(inputs)
        expected = (inputs.get("_expected") or "")
        j = H.judge(res, inputs.get("question") or "", expected)
    except nv.CircuitOpen as e:
        aborted["flag"] = True
        print(f"\n!! CIRCUIT BREAKER OPEN: {e} — stopping safely, no provider switch")
        return {"status": "skipped_circuit_open", "generated_answer": "",
                "retrieved_contexts": [], "citations": [], "llm_used": False}
    res["judge"] = j
    if j.get("status") == "scored":
        for dim, v in j["scores"].items():
            res[f"{dim}_score"] = v["score"]
            res[f"{dim}_reason"] = v["reason"]
        for k in ("judge_input_tokens", "judge_output_tokens", "judge_latency_ms",
                  "judge_retry_count", "judge_rate_limited"):
            res[k] = j.get(k)
    else:
        res["judge_status"] = j.get("status")
        res["judge_reason"] = j.get("reason")
    res["request_success"] = H.request_success(res)
    H.append_checkpoint(CKPT, {"case_id": cid, "fingerprint_hash": FP["fingerprint_hash"],
                               "route": inputs.get("route"), "result": res})
    done[cid] = {"result": res}
    print(f"  {cid} [{inputs.get('route')}] status={res['status']} "
          f"succ={res['request_success']} corr={res.get('correctness_score')} "
          f"comp={res.get('completeness_score')} grnd={res.get('groundedness_score')} "
          f"app={res.get('app_generation_latency_ms')}ms judge={res.get('judge_latency_ms')}ms",
          flush=True)
    return res


# deterministic evaluators (no extra LLM calls — judge already ran in target)
def ev_request_success(outputs: dict, **_):
    return {"key": "request_success", "score": (outputs or {}).get("request_success", 0),
            "comment": (outputs or {}).get("status")}

def _dim(outputs, dim, keyname):
    o = outputs or {}
    s = o.get(f"{dim}_score")
    if s is None:
        return {"key": keyname, "score": None,
                "comment": o.get("judge_status") or
                           ("not_scored: LLM-free route" if o.get("llm_used") is False else "not scored")}
    return {"key": keyname, "score": s, "comment": (o.get(f"{dim}_reason") or "")[:300]}

def ev_correctness(outputs: dict, **_):  return _dim(outputs, "correctness", "answer_correctness")
def ev_completeness(outputs: dict, **_): return _dim(outputs, "completeness", "answer_completeness")
def ev_groundedness(outputs: dict, **_): return _dim(outputs, "groundedness", "answer_groundedness")


# expected answer must reach the target for the judge; attach it to inputs
data = []
for e in todo:
    inp = dict(e.inputs or {})
    inp["_expected"] = (e.outputs or {}).get("expected_answer")
    e_ = e
    e_.inputs.update({"_expected": inp["_expected"]})
    data.append(e_)

res = c.evaluate(
    target,
    data=data,
    evaluators=[ev_request_success, ev_correctness, ev_completeness, ev_groundedness],
    experiment_prefix=f"{LOGICAL}-{STAGE}",
    max_concurrency=1,
    metadata={
        "evaluation_type": "offline_model_evaluation",
        "data_classification": "synthetic_public",
        "logical_experiment": LOGICAL, "stage": STAGE,
        "production_model_at_time_of_test": "groq/openai/gpt-oss-120b",
        "groq_calls": 0,
        "ragas": "DEFERRED", "deepeval": "DEFERRED",
        "langchain": "NOT_IMPLEMENTED", "langgraph": "NOT_IMPLEMENTED",
        **FP,
    },
)
n = sum(1 for _ in res)
print(f"\nstage complete: {n} case(s) | experiment {getattr(res,'experiment_name',LOGICAL)}")
print("=== NVIDIA provider stats ===")
print(json.dumps(nv.STATS, indent=2))
print(f"circuit_open={aborted['flag']}")
json.dump({"fingerprint": FP, "stats": nv.STATS,
           "experiment": getattr(res, "experiment_name", LOGICAL)},
          open(os.path.join(os.path.dirname(CKPT), f"nvidia20b_stage_{STAGE}.json"), "w"), indent=2)
