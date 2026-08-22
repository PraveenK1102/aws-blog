"""MultiTenantRAG — offline evaluation harness (LangSmith Baseline v1).

Reuses the PRODUCTION RAG code (imported from the ask app) so retrieved contexts,
citations and generated answers all come from the SAME execution. Nothing here is a
re-implementation of retrieval or generation.

Scope guarantees:
  * NO changes to retrieval params, prompts, models, chunking, embeddings, TOP_K or floor.
  * App-level LangSmith tracing is DISABLED for the harness (LANGSMITH_TRACING unset),
    so evaluation runs never pollute the operational project `multitenant-rag-prod`.
  * The semantic cache is BYPASSED: (a) a cache hit would skip generation and hide the
    RAG path we are measuring, (b) semcache.store() would otherwise write evaluation
    answers into the production per-tenant cache. Documented deviation.
  * RAGAS / DeepEval / LangChain / LangGraph: NOT USED (deferred by architect decision).

Evaluators:
  * request_success   — deterministic (1/0); the `[Error while generating response]`
                        fallback is NOT a success.
  * answer_correctness + answer_completeness — ONE structured judge call per case
                        (gpt-oss-20b) returning both scores, surfaced as two feedback keys.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from typing import Any

import boto3
import requests

# --- import production RAG code (env must be set before import) -------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas", "ask"))
os.environ.pop("LANGSMITH_TRACING", None)          # harness must not write to prod project
os.environ.setdefault("TENANTS_TABLE", "multitenant-tenants")
os.environ.setdefault("USAGE_TABLE", "multitenant-usage-logs")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import app as prod                                   # noqa: E402  (production ask app)
from common.secrets import get_groq_key              # noqa: E402

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "openai/gpt-oss-20b")
MIN_INTERVAL = float(os.environ.get("EVAL_MIN_INTERVAL_SECONDS", "20"))
MAX_RETRIES = int(os.environ.get("EVAL_MAX_RETRIES", "4"))
FALLBACK_TEXT = "[Error while generating response]"

_last_call = [0.0]
QUOTA: dict[str, Any] = {}      # most recent Groq rate-limit headers (safe values only)


def _pace() -> None:
    """Serialize + space out provider calls (max_concurrency is 1 by construction)."""
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _capture_quota(resp) -> None:
    """Record only non-sensitive rate-limit headers. Never logs auth values."""
    for h in ("x-ratelimit-limit-requests", "x-ratelimit-remaining-requests",
              "x-ratelimit-reset-requests", "x-ratelimit-limit-tokens",
              "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens", "retry-after"):
        if h in resp.headers:
            QUOTA[h] = resp.headers[h]


class RateLimited(Exception):
    pass


def judge_call(system: str, user: str) -> str:
    """Single judge completion with bounded backoff. Honors Retry-After."""
    key = get_groq_key()
    for attempt in range(MAX_RETRIES):
        _pace()
        r = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": JUDGE_MODEL, "temperature": 0,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "max_tokens": 700},
            timeout=90,
        )
        _capture_quota(r)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        if r.status_code == 429 and attempt < MAX_RETRIES - 1:
            ra = r.headers.get("retry-after")
            delay = float(ra) if ra else min(2 ** attempt * 5, 60)
            time.sleep(delay + random.uniform(0, 2))          # jitter
            continue
        raise RateLimited(f"judge HTTP {r.status_code}") if r.status_code == 429 \
            else RuntimeError(f"judge HTTP {r.status_code}")
    raise RateLimited("judge: retries exhausted")


# ---------------------------------------------------------------------------
# TARGET — runs the real RAG pipeline for one dataset example
# ---------------------------------------------------------------------------
def _tenant_of(name: str) -> str | None:
    return _NAME2TID.get((name or "").strip())


_NAME2TID: dict[str, str] = {}
_GRP2ID: dict[str, str] = {}


def load_seed_map(manifest_path: str) -> None:
    m = json.load(open(manifest_path))
    for t in m["tenants"]:
        _NAME2TID[t["display_name"].split(" (seed")[0]] = t["tenant_id"]
    for g in m["groups"]:
        _GRP2ID[g["name"].split(" (seed")[0]] = g["group_id"]


def _contexts_from(results) -> list[str]:
    """The ACTUAL retrieved chunk texts for this execution (no reconstruction)."""
    out = []
    for r in results:
        p = getattr(r, "payload", {}) or {}
        out.append(f"[{p.get('title','')}] {p.get('chunk_text','')}")
    return out


def rag_target(inputs: dict) -> dict:
    """Mirrors app.ask()/ask_group()/global_search_ep() decision flow using the
    production functions. Semantic cache intentionally bypassed (see module docstring)."""
    route = inputs.get("route"); target = inputs.get("target")
    question = inputs["question"]
    t0 = time.time()
    out = {"generated_answer": "", "retrieved_contexts": [], "citations": [],
           "status": "completed", "error_type": None, "model": None,
           "top_dense": None, "input_tokens": 0, "output_tokens": 0}
    try:
        if route == "global":
            dense = prod._embed_dense(question)
            qc = prod._get_qdrant_client()
            res = qc.query_points(collection_name=prod.COLLECTION_NAME, query=dense,
                                  using="dense", limit=10, with_payload=True)
            best: dict[str, dict] = {}
            for p in res.points:
                pid = p.payload.get("post_id")
                if not pid:
                    continue
                s = round(p.score, 4)
                if pid not in best or s > best[pid]["score"]:
                    best[pid] = {"title": p.payload.get("title", ""),
                                 "writer": prod._tenant_name(p.payload.get("tenant_id", "")),
                                 "score": s}
            ranked = sorted(best.values(), key=lambda c: c["score"], reverse=True)
            out["retrieved_contexts"] = _contexts_from(res.points)
            out["citations"] = [f'{r["title"]} — {r["writer"]} ({r["score"]})' for r in ranked]
            out["generated_answer"] = "; ".join(out["citations"]) or "(no results)"
            out["top_dense"] = ranked[0]["score"] if ranked else 0.0
            out["model"] = "retrieval-only (LLM-free)"
            return out

        # ---- LLM routes ----
        if route == "multi":
            tids = [_tenant_of(n) for n in (target or "").split(",")]
            tids = [t for t in tids if t]
            results, top = prod._hybrid_search_multi(question, tids)
            out["top_dense"] = round(top, 4)
            out["retrieved_contexts"] = _contexts_from(results)
            if not results or top < prod.RETRIEVAL_FLOOR:
                out["generated_answer"] = "No one in this selection has written about that yet."
                out["status"] = "completed"
                return out
            ctx = prod._llm_context(results)
            system = prod._build_group_system_prompt(ctx)
            model = prod.GROQ_MODEL
            cites = prod._dedupe_citations_attributed(ctx)
        elif route == "group":
            gid = _GRP2ID.get((target or "").strip())
            from common import groups as groupstore
            tids = groupstore.member_tenant_ids(gid) if gid else []
            results, top = prod._hybrid_search_multi(question, tids)
            out["top_dense"] = round(top, 4)
            out["retrieved_contexts"] = _contexts_from(results)
            if not results or top < prod.RETRIEVAL_FLOOR:
                out["generated_answer"] = "No one in this selection has written about that yet."
                return out
            ctx = prod._llm_context(results)
            system = prod._build_group_system_prompt(ctx)
            model = prod.GROQ_MODEL
            cites = prod._dedupe_citations_attributed(ctx)
        else:  # single profile
            tid = _tenant_of(target)
            tenant = prod._get_tenant(tid) if tid else None
            if not tenant:
                out["status"] = "application_error"; out["error_type"] = "TenantNotFound"
                return out
            results, top = prod._hybrid_search(question, tid)
            out["top_dense"] = round(top, 4)
            out["retrieved_contexts"] = _contexts_from(results)
            if not results or top < prod.RETRIEVAL_FLOOR:
                titles = prod._tenant_post_titles(tid)
                if not titles:
                    out["generated_answer"] = (
                        f"{tenant['display_name']} hasn't published any posts yet, "
                        "so there's nothing for me to answer from.")
                    return out
                system = prod._build_profile_prompt(tenant, titles)
                model = prod.GROQ_MODEL_SMALL
                cites = []
            else:
                ctx = prod._llm_context(results)
                system = prod._build_system_prompt(tenant, ctx)
                model = prod.GROQ_MODEL
                cites = prod._dedupe_citations(ctx)

        out["model"] = model
        parts = []
        try:
            _pace()
            for ev in prod.stream_answer(system, question, model=model):
                if ev["type"] == "content":
                    parts.append(ev["text"])
                elif ev["type"] == "usage":
                    out["input_tokens"] = ev.get("input_tokens", 0)
                    out["output_tokens"] = ev.get("output_tokens", 0)
        except Exception as e:
            msg = str(e)
            out["status"] = "provider_rate_limit" if "429" in msg else "generation_error"
            out["error_type"] = type(e).__name__
            out["generated_answer"] = FALLBACK_TEXT
            return out
        out["generated_answer"] = "".join(parts)
        out["citations"] = [c.get("title") if isinstance(c, dict) else str(c) for c in cites]
        if not out["generated_answer"].strip():
            out["status"] = "generation_error"; out["error_type"] = "EmptyAnswer"
            out["generated_answer"] = FALLBACK_TEXT
        return out
    except Exception as e:
        out["status"] = "application_error"; out["error_type"] = type(e).__name__
        return out
    finally:
        out["latency_ms"] = round((time.time() - t0) * 1000, 1)


# ---------------------------------------------------------------------------
# EVALUATORS
# ---------------------------------------------------------------------------
def request_success(outputs: dict, **_) -> dict:
    ans = (outputs or {}).get("generated_answer") or ""
    ok = bool(ans.strip()) and FALLBACK_TEXT not in ans and \
        (outputs or {}).get("status") == "completed"
    return {"key": "request_success", "score": 1 if ok else 0,
            "comment": (outputs or {}).get("status") or "unknown"}


JUDGE_SYS = (
    "You grade a retrieval-augmented answer against a reference answer. "
    "Reply with ONLY a JSON object, no prose:\n"
    '{"correctness_score":1.0|0.5|0.0,"correctness_reason":"<=25 words",'
    '"completeness_score":1.0|0.5|0.0,"completeness_reason":"<=25 words"}\n'
    "correctness: does the answer agree with the reference (no contradictions/invention)? "
    "1.0 correct, 0.5 partially correct, 0.0 incorrect.\n"
    "completeness: are ALL important facts of the reference present? "
    "1.0 all, 0.5 some missing, 0.0 most missing.\n"
    "A refusal/no-evidence answer is CORRECT when the reference expects a refusal."
)

_ALLOWED = {1.0, 0.5, 0.0}


def _parse_judge(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in judge output")
    d = json.loads(m.group(0))
    for k in ("correctness_score", "completeness_score"):
        v = float(d[k])
        if v not in _ALLOWED:                     # never silently rescale
            raise ValueError(f"{k}={v} not in {{1.0,0.5,0.0}}")
        d[k] = v
    return d


def combined_judge(inputs: dict, outputs: dict, reference_outputs: dict, **_) -> list[dict]:
    """ONE judge call -> TWO feedback keys (answer_correctness, answer_completeness)."""
    ans = (outputs or {}).get("generated_answer") or ""
    ref = (reference_outputs or {}).get("expected_answer") or ""
    if (outputs or {}).get("status") != "completed":
        # provider/app failure is NOT an answer-quality score
        return [{"key": "answer_correctness", "score": None,
                 "comment": f"not scored: {(outputs or {}).get('status')}"},
                {"key": "answer_completeness", "score": None,
                 "comment": f"not scored: {(outputs or {}).get('status')}"}]
    user = (f"QUESTION:\n{inputs.get('question')}\n\n"
            f"REFERENCE ANSWER:\n{ref}\n\nCANDIDATE ANSWER:\n{ans[:3000]}")
    last = None
    for attempt in range(3):                      # bounded retry: malformed output only
        try:
            d = _parse_judge(judge_call(JUDGE_SYS, user))
            return [
                {"key": "answer_correctness", "score": d["correctness_score"],
                 "comment": str(d.get("correctness_reason", ""))[:300]},
                {"key": "answer_completeness", "score": d["completeness_score"],
                 "comment": str(d.get("completeness_reason", ""))[:300]},
            ]
        except RateLimited as e:
            return [{"key": "answer_correctness", "score": None, "comment": "provider_rate_limit"},
                    {"key": "answer_completeness", "score": None, "comment": "provider_rate_limit"}]
        except Exception as e:
            last = e
            if attempt == 2:
                break
    return [{"key": "answer_correctness", "score": None,
             "comment": f"custom_evaluator_error: {type(last).__name__}"},
            {"key": "answer_completeness", "score": None,
             "comment": f"custom_evaluator_error: {type(last).__name__}"}]
