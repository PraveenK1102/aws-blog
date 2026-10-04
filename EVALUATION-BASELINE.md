# MultiTenantRAG — Evaluation Baseline v1

**Status: NOT COMPLETE — BLOCKED before launch by two architect-defined gates.**
Date 2026-08-22 · Git `03fda30` (deployed) · No optimization performed; measurement only.

RAGAS: `NOT IMPLEMENTED — DEFERRED` · DeepEval: `NOT IMPLEMENTED — DEFERRED` ·
LangChain: `NOT IMPLEMENTED` · LangGraph: `NOT IMPLEMENTED` ·
NVIDIA production integration: `NOT IMPLEMENTED`.

---

## 1. Old partial experiment — ABORTED

| Field | Value |
|---|---|
| LangSmith project | `rag-baseline-v1-808d0962` (annotated "ABORTED — GROQ TPD QUOTA EXHAUSTED at 27/60") |
| Cases completed | **27 of 60** |
| Judge used | Groq `openai/gpt-oss-20b` (superseded) |
| Reason | Groq hard daily-token wall: `Rate limit reached … tokens per day (TPD): Limit 200000, Used 198795` |
| Disposition | **NOT the baseline.** Preserved, not deleted, not continued from case 28, not merged with any future run. Local export JSON was never written (dumped only on completion). |

Two 3-case preflight projects (`rag-baseline-v1-5566bfe5`, `rag-baseline-v1-03915416`) are also
annotated as preflights, not baselines.

### Why the quota gate missed it
Groq exposes `x-ratelimit-limit/remaining-requests` (RPD) and `…-tokens` (TPM per-minute) on success
responses but **not the TPD dimension**. The pre-run gate cleared on 997/1000 requests and
7634/8000 TPM. The binding 200,000-tokens/day limit is invisible until breached. **Header-based
quota gating is therefore unreliable on Groq** — an explicit finding for future runs.

---

## 2. Planned clean baseline (configuration approved, NOT yet run)

| Field | Value |
|---|---|
| Experiment prefix | `rag-baseline-v1-groq120b-nvidia20b-judge` |
| Application provider / model | **Groq** `openai/gpt-oss-120b` (unchanged production model) |
| Judge provider / model | **NVIDIA** `openai/gpt-oss-20b` via `https://integrate.api.nvidia.com/v1` |
| Judge credential | Secrets Manager `multitenant/nvidia` (ap-south-1); **no Lambda IAM grant** |
| Dataset | `multitenant-rag-eval-60-v1` · id `d426fe19-3757-4442-b893-99a6cf031b68` · 60 examples · tag `baseline-v1` · PRIVATE |
| Embeddings / sparse / fusion | Bedrock Titan Text V2 / fastembed BM25 / Qdrant RRF |
| TOP_K / retrieval floor | 5 / 0.15 |
| Concurrency | `max_concurrency = 1`; NVIDIA judge min interval **6 s** (≈10 judge req/min) |
| Classification | `data_classification = synthetic_public`, `environment = production_rag_eval` |
| Recorded judge id | `evaluation_judge = nvidia/openai/gpt-oss-20b` (reused for future controlled experiments) |

---

## 3. BLOCKER A — projected Groq application tokens exceed the cap

Measured from the 8 real application executions with token data across the aborted run + preflights,
weighted by the dataset's actual route mix (single 21 / multi 13 / group 18 / global 8; global uses
**0** LLM tokens):

| Route | N observed | Mean total tokens | Max |
|---|---|---|---|
| single | 3 | 2,647 | 2,705 |
| multi | 2 | 3,892 | 4,085 |
| group | 3 | 4,311 | 4,792 |
| global | — | 0 (LLM-free) | 0 |

| Projection (application-only, Groq) | Tokens |
|---|---|
| Route-weighted **mean** | **183,788** |
| Route-weighted **conservative (max)** | **196,166** |
| Architect cap (≈30% headroom) | 140,000 |
| Groq TPD limit | 200,000 |

**Verdict:** the mean projection **exceeds the 140,000 cap by ~31%** and consumes **~92%** of the
entire daily TPD (conservative: ~98%), leaving essentially no headroom for 429 retries — which
themselves re-consume tokens. Per the architect's rule the run was **not started**:
`DECISION REQUIRED — GROQ APPLICATION TOKEN BUDGET MAY EXCEED CLEAN DAILY BASELINE CAP`.

