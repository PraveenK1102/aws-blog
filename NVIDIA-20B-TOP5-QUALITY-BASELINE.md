# NVIDIA GPT-OSS-20B — Top-5 Quality Baseline

Date 2026-08-23 · **complete: 60/60 cases** · measurement only, production unchanged

## 1. Experiment identity

| Field | Value |
|---|---|
| Logical experiment | `rag-model-eval-nvidia20b-top5-v1` |
| Fingerprint | `856b2bf583419c93` (NEW — includes the top-5 context policy) |
| Dataset | `multitenant-rag-eval-60-v1` (`d426fe19-3757-4442-b893-99a6cf031b68`), version `baseline-v1`, 60 examples, PRIVATE, synthetic_public |
| Application model | **NVIDIA `openai/gpt-oss-20b`** |
| Judge model | **NVIDIA `openai/gpt-oss-120b`** |
| Production (untouched) | Groq `openai/gpt-oss-120b` |
| Context policy | `MAX_LLM_CONTEXT_CHUNKS = 5`; TOP_K 5, floor 0.15, chunk 500/50 — all unchanged |

The previous 3/60 NVIDIA run used a different (pre-cap) policy and a different fingerprint; **none of
its results are reused here**.

## 2. Git HEAD vs deployed SHA — kept distinct

| Concept | Value |
|---|---|
| `repo_head` (== `origin/main`) | **`e28029b`** — evaluation tooling only |
| `deployed_ask_sha` | **`d5af30e`** — with `MAX_LLM_CONTEXT_CHUNKS=5` |

`e28029b` was **never deployed**; `d5af30e` remains the deployed ask image. Both are recorded in the
experiment fingerprint.

## 3. Dataset distribution (exact, verified)

| Route | Cases | LLM used |
|---|---|---|
| single | 21 | 21 |
| multi | 13 | 13 |
| group | 18 | 18 |
| **global** | **8** | **0 (LLM-free by design)** |
| **Total** | **60** | **52 generative** |

## 4. Reliability

| Metric | Value |
|---|---|
| Application records | **60/60**, all `completed` |
| **request_success (generative)** | **52/52 = 100%** |
| Application provider errors | **0** |
| Application timeouts / 429s / 5xx | **0 / 0 / 0** |
| Judge scored | **52/52** (49 on first pass; 3 provider-errors — case-002/006/015 — re-judged successfully on retry with a longer timeout; **application generation was never re-run**) |
| Judge timeouts / retries (first pass) | 29 / 29 across 78 requests — recovered |
| Circuit-breaker events | **0** |

Reliability and quality denominators are separate: no provider failure was ever scored as
`answer_correctness = 0`.

## 5–7. Quality (52 scored generative cases)

| Metric | Mean | 1.0 | 0.5 | 0.0 |
|---|---|---|---|---|
| **answer_correctness** | **0.740** | 38 (73.1%) | 1 (1.9%) | 13 (25.0%) |
| **answer_completeness** | **0.721** | 35 (67.3%) | 5 (9.6%) | 12 (23.1%) |
| **answer_groundedness** | **0.798** | 39 (75.0%) | 5 (9.6%) | 8 (15.4%) |

## 8. Category results (categories from existing suite titles/routes)

### By route
| Route | n | correctness | completeness | groundedness | refusals |
|---|---|---|---|---|---|
| single | 21 | 0.786 | 0.762 | 0.738 | 2 |
| multi | 13 | 0.692 | 0.692 | 0.731 | 3 |
| group | 18 | 0.722 | 0.694 | 0.917 | 3 |

### By scenario (worst first)
| Scenario | n | correctness | completeness | groundedness | refusals |
|---|---|---|---|---|---|
| **compound multi-part** | 3 | **0.000** | **0.000** | 0.500 | 1 |
| group / cross-tenant | 15 | 0.733 | 0.700 | 0.900 | 2 |
| multi-profile | 8 | 0.750 | 0.750 | 0.750 | 1 |
| temporal / latest-state | 2 | 0.750 | 1.000 | 0.750 | 0 |
| single-profile factual | 18 | 0.778 | 0.722 | 0.778 | 1 |
| cross-document synthesis | 1 | 1.000 | 1.000 | 1.000 | 0 |
| exact fact / identifier | 2 | 1.000 | 1.000 | 1.000 | 0 |
| negative / scope isolation | 3 | 1.000 | 1.000 | 0.667 | 3 |

