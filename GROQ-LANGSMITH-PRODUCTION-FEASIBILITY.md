# Groq Production-Provider Observability + Free-Tier Feasibility

Experiment `groq-routed-rag-observability-v1`, fingerprint `9584e29a26b57de5`.
LangSmith dev project **`multitenant-rag-dev-groq-observability-v1`** — production project untouched.
**Phases A, B, C executed. Phases D and E NOT run — the Groq request ceiling could not accommodate them.**
RAGAS 0 · DeepEval 0 · NVIDIA 0 · LLM judge 0. Nothing deployed.

---

## 1. Objective
Run the frozen routed architecture on the real production provider arrangement, instrument it deeply in
LangSmith, compare stage-by-stage latency against the existing NVIDIA measurements, and characterise whether
the current Groq **free** plan can sustain the workload behind the synchronous API.

## 2. Frozen architecture
Nothing changed: Router V2 prompt/parser, decomposition prompt/parser, graph topology, `Send` fan-out,
`Semaphore(2)`, coverage-aware merge, `TOP_K`, chunking, RRF, `RETRIEVAL_FLOOR`, Titan model, Qdrant
collections, `MAX_LLM_CONTEXT_CHUNKS=5`. **Provider swap only** — the frozen modules are imported and their
prompts reused byte-identical; only the HTTP transport differs. `case-041` untouched.

## 3. Production provider arrangement
| Stage | Model |
|---|---|
| Router V2 | Groq `openai/gpt-oss-20b` |
| Decomposition | Groq `openai/gpt-oss-20b` |
| Final generation | Groq `openai/gpt-oss-120b` |

## 4. API timeout boundary — verified read-only

| Layer | Value | Source |
|---|---|---|
| API Gateway | **HTTP API**, `AWS_PROXY`, **`TimeoutInMillis = 30000`** | `apigatewayv2 get-integrations` |
| ask Lambda timeout | 60 s | `get-function-configuration` |
| Groq request timeout in production code | 60 s | `ask/llm.py:32` |
| Lambda Web Adapter | `AWS_LWA_INVOKE_MODE` unset → default buffered | env inspection |

**Actual external request deadline: 30,000 ms** — API Gateway is the binding limit; both the Lambda (60 s)
and the Groq client timeout (60 s) sit *above* it, so a slow generation hits the gateway first and the client
never learns why. **Recommended internal safety deadline: 24,000 ms** (80%), leaving headroom for response
serialisation and cold-start jitter.

## 5. LangSmith dev project
`multitenant-rag-dev-groq-observability-v1`, tag `experiment=groq-routed-rag-observability-v1`, fingerprint
`9584e29a26b57de5`. Production project `multitenant-rag-prod` received **no** traffic and its configuration
was not modified. No RAGAS/DeepEval/judge scores attached — latency, tokens, errors, route, branch counts,
provider/model and rate-limit metadata only.

## 6. Trace hierarchy — as executed
```
routed_request
├── resolve_scope
├── router_v2                        (llm, groq 20B)
├── [simple]  retrieval_branch → titan_embedding, bm25_encode,
│                                qdrant_dense_probe, qdrant_hybrid_rrf
├── [compound] decomposition         (llm, groq 20B)
│              retrieval_branch ×N → same four children each
│              merge_evidence
├── build_context
└── generation                       (llm, groq 120B)
```
Only real work carries a span; no synthetic durations.

## 7. Trace completeness — **100%**
Read back via the LangSmith SDK: **221 spans, 76 root runs, 0 errors**, 81/81 Phase-A spans carrying
`local_duration_ms`.

| Check | Result |
|---|---|
| Phase-A roots fully complete | **6/6 = 100%** |
| Orphan spans | **0** |
| Simple roots carrying a `decomposition` span | **0** ✅ |
| Compound roots with `decomposition` + `merge_evidence` | 3/3 ✅ |
| `branch_count` metadata == child `retrieval_branch` count | ✅ all |
| Retrieval children correctly nested under each branch | ✅ 9 branches × 4 children = 36 |
| Errors | 0 |

