"""NVIDIA GPT-OSS-20B model evaluation harness (OFFLINE, evaluation-only).

Application generation under test : NVIDIA openai/gpt-oss-20b
Judge                            : NVIDIA openai/gpt-oss-120b
Production (untouched)           : Groq openai/gpt-oss-120b

Retrieval + prompt construction are the PRODUCTION functions, imported from the
ask app — nothing about RAG is re-implemented and no retrieval setting changes.
The ONLY intentional difference vs production is which provider/model generates
the answer.

Invariant: **ZERO Groq calls.** `install_groq_guard()` replaces the production
Groq entry points with functions that raise, so any accidental use fails fast
instead of silently spending Groq quota.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time

# --- import production RAG code (env set before import) ---------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lambdas"))
sys.path.insert(0, os.path.join(_HERE, "..", "lambdas", "ask"))
os.environ.pop("LANGSMITH_TRACING", None)          # never write to multitenant-rag-prod
os.environ.setdefault("TENANTS_TABLE", "multitenant-tenants")
os.environ.setdefault("USAGE_TABLE", "multitenant-usage-logs")
os.environ.setdefault("AWS_REGION", "ap-south-1")

import app as prod                                  # noqa: E402
import nvidia_provider as nv                        # noqa: E402

APP_MODEL = os.environ.get("NVIDIA_APP_MODEL", "openai/gpt-oss-20b")
JUDGE_MODEL = os.environ.get("NVIDIA_JUDGE_MODEL", "openai/gpt-oss-120b")
APP_MAX_TOKENS = int(os.environ.get("NVIDIA_APP_MAX_TOKENS", "1200"))
JUDGE_MAX_TOKENS = int(os.environ.get("NVIDIA_JUDGE_MAX_TOKENS", "1200"))
FALLBACK_TEXT = "[Error while generating response]"
CTX_CHAR_CAP = 1200                                 # per-chunk cap for judge input only


class GroqCallForbidden(RuntimeError):
    """Raised if anything tries to use Groq during this experiment."""


def install_groq_guard() -> None:
    """Make any Groq usage fail fast (PHASE 3 zero-call assertion)."""
    def _forbidden(*_a, **_k):
        raise GroqCallForbidden(
            "Groq is disabled for rag-model-eval-nvidia20b-v1 (groq_calls_expected=0)")
    prod.stream_answer = _forbidden
    try:
        import llm as _llm
        _llm._post_with_retry = _forbidden
        _llm.stream_answer = _forbidden
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fingerprint (PHASE 4)
# ---------------------------------------------------------------------------
def fingerprint(dataset_id: str, dataset_version: str) -> dict:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_HERE,
                         capture_output=True, text=True).stdout.strip() or "unknown"
    fp = {
        "git_sha": sha,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "application_provider": "nvidia",
        "application_model": APP_MODEL,
        "judge_provider": "nvidia",
        "judge_model": JUDGE_MODEL,
        "embedding_model": "titan-text-v2",
        "sparse_model": "fastembed-bm25",
        "retrieval": "hybrid-rrf",
        "top_k": prod.TOP_K,
        "retrieval_floor": prod.RETRIEVAL_FLOOR,
        "prompt_version": "production-app.py@" + sha[:7],
    }
    fp["fingerprint_hash"] = hashlib.sha256(
        json.dumps(fp, sort_keys=True).encode()).hexdigest()[:16]
    return fp


# ---------------------------------------------------------------------------
# Seed map
# ---------------------------------------------------------------------------
_NAME2TID: dict[str, str] = {}
_GRP2ID: dict[str, str] = {}


def load_seed_map(manifest_path: str) -> None:
    m = json.load(open(manifest_path))
    for t in m["tenants"]:
        _NAME2TID[t["display_name"].split(" (seed")[0]] = t["tenant_id"]
    for g in m["groups"]:
        _GRP2ID[g["name"].split(" (seed")[0]] = g["group_id"]


def _contexts_from(results) -> list[str]:
    """ACTUAL retrieved chunk texts for THIS execution (never reconstructed)."""
    out = []
    for r in results:
        p = getattr(r, "payload", {}) or {}
        out.append(f"[{p.get('title','')}] {p.get('chunk_text','')}")
    return out


# ---------------------------------------------------------------------------
# APPLICATION: production retrieval/prompt + NVIDIA generation
# ---------------------------------------------------------------------------
def run_case(inputs: dict) -> dict:
    route = inputs.get("route")
    target = inputs.get("target")
    question = inputs["question"]
    out = {
        "generated_answer": "", "retrieved_contexts": [], "citations": [],
        "status": "completed", "error_type": None,
        "application_provider": "nvidia", "application_model": None,
        "top_dense": None, "app_input_tokens": None, "app_output_tokens": None,
        "app_generation_latency_ms": None, "retrieval_latency_ms": None,
        "app_retry_count": 0, "app_rate_limited": False,
    }
    try:
        # ---- retrieval (PRODUCTION logic, unchanged) ----
        t_r = time.time()
        if route == "global":
            # LLM-free by design — no NVIDIA generation call for global search
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
            out["retrieval_latency_ms"] = round((time.time() - t_r) * 1000, 1)
            out["retrieved_contexts"] = _contexts_from(res.points)
            out["citations"] = [f'{r["title"]} — {r["writer"]} ({r["score"]})' for r in ranked]
            out["generated_answer"] = "; ".join(out["citations"]) or "(no results)"
            out["top_dense"] = ranked[0]["score"] if ranked else 0.0
            out["application_model"] = "retrieval-only (LLM-free)"
            out["llm_used"] = False
            return out

        out["llm_used"] = True
        if route == "multi":
            tids = [_NAME2TID.get(n.strip()) for n in (target or "").split(",")]
            tids = [t for t in tids if t]
            results, top = prod._hybrid_search_multi(question, tids)
            ctx = prod._llm_context(results) if results else []
            system = prod._build_group_system_prompt(ctx) if ctx else None
            cites = prod._dedupe_citations_attributed(ctx) if ctx else []
            empty_msg = "No one in this selection has written about that yet."
        elif route == "group":
            gid = _GRP2ID.get((target or "").strip())
            from common import groups as groupstore
            tids = groupstore.member_tenant_ids(gid) if gid else []
            results, top = prod._hybrid_search_multi(question, tids)
            ctx = prod._llm_context(results) if results else []
            system = prod._build_group_system_prompt(ctx) if ctx else None
            cites = prod._dedupe_citations_attributed(ctx) if ctx else []
            empty_msg = "No one in this selection has written about that yet."
        else:  # single
            tid = _NAME2TID.get((target or "").strip())
            tenant = prod._get_tenant(tid) if tid else None
            if not tenant:
                out["status"] = "retrieval_error"; out["error_type"] = "TenantNotFound"
                return out
            results, top = prod._hybrid_search(question, tid)
            if not results or top < prod.RETRIEVAL_FLOOR:
                titles = prod._tenant_post_titles(tid)
                if not titles:
                    out["retrieval_latency_ms"] = round((time.time() - t_r) * 1000, 1)
                    out["generated_answer"] = (
                        f"{tenant['display_name']} hasn't published any posts yet, "
                        "so there's nothing for me to answer from.")
                    out["llm_used"] = False
                    return out
                system = prod._build_profile_prompt(tenant, titles)
                cites = []
            else:
                ctx = prod._llm_context(results)
                system = prod._build_system_prompt(tenant, ctx)
                cites = prod._dedupe_citations(ctx)
            empty_msg = None

        out["retrieval_latency_ms"] = round((time.time() - t_r) * 1000, 1)
        out["top_dense"] = round(top, 4)
        # Record the context the MODEL ACTUALLY RECEIVED (capped), because
        # groundedness is judged against it. `results` may be a wider pool.
        out["retrieval_candidate_count"] = len(results)
        out["llm_context_chunk_count"] = len(ctx)
        out["retrieved_contexts"] = _contexts_from(ctx)

        if route in ("multi", "group") and (not results or top < prod.RETRIEVAL_FLOOR):
            out["generated_answer"] = empty_msg
            out["llm_used"] = False
            return out

        # ---- generation: NVIDIA (the ONLY intentional change vs production) ----
        out["application_model"] = APP_MODEL
        try:
            r = nv.chat(APP_MODEL,
                        [{"role": "system", "content": system},
                         {"role": "user", "content": question}],
                        max_tokens=APP_MAX_TOKENS)
        except nv.CircuitOpen:
            raise
        except nv.NvidiaAuthError as e:
            out["status"] = "application_provider_error"; out["error_type"] = type(e).__name__
            out["generated_answer"] = FALLBACK_TEXT
            return out
        except nv.NvidiaError as e:
            msg = str(e)
            out["status"] = "application_rate_limit" if "429" in msg else "application_provider_error"
            out["error_type"] = type(e).__name__
            out["generated_answer"] = FALLBACK_TEXT
            return out
        out["app_input_tokens"] = r["input_tokens"]
        out["app_output_tokens"] = r["output_tokens"]
        out["app_generation_latency_ms"] = r["latency_ms"]
        out["app_retry_count"] = r["retry_count"]
        out["app_rate_limited"] = r["rate_limited"]
        out["generated_answer"] = r["content"]
        out["citations"] = [c.get("title") if isinstance(c, dict) else str(c) for c in cites]
        if not (r["content"] or "").strip():
            out["status"] = "generation_error"; out["error_type"] = "EmptyAnswer"
            out["generated_answer"] = FALLBACK_TEXT
        return out
    except nv.CircuitOpen:
        raise
    except Exception as e:
        out["status"] = "retrieval_error" if "qdrant" in type(e).__name__.lower() \
            else "application_provider_error"
        out["error_type"] = type(e).__name__
        return out


# ---------------------------------------------------------------------------
# DETERMINISTIC evaluator
# ---------------------------------------------------------------------------
def request_success(result: dict) -> int:
    ans = (result.get("generated_answer") or "").strip()
    return 1 if (ans and FALLBACK_TEXT not in ans and result.get("status") == "completed") else 0


# ---------------------------------------------------------------------------
# JUDGE: ONE structured 120B call -> correctness + completeness + groundedness
# ---------------------------------------------------------------------------
JUDGE_SYS = (
    "You are grading a retrieval-augmented answer. Reply with ONLY a JSON object, no prose:\n"
    '{"correctness":{"score":1.0,"reason":"<=25 words"},'
    '"completeness":{"score":1.0,"reason":"<=25 words"},'
    '"groundedness":{"score":1.0,"reason":"<=25 words"}}\n'
    "Each score must be exactly 1.0, 0.5 or 0.0.\n"
    "correctness: does the ANSWER agree factually with the REFERENCE? Equivalent wording passes. "
    "A refusal/no-evidence answer is correct when the reference expects a refusal.\n"
    "completeness: are the important facts of the REFERENCE present? Ignore harmless style differences.\n"
    "groundedness: are the factual claims in the ANSWER supported by the RETRIEVED CONTEXT? "
    "Judge support by the context ONLY — do not use the reference for this score. "
    "Unsupported/invented claims lower it.\n"
    "Give concise evaluation summaries, not step-by-step reasoning."
)
_ALLOWED = {1.0, 0.5, 0.0}
_DIMS = ("correctness", "completeness", "groundedness")


def parse_judge(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("no JSON object in judge output")
    d = json.loads(m.group(0))
    out = {}
    for dim in _DIMS:
        v = d[dim]
        score = float(v["score"] if isinstance(v, dict) else v)
        if score not in _ALLOWED:                      # never silently rescale
            raise ValueError(f"{dim} score {score} not in {{1.0,0.5,0.0}}")
        out[dim] = {"score": score,
                    "reason": str((v.get("reason") if isinstance(v, dict) else "") or "")[:300]}
    return out


def judge(result: dict, question: str, expected: str) -> dict:
    """One structured 120B judge call. Returns scores or a typed failure."""
    if not result.get("llm_used", True):
        return {"status": "not_scored", "reason": "route is LLM-free (global search)"}
    if result.get("status") != "completed":
        return {"status": "not_scored", "reason": f"application status={result.get('status')}"}
    ctx = "\n---\n".join(c[:CTX_CHAR_CAP] for c in (result.get("retrieved_contexts") or [])) \
          or "(no retrieved context)"
    user = (f"QUESTION:\n{question}\n\nREFERENCE ANSWER:\n{expected}\n\n"
            f"RETRIEVED CONTEXT:\n{ctx}\n\nANSWER TO GRADE:\n{(result.get('generated_answer') or '')[:3000]}")
    last = None
    for attempt in range(3):                           # bounded: malformed output only
        try:
            r = nv.chat(JUDGE_MODEL,
                        [{"role": "system", "content": JUDGE_SYS},
                         {"role": "user", "content": user}],
                        max_tokens=JUDGE_MAX_TOKENS)
        except nv.CircuitOpen:
            raise
        except nv.NvidiaAuthError as e:
            return {"status": "judge_provider_error", "reason": type(e).__name__}
        except nv.NvidiaError as e:
            kind = "judge_rate_limit" if "429" in str(e) else "judge_provider_error"
            return {"status": kind, "reason": type(e).__name__}
        try:
            scores = parse_judge(r["content"])
            return {"status": "scored", "scores": scores,
                    "judge_input_tokens": r["input_tokens"],
                    "judge_output_tokens": r["output_tokens"],
                    "judge_latency_ms": r["latency_ms"],
                    "judge_retry_count": r["retry_count"],
                    "judge_rate_limited": r["rate_limited"]}
        except Exception as e:
            last = e
    return {"status": "judge_parse_error", "reason": type(last).__name__ if last else "unknown"}


# ---------------------------------------------------------------------------
# Durable checkpointing (PHASE 13) — persisted after EVERY case, atomically
# ---------------------------------------------------------------------------
def load_checkpoint(path: str, fp_hash: str) -> dict:
    done = {}
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue                                # tolerate a torn last line
            if rec.get("fingerprint_hash") == fp_hash and rec.get("case_id"):
                done[rec["case_id"]] = rec              # later record wins
    return done


def append_checkpoint(path: str, record: dict) -> None:
    """Append + flush + fsync so a kill cannot lose a completed case."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