**Answer to the question posed:** yes, but the split is sharper than "simple good / complex bad".
Simple factual and negative/scope cases are strong (0.78–1.00). **Compound multi-part questions fail
completely (0.000 on both correctness and completeness, n=3)** — a far worse cliff than the modest
single→multi/group drop (0.786 → 0.692/0.722).

## 9. case-004 analysis (`Dev Iyer + Arjun Vale — cross-user QL-2D`)

| Field | Value |
|---|---|
| correctness / completeness / groundedness | **1.0 / 1.0 / 1.0** |
| refusal | no |
| top-5 source diversity | **1 distinct post** (1 tenant) |
| candidate (top-10) source diversity | **1 distinct post** (1 tenant) |
| candidates → context | 10 → 5 (cap active), ctx ≈1,965 est. tokens |
| scores | top1 1.000, top5 0.271, top10 0.207; gaps 0.729 / 0.065 |
| expected cross-document evidence outside top5? | **No** — the whole candidate pool came from one post, so diversity was unobtainable; the answer was still fully correct |
| judge reason | "Answer matches reference facts about enclosure height and lever blockage." |

**This retracts the flag I raised after the capacity check.** Single-source top-5 did **not** harm this
case — it scored perfectly. (It also answered in ~2.8 s, versus a ~7-minute stall pre-cap.)

## 10. case-022 analysis (`Field Circle — compound Meral + Hollow Stair`)

| Field | Value |
|---|---|
| correctness / completeness / groundedness | **0.0 / 0.0 / 1.0** |
| refusal occurred? | **No** — it answered (contrast: it *did* refuse during the capacity check on the same prompt) |
| top-5 source diversity | 3 distinct posts, 2 tenants |
| candidate diversity | 3 distinct posts, 2 tenants |
| scores | top1 0.500, top5 0.250, top10 0.167; gaps 0.250 / 0.083 |
| useful evidence in positions 6–10? | **NO** — 4 reference tokens absent from top-5 **and** from the 6–10 snippets |
| judge reason | "Answer omits the 11.4 Meral Index from reference, contradicting factual content." |

It answered the Hollow-Stair half and explicitly said the excerpts "don't mention a Meral Index at
all" — i.e. it **answered one half of a two-part question**. Groundedness 1.0 (what it did say was
supported); correctness 0.0 (a required fact is missing). **Expanding to top-10 would not fix this** —
the Meral evidence is not in the retrieved pool at all.

⚠️ **Non-determinism note:** the same prompt produced a *refusal* (77 output tokens) during
yesterday's capacity check and a *partial answer* (520 output tokens) here, at `temperature = 0`.
Single-run scores on this provider carry real variance.

## 11. Failed / partial cases (21 of 52 below 1.0 on some dimension)

| case | route / scenario | corr / comp / grnd | refusal | posts top5 / cand | classification |
|---|---|---|---|---|---|
| case-003 | single / temporal | 0.5/1.0/0.5 | no | 1/1 | weak support + factual mismatch |
| case-005 | group | 1.0/0.5/1.0 | no | 2/4 | partial — missing expected detail |
| case-008 | multi | 0.0/0.0/0.0 | no | 2/2 | unsupported claim + factual mismatch |
| case-014 | single / negative | 1.0/1.0/0.0 | **yes (correct)** | 2/2 | groundedness artefact — see note |
| case-018 | multi / **compound** | 0.0/0.0/0.5 | yes | 2/2 | refusal despite evidence |
| case-020 | multi / **compound** | 0.0/0.0/0.0 | no | **1**/3 | source-diversity + factual mismatch |
| case-022 | group / **compound** | 0.0/0.0/1.0 | no | 3/3 | answered one half only |
| case-023 | multi | 0.0/0.0/0.0 | yes | 3/3 | refusal despite evidence |
| case-024 | single | 0.0/0.0/0.5 | no | 2/2 | factual mismatch |
| case-026 | single | 0.0/0.0/0.5 | no | 1/1 | factual mismatch (temporal update missed) |
| case-030 | group | 0.0/0.0/1.0 | no | 2/2 | factual mismatch |
| case-032 | group | 0.0/0.0/0.0 | yes | 2/2 | refusal despite evidence |
| case-033 | group | 0.0/0.5/1.0 | no | 2/3 | **evidence present in 6–10** (see §13) |
| case-039 | single | 1.0/0.5/1.0 | no | 2/2 | partial — missing detail |
| case-040 | single | 1.0/0.5/1.0 | no | 2/2 | partial — missing detail |
| case-043 | single | 0.0/0.0/0.0 | no | 1/1 | unsupported + factual mismatch |
| case-045 | single | 0.0/0.0/0.0 | no | 1/1 | unsupported + factual mismatch |
| case-052 | group | 0.0/0.0/1.0 | no | **1**/1 | source-diversity + factual mismatch |
| case-054 | single | 1.0/1.0/0.0 | **yes (correct)** | 2/2 | groundedness artefact |
| case-058 | group | 1.0/1.0/0.5 | no | 3/3 | weakly-supported claim |
| case-059 | group | 1.0/0.5/1.0 | no | 4/4 | partial — missing detail |