Phase-A span census: 6 `routed_request`, 6 `resolve_scope`, 6 `router_v2`, 3 `decomposition`,
9 `retrieval_branch`, 9 each of `titan_embedding`/`bm25_encode`/`qdrant_dense_probe`/`qdrant_hybrid_rrf`,
3 `merge_evidence`, 6 `build_context`, 6 `generation` = **81**, exactly as predicted.

## 8. Phase A — smoke, real end-to-end graph
3 adjudicated-simple (`case-001/014/021`) + 3 adjudicated-compound (`case-018/020/022`), concurrency 1.
All 6 succeeded. Every case produced **exactly 5 context chunks**; citations 1–5. Simple cases took 1 branch,
compound cases 2. 15 logical calls cost **19 physical requests** (4× 429, 4 retries, **51 s of backoff**).

## 9. Router equivalence — **GATE PASSED**
52 generative development cases, router only.

| | NVIDIA (frozen) | **Groq 20B** |
|---|---|---|
| parse success | 52/52 | **52/52** |
| TP / FP / TN / FN | 11 / 5 / 34 / 0 | **11 / 5 / 34 / 0** |
| precision | 0.6875 | **0.6875** |
| **recall** | 1.0000 | **1.0000** |
| specificity | 0.8718 | **0.8718** |
| accuracy | 0.9000 | **0.9000** |

**Aggregate metrics are identical.** Decision agreement **50/52 = 96.15%**, and the two disagreements
cancel: Groq fixes `case-002` (correctly simple, NVIDIA had it as a false positive) and breaks `case-024`
(calls it compound; ground truth simple). Same error *count*, different error *composition*.

Gate (§16): all outputs parse ✅ · compound recall 1.000 ≥ 0.90 ✅ · no semantic collapse ✅. Router V2 was
**not tuned**.

## 10. Router latency / tokens
Pure provider latency: **mean 864.9 ms · p50 789.1 · p95 1338.6 · max 2260.4**.
Tokens: 523.3 in / 331.5 out mean; 44,449 total. Retries **0**. Reason codes:
`single_retrieval_need` 22, `multiple_independent_retrieval_needs` 18, `single_entity_multi_attribute` 8,
`negative_or_scope_check` 4.

## 11. Decomposition equivalence
18 V2-compound cases, decomposition only, structural comparison (byte-identity not required).

| | NVIDIA | **Groq 20B** |
|---|---|---|
| usable subquestion rate | 15/18 | **15/18 = 0.833** |
| fallback (no usable decomposition) | 3 | **3 — the same 3** |
| subquestion-count distribution | 2:14, 3:1, 0:3 | **2:13, 3:2, 0:3** |
| same count as NVIDIA | — | **17/18** |
| parse errors | 0 | **0** |

Preservation in Groq subquestions: identifiers **4/4**, entities ≥80% **13/15**, time/scope words **6/7**.
Not tuned.

## 12. Decomposition latency / tokens
Pure provider: **mean 662.8 ms · p50 642.9 · p95 863.0 · max 890.8**. Tokens 240.9 in / 224.8 out mean;
8,383 total. Retries 0.

## 13. Groq 120B generation
**Phase D (the 18-case generation test) was NOT run** — see §18. The only 120B evidence is Phase A's 6
generations, all successful. Pure provider latency **mean 1210.8 ms · p95 1742.8 · max ~1750**, with
**2140.3 input / 299.0 output** tokens mean. 6 logical calls cost 10 physical (4 retries from 429s).

## 14. Generation latency / tokens
See §13. Input tokens are the story: **~2,140 per generation against an 8,000 TPM ceiling** — roughly
**3.7 generations per minute** before rate limiting.

## 15. Full production-provider sample — **NOT EXECUTED**
Phase E needs 54 requests (18 router + 18 decomposition + 18 generation). After A+B+C+concurrency the task
had **7** of 100 remaining. Per §20, Phase E was not started.

## 16. LangSmith node-by-node latency

Two columns, because they differ materially and only one is provider latency:

