# RAG Architecture Stress Corpus v1 — Specification

**DESIGN ONLY. Nothing generated, nothing seeded, no AWS or provider call.**
**Date:** 2026-08-27 · **Seed:** `20260827` · deterministic (byte-identical on regeneration)

> **Data classification.** Every identity and fact here is **synthetic test data** for
> architecture evaluation. These are not real people, not production users, and none of this
> has been written to production.

## 1. Purpose
This is a **corpus-scale and retrieval-complexity** stress design, not an HTTP load test. It
does not measure requests/sec, Lambda concurrency or API Gateway throughput. It measures what
happens to retrieval quality as the index grows and as semantically confusable content
accumulates around a fixed answer.

The architecture under test is unchanged: tenant scope → semantic cache → Router V2 →
LangGraph decomposition → Titan dense + local BM25 → Qdrant dense probe + hybrid RRF →
coverage-aware merge → `MAX_LLM_CONTEXT_CHUNKS = 5` → Groq generation → citation parity.

## 2. Corpus shape
```
100 synthetic users × 18 posts = 1,800 posts
7,270 structured facts
240 golden evaluation cases
```

| Category | Per cohort | Total |
|---|---:|---:|
| High-overlap job search | 10 | 40 |
| AI / ML / software engineering | 5 | 20 |
| Travel / food | 3 | 12 |
| Engineering notes | 3 | 12 |
| Noise / low-signal | 2 | 8 |
| Adversarial retrieval | 2 | 8 |
| **Total** | **25** | **100** |

## 3. Cohorts and the scale ladder
Four cohorts of 25, each with an **identical difficulty composition**, so growth comparisons
are meaningful and hard users are not back-loaded.

| Stage | Users | Posts |
|---|---:|---:|
| A | 25 | 450 |
| A+B | 50 | 900 |
| A+B+C | 75 | 1,350 |
| A+B+C+D | 100 | 1,800 |

## 4. High-overlap job-search design
The 40 job-search users deliberately share companies (Amazon, Microsoft, NVIDIA, Google,
Adobe, Atlassian, Zoho, Freshworks, Razorpay and two synthetic startups), rounds, roles and
technologies. The **same company recurs with different outcomes** — offer, rejected at system
design, rejected at DSA, rejected at behavioural, withdrew, ghosted, passed all rounds,
rejected by hiring manager — so a question about one person cannot be answered by pattern
-matching the company name.

Roles and technologies are reused on purpose (Python, Java, FastAPI, Spring, React,
PostgreSQL, Redis, AWS, Docker, Kubernetes, PyTorch, RAG, LangGraph, Qdrant, vector search).
Retrieval must not succeed merely because each user speaks a private vocabulary.

**Controlled contradictions** (§7): different users legitimately report different results for
the same subject — e.g. Redis latency improved 35% / improved 18% / made no difference. Each
value stays attributable to exactly one `(user, post, fact)`, so the ground truth is never
ambiguous even though the corpus is.

**Temporal updates** (§8): `status_change` facts carry `temporal_status` of `superseded` or
`latest`, supporting "what were they targeting originally?" versus "what now?". 534 facts are
non-current.

## 5. Adversarial retrieval design
Eight adversarial users carry near-identical rare tokens drawn from eight families:
`Project Bluefin / Bluefire / Bluebird` · `INC-731 / INC-713 / INC-137` ·
`QL-2C / QL-2D / QL-2B` · `Model R14 / R41 / R144` · `WG-03 / WG-30 / WG-003` ·
`batch-9812 / 9821 / 9182` · `SKU-44A1 / 44AI / 441A` · `runbook-7 / 77 / 07`.

639 facts carry a rare token and its confusable siblings, and posts sharing a family are
cross-linked via `confusable_post_ids`. These separate **BM25 exact-token matching** from
**dense semantic similarity** — a dense-only retriever tends to treat `INC-731` and `INC-713`
as interchangeable.

## 6. Noise, travel/food and engineering notes
**Noise (8 users)** carry diary entries, formatting tests and status updates with deliberate
keyword overlap — "I watched an Amazon Prime movie" must not outrank "I interviewed at Amazon
for an ML role". Noise posts carry **zero** evaluation-relevant facts (only `noise_fact_ids`),
so a noise hit is always measurably wrong. This is keyword collision, **not** prompt injection
— injection remains a separate experiment.

**Travel/food (12)** reuse the same places and venues with different ground truth, including
`mentioned_without_visiting`, so "who visited X?" cannot be answered by mere co-occurrence.

**Engineering notes (12)** overlap on caching, Redis, indexes, transactions, queues, Docker,
AWS, rate limiting, timeouts, retries, CI/CD, React and API design, each with distinct numbers
— a second high-overlap domain independent of job search.

## 7. Fact manifest
**7,270 facts**, 3–6 per substantial post, 0 for noise posts. Stable IDs of the form
`FACT-U017-P06-003` exist **before** any prose is written.

