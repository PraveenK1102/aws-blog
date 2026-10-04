# NVIDIA GPT-OSS-20B — Offline Model Evaluation v1

> ## ⛔ STATUS: INCOMPLETE — 3 of 60 cases. HALTED by an NVIDIA free-endpoint stall.
> **`USER ACTION REQUIRED — WAIT FOR FREE QUOTA / NVIDIA CAPACITY`**
>
> The 3-case preflight **PASSED** (all 15 checks, all scores 1.0). The run then stalled at
> `case-004` and was stopped cleanly. **No fallback to Groq. No paid upgrade. Nothing deleted.**
>
> ### Root cause — input-size-dependent stalling, measured
> | Probe (same key, same moment) | Result |
> |---|---|
> | Tiny prompt (~15 tokens) → `openai/gpt-oss-20b` | **HTTP 200 in 0.6 s** |
> | Realistic RAG prompt (~4,500 tokens) → `openai/gpt-oss-20b` | **stalled > 120 s, no response** |
> | `case-004` (multi-profile, large context) in the runner | **network-blocked ~7 min** (process 0.1% CPU, 2.19 s CPU time — waiting on the socket) |
>
> No `429`, no `Retry-After`, **no rate-limit headers of any kind**. So the circuit breaker (which
> triggers on 429s) never fired; the stall surfaces only as a request timeout. This is why the
> earlier tiny validation smokes all passed while the real workload cannot proceed: this RAG
> pipeline inherently sends 2.4k–7.5k-token prompts (5 retrieved chunks from ~500-line posts).
>
> **Conclusion:** the NVIDIA free/prototyping endpoint is currently not usable for this RAG
> evaluation workload. Provider *compatibility* is proven (see §17); sustained *capacity* is not.
> Resuming needs no code change — the checkpoint is durable and `run_nvidia_eval.py` resumes at
> `case-004` without re-spending quota on the 3 completed cases.

_Generated 2026-08-22T14:58:59+00:00. Measurement only — production unchanged._

## 1. Experiment identity
- **Logical experiment:** `rag-model-eval-nvidia20b-v1`
- **LangSmith shards:** `rag-model-eval-nvidia20b-v1-part-01-20bad83c`
- **Dataset:** `multitenant-rag-eval-60-v1` (`d426fe19-3757-4442-b893-99a6cf031b68`), version `baseline-v1`, 60 examples, PRIVATE, synthetic_public
- **Git SHA:** `03fda30acf1360233147fb799c09a67512677e44`
- **Fingerprint:** `bb568d38b82b05a6` — application_model=openai/gpt-oss-20b, judge_model=openai/gpt-oss-120b, top_k=5, retrieval_floor=0.15, retrieval=hybrid-rrf, embedding_model=titan-text-v2, sparse_model=fastembed-bm25

## 2. Models
| Role | Provider | Model |
|---|---|---|
| Application under test | NVIDIA | `openai/gpt-oss-20b` |
| Judge | NVIDIA | `openai/gpt-oss-120b` |
| Production (UNCHANGED) | Groq | `openai/gpt-oss-120b` |

**Groq calls during this experiment: 0** (actively guarded — any Groq entry point raises).

## 3. Routing
| Route | Cases in dataset | Ran | LLM used |
|---|---|---|---|
| global | 8 | 0 | 0 |
| group | 18 | 0 | 0 |
| multi | 13 | 0 | 0 |
| single | 21 | 3 | 3 |

Global search stayed **LLM-free** by design — no NVIDIA generation call was made for it.

## 4. Reliability
- Cases in dataset: **60** · ran: **3** · not run: **57**
- request_success: **100.0%** (3/3)

| Status | Count |
|---|---|
| `completed` | 3 |

- Provider/application failures: **0**
- Judge failures (parse/provider/rate-limit): **0**

> Provider failure is recorded as a **separate status**, never as answer_correctness = 0. Reliability and quality denominators are reported independently.

## 5. Answer correctness
- **Mean: 1.0** over N=3 scored generative cases
- full (1.0): **3 (100.0%)** · partial (0.5): **0 (0.0%)** · fail (0.0): **0 (0.0%)**

## 6. Completeness
- **Mean: 1.0** over N=3 scored generative cases
- full (1.0): **3 (100.0%)** · partial (0.5): **0 (0.0%)** · fail (0.0): **0 (0.0%)**

## 7. Groundedness
- **Mean: 1.0** over N=3 scored generative cases
- full (1.0): **3 (100.0%)** · partial (0.5): **0 (0.0%)** · fail (0.0): **0 (0.0%)**

## 8. Retrieval metrics
`retrieval_hit_at_k` = **NOT_SUPPORTED** · `citation_correctness` = **NOT_SUPPORTED** — the 60-case corpus carries no deterministic expected source/post identifiers (verified: example metadata has only case_id/route/target/title/source/prior_score). No source truth was fabricated.

## 9. Category analysis
_Categories derived from the EXISTING suite titles/routes, not invented after seeing results._

### By route
| Route | N | Correctness | Completeness | Groundedness |
|---|---|---|---|---|
| single | 3 | 1.0 | 1.0 | 1.0 |

### By scenario
| Scenario | N | Correctness | Completeness | Groundedness |
|---|---|---|---|---|
| exact fact / identifier | 1 | 1.0 | 1.0 | 1.0 |
| single-profile factual | 1 | 1.0 | 1.0 | 1.0 |
| temporal / latest-state | 1 | 1.0 | 1.0 | 1.0 |

