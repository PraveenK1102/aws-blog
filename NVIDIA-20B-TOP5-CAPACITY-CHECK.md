# NVIDIA GPT-OSS-20B — Realistic Top-5 Capacity Check

Date 2026-08-23 · 3 application-generation cases only · **no judge, no Groq, no quality scoring**

## 1. Git / AWS state (verified, not assumed)

| Item | Value |
|---|---|
| branch / HEAD | `main` / `d5af30e` |
| `origin/main` | `d5af30e` (in sync) |
| Deployed ask image | `multitenant-ask:d5af30e7f4cc679b2625d6a623d4a7857b1f8094` |
| Deployed digest | `sha256:2791dfa4059193ad5488402eda3dcc9caa4aa51d91167fee47cfd7e6addb3e48` |
| `MAX_LLM_CONTEXT_CHUNKS` | **5** (env, 14 keys total) |
| `GROQ_MODEL` (production, untouched) | `openai/gpt-oss-120b` |
| Cap present in committed image code | yes — 10 references to `_llm_context` / `MAX_LLM_CONTEXT_CHUNKS` in `app.py@d5af30e` |

Deployed state **matches the handoff**, so no `DECISION REQUIRED — DEPLOYED STATE DOES NOT MATCH HANDOFF`.

## 2. Selected cases (chosen by route + title only, before running; prior scores never consulted)

| Slot | case_id | route | title |
|---|---|---|---|
| A — single-profile factual | **case-024** | single | `single:Dev Iyer` (Forty-Minute Rule / newest Quill Cell) |
| B — multi / cross-document | **case-004** | multi | `Dev Iyer + Arjun Vale — cross-user QL-2D` |
| C — group, compound/conflicting | **case-022** | group | `Field Circle — compound Meral + Hollow Stair` |

Case-004 was deliberately included because it is the case that **stalled ~7 minutes** during the
pre-cap run — an infrastructure event, not a score — making it the cleanest before/after with a
single changed variable.

## 3. Context-cap verification (asserted at runtime, not assumed)

| case | candidates retrieved | → LLM context chunks | distinct source posts in context | chat history |
|---|---|---|---|---|
| case-024 (single) | 5 | **5** | 2 | no |
| case-004 (multi) | **10** | **5** ✅ capped | 1 | no |
| case-022 (group) | **10** | **5** ✅ capped | 3 | no |

Retrieval breadth unchanged (group/multi still fetch and rank 10). The run asserts
`llm_context_chunk_count <= MAX_LLM_CONTEXT_CHUNKS` for every case. No manual text truncation was
used anywhere — only the production `_llm_context()` helper.

## 4. Per-case measurements

| case | route | est. ctx tok | est. total prompt tok | **actual input tok** | actual output tok | latency | HTTP | timeout | 429 | 5xx | valid answer |
|---|---|---|---|---|---|---|---|---|---|---|---|
| case-024 | single | 1,967 | 2,551 | **2,240** | 340 | **2,853.7 ms** | 200 | no | no | no | **yes** |
| case-004 | multi | 1,965 | 2,184 | **1,829** | 211 | **3,589.7 ms** | 200 | no | no | no | **yes** |
| case-022 | group | 2,431 | 2,658 | **2,003** | 77 | **968.5 ms** | 200 | no | no | no | **yes** |

Retrieval latency (offline harness): 1,489 / 468 / 1,072 ms. `finish_reason = stop` on all three
(no truncation). Estimated-vs-actual input tokens are within ~10–16%, confirming the `chars/4`
heuristic is a reasonable planning proxy (it slightly over-estimates here).

## 5. Timeout / rate-limit observations

| Metric | Value |
|---|---|
| NVIDIA requests | 3 |
| successes | 3 |
| HTTP 429 | **0** |
| HTTP 5xx | **0** |
| timeouts | **0** |
| retries | **0** |
| circuit-breaker events | **0** |
| pacing sleep (orchestration overhead, excluded from latencies) | 4.01 s |

Bounded settings used: explicit **120 s** timeout per request, `NVIDIA_MAX_ATTEMPTS=2` (one initial
+ at most one retry), concurrency 1, 6 s minimum interval. Nothing sat blocked; the longest single
request was 3.6 s.

