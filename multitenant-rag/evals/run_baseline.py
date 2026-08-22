"""Run the LangSmith offline baseline experiment (rag-baseline-v1).

Usage:
  python run_baseline.py <manifest.json> <out.json> [case-001,case-002,case-003]

max_concurrency is pinned to 1. Pacing via EVAL_MIN_INTERVAL_SECONDS.
No RAGAS / DeepEval / LangChain / LangGraph.
"""
import json, os, sys, subprocess, warnings; warnings.filterwarnings("ignore")
import boto3
from langsmith import Client

# --- mirror PRODUCTION config exactly ------------------------------------------------
# Read the deployed ask Lambda's env so the harness can never drift from prod
# (e.g. model ids). MUST happen before `import harness`, which imports the app.
_cfg = boto3.client("lambda", region_name="ap-south-1").get_function_configuration(
    FunctionName="multitenant-ask").get("Environment", {}).get("Variables", {})
for _k in ("GROQ_MODEL", "GROQ_MODEL_SMALL", "RETRIEVAL_FLOOR", "QDRANT_COLLECTION",
           "TENANTS_TABLE", "USERS_TABLE", "POSTS_TABLE", "USAGE_TABLE",
           "S3_CONTENT_BUCKET"):
    if _k in _cfg:
        os.environ[_k] = _cfg[_k]
_cfg_public = {k: _cfg.get(k) for k in ("GROQ_MODEL", "GROQ_MODEL_SMALL", "RETRIEVAL_FLOOR")}
print("mirroring prod config:", json.dumps(_cfg_public))

import harness
from harness import rag_target, request_success, combined_judge, QUOTA, load_seed_map

MANIFEST, OUT = sys.argv[1], sys.argv[2]
ONLY = [c.strip() for c in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
DATASET = "multitenant-rag-eval-60-v1"
PREFIX = "rag-baseline-v1"

load_seed_map(MANIFEST)
key = json.loads(boto3.client("secretsmanager", region_name="ap-south-1")
                 .get_secret_value(SecretId="multitenant/langsmith")["SecretString"])["api_key"]
# Separate client for OFFLINE SYNTHETIC evaluation: full content is allowed here by
# architect decision. Production tracing keeps hide_inputs/hide_outputs (different
# client, inside the Lambda) — nothing global is disabled.
c = Client(api_key=key)

sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                     cwd=os.path.dirname(MANIFEST)).stdout.strip() or "unknown"

examples = list(c.list_examples(dataset_name=DATASET))
if ONLY:
    examples = [e for e in examples if (e.inputs or {}).get("case_id") in ONLY]
    examples.sort(key=lambda e: (e.inputs or {}).get("case_id"))
n = len(examples)

app_calls = n                     # 1 generation per LLM case (global cases: 0)
judge_calls = n                   # ONE combined structured judge call per case
print(f"=== PHASE 17 call-volume estimate ({n} cases) ===")
print(f"  application generation calls : ~{app_calls} (global/LLM-free cases make 0)")
print(f"  custom judge calls (combined): ~{judge_calls}")
print(f"  RAGAS / DeepEval             : 0 (DEFERRED)")
print(f"  TOTAL major LLM calls        : ~{app_calls + judge_calls} "
      f"(+bounded retries; not exact — app branches vary)")
print(f"  pacing: max_concurrency=1, EVAL_MIN_INTERVAL_SECONDS={harness.MIN_INTERVAL}")

res = c.evaluate(
    rag_target,
    data=examples,
    evaluators=[request_success, combined_judge],
    experiment_prefix=PREFIX,
    max_concurrency=1,
    metadata={
        "data_classification": "synthetic_public", "evaluation_type": "offline",
        "environment": "evaluation", "production_code_sha": sha,
        "primary_model": os.environ.get("GROQ_MODEL"), "small_model": os.environ.get("GROQ_MODEL_SMALL"),
        "judge_model": harness.JUDGE_MODEL, "retrieval": "titan+bm25+rrf",
        "top_k": 5, "retrieval_floor": float(os.environ.get("RETRIEVAL_FLOOR", "0.15")),
        "dataset": DATASET, "dataset_version": "baseline-v1",
        "ragas": "DEFERRED", "deepeval": "DEFERRED",
        "langchain": "NOT_IMPLEMENTED", "langgraph": "NOT_IMPLEMENTED",
        "semantic_cache": "bypassed_for_eval",
    },
)

rows = []
for r in res:
    ex, run = r.get("example"), r.get("run")
    fb = {f.key: f for f in (r.get("evaluation_results", {}) or {}).get("results", [])}
    o = (run.outputs or {}) if run else {}
    rows.append({
        "case_id": (ex.inputs or {}).get("case_id") if ex else None,
        "route": (ex.inputs or {}).get("route") if ex else None,
        "target": (ex.inputs or {}).get("target") if ex else None,
        "question": (ex.inputs or {}).get("question") if ex else None,
        "expected_answer": (ex.outputs or {}).get("expected_answer") if ex else None,
        "generated_answer": o.get("generated_answer"),
        "retrieved_contexts": o.get("retrieved_contexts") or [],
        "citations": o.get("citations") or [],
        "status": o.get("status"), "error_type": o.get("error_type"),
        "model": o.get("model"), "top_dense": o.get("top_dense"),
        "app_input_tokens": o.get("input_tokens"), "app_output_tokens": o.get("output_tokens"),
        "application_latency_ms": o.get("latency_ms"),
        "request_success": getattr(fb.get("request_success"), "score", None),
        "answer_correctness": getattr(fb.get("answer_correctness"), "score", None),
        "answer_correctness_reason": getattr(fb.get("answer_correctness"), "comment", None),
        "answer_completeness": getattr(fb.get("answer_completeness"), "score", None),
        "answer_completeness_reason": getattr(fb.get("answer_completeness"), "comment", None),
        "langsmith_run_id": str(run.id) if run else None,
    })

json.dump({"experiment": getattr(res, "experiment_name", PREFIX), "cases": rows,
           "groq_quota_headers": QUOTA}, open(OUT, "w"), indent=2, ensure_ascii=False)
print(f"\nexperiment: {getattr(res, 'experiment_name', PREFIX)}")
print(f"rows written: {len(rows)} -> {OUT}")
print("groq quota headers (safe):", json.dumps(QUOTA, indent=2))