| Span | N | wall mean (ms) | wall p95 | **provider mean** | **provider p95** |
|---|---|---|---|---|---|
| `routed_request` (Phase A e2e) | 6 | **12,099.8** | 23,849.1 | — | — |
| `resolve_scope` | 6 | 25.0 | 111.5 | — | — |
| `router_v2` (A) | 6 | 884.5 | 1,347.6 | **766.6** | 971.3 |
| `router_v2` (B) | 52 | 6,887.5 | 7,657.6 | **864.9** | 1,338.6 |
| `decomposition` (A) | 3 | 918.4 | 977.2 | **641.4** | 654.1 |
| `decomposition` (C) | 18 | 6,668.2 | 7,278.7 | **662.8** | 863.0 |
| `retrieval_branch` | 9 | **885.3** | 1,201.1 | — | — |
| `titan_embedding` | 9 | **160.5** | 283.3 | — | — |
| `bm25_encode` | 9 | **12.0** | 62.9 | — | — |
| `qdrant_dense_probe` | 9 | **526.3** | 682.9 | — | — |
| `qdrant_hybrid_rrf` | 9 | **168.9** | 184.3 | — | — |
| `merge_evidence` | 3 | **0.4** | 0.5 | — | — |
| `build_context` | 6 | **0.3** | 0.4 | — | — |
| `generation` | 6 | 9,800.2 | 21,083.2 | **1,210.8** | 1,742.8 |

**Systematic wall-vs-provider mismatch, reported per §6.** The B/C `router_v2` and `decomposition` wall
durations (~6.7–6.9 s) **enclose the deliberate 7 s inter-request pacing sleep** I added to stay under TPM;
Phase A `generation` wall (9.8 s mean, 21.1 s p95) **encloses 429 backoff**. Local monotonic timers agreed
with LangSmith throughout — the gap is real elapsed time inside the span, not a measurement error. Provider
latency is the `latency_ms` metadata measured around the HTTP call alone. **Any latency conclusion must use
the provider column.** Notably, `qdrant_dense_probe` (526 ms) is 3× `qdrant_hybrid_rrf` (169 ms) — the
"cheap" relevance probe is the more expensive of the two Qdrant calls, which was not previously visible.

## 17. Groq free-plan rate-limit observations
Exact headers returned by the account (only these were present; none invented):

```
x-ratelimit-limit-requests: 1000        x-ratelimit-remaining-requests: 919
x-ratelimit-reset-requests: 1h56m38.4s
x-ratelimit-limit-tokens:   8000        x-ratelimit-remaining-tokens:   5123
x-ratelimit-reset-tokens:   21.577s
retry-after: (present on 429 responses)
```

**Tokens-per-minute is the binding constraint, not requests-per-minute.** RPM is 1,000 with ~920 remaining
after the whole experiment; TPM is **8,000** and fell to **70 remaining** during Phase A. Every 429 in this
experiment was a token-rate rejection, not a request-rate one.

**First 429: Phase A**, at 1 s pacing — 4 in total, all in Phase A, costing 51 s of backoff.
**Zero 429s in Phases B and C** after pacing was raised to 7 s per request.

## 18. Groq capacity characterization
Stated only from observed evidence, no extrapolation:

| Measure | Value |
|---|---|
| Total physical Groq requests | **93** of a 100 ceiling |
| Total logical calls | 89 |
| 20B requests | 83 |
| 120B requests | 10 |
| Total tokens | **76,733** (48,343 in / 28,390 out) |
| 429 responses | **4** (all Phase A) |
| 5xx / timeouts | **0 / 0** |
| Retries / backoff | 4 / **51.0 s** |
| Highest tested concurrency | **2** (20B, 4 requests) — **healthy**: 4/4 in 1.93 s wall, 0 × 429, latency unchanged (877.8 ms vs 864.9 ms sequential) |

**Observed sustainable behaviour:** *within this experiment the free plan sustained **70 consecutive 20B
calls** (52 router + 18 decomposition) at ~7 s spacing with **zero** 429s, and a bounded concurrency-2 burst
of 4 20B requests with zero 429s. Rate limiting appeared only in Phase A at ~1 s spacing with 120B
generations in the mix, and was TPM-driven.*

