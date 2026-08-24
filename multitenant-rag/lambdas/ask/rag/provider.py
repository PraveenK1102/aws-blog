"""Deadline-aware, non-streaming Groq client for the routed path.

WHY A SECOND CLIENT INSTEAD OF REUSING llm.stream_answer
--------------------------------------------------------
`llm.stream_answer` is the streaming client for the existing production path and
stays exactly as it is. It is unsuitable for the routed path for two reasons the
audit surfaced:

  * `timeout=60` on every request, and `MAX_RETRIES = 4` with waits up to 30 s
    each. A single 429 storm can therefore block for well over the 30,000 ms API
    Gateway deadline — the request dies at the gateway with no controlled
    response.
  * The router and decomposition calls want one complete JSON object, not a
    token stream.

This module makes every call bounded by what remains of the request budget, and
implements the reactive-only 429 policy: production NEVER paces proactively.
The 7 s experiment pacing is deliberately absent — see PRODUCTION-ROUTED-RAG-
HARDENING.md §17.
"""
import json
import re
import time

import requests

from common.logger import get_logger
from common.secrets import get_groq_key

from . import config
from .budget import DeadlineExceeded

log = get_logger("rag.provider")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)s")


class ProviderError(RuntimeError):
    """Groq call failed in a way the caller must handle (fallback or error)."""

    def __init__(self, kind: str, status: int | None = None):
        super().__init__(f"groq {kind}" + (f" status={status}" if status else ""))
        self.kind = kind
        self.status = status


class ProviderRateLimited(ProviderError):
    """429 that could NOT be retried inside the remaining request budget."""

    def __init__(self, suggested_wait_ms: int | None = None):
        super().__init__("rate_limited", 429)
        self.suggested_wait_ms = suggested_wait_ms


def _suggested_wait_ms(body: str, headers) -> int | None:
    """Provider's own hint, from the body text or the reset header."""
    m = _RETRY_AFTER_RE.search(body or "")
    if m:
        return int(float(m.group(1)) * 1000)
    ra = (headers or {}).get("retry-after")
    if ra:
        try:
            return int(float(ra) * 1000)
        except ValueError:
            pass
    return None


def chat(model: str, messages: list[dict], *, budget, kind: str,
         max_tokens: int, ceiling_ms: int, temperature: float = 0.0) -> dict:
    """One bounded, non-streaming Groq completion.

    Consumes budget BEFORE the call (so a malformed graph cannot over-spend) and
    derives its HTTP timeout from what remains of the request deadline. Raises
    ProviderError / ProviderRateLimited / DeadlineExceeded — never sleeps longer
    than the deadline permits, and never sleeps at all unless the provider itself
    returned a 429 with a short enough hint.
    """
    budget.spend_groq(kind)                      # raises BudgetExceeded first
    timeout_s = budget.timeout_for(kind, ceiling_ms) / 1000.0   # raises DeadlineExceeded

    api_key = get_groq_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": False,
               "temperature": temperature, "max_tokens": max_tokens}

    attempts_left = config.MAX_RATE_LIMIT_RETRIES + 1
    while attempts_left > 0:
        attempts_left -= 1
        t0 = time.monotonic()
        try:
            resp = requests.post(GROQ_API_URL, headers=headers, json=payload,
                                 timeout=timeout_s)
        except requests.Timeout:
            log.warning("groq timeout", kind=kind, timeout_s=round(timeout_s, 1))
            raise ProviderError("timeout")
        except requests.RequestException as e:
            log.warning("groq transport error", kind=kind, error_type=type(e).__name__)
            raise ProviderError(f"transport:{type(e).__name__}")

        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if resp.status_code == 200:
            try:
                d = resp.json()
            except json.JSONDecodeError:
                raise ProviderError("bad_json", 200)
            usage = d.get("usage") or {}
            budget.record_tokens(usage.get("prompt_tokens"), usage.get("completion_tokens"))
            choice = (d.get("choices") or [{}])[0]
            return {"content": (choice.get("message") or {}).get("content") or "",
                    "finish_reason": choice.get("finish_reason"),
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                    "latency_ms": latency_ms}

        if resp.status_code == 429:
            budget.record_rate_limit()
            wait_ms = _suggested_wait_ms(resp.text[:500], resp.headers)
            # A retry is allowed ONLY when the provider's own wait is short AND
            # the remaining request budget fits BOTH the wait and another call.
            fits = (wait_ms is not None
                    and wait_ms <= config.MAX_RATE_LIMIT_WAIT_MS
                    and attempts_left > 0
                    and budget.can_afford_ms(wait_ms + config.MIN_CALL_HEADROOM_MS))
            log.info("groq rate limited", kind=kind, suggested_wait_ms=wait_ms,
                     remaining_budget_ms=budget.remaining_ms(), retrying=bool(fits))
            if not fits:
                raise ProviderRateLimited(wait_ms)
            budget.record_retry()
            time.sleep(wait_ms / 1000.0)
            # Re-derive the timeout: the sleep consumed real budget.
            try:
                timeout_s = budget.timeout_for(kind, ceiling_ms) / 1000.0
            except DeadlineExceeded:
                raise ProviderRateLimited(wait_ms)
            continue

        body = resp.text[:300]
        log.error("groq error", kind=kind, status=resp.status_code, body=body)
        raise ProviderError("http_error", resp.status_code)

    raise ProviderRateLimited(None)