| Fact type | Count |
|---|---:|
| measurement | 2,516 |
| event_outcome | 1,432 |
| preference | 1,207 |
| location_visit | 942 |
| identifier | 639 |
| status_change | 534 |

Temporal (non-current) **534** · confusable **639** · rare-token **639** · high-importance
**1,897**. Each record carries `fact_id, user_id, post_id, topic, fact_type, subject,
predicate, value, date, temporal_status, expected_evidence, confusable_with, rare_tokens,
importance`.

## 8. Post length distribution
| Class | Words | Count | Share |
|---|---|---:|---:|
| Medium | 500–800 | 1,068 | 59.3% |
| Long | 900–1,200 | 560 | 31.1% |
| Short / noise | 150–350 | 172 | 9.6% |

Projected total **1.06M–1.59M words**. Against the production chunker (~500-token target,
~2,000 chars with 200 overlap) that projects roughly **3,400–5,000 chunks**, landing in the
upper part of the 4,000–6,000 engineering target. **This is an estimate only** — the real
count is knowable only once the actual chunker processes real prose, and markdown-structure
splitting will likely push it higher.

## 9. Golden evaluation set — 240 cases
| Category | Count |
|---|---:|
| Simple factual | 60 |
| High-overlap discrimination | 40 |
| Cross-user comparison | 35 |
| Compound / decomposition | 35 |
| Scope isolation | 25 |
| Exact-token / BM25 | 15 |
| Unanswerable | 15 |
| Temporal / update | 15 |

Answerable **200** · unanswerable **40** (15 explicit + 25 scope-isolation) ·
`should_decompose` **61**.

Scope coverage: single-user 130 · 2-user 42 · 3-user 17 · 4-user 16 · 5-user 14 · 6-user 6 ·
global 15. Group scope is expressible via `scope_group_ids` but no AWS group is created here.

Each case carries `question_id, question, minimum_cohort, minimum_user_count, scope_type,
scope_user_ids, scope_group_ids, query_type, answerable, expected_user_ids,
expected_post_ids, expected_fact_ids, expected_answer_facts, should_decompose,
expected_router_class, expected_router_reason, expected_citation_post_ids,
forbidden_user_ids, forbidden_post_ids, required_fact_count, scale_stability, notes`.

## 10. Fixed ground truth vs scale-dependent
**202 `fixed_ground_truth`** — every expected user lives in **Cohort A**, so the answer is
already correct at 25 users and later cohorts can only add distractors. This is the metric
that answers *does retrieval degrade as confusable content enters the index?*

**38 `scale_dependent`** — the authoritative source first exists in a later cohort, so the
case is only scorable at or above its `minimum_cohort`. These are measured **separately** and
must never be mixed into the fixed-ground-truth comparison.

The validator enforces the invariant directly: a `fixed_ground_truth` case expecting a
non-Cohort-A user is a hard failure.

## 11. Context-cap pressure
Compound cases requiring **2 (8) · 3 (8) · 4 (7) · 5 (6) · 6 (6)** independent facts. The
6-fact class sits deliberately beyond `MAX_LLM_CONTEXT_CHUNKS = 5`, which is what will show
whether the **context cap** rather than retrieval becomes the bottleneck.

## 12. Duplicate strategy
Type 1 repeated terminology (companies, technologies, places). Type 2 near-duplicate meaning
with different facts (same company, different outcome). Type 3 exact repeated boilerplate,
confined to noise posts. **No principal ground-truth fact ever lives in duplicated
boilerplate**, so every important fact stays attributable.

## 13. Metrics this design enables (not computed here)
Evidence Recall@5 · expected-source hit rate · MRR where one source is authoritative ·
wrong-user retrieval rate · cross-scope leakage · exact-token hit rate · noise displacement ·
compound evidence coverage · context fact coverage · citation-source correctness ·
decline correctness · Router V2 accuracy · decomposition lift over single-query retrieval.

`forbidden_user_ids` / `forbidden_post_ids` are what make leakage and noise displacement
measurable rather than merely observable.

## 14. Two evaluation layers
**Layer 1 — retrieval:** all 240 cases, **no generation call required**.
**Layer 2 — routed answer:** a 60–80 case subset spanning simple, compound, scope,
unanswerable, citation and temporal, to keep provider usage controlled while still exercising
the end-to-end graph. Neither layer is executed in this task.

## 15. Non-goals and constraints honoured
No provider-specific tailoring; no hidden hints such as "the correct answer is…"; the corpus
evaluates the architecture rather than gaming the current models. No architecture change. No
AWS mutation. The deleted 25-user corpus was not recreated.

## 16. Known limitations
1. Chunk-count projection is an estimate; only the real chunker settles it.
2. Fact `value`s are structural placeholders — prose generation must render them faithfully,
   which is what the batch contract in the generation plan enforces.
3. Questions are templated (`[simple]`, `[compound]` …). Natural phrasing is a generation-time
   concern; the prefixes make category filtering trivial and should be stripped before use.
4. Group-scope cases are expressible but no group membership is modelled yet.
5. 40 of 240 cases are unanswerable by design — a high decline rate is expected, not a fault.