**Root cause (not a misconfiguration):** the seeded synthetic posts are ~500 lines each → ~40–43
chunks/post, so 5 retrieved chunks are 2.4k–4.8k input tokens. Group/multi routes retrieve across
several tenants and are the most expensive. Retrieval settings were **not** altered to shrink this.

## 4. BLOCKER B — today's Groq TPD has not recovered

Exhausted at 19:36:40 IST; at 19:48 only ~12.6 min had elapsed. Refill ≈ 200,000/day ≈ **139
tokens/min**, i.e. **~1,745 tokens recovered** against ~184,000 required. Full recovery ≈ 22 h.
`USER ACTION REQUIRED — WAITING FOR FRESH GROQ DAILY TOKEN QUOTA`. Only one small probe was used;
no repeated probing.

**Consequence:** the NVIDIA-judge 3-case preflight could **not** be executed either — its application
leg requires Groq generation. Preflight status: **NOT RUN (blocked upstream)**, not failed.

---

## 5. What IS validated and reusable

- **Observability error fix** — commit `03fda30`, pushed, **DEPLOYED** to `multitenant-ask`
  (image `03fda30…`), clean cold start, tracing enabled, 13 env vars preserved, 21/21 tests pass.
  A caught Groq failure now marks the `groq_generation` span as a real LangSmith error (exception
  class only) and the root reports `result_type=generation_error`; this also closed a
  cache-poisoning path where an empty answer was written to the semantic cache.
- **Dataset** created, validated (60 cases, case-001…case-060, no gaps/dupes/missing), tagged, private.
- **Harness** (`multitenant-rag/evals/`) reuses production RAG functions, mirrors the deployed Lambda's
  model config, disables app-level tracing (never writes to `multitenant-rag-prod`), and bypasses the
  semantic cache so eval answers cannot pollute the production cache.
- **Prior Groq-judge preflight PASSED** all 13 checks (real contexts captured, one combined judge call
  → two feedback keys, secret scan clean).
- **Eval tooling tests:** 23/23 pass (corpus validation, judge parsing, score normalization, failure
  classification, retry bounds, quota-header redaction, percentile math, export redaction).
- **NVIDIA judge** validated end-to-end (see `NVIDIA-MODEL-INVENTORY.md`): auth PASS, 102 models,
  `openai/gpt-oss-20b` streams with `delta.content` + separate reasoning fields + usage.

---

## 6. Production latency — MEASURED (operational, redacted)
_Source: `multitenant-rag-prod`, 792 spans / 130 traces. Not evaluator latency._

| Flow | N | Mean | P50 | P95 | P99 | Max |
|---|---|---|---|---|---|---|
| ask_request | 50 | 4942.5 | 2079.5 | 17593.7 | 22194.2 | 24770.3 |
| group_ask_request | 65 | 12308.4 | 15806.1 | 30418.5 | 32597.8 | 34504.4 |
| global_search_request | 15 | 506.2 | 556.1 | 602.9 | 617.8 | 621.5 |

## 7. Component latency — MEASURED (ms)

| Component | N | Mean | P50 | P95 | P99 | Max |
|---|---|---|---|---|---|---|
| groq_generation | 94 | 9899.1 | 12212.0 | 25756.7 | 31032.5 | 33862.8 |
| retrieval | 113 | 810.4 | 677.8 | 2769.9 | 2928.6 | 2985.7 |
| hybrid_qdrant_search | 98 | 792.0 | 583.5 | 2783.4 | 2920.3 | 2928.9 |
| semantic_cache | 47 | 538.0 | 560.7 | 761.2 | 780.3 | 790.1 |
| qdrant_search | 15 | 399.0 | 459.5 | 462.0 | 462.5 | — |
| dense_embedding | 127 | 101.8 | 100.9 | 131.4 | 149.2 | 149.9 |
| completion | 88 | 71.7 | 0.4 | 462.2 | 465.3 | 465.4 |
| result_processing | 15 | 2.7 | 0.3 | 10.7 | 23.9 | 27.2 |
| context_preparation | 65 | 1.7 | 0.3 | 5.4 | 27.6 | 28.4 |