**Groundedness artefact (worth fixing later, not now):** for a *correct refusal* (case-014, case-054)
the judge scores groundedness 0.0 because a refusal has no supporting context. That drags the
groundedness mean down without indicating a real defect. The `negative / scope isolation` row
(correctness 1.000, groundedness 0.667) is entirely this effect.

## 12. Refusals
8 of 52 answers were application-level refusals (deterministic detector). Mean correctness for
refusals **0.625** vs **0.761** for non-refusals — a moderate signal. 3 of them (case-014, case-054,
and one negative-scope case) were *correct* refusals; the rest (case-018, case-023, case-032) refused
despite usable retrieved evidence.

## 13. Top-5 vs candidate-6–10 diagnostic (group/multi failures only; candidates never sent to the model)

Deterministic reference-token presence check across the already-retrieved candidates:

| Verdict | Cases |
|---|---|
| **YES** — evidence absent from top-5 but present in 6–10 | **1** — case-033 (`Kestrel Station`, `Two`; 2 of 7 tokens) |
| **NO** — all reference tokens already in top-5 | 5 — case-005, case-008, case-020, case-032, case-052, case-058, case-059 |
| **NO** — tokens absent from top-5 **and** from 6–10 | 3 — case-018, case-022, case-023 |
| UNCLEAR (no distinctive tokens) | 1 — case-030 |

**This is the headline finding: only 1 of 21 failures (~5%) would plausibly be fixed by expanding to
top-10.** For most failures the evidence was *already in the top-5 and the model did not use it*, or
it was *missing from the entire 10-candidate pool* (a retrieval-recall problem that more slots cannot
solve). Caveat: 6–10 snippets are truncated to 700 chars, so "absent from 6–10" is suggestive, not
conclusive.

## 14. Runtime signal analysis — "Potential Adaptive Retrieval Signals"

Descriptive comparisons only; no classifier, no thresholds chosen.

| Signal | Group | correctness | Others | correctness | Predictive? |
|---|---|---|---|---|---|
| **compound multi-part scenario** | n=3 | **0.000** | n=49 | 0.786 | **STRONG** |
| answer is a refusal | n=8 | 0.625 | n=44 | 0.761 | moderate |
| top-5 all from ONE post | n=22 | 0.750 | n=30 | 0.733 | **no effect** |
| ≥4 of 5 chunks from one post | n=36 | 0.764 | n=16 | 0.688 | **inverted** (slightly better) |
| candidates more diverse than top-5 | n=7 | 0.714 | n=45 | 0.744 | **no effect** |

`gap_top5_top10` was available for 31 cases (mean 0.118); RRF fused scores are rank artefacts, so
absolute gaps are weak evidence by construction.

**Honest conclusion: the source-diversity hypothesis is NOT supported by this baseline.** Low top-5
diversity (even 5/5 chunks from one post) did not predict failure — it was marginally *better*. The
only strong predictor is the **compound multi-part question type**, which is a query-decomposition
signal, not a retrieval-breadth signal. This weakens the evidential case for a 5→10 escalation trigger
and strengthens the case for query decomposition / multi-hop retrieval.

## 15. NVIDIA 20B application latency (ms) — N=52
| mean | p50 | p95 | p99 | max |
|---|---|---|---|---|
| **3,384.9** | 2,409.9 | 8,947.0 | 9,544.7 | 9,957.9 |

## 16. NVIDIA 120B judge latency (ms) — N=52 · *offline evaluation overhead, never user-facing*
| mean | p50 | p95 | p99 | max |
|---|---|---|---|---|
| **40,447.6** | 19,399.0 | 135,373.6 | 146,077.3 | 149,505.3 |

