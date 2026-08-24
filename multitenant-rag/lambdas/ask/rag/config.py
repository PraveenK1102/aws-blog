"""Routed-RAG configuration: feature flag, deadline, bounded timeouts, models.

Every value is env-overridable so the deployment can be tuned without a code
change, but the DEFAULTS are the architect-approved production values.
"""
import os

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in _TRUTHY


# --- Feature flag -----------------------------------------------------------
# false (DEFAULT) -> the existing production RAG path runs, byte-for-byte.
# true            -> the routed LangGraph path runs.
# This is a deploy/rollback switch, NOT a percentage canary.
def routed_rag_enabled() -> bool:
    """Read at request time so flipping the Lambda env var takes effect on the
    next invocation without a redeploy (and without a cold start)."""
    return _flag("ROUTED_RAG_ENABLED", "false")


# --- Models (architect-frozen arrangement) ---------------------------------
# Router V2 + decomposition -> Groq 20B; final generation -> Groq 120B.
ROUTER_MODEL = os.environ.get("ROUTED_ROUTER_MODEL",
                              os.environ.get("GROQ_MODEL_SMALL", "openai/gpt-oss-20b"))
DECOMPOSE_MODEL = os.environ.get("ROUTED_DECOMPOSE_MODEL",
                                 os.environ.get("GROQ_MODEL_SMALL", "openai/gpt-oss-20b"))
GENERATION_MODEL = os.environ.get("ROUTED_GENERATION_MODEL",
                                  os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"))

# --- Deadline ---------------------------------------------------------------
# API Gateway HTTP API AWS_PROXY integration TimeoutInMillis is verified at
# 30,000 ms. 24,000 ms leaves 6 s of headroom for cold start, LWA buffering,
# response assembly and network.
REQUEST_DEADLINE_MS = int(os.environ.get("REQUEST_DEADLINE_MS", "24000"))

# Never start a new external operation with less than this left — a call that
# cannot finish is worse than not making it (it burns the remaining budget AND
# fails).
MIN_CALL_HEADROOM_MS = int(os.environ.get("MIN_CALL_HEADROOM_MS", "1200"))

# Reserve kept for post-generation work (citations, cache write, NDJSON assembly)
# so the last provider call cannot consume the entire deadline.
TAIL_RESERVE_MS = int(os.environ.get("TAIL_RESERVE_MS", "1500"))

# --- Per-operation ceilings (each is ALSO clamped to remaining budget) ------
ROUTER_TIMEOUT_MS = int(os.environ.get("ROUTED_ROUTER_TIMEOUT_MS", "6000"))
DECOMPOSE_TIMEOUT_MS = int(os.environ.get("ROUTED_DECOMPOSE_TIMEOUT_MS", "6000"))
GENERATION_TIMEOUT_MS = int(os.environ.get("ROUTED_GENERATION_TIMEOUT_MS", "12000"))
TITAN_TIMEOUT_MS = int(os.environ.get("ROUTED_TITAN_TIMEOUT_MS", "4000"))
QDRANT_TIMEOUT_MS = int(os.environ.get("ROUTED_QDRANT_TIMEOUT_MS", "5000"))

# --- Token ceilings (parity with the validated experiment) -----------------
ROUTER_MAX_TOKENS = int(os.environ.get("ROUTED_ROUTER_MAX_TOKENS", "1500"))
DECOMPOSE_MAX_TOKENS = int(os.environ.get("ROUTED_DECOMPOSE_MAX_TOKENS", "700"))
GENERATION_MAX_TOKENS = int(os.environ.get("ROUTED_GENERATION_MAX_TOKENS", "1024"))

# --- Concurrency -----------------------------------------------------------
# Semaphore(2) — the validated bounded branch concurrency.
MAX_BRANCH_CONCURRENCY = int(os.environ.get("ROUTED_BRANCH_CONCURRENCY", "2"))

# --- 429 policy ------------------------------------------------------------
# A rate-limit retry is allowed ONLY when the provider's own suggested wait is
# shorter than this AND the remaining request budget still permits the retry
# plus the call itself. Production NEVER paces proactively (see §16) — this is
# purely reactive and deadline-gated.
MAX_RATE_LIMIT_WAIT_MS = int(os.environ.get("ROUTED_MAX_RATE_LIMIT_WAIT_MS", "2000"))
MAX_RATE_LIMIT_RETRIES = int(os.environ.get("ROUTED_MAX_RATE_LIMIT_RETRIES", "1"))