## 6. Conclusion

### **NVIDIA 20B TOP-5 REALISTIC CAPACITY CHECK = PASS**

All three routes succeeded with realistic top-5 RAG prompts (~1.8k–2.2k actual input tokens).

**Before vs after, one variable changed:**

| | pre-cap (10 chunks) | post-cap (5 chunks) |
|---|---|---|
| group/multi prompt size | ~4.9k tokens | ~2.0–2.7k tokens |
| case-004 outcome | **network-blocked ~7 min, no response** | **HTTP 200 in 3.6 s** |

This confirms the earlier diagnosis: the NVIDIA free endpoint's failure was **input-size dependent**,
and the top-5 cap moves realistic RAG prompts back into the range it serves reliably.

⚠️ **What this does NOT establish:** three successful requests prove neither sustained throughput,
quota, SLA, nor availability. The free endpoint still exposes no rate-limit headers, and the earlier
run degraded only after a number of requests. A 60-case run remains a materially larger load.

## 7. Two quality flags for the architect (observations only — no judge invoked, no correctness scored)

These are recorded because they are visible in the raw output, not because quality was assessed:

1. **case-022 (group, compound) returned a refusal** — `"No one in this selection has written about
   this."` (77 output tokens) despite `top_dense = 0.5728` and 3 distinct source posts in context.
   The same compound question is recorded in the original suite as having been answered well when the
   group path sent **10** chunks. This may indicate the 5-chunk cap removes evidence needed for
   compound/multi-fact questions.
2. **case-004 (cross-document) had only 1 distinct source post** among its top-5 chunks — all five
   RRF winners came from a single post, so the second author's contributing evidence may not be
   present at all.

Both are exactly the kind of trade-off the planned adaptive 5→10→15 escalation is meant to address.
Neither is confirmed: verifying them requires the 60-case quality evaluation, which was **not** run.
Chunk size, TOP_K, retrieval floor, RRF, embeddings and Qdrant were **not** changed.

## 8–11. Usage / changes

| Item | Value |
|---|---|
| **Groq calls** | **0** (guard active: production Groq entry points replaced with raisers) |
| **NVIDIA 20B application calls** | **3** |
| **NVIDIA 120B judge calls** | **0** |
| Paid actions | **No paid upgrade/action performed** |
| Production changes | **NONE** — no deployment, no env change, no IAM change, no model/provider change |
| Qdrant | untouched (no re-chunk, no re-embed, no mutation) |
| RAGAS / DeepEval / LangChain / LangGraph | NOT IMPLEMENTED / DEFERRED — unchanged |

## 12. Documentation cleanup performed (`CURRENT-IMPLEMENTATION.md`)

Verified against code + live AWS before editing:
- Corrected the **deployed ask image** from the stale `951a6ae…` to `d5af30e…` with the real digest
  `sha256:2791dfa4…`, and noted `MAX_LLM_CONTEXT_CHUNKS=5`.
- Fixed two other stale "deployed 951a6ae this task" claims (architecture diagram + AWS components table)
  and the "Currently running in AWS" line.
- **Removed a stale duplicate block** that claimed the active offline **judge is `openai/gpt-oss-20b`**.
  That configuration was designed but never run; it is now explicitly labelled superseded/paused, and
  the active configuration is stated once: application NVIDIA `gpt-oss-20b`, judge NVIDIA `gpt-oss-120b`.
- Recorded the NVIDIA candidate-evaluation status: **3/60 completed then halted, those 3 cases predate
  the top-5 cap**, plus this capacity-check PASS.
- Historical dated entries under "Recent Changes" retain their original SHAs (correct as history).

No runtime behaviour was changed by the cleanup.

## 13. Next step — listed only, NOT executed

- **60-case NVIDIA GPT-OSS-20B quality evaluation** using this same top-5 context policy
  (application NVIDIA `gpt-oss-20b`, judge NVIDIA `gpt-oss-120b`, dataset
  `multitenant-rag-eval-60-v1` @ `baseline-v1`, retrieval unchanged). Awaiting architect approval.
- Note for that run: the 3 pre-cap cases (case-001/002/003) were generated under a **different context
  policy**, so they carry a different fingerprint and should not be reused as top-5 results.