> **P99 caveat:** with 15–65 samples per flow, p99 describes the extreme tail of this observation
> window only — it is **not** a statistically robust SLO. Observed p95/p99 on `groq_generation` are
> further **inflated by this project's own evaluation traffic** hitting Groq 429 retry waits, not by
> user load. An isolated group ask measured **2.5 s** end-to-end when unthrottled.

## 8. Cache behaviour — MEASURED
- **Hit rate 36.2%** (17 hits / 30 misses over 47 cache-consulting flows).
- `ask_request` on **hit**: mean 574.0 ms (p95 599.3) · on **miss**: mean 7540.8 ms (p95 18893.3)
  → **~13× faster on a hit**; the single largest latency lever observed.

## 9. Tokens — application vs evaluation (never combined)

| Source | Provider / model | N | Mean in | Mean out | Mean total |
|---|---|---|---|---|---|
| Application (production traces) | Groq `openai/gpt-oss-120b` | 94 | 1976.6 | 191.8 | 2168.4 |
| Application (eval executions observed) | Groq `openai/gpt-oss-120b` | 8 | — | — | 2647–4311 by route |
| Evaluation judge | NVIDIA `openai/gpt-oss-20b` | 0 | — | — | **not yet measured (run blocked)** |

Judge overhead / NVIDIA judge latency percentiles: **pending the clean run.**

## 10. Bottleneck evidence (no changes made)
- **Mean ranking:** `groq_generation` 9899 ms ≫ `retrieval` 810 ms ≈ `hybrid_qdrant_search` 792 ms.
- LLM generation mean **9899 ms** vs whole retrieval stack **1293 ms** → generation dominates by ~7.7×.
- Single **4943 ms** vs group **12308 ms** vs global **506 ms** (global is LLM-free).
- 8 errored spans, all `UnexpectedResponse` on retrieval — the earlier Qdrant free-cluster pause.

## 11. Limitations
- **Baseline incomplete** — no quality/correctness/completeness numbers exist yet; the 27-case partial
  run is explicitly excluded.
- **Synthetic corpus** (fictional 50-post/6-profile seed); absolute scores will not transfer to real users.
- **60 cases only**; 15–65 production samples per flow; p99 descriptive, not an SLO.
- **Groq TPD (200k/day) is invisible in success headers** — quota must be projected, not read.
- **Projected 60-case application usage (~184k) is ~92% of the full daily TPD**, leaving no retry room.
- **LLM-as-judge variability:** single judge, temperature 0, no human adjudication or multi-judge panel.
- **Known judge-bias risk (recorded, not solved):** a future experiment evaluating GPT-OSS-20B as the
  *application* model would be judged by GPT-OSS-20B → same-model-family bias. Later cross-checks:
  human-reviewed cases, RAGAS, DeepEval, or a different judge.
- **NVIDIA free endpoint exposes no rate-limit headers**; client-side throttling (1 concurrency, 6 s
  interval, bounded backoff, 3-consecutive-429 circuit breaker) is the only control. A successful
  offline run would prove API compatibility and evaluation viability — **not** production throughput,
  SLA, entitlement, or availability.
- **Semantic cache bypassed** in evaluation, so measured quality reflects the full RAG path.
- Judge token usage not yet instrumented (estimated in v1 tooling).
- RAGAS / DeepEval metrics (faithfulness, answer relevancy, context precision/recall) **deferred**.
- **Group scope is membership-based** (union of member tenants' posts), not per-post group tag.

## 12. Conclusion
The instrumentation, dataset, harness, judge provider and production measurement are all in place and
verified. The clean 60-case baseline was **not started** because the projected Groq application-token
cost (~184k) exceeds the architect's 140k cap and today's TPD is exhausted. Operationally, the system
is generation-bound (≈7.7× the retrieval stack) and the semantic cache is the strongest latency lever
(~13× on a hit). No architecture changes are recommended here.

## 13. Candidate future experiments (listed only — NOT executed)
Groq 120B vs NVIDIA 120B (provider held constant) · 120B vs 20B · 20B vs Llama-3.1-8B ·
Nemotron Nano 9B · TOP_K 5 vs 8 · retrieval-floor variants · dense-only vs hybrid · prompt variants ·
alternate/multi judge · RAGAS + DeepEval over this same corpus and real retrieved contexts.
All must reuse `multitenant-rag-eval-60-v1` with unchanged retrieval settings.
