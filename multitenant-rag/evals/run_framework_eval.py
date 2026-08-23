"""RAGAS + DeepEval evaluation of the frozen routed-RAG artifacts. OFFLINE.

Reads ONLY persisted artifacts. Makes NO Titan, Qdrant, Groq or NVIDIA-120B call
and never regenerates an application answer or retrieves anything.

Evaluator: NVIDIA `nvidia/nemotron-3-nano-30b-a3b` — deliberately NOT the
GPT-OSS-20B application model, so the judge is independent of the system judged.
RAGAS AnswerRelevancy embeddings are LOCAL (HuggingFace), so no embedding
provider is called.

Every evaluator request passes through one bounded counter; the run raises
BudgetExceeded rather than exceed the cap.

Usage: run_framework_eval.py calibrate|full  <max_calls>
"""
import json, os, re, sys, time, threading, hashlib, warnings
warnings.filterwarnings("ignore")

STAGE = sys.argv[1] if len(sys.argv) > 1 else "calibrate"
MAX_CALLS = int(sys.argv[2]) if len(sys.argv) > 2 else 400
HERE = os.path.dirname(os.path.abspath(__file__)); OUTD = os.path.join(HERE, "output")
OUT = "/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog"
CKPT = os.path.join(OUTD, f"framework_eval_{STAGE}.jsonl")
EVALUATOR = os.environ.get("EVAL_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
BASE_URL = "https://integrate.api.nvidia.com/v1"
MIN_INTERVAL = float(os.environ.get("EVAL_MIN_INTERVAL", "1.0"))
# reasoning-style evaluator: it emits chain-of-thought before the JSON, so a small
# ceiling truncates it mid-thought and yields zero parseable objects.
EVAL_MAX_TOKENS = int(os.environ.get("EVAL_MAX_TOKENS", "8000"))

# calibration records: strong improvement pair, unchanged, refusal-style, partial
CALIB = ["case-018::baseline", "case-018::routed", "case-007::routed",
         "case-002::routed", "case-056::routed", "case-041::routed"]

class BudgetExceeded(RuntimeError): pass
CTR = {"evaluator_calls": 0, "ragas_calls": 0, "deepeval_calls": 0,
       "errors": 0, "in_tokens": 0, "out_tokens": 0}
_who = {"tag": "unknown"}
_lock = threading.Lock()
_last = [0.0]

import boto3
def _key():
    raw = boto3.client("secretsmanager", region_name="ap-south-1") \
        .get_secret_value(SecretId="multitenant/nvidia")["SecretString"]
    try: return json.loads(raw).get("NVIDIA_API_KEY") or json.loads(raw).get("api_key") or raw
    except Exception: return raw.strip()

from openai import OpenAI, AsyncOpenAI
_API_KEY = _key()
_raw_client = OpenAI(base_url=BASE_URL, api_key=_API_KEY, timeout=180.0, max_retries=0)
_raw_aclient = AsyncOpenAI(base_url=BASE_URL, api_key=_API_KEY, timeout=180.0, max_retries=0)

class BoundedCompletions:
    def __init__(self, inner): self._inner = inner
    def create(self, *a, **k):
        with _lock:
            if CTR["evaluator_calls"] + 1 > MAX_CALLS:
                raise BudgetExceeded(f"evaluator_calls would reach "
                                     f"{CTR['evaluator_calls']+1} > cap {MAX_CALLS}")
            CTR["evaluator_calls"] += 1
            CTR[f"{_who['tag']}_calls"] = CTR.get(f"{_who['tag']}_calls", 0) + 1
            wait = MIN_INTERVAL - (time.time() - _last[0])
            if wait > 0: time.sleep(wait)
            _last[0] = time.time()
        k.setdefault("max_tokens", EVAL_MAX_TOKENS)   # reasoning-style model needs headroom
        try:
            r = self._inner.create(*a, **k)
        except Exception:
            CTR["errors"] += 1; raise
        try:
            CTR["in_tokens"] += r.usage.prompt_tokens; CTR["out_tokens"] += r.usage.completion_tokens
        except Exception: pass
        return r
class BoundedChat:
    def __init__(self, inner): self.completions = BoundedCompletions(inner.completions)
class BoundedClient:
    def __init__(self, inner): self._inner = inner; self.chat = BoundedChat(inner.chat)
    def __getattr__(self, n): return getattr(self._inner, n)
client = BoundedClient(_raw_client)

def _reserve():
    """Shared bound check + pacing for the async path."""
    with _lock:
        if CTR["evaluator_calls"] + 1 > MAX_CALLS:
            raise BudgetExceeded(f"evaluator_calls would reach {CTR['evaluator_calls']+1} > cap {MAX_CALLS}")
        CTR["evaluator_calls"] += 1
        CTR[f"{_who['tag']}_calls"] = CTR.get(f"{_who['tag']}_calls", 0) + 1
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()

# NOTE: do NOT wrap the async client in a proxy class. ragas' instructor adapter
# does isinstance(client, AsyncOpenAI) to pick sync vs async, and a proxy fails it
# with "Cannot use agenerate() with a synchronous client". Patch the bound method
# on the genuine client object so the type is preserved.
_real_acreate = _raw_aclient.chat.completions.create
async def _bounded_acreate(*a, **k):
    _reserve()
    k.setdefault("max_tokens", EVAL_MAX_TOKENS)
    try:
        r = await _real_acreate(*a, **k)
    except Exception:
        CTR["errors"] += 1; raise
    try:
        CTR["in_tokens"] += r.usage.prompt_tokens; CTR["out_tokens"] += r.usage.completion_tokens
    except Exception: pass
    return r
_raw_aclient.chat.completions.create = _bounded_acreate
aclient = _raw_aclient

# ---------------- inputs ----------------
recs = [json.loads(l) for l in open(f"{OUT}/ragas-deepeval-eval-inputs.jsonl", encoding="utf-8")]
by_id = {r["record_id"]: r for r in recs}
targets = CALIB if STAGE == "calibrate" else [r["record_id"] for r in recs]
targets = [t for t in targets if t in by_id]

done = {}
if os.path.exists(CKPT):
    for l in open(CKPT):
        try:
            d = json.loads(l); done[(d["record_id"], d["framework"])] = d
        except Exception: pass

print(f"=== RAGAS + DeepEval framework evaluation [{STAGE}] ===")
print(f"  evaluator: {EVALUATOR}  (application model is gpt-oss-20b — deliberately different)")
print(f"  records: {len(targets)}   hard call cap: {MAX_CALLS}")
print(f"  no Titan / Qdrant / Groq / NVIDIA-120B calls; local embeddings for AnswerRelevancy\n")

# ---------------- RAGAS ----------------
from ragas.llms import llm_factory
from ragas.embeddings import HuggingFaceEmbeddings
from ragas.metrics.collections import (Faithfulness, AnswerRelevancy,
                                       ContextPrecisionWithReference, ContextRecall)
import asyncio
rl = llm_factory(model=EVALUATOR, provider="openai", client=aclient)  # async: ascore -> agenerate
remb = HuggingFaceEmbeddings(model="sentence-transformers/all-MiniLM-L6-v2")
RAGAS_METRICS = {
    "ragas_faithfulness": (Faithfulness(llm=rl),
        lambda m, r: m.ascore(user_input=r["question"], response=r["answer"],
                              retrieved_contexts=r["retrieved_contexts"])),
    "ragas_answer_relevancy": (AnswerRelevancy(llm=rl, embeddings=remb),
        lambda m, r: m.ascore(user_input=r["question"], response=r["answer"])),
    "ragas_context_precision": (ContextPrecisionWithReference(llm=rl),
        lambda m, r: m.ascore(user_input=r["question"], reference=r["reference_answer"],
                              retrieved_contexts=r["retrieved_contexts"])),
    "ragas_context_recall": (ContextRecall(llm=rl),
        lambda m, r: m.ascore(user_input=r["question"], retrieved_contexts=r["retrieved_contexts"],
                              reference=r["reference_answer"])),
}

# ---------------- DeepEval ----------------
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from deepeval.metrics import (FaithfulnessMetric, AnswerRelevancyMetric,
                              ContextualPrecisionMetric, ContextualRecallMetric)

class NvidiaEvaluator(DeepEvalBaseLLM):
    def load_model(self): return client
    def get_model_name(self): return EVALUATOR
    @staticmethod
    def _json_objects(txt: str):
        """Yield balanced top-level JSON objects, longest-last. The evaluator emits a
        reasoning preamble around its JSON, so a greedy '{.*}' regex either swallows
        prose or stops at the wrong brace."""
        out, depth, start, instr, esc = [], 0, None, False, False
        for i, ch in enumerate(txt):
            if instr:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': instr = False
                continue
            if ch == '"': instr = True
            elif ch == "{":
                if depth == 0: start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(txt[start:i+1]); start = None
        return out

    def generate(self, prompt: str, schema=None) -> str:
        msgs = [{"role": "user", "content": prompt}]
        if schema is not None:
            msgs.append({"role": "system", "content":
                "Respond with ONLY one JSON object conforming exactly to this schema. "
                "No prose, no explanation, no markdown fence, no reasoning:\n"
                + json.dumps(schema.model_json_schema())})
        r = client.chat.completions.create(model=EVALUATOR, messages=msgs, temperature=0.0)
        txt = r.choices[0].message.content or ""
        if schema is None: return txt
        cands = self._json_objects(txt)
        for blob in sorted(cands, key=len, reverse=True):      # richest object first
            try: return schema.model_validate_json(blob)
            except Exception: continue
        # do NOT fabricate an empty schema instance — that would surface as a real score
        raise ValueError(f"evaluator returned no schema-valid JSON ({len(cands)} objects seen)")
    async def a_generate(self, prompt: str, schema=None):
        return self.generate(prompt, schema)
dl = NvidiaEvaluator()
DE_METRICS = {
    "deepeval_faithfulness": FaithfulnessMetric(model=dl, async_mode=False, include_reason=False),
    "deepeval_answer_relevancy": AnswerRelevancyMetric(model=dl, async_mode=False, include_reason=False),
    "deepeval_contextual_precision": ContextualPrecisionMetric(model=dl, async_mode=False, include_reason=False),
    "deepeval_contextual_recall": ContextualRecallMetric(model=dl, async_mode=False, include_reason=False),
}

def persist(row):
    with open(CKPT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())

halted = None
for rid in targets:
    r = by_id[rid]
    # ---- RAGAS ----
    if (rid, "ragas") not in done:
        _who["tag"] = "ragas"; scores = {}; errs = {}
        for name, (metric, call) in RAGAS_METRICS.items():
            try:
                res = asyncio.run(call(metric, r))
                scores[name] = float(getattr(res, "value", res))
            except BudgetExceeded as e: halted = str(e); break
            except Exception as e:
                errs[name] = f"{type(e).__name__}: {str(e)[:110]}"; scores[name] = None
        if halted: break
        row = {"record_id": rid, "case_id": r["case_id"], "variant": r["variant"],
               "framework": "ragas", "scores": scores, "errors": errs,
               "evaluator": EVALUATOR, "calls_so_far": CTR["evaluator_calls"]}
        persist(row); done[(rid, "ragas")] = row
        print(f"  [ragas   ] {rid:<22} " +
              " ".join(f"{k.split('_',1)[1][:12]}={('%.3f'%v) if isinstance(v,float) else 'ERR'}"
                       for k, v in scores.items()) + f"  calls={CTR['evaluator_calls']}", flush=True)
    # ---- DeepEval ----
    if (rid, "deepeval") not in done:
        _who["tag"] = "deepeval"; scores = {}; errs = {}
        tc = LLMTestCase(input=r["question"], actual_output=r["answer"],
                         expected_output=r["reference_answer"],
                         retrieval_context=list(r["retrieved_contexts"]))
        for name, metric in DE_METRICS.items():
            try:
                metric.measure(tc); scores[name] = float(metric.score)
            except BudgetExceeded as e: halted = str(e); break
            except Exception as e:
                errs[name] = f"{type(e).__name__}: {str(e)[:110]}"; scores[name] = None
        if halted: break
        row = {"record_id": rid, "case_id": r["case_id"], "variant": r["variant"],
               "framework": "deepeval", "scores": scores, "errors": errs,
               "evaluator": EVALUATOR, "calls_so_far": CTR["evaluator_calls"]}
        persist(row); done[(rid, "deepeval")] = row
        print(f"  [deepeval] {rid:<22} " +
              " ".join(f"{k.split('_',1)[1][:12]}={('%.3f'%v) if isinstance(v,float) else 'ERR'}"
                       for k, v in scores.items()) + f"  calls={CTR['evaluator_calls']}", flush=True)

json.dump(CTR, open(os.path.join(OUTD, f"framework_eval_{STAGE}_counters.json"), "w"), indent=2)
print(f"\n=== EVALUATOR USAGE ===")
for k, v in CTR.items(): print(f"  {k}: {v}")
print(f"  cap: {MAX_CALLS}")
print(f"\nrecords complete: {len({k[0] for k in done})}/{len(targets)}")
if halted:
    print(f"\n*** DECISION REQUIRED — EVALUATOR CALL CAP REACHED ***\n    {halted}"); sys.exit(5)
