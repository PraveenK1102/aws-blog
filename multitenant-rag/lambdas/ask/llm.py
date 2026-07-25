"""Groq LLM streaming client.

Provider abstraction — Groq is v1's implementation. Swap by env var later.
"""

import os
from typing import Iterator

import requests

from common.logger import get_logger
from common.secrets import get_groq_key


log = get_logger("llm")

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def stream_answer(system_prompt: str, user_prompt: str) -> Iterator[dict]:
    """
    Stream tokens from Groq. Yields events:
      {"type": "content", "text": "..."}  — a token or word
      {"type": "usage", "input_tokens": N, "output_tokens": N}  — final metadata

    Uses OpenAI-compatible streaming API. Groq returns SSE (server-sent events).
    """
    api_key = get_groq_key()

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": True,
        "temperature": 0.3,   # low for factual RAG answers
        "max_tokens": 1024,
    }

    with requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        stream=True,
        timeout=30,
    ) as resp:
        if resp.status_code != 200:
            body = resp.text[:500]
            log.error("groq error", status=resp.status_code, body=body)
            raise RuntimeError(f"Groq error {resp.status_code}: {body}")

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
