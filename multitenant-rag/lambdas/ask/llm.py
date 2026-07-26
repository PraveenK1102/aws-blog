"""Groq LLM streaming client.

Provider abstraction — Groq is v1's implementation. Swap by env var later.
"""

import os
import re
import time
from typing import Iterator

import requests

from common.logger import get_logger
from common.secrets import get_groq_key


log = get_logger("llm")

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
# Small/fast model for easy tasks (the empty-retrieval overview-or-decline call),
# so we don't spend the 70B on them. Defaults to the same as GROQ_MODEL if unset,
# so behaviour is unchanged until prod sets this to an 8B model.
GROQ_MODEL_SMALL = os.environ.get("GROQ_MODEL_SMALL", GROQ_MODEL)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_RETRIES = 4  # on 429 rate-limit, honoring Groq's suggested wait


def _post_with_retry(payload: dict, api_key: str) -> requests.Response:
    """POST to Groq, retrying on 429 using the 'try again in Xs' hint."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, stream=True, timeout=60)
        if resp.status_code == 200:
            return resp
        if resp.status_code == 429 and attempt < MAX_RETRIES - 1:
            body = resp.text
            resp.close()
            m = re.search(r"try again in ([\d.]+)s", body)
            wait = min((float(m.group(1)) + 0.5) if m else 5.0, 30.0)
            log.info("groq rate limited; retrying", wait_seconds=round(wait, 1), attempt=attempt + 1)
            time.sleep(wait)
            continue
        body = resp.text[:500]
        resp.close()
        log.error("groq error", status=resp.status_code, body=body)
        raise RuntimeError(f"Groq error {resp.status_code}: {body}")
    raise RuntimeError("Groq error: exhausted retries")


def stream_answer(system_prompt: str, user_prompt: str, history: list | None = None,
                  model: str | None = None) -> Iterator[dict]:
    """
    Stream tokens from Groq. Yields events:
      {"type": "content", "text": "..."}  — a token or word
      {"type": "usage", "input_tokens": N, "output_tokens": N}  — final metadata

    `history` is prior conversation turns [{"role":"user"|"assistant","content":...}]
    inserted between the system prompt and the current question, so follow-ups
    ("yes", "tell me more") have context.

    `model` overrides the default (GROQ_MODEL) — used to route easy tasks to a
    cheaper/faster model (see GROQ_MODEL_SMALL).

    Uses OpenAI-compatible streaming API. Groq returns SSE (server-sent events).
    """
    api_key = get_groq_key()

    messages = [{"role": "system", "content": system_prompt}]
    for turn in (history or []):
        role = turn.get("role")
        content = turn.get("content") or turn.get("text") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": model or GROQ_MODEL,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,   # low for factual RAG answers
        "max_tokens": 1024,
    }

    with _post_with_retry(payload, api_key) as resp:
        input_tokens = 0
        output_tokens = 0

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue

            data = line[6:]  # strip "data: "
            if data == "[DONE]":
                yield {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens}
                return

            try:
                import json
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            # Track usage if provided (Groq includes usage in final chunk)
            usage = event.get("x_groq", {}).get("usage") or event.get("usage")
            if usage:
                input_tokens = usage.get("prompt_tokens", input_tokens)
                output_tokens = usage.get("completion_tokens", output_tokens)

            choices = event.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            content = delta.get("content")
            if content:
                yield {"type": "content", "text": content}