## 10. Failure analysis
No scored case fell below 1.0 on any dimension.

## 11. NVIDIA 20B application latency (ms)
| Metric | N | Mean | P50 | P95 | P99 | Max |
|---|---|---|---|---|---|---|
| nvidia20b_generation_latency_ms | 3 | 3344.4 | 2689.0 | 4971.0 | 5173.9 | 5224.6 |
| evaluation retrieval_latency_ms | 3 | 1108.3 | 1017.8 | 1359.7 | 1390.1 | 1397.7 |

## 12. NVIDIA 120B judge latency (ms) — offline evaluation overhead
| Metric | N | Mean | P50 | P95 | P99 | Max |
|---|---|---|---|---|---|---|
| nvidia120b_judge_latency_ms | 3 | 42519.0 | 42501.4 | 73676.8 | 76447.9 | 77140.7 |

> Judge latency is **evaluation overhead**, never user-facing RAG latency. Deliberate 6 s pacing is excluded from all model latencies (total pacing sleep: 7.96s).

## 13. Tokens — application vs judge (never combined)
| Source | N | Mean in | Mean out | Total in | Total out | Grand total |
|---|---|---|---|---|---|---|
| Application NVIDIA `openai/gpt-oss-20b` | 3 | 2422 | 208 | 7266 | 624 | 7890 |
| Judge NVIDIA `openai/gpt-oss-120b` | 3 | 1577.7 | 301 | 4733 | 903 | 5636 |

## 14. Rate-limit / free-tier observations
| Metric | Value |
|---|---|
| requests | 7 |
| successes | 6 |
| http_429 | 0 |
| http_5xx | 0 |
| timeouts | 1 |
| retries | 1 |
| circuit_breaker_events | 0 |

- Client-side control only: concurrency 1, 6 s minimum interval, bounded backoff (30/60/120 s + jitter), 3-consecutive-429 circuit breaker. NVIDIA exposes no usable rate-limit headers.
- **No paid upgrade/action was performed.** This does not establish that the service is permanently free.

## 15. Comparison with existing production observations
**PRODUCTION OPERATIONAL DATA** (Groq gpt-oss-120b in Lambda), for context only:

| Flow | N | Mean | P50 | P95 | P99 | Max |
|---|---|---|---|---|---|---|
| ask_request | 50 | 4942.5 | 2079.5 | 17593.7 | 22194.2 | 24770.3 |
| group_ask_request | 65 | 12308.4 | 15806.1 | 30418.5 | 32597.8 | 34504.4 |
| global_search_request | 15 | 506.2 | 556.1 | 602.9 | 617.8 | 621.5 |

- Production `groq_generation` mean: **9899.1 ms**
- This experiment's NVIDIA-20B generation mean: **3344.4 ms**

> ⚠️ **This is NOT a controlled model-size latency comparison.** Provider (Groq vs NVIDIA) AND execution environment (Lambda production vs local offline harness) both differ, and the production figures are additionally inflated by earlier Groq 429 retry waits. A valid model-size comparison must run NVIDIA 20B vs NVIDIA 120B under the same provider and harness. Quality comparison is also not strictly like-for-like: the paused Groq baseline never completed, so no Groq quality numbers exist.

## 16. Limitations
- Synthetic 60-case corpus (fictional seed) — absolute scores do not transfer to real users.
- Small sample: 60 cases, 52 generative; per-scenario cells are single-digit N.
- **Judge is GPT-OSS-120B and the application is GPT-OSS-20B — same model family.** Using the larger model avoids direct self-judging but does NOT eliminate model-family bias.
- Single judge, temperature 0, no human adjudication and no multi-judge panel.
- Free NVIDIA endpoint: quota, SLA and entitlement are unknown and unobservable (no rate-limit headers). Success here proves API compatibility and evaluation viability, NOT production throughput/SLA/availability.
- Production provider differs (Groq) — this experiment does not measure production behaviour.
- Retrieval source-truth metrics unsupported (no deterministic expected post ids).
- Semantic cache bypassed, so this measures the full RAG path rather than the cache-hit path.
- RAGAS and DeepEval remain DEFERRED (not installed); their metrics are not reported.
- Group scope is membership-based (union of member tenants' posts), not per-post group tag.

## 17. Conclusion — how well did NVIDIA GPT-OSS-20B perform?

**Sample far too small to answer the engineering question.** Only 3 of 60 cases completed (all single-profile). On those 3 the model scored perfectly, and the pipeline/judge/feedback mechanics are proven end-to-end — but 3 single-profile cases say nothing about multi-document, conflicting-evidence or group performance, which is exactly what the question hinges on.

On 3 scored generative cases: correctness **1.0** (100.0% fully correct), completeness **1.0**, groundedness **1.0**. request_success **100.0%**.

Weakest scenarios by correctness: `exact fact / identifier` 1.0 (n=1), `single-profile factual` 1.0 (n=1), `temporal / latest-state` 1.0 (n=1).

**Production was not changed.** This is evidence for the architect's decision, not a recommendation.

## 18. Recommended next experiment (NOT executed)
- `NVIDIA GPT-OSS-20B vs NVIDIA GPT-OSS-120B` on the hardest 10–15 cases — same provider, retrieval, prompt, contexts and harness, so model size is the only variable.