The 120B concurrency-2 half of §22 was **deliberately skipped**: 4 concurrent generations at ~2,140 input
tokens each would push ~8,560 tokens into a single 8,000-token window — engineering a 429 rather than
characterising capacity, which §9 forbids.

**Practical ceiling implied by TPM, not asserted beyond evidence:** at ~2,440 tokens per compound request
(router 855 + decomposition 466 + generation 2,439 ≈ 3,760 measured), 8,000 TPM supports roughly **2
compound requests per minute** on the free plan.

## 19. Groq vs NVIDIA — stage by stage

| Stage | NVIDIA | Groq | Note |
|---|---|---|---|
| Router mean | 4,567.9 ms | **864.9 ms** | same frozen prompt — **5.3× faster** |
| Router p95 | 13,624.2 ms | **1,338.6 ms** | **10.2× faster** |
| Decomposition mean | 7,329.2 ms | **662.8 ms** | same frozen analyzer — **11.1× faster** |
| Generation mean | 8,361.6 ms | **1,210.8 ms** | ⚠️ **model differs** |
| Merge mean | 0.1 ms | 0.4 ms | identical implementation |
| Titan embedding | not measured | 160.5 ms | NVIDIA observability gap |
| BM25 encode | not measured | 12.0 ms | NVIDIA observability gap |
| Qdrant dense probe | not measured | 526.3 ms | NVIDIA observability gap |
| Qdrant hybrid RRF | not measured | 168.9 ms | NVIDIA observability gap |
| Retrieval branch | not measured | 885.3 ms | NVIDIA observability gap |
| End-to-end mean | 18,764.0 ms | 12,099.8 ms | Groq figure **includes 429 backoff** |
| End-to-end p95 | 32,019.1 ms | 23,849.1 ms | same caveat |

**Explicit caveat:** for final generation the provider **and the model** both changed — NVIDIA GPT-OSS-**20B**
versus Groq GPT-OSS-**120B**. Provider is not the only changed variable on that row. Router and
decomposition are true provider-only comparisons (identical frozen prompts and models).

## 20. Critical-path latency
Per §28, parallel retrieval counted as the **slowest branch**, not the sum:

| Component | mean | p95 |
|---|---|---|
| resolve_scope | 25.0 | 111.5 |
| router_v2 | 766.6 | 971.3 |
| decomposition | 641.4 | 654.1 |
| retrieval_branch (critical path) | 885.3 | 1,201.1 |
| merge_evidence | 0.4 | 0.5 |
| build_context | 0.3 | 0.4 |
| generation | 1,210.8 | 1,742.8 |
| **Projected compound total** | **3,529.8 ms** | **4,681.7 ms** |

Observed Phase A compound mean was **18,432.4 ms** — the ~15 s difference is **rate-limit backoff**, not
compute.

## 21. API timeout verdict

| Basis | p95 | vs 24 s safety | vs 30 s hard | Class |
|---|---|---|---|---|
| Projected critical path (no rate limiting) | **4,681.7 ms** | far below | far below | **GREEN** |
| Observed Phase A end-to-end (with free-plan backoff) | **23,849.1 ms** | **at the line** (max 25,335 ms exceeds it) | below | **YELLOW** |

> ## VERDICT: YELLOW
> **The architecture itself is comfortably GREEN — 4.7 s projected p95 against a 30 s deadline. The free
> plan is what makes it YELLOW.** Backoff from the 8,000 TPM ceiling inflated observed p95 to 23.8 s and max
> to 25.3 s, above the 24 s safety deadline though still inside the 30 s hard deadline.

This is a **quota** problem, not a latency problem. For comparison, NVIDIA's routed p95 of 32.0 s was
already **above the 30 s hard deadline** — Groq moves the architecture from infeasible to feasible-with-a-quota-caveat.

