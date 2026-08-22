"""NVIDIA NIM provider for OFFLINE EVALUATION ONLY.

One GLOBAL request scheduler is shared by every NVIDIA call (20B application
generation AND 120B judge), because the free endpoint exposes no usable
rate-limit headers:

  * concurrency 1 (sequential by construction)
  * minimum interval between ANY two NVIDIA requests: NVIDIA_MIN_INTERVAL (default 6s)
  * 429 -> honor Retry-After, else bounded backoff 30/60/120s + jitter
  * 3 CONSECUTIVE 429s -> CircuitOpen (caller must stop; never switch provider, never pay)

Never used by the production Lambda. Credential is read from Secrets Manager
(`multitenant/nvidia`) using the executor's AWS credentials and is never logged.
"""
from __future__ import annotations

import json
import os
import random
import time

import boto3
import requests

BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
CHAT_URL = f"{BASE_URL}/chat/completions"
MIN_INTERVAL = float(os.environ.get("NVIDIA_MIN_INTERVAL", "6"))
MAX_ATTEMPTS = int(os.environ.get("NVIDIA_MAX_ATTEMPTS", "4"))
BACKOFF = [30, 60, 120]                      # per architect: bounded, escalating
CIRCUIT_THRESHOLD = 3                        # consecutive 429s


class CircuitOpen(RuntimeError):
    """3 consecutive NVIDIA 429s — stop the experiment safely."""


class NvidiaAuthError(RuntimeError):
    """401/403 — stop immediately."""


class NvidiaError(RuntimeError):
    """Transient/other provider failure after bounded retries."""


STATS = {
    "requests": 0, "successes": 0, "http_429": 0, "http_5xx": 0,
    "timeouts": 0, "retries": 0, "circuit_breaker_events": 0,
    "pacing_wait_seconds": 0.0,
}
_last_call = [0.0]
_consecutive_429 = [0]
_key_cache: list = [None]


def _key() -> str:
    if _key_cache[0] is None:
        raw = boto3.client("secretsmanager", region_name="ap-south-1") \
            .get_secret_value(SecretId="multitenant/nvidia")["SecretString"]
        _key_cache[0] = json.loads(raw)["api_key"]
    return _key_cache[0]


def _pace() -> None:
    """Global rate gate. Pacing time is orchestration overhead — it is recorded
    separately and must NOT be counted as model/application latency."""
    wait = MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        STATS["pacing_wait_seconds"] += wait
        time.sleep(wait)
    _last_call[0] = time.time()


def chat(model: str, messages: list[dict], max_tokens: int = 900,
         temperature: float = 0.0, timeout: int = 180) -> dict:
    """One NVIDIA chat completion through the global scheduler.

    Returns {content, input_tokens, output_tokens, latency_ms, retry_count,
             finish_reason, rate_limited}. `latency_ms` EXCLUDES pacing sleep.
    """
    retries = 0
    rate_limited = False
    for attempt in range(MAX_ATTEMPTS):
        _pace()
        STATS["requests"] += 1
        t0 = time.time()
        try:
            r = requests.post(
                CHAT_URL,
                headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature},
                timeout=timeout,
            )
        except requests.Timeout:
            STATS["timeouts"] += 1
            retries += 1; STATS["retries"] += 1
            if attempt == MAX_ATTEMPTS - 1:
                raise NvidiaError("timeout after bounded retries")
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)] / 4 + random.uniform(0, 2))
            continue
        latency = round((time.time() - t0) * 1000, 1)

        if r.status_code in (401, 403):
            raise NvidiaAuthError(f"HTTP {r.status_code}")

        if r.status_code == 429:
            STATS["http_429"] += 1
            rate_limited = True
            _consecutive_429[0] += 1
            if _consecutive_429[0] >= CIRCUIT_THRESHOLD:
                STATS["circuit_breaker_events"] += 1
                raise CircuitOpen(f"{_consecutive_429[0]} consecutive 429s")
            if attempt == MAX_ATTEMPTS - 1:
                raise NvidiaError("429 after bounded retries")
            ra = r.headers.get("retry-after")
            delay = float(ra) if ra else BACKOFF[min(attempt, len(BACKOFF) - 1)]
            retries += 1; STATS["retries"] += 1
            time.sleep(delay + random.uniform(0, 3))
            continue

        if r.status_code >= 500:
            STATS["http_5xx"] += 1
            retries += 1; STATS["retries"] += 1
            if attempt == MAX_ATTEMPTS - 1:
                raise NvidiaError(f"HTTP {r.status_code} after bounded retries")
            time.sleep(min(5 * (attempt + 1), 20) + random.uniform(0, 2))
            continue

        if r.status_code != 200:
            raise NvidiaError(f"HTTP {r.status_code}")

        _consecutive_429[0] = 0              # success resets the circuit
        STATS["successes"] += 1
        j = r.json()
        ch = (j.get("choices") or [{}])[0]
        msg = ch.get("message", {}) or {}
        u = j.get("usage") or {}
        return {
            "content": (msg.get("content") or "").strip(),
            "input_tokens": u.get("prompt_tokens"),
            "output_tokens": u.get("completion_tokens"),
            "latency_ms": latency,
            "retry_count": retries,
            "finish_reason": ch.get("finish_reason"),
            "rate_limited": rate_limited,
        }
    raise NvidiaError("exhausted attempts")