The judge is ~12× slower than the application model and was the only source of provider trouble.
Deliberate pacing (≈118 s total sleep across both stages) is excluded from these latencies.

## 17–18. Tokens — application vs judge (never combined)

| Source | N | mean in | mean out | total in | total out | grand total |
|---|---|---|---|---|---|---|
| Application NVIDIA `gpt-oss-20b` | 52 | 2,103.8 | 290.0 | 109,400 | 15,078 | **124,478** |
| Judge NVIDIA `gpt-oss-120b` | 52 | 1,500.1 | 425.5 | 78,004 | 22,127 | **100,131** |

Application input ≈2.1k tokens/case confirms the top-5 policy in practice (pre-cap group/multi prompts
were ~4.9k).

## 19. Rate limits / timeouts / retries

| Metric | Stage A (app) | Stage B (judge, first pass) | Judge retry pass |
|---|---|---|---|
| requests | 52 | 78 | 3 |
| successes | 52 | 49 | 3 |
| HTTP 429 | **0** | **0** | **0** |
| HTTP 5xx | **0** | **0** | **0** |
| timeouts | **0** | 29 | 0 |
| retries | **0** | 29 | 0 |
| circuit-breaker events | **0** | **0** | **0** |

Application generation was flawless. All instability was in the 120B judge, and the two-stage design
meant it never cost a single re-generation.

## 20. Global search (8 cases) — reported separately, NOT in the model-quality denominator

All 8 completed, **LLM-free** (`llm_used = false`, zero NVIDIA 20B calls, not judged). Ranked distinct
posts returned per case: case-010 → 1, case-011 → 2, case-012 → 2, case-013 → 1, case-048 → 1,
case-049 → 4, case-050 → 2, case-051 → 1.

The suite carries **no structured** `expected_source` / `expected_post` / `expected_citations` fields
(only free-text expected answers), so `retrieval_hit_at_k` and `citation_correctness` remain
**NOT_SUPPORTED** and no generation-quality score was invented for these cases.

## 21. Limitations
- **Synthetic, deliberately repetitive 60-case corpus** — absolute scores will not transfer to real users.
- **n is small**, and several scenario cells are n=1–3 (compound = 3). Treat scenario means as directional.
- **Same model family**: judge `gpt-oss-120b` vs application `gpt-oss-20b`. Using the larger model
  avoids direct self-judging but does **not** eliminate model-family bias.
- **Single judge, single run, no human adjudication.** Demonstrated provider non-determinism at
  `temperature=0` (case-022 refused yesterday, answered today) means ±1 case of noise is plausible.
- **Groundedness is not meaningful for correct refusals** (case-014/054) and currently penalises them.
- 6–10 snippets truncated to 700 chars → "absent from 6–10" is suggestive, not conclusive.
- Free NVIDIA endpoint: no quota headers; capacity was fine here but is not guaranteed.
- Chunk size (500/50) unchanged, so context count and chunk size were never varied together.
- Production runs Groq `gpt-oss-120b`; this measures **NVIDIA `gpt-oss-20b`**, not production behaviour.

## 22. Conclusion

**NVIDIA GPT-OSS-20B with at most 5 retrieved chunks: correctness 0.740, completeness 0.721,
groundedness 0.798, request_success 100%.** Strong on simple factual and negative/scope questions
(0.78–1.00), materially weaker on multi/group (≈0.69–0.72), and a **complete failure on compound
multi-part questions (0.000)**.

The baseline also **weakens the original motivation for adaptive 5→10 retrieval**: only ~5% of failures
show evidence uniquely available in positions 6–10, and low source diversity did not predict failure.
The dominant failure modes are (a) the model not using evidence already present in its context,
(b) evidence missing from the whole candidate pool, and (c) compound questions being answered only
half-way. Production was not changed and no recommendation is made here.

## 23. Next experiment — LIST ONLY, not executed
- **Failed/partial top-5 cases only: top-5 vs top-10**, same provider/model/retrieval/prompt/harness
  (expected to help ~1 case on this evidence — worth running precisely because the prediction is now low).
- Query **decomposition / multi-hop** for compound multi-part questions — the strongest signal found.
- Chunk-size A/B (500 vs smaller) as a *separate* experiment, never combined with a context-count change.
- Judge robustness: multi-judge panel or human adjudication on the 21 failures; fix the
  refusal-groundedness artefact.
- Only if needed afterwards: top-15 / query rewrite.
