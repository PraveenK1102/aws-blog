# NVIDIA Provider Validation — Model Inventory

**Date:** 2026-08-22 · **Endpoint:** `https://integrate.api.nvidia.com/v1` (OpenAI-compatible)
**Scope:** credential + model-access validation only. **No** application integration, **no** production
change, **no** Lambda/IAM change. Groq remains the production provider.

- **Authentication:** PASS (`GET /v1/models` → HTTP 200)
- **Total models returned by authenticated `/v1/models`:** **102**
- Model IDs below are the **exact API IDs from the authenticated response** — not website slugs.

## Architect shortlist

| Candidate | Exact API Model ID | Accessible | Smoke | Latency | Notes |
|---|---|---|---|---|---|
| OpenAI GPT-OSS 120B | `openai/gpt-oss-120b` | YES | **PASS** | 6389 ms | Reasoning model. First attempt at `max_tokens=40` timed out (90 s); passed cleanly at `max_tokens=200`. in/out 71/45. |
| OpenAI GPT-OSS 20B | `openai/gpt-oss-20b` | YES | **PASS** | 1339 ms | Reasoning model. in/out 71/101. Streaming verified (below). |
| Meta Llama 3.1 8B Instruct | `meta/llama-3.1-8b-instruct` | YES | **PASS** | 1561 ms | Non-reasoning; cleanest/cheapest (out = 4 tokens). No separate reasoning field. |
| NVIDIA Nemotron Nano 9B v2 | `nvidia/nvidia-nemotron-nano-9b-v2` | YES | **PASS** | 2841 ms | Reasoning model. in/out 21/128. |
| NVIDIA Nemotron 3 Nano 30B A3B | `nvidia/nemotron-3-nano-30b-a3b` | YES | **PASS** | 865 ms | Optional/low priority; tested because the first four were healthy and cost was immaterial. in/out 24/95. |

No ambiguity: each candidate resolved to exactly one non-excluded model ID.

**Excluded by instruction (not tested):** `llama-3.1-nemotron-safety-guard-8b-v3` (safety),
`llama-3.1-nemotron-nano-vl-8b-v1` (VL), `nemotron-3-embed-1b`, `nv-embedcode-7b-v1` (embeddings),
`rerank-qa-mistral-4b` (rerank), plus translation/audio/video/vision models.

## Operational finding — reasoning models need a token budget for content

Every candidate except `meta/llama-3.1-8b-instruct` is a **reasoning model** that emits tokens into
**separate** `reasoning` / `reasoning_content` fields. At `max_tokens=40` all of them returned
HTTP 200 with `finish_reason="length"` and an **empty `content`** — the budget was consumed by
reasoning before any answer text. At `max_tokens=200` all returned `"NVIDIA_OK"` with
`finish_reason="stop"`. Any future integration must size `max_tokens` accordingly.

## Streaming compatibility probe (`openai/gpt-oss-20b`)

| Check | Result |
|---|---|
| Stream request accepted | **YES** (HTTP 200, 39 SSE events) |
| Delta field shape | `['content', 'reasoning', 'reasoning_content', 'role']` |
| Separate reasoning fields present | **YES** (`reasoning`, `reasoning_content`) |
| Usage returned in stream | **YES** — `{prompt_tokens: 71, completion_tokens: 45, total_tokens: 116}` |
| Clean reconstruction from `delta.content` only | **YES** (`"NVIDIA_OK"`) |

**Compatibility verdict:** the existing `lambdas/ask/llm.py` SSE parser reads only `delta.content`
plus `usage`, so NVIDIA's stream is **structurally drop-in** — reasoning stays in fields the parser
already ignores, exactly as with Groq. A future provider abstraction would need only a different base
URL + credential. **`llm.py` was NOT modified.**

## Rate limits / quota visibility

NVIDIA returned **no** `x-ratelimit-*` or `retry-after` headers on any call (success or otherwise), so
**remaining quota is not observable from responses**. No 429 or quota error was encountered during
validation. Note this mirrors a gap found the same day on Groq, whose headers expose RPD and TPM but
**not** the tokens-per-day (TPD) limit that actually throttles us — quota gating cannot rely on
response headers for either provider.

## Credential

- Secret: `multitenant/nvidia` in `ap-south-1` (ARN suffix `-xdJYZn`), created by the user.
- Verified: secret exists, contains only the key `api_key`, value non-empty. **Value never printed.**
- Contains **only** the credential — no model name, base URL, or provider config.
- **No IAM grant added.** `multitenant-ask-role` cannot read this secret; production does not use NVIDIA.

## Future experiments (recorded only — NOT executed)

All must reuse the same LangSmith golden dataset (`multitenant-rag-eval-60-v1`) with **unchanged**
retrieval settings (Titan V2 + BM25 + RRF, TOP_K 5, floor 0.15).

| ID | Configuration | Purpose |
|---|---|---|
| A | Groq + `openai/gpt-oss-120b` | Baseline provider (current production) |
| B | NVIDIA + `openai/gpt-oss-120b` | **Provider comparison, model held constant** |
| C | NVIDIA + `openai/gpt-oss-20b` | Smaller-model quality/latency |
| D | NVIDIA + `meta/llama-3.1-8b-instruct` | True ~8B general-purpose RAG candidate |
| E | NVIDIA + `nvidia/nvidia-nemotron-nano-9b-v2` | Alternative small reasoning model |

Embedding-provider comparison (e.g. `nemotron-3-embed-1b`) is a separate future experiment; Bedrock
Titan V2, Qdrant, BM25, RRF, TOP_K and the retrieval floor were **not** touched.