## 22. Reliability / retries
| | Logical | Physical | Inflation |
|---|---|---|---|
| Phase A (1 s pacing) | 15 | **19** | **1.27×** |
| Phase B (7 s pacing) | 52 | **52** | 1.00× |
| Phase C (7 s pacing) | 18 | **18** | 1.00× |
| Concurrency check | 4 | 4 | 1.00× |

Per §32: **low observed provider latency did hide retry/backoff in Phase A** — 51 s of sleep inside spans
whose provider `latency_ms` was ~1.2 s. Pacing at 7 s eliminated it entirely. 0 5xx, 0 timeouts throughout.

## 23. Deterministic regression safety check
Not the purpose of the LangSmith project; kept local. Phase A's 3 compound cases, NVIDIA-20B routed answer
vs Groq-120B answer, using the existing reference-fact evaluator — exported in
`groq120b-generation-results.csv`. **Phase D's full 18-case comparison was not run**, so no aggregate
provider-regression claim is made.

## 24. NVIDIA observability gaps
**GAP CONFIRMED.** The NVIDIA live run recorded node latencies only for `decompose`, `final_answer`,
`normal_answer`, `merge_evidence` and `normal_retrieve`. Missing for an apples-to-apples comparison:

- `titan_embedding` (per-call)
- `bm25_encode` (per-call)
- `qdrant_dense_probe` (per-call)
- `qdrant_hybrid_rrf` (per-call)
- `retrieval_branch` (per-branch wall)
- `resolve_scope`, `build_context`
- router latency **inside the graph** (the NVIDIA router figures come from the separate router experiment, not the routed run)

**No NVIDIA calls were made to fill these.** Smallest future mirrored sample that would close the gap:
**6 cases (3 simple + 3 compound) through the same instrumented graph on NVIDIA** — 6 router + 3
decomposition + 6 generation = **15 NVIDIA 20B calls**, plus 9 Titan and 18 physical Qdrant calls. Architect
to decide.

## 25. RAGAS / DeepEval status
**RAGAS 0 calls · DeepEval 0 calls · NVIDIA 120B judge 0 calls.** Framework evaluation remains **blocked by
evaluator reliability**, not application architecture. Deterministic evidence from the routed experiment
stands unchanged: context coverage **+13**, answer coverage **+10**, zero regressions, 18/18 live success.

## 26. Production status
**UNCHANGED. Nothing deployed.** `d5af30e`, Groq `openai/gpt-oss-120b`, `MAX_LLM_CONTEXT_CHUNKS=5`. No
LangGraph in any Lambda requirement, ask Lambda untouched, no DLQ created, API timeout unchanged, Groq model
env unchanged, production LangSmith project untouched.

## 27. Recommendation
Documented only; nothing actioned.

1. **The routed architecture is latency-feasible on Groq.** 4.7 s projected p95 against a 30 s deadline is a
   large margin, and Groq is 5–11× faster than NVIDIA at every provider-only stage. The earlier NVIDIA-based
   RED assessment (p95 32 s > 30 s) does **not** carry over to the production provider.
2. **The binding risk is the free plan's 8,000 TPM**, and it is a quota decision rather than an engineering
   one. At ~3,760 tokens per compound request the free plan supports roughly 2 compound requests/minute.
   Options, in order of cost: accept it at current (zero) traffic; add a token-aware admission gate that
   degrades compound → simple when TPM headroom is low; or raise the Groq plan.
3. **Add a per-request token budget alongside the time budget.** The audit's P0 "request budgets" item should
   count *tokens*, not just calls — TPM is what actually rejects.
4. **`qdrant_dense_probe` at 526 ms is 3× the hybrid query** and is pure gate overhead. Worth a look
   sometime, though it is only ~15% of the projected critical path. Not urgent, and out of scope here.
5. **Close the NVIDIA observability gap only if a defensible stage-by-stage provider comparison is wanted** —
   15 NVIDIA calls (§24). Otherwise the Groq numbers stand alone as the production-relevant ones.
6. **Not indicated:** deploying, changing the architecture, tuning the router, upgrading Groq without a
   traffic reason, or running Phases D/E on the remaining 7-request budget.
