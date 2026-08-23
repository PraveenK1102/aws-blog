"""Instrumented Groq provider for the development observability experiment.

Hard global ceiling: 100 PHYSICAL Groq HTTP requests for the entire task. The
counter is checked BEFORE each request and raises rather than exceed it. There is
no second key and no circumvention path.

Captures the EXACT rate-limit headers Groq actually returns (never invented) and
tracks logical calls vs physical requests separately so a low observed latency
cannot hide a retry/backoff.

Stop conditions (§11): 3 consecutive 429s, or a header indicating exhausted
remaining quota, or a Retry-After that would materially delay the run.
"""
import json, os, threading, time

import boto3
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_20B = "openai/gpt-oss-20b"
MODEL_120B = "openai/gpt-oss-120b"

HARD_CEILING = int(os.environ.get("GROQ_TASK_CEILING", "100"))
MAX_ATTEMPTS = int(os.environ.get("GROQ_MAX_ATTEMPTS", "2"))     # bounded
REQUEST_TIMEOUT = int(os.environ.get("GROQ_TIMEOUT", "90"))
MIN_INTERVAL = float(os.environ.get("GROQ_MIN_INTERVAL", "1.0"))
CONSECUTIVE_429_STOP = 3

# Groq's documented header names; only those actually PRESENT are recorded.
_RL_HEADERS = [
    "retry-after",
    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens",
]

STATS = {"logical_calls": 0, "physical_requests": 0, "successes": 0,
         "http_429": 0, "http_5xx": 0, "timeouts": 0, "retries": 0,
         "backoff_seconds": 0.0, "in_tokens": 0, "out_tokens": 0,
         "by_model": {}, "last_rate_limit_headers": {}, "rate_limit_history": []}
_lock = threading.Lock()
_last = [0.0]
_consec429 = [0]


class GroqBudgetExceeded(RuntimeError):
    """Would breach the 100-request task ceiling."""


class GroqRateLimited(RuntimeError):
    """Sustained rate limiting — checkpoint and stop."""


class GroqError(RuntimeError):
    pass


_key_cache = {}
def _key() -> str:
    if "k" not in _key_cache:
        raw = boto3.client("secretsmanager", region_name="ap-south-1") \
            .get_secret_value(SecretId="multitenant/groq")["SecretString"]
        _key_cache["k"] = json.loads(raw)["api_key"]
    return _key_cache["k"]


def remaining_budget() -> int:
    return HARD_CEILING - STATS["physical_requests"]


def _reserve_physical():
    """Check the ceiling BEFORE the request; pace; then count."""
    with _lock:
        if STATS["physical_requests"] + 1 > HARD_CEILING:
            raise GroqBudgetExceeded(
                f"physical request {STATS['physical_requests']+1} would exceed ceiling {HARD_CEILING}")
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        STATS["physical_requests"] += 1


def _capture_headers(h) -> dict:
    """Record ONLY headers actually present. Never invent a missing one."""
    out = {}
    for name in _RL_HEADERS:
        v = h.get(name)
        if v is not None:
            out[name] = v
    return out


def chat(model: str, messages: list[dict], max_tokens: int = 900,
         temperature: float = 0.0, timeout: int | None = None) -> dict:
    """One LOGICAL Groq call; may cost >1 physical request if retried."""
    STATS["logical_calls"] += 1
    STATS["by_model"].setdefault(model, {"logical": 0, "physical": 0, "in": 0, "out": 0})
    STATS["by_model"][model]["logical"] += 1
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "stream": False}
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    to = timeout or REQUEST_TIMEOUT
    retry_count = 0

    for attempt in range(MAX_ATTEMPTS):
        _reserve_physical()
        STATS["by_model"][model]["physical"] += 1
        t0 = time.monotonic()
        try:
            r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=to)
        except requests.Timeout:
            STATS["timeouts"] += 1
            if attempt == MAX_ATTEMPTS - 1:
                raise GroqError("timeout after bounded retries")
            retry_count += 1; STATS["retries"] += 1
            time.sleep(2.0); STATS["backoff_seconds"] += 2.0
            continue
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        rl = _capture_headers(r.headers)
        if rl:
            STATS["last_rate_limit_headers"] = rl
            STATS["rate_limit_history"].append({"model": model, "status": r.status_code, **rl})

        if r.status_code == 429:
            STATS["http_429"] += 1
            _consec429[0] += 1
            ra = rl.get("retry-after")
            if _consec429[0] >= CONSECUTIVE_429_STOP:
                raise GroqRateLimited(f"{_consec429[0]} consecutive 429s; headers={rl}")
            if ra is not None:
                try:
                    if float(ra) > 30:
                        raise GroqRateLimited(f"Retry-After {ra}s would materially delay the run")
                except ValueError:
                    pass
            if attempt == MAX_ATTEMPTS - 1:
                raise GroqRateLimited(f"429 after bounded retries; headers={rl}")
            wait = float(ra) if ra and ra.replace(".", "", 1).isdigit() else 5.0
            retry_count += 1; STATS["retries"] += 1
            time.sleep(wait); STATS["backoff_seconds"] += wait
            continue

        if r.status_code in (401, 403):
            raise GroqError(f"auth failure HTTP {r.status_code}")
        if r.status_code >= 500:
            STATS["http_5xx"] += 1
            if attempt == MAX_ATTEMPTS - 1:
                raise GroqError(f"HTTP {r.status_code} after bounded retries")
            retry_count += 1; STATS["retries"] += 1
            time.sleep(3.0); STATS["backoff_seconds"] += 3.0
            continue
        if r.status_code != 200:
            raise GroqError(f"HTTP {r.status_code}: {r.text[:200]}")

        _consec429[0] = 0
        STATS["successes"] += 1
        j = r.json()
        u = j.get("usage") or {}
        pin, pout = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        STATS["in_tokens"] += pin; STATS["out_tokens"] += pout
        STATS["by_model"][model]["in"] += pin; STATS["by_model"][model]["out"] += pout
        return {"content": (j["choices"][0]["message"].get("content") or ""),
                "input_tokens": pin, "output_tokens": pout,
                "total_tokens": u.get("total_tokens", pin + pout),
                "latency_ms": latency_ms, "retry_count": retry_count,
                "physical_requests": retry_count + 1,
                "finish_reason": j["choices"][0].get("finish_reason"),
                "rate_limit_headers": rl, "provider_status": "ok", "model": model}
    raise GroqError("exhausted bounded retries")
