# RAG Stress Corpus V1 — Cohort A Generation Report

**Status: GENERATED / VALIDATED / CHUNK-MEASURED / NOT INGESTED.**
**AWS mutations: 0 · Titan 0 · Groq 0 · NVIDIA 0 · RAGAS 0 · DeepEval 0 · Qdrant writes 0.**
**Date:** 2026-08-27 · **Seed:** 20260827

> Synthetic RAG benchmark corpus. These are synthetic personas, not real users, not
> customers, and not production usage. Nothing has been ingested.

## 1. Cohort A
25 users · 450 posts · 1,821 facts. Composition exactly as the accepted spec: 10 high-overlap
job-search, 5 AI/ML/SWE, 3 travel/food, 3 engineering-notes, 2 noise, 2 adversarial.

## 2. Generation method
Deterministic local generation. **No LLM or external API was called.** Structured facts are
rendered into sentences and surrounded by combinatorial narrative fragments (384 combinations
per category, 480 for noise, 216 for adversarial), seeded per post.

Batches with a validation gate after each: **A1** users 1–10 (180 posts) → **A2** +11–20
(360) → **A3** +21–25 (450). All three gates PASS.

**Anti-leakage:** the generator reads only the user, post and fact manifests. It never
references `rag_stress_eval_v1.json` — asserted by AST test, and by a string check that the
only manifests it loads are the three structural ones.

## 3. Word distribution — PASS
| Class | n | min | mean | max | target |
|---|---:|---:|---:|---:|---|
| short | 43 | 196 | ~259 | 349 | 150–350 |
| medium | 267 | 502 | ~670 | 799 | 500–800 |
| long | 140 | 902 | ~1059 | 1196 | 900–1200 |

**0 posts out of range.** Lengths deliberately spread across each band rather than hugging
the floor.

## 4. Fact fidelity — PASS
1,821 expected facts · **1,821 traced** · 0 missing · 0 wrong-owner · 0 identifier mismatches
· 0 numeric mismatches. Every `fact_trace.json` excerpt exists **verbatim** in its post, with
`evidence_sha256` and character offset. Every rare token (INC-731 / INC-713, QL-2C / QL-2D,
Project Bluefin / Bluefire / Bluebird, Model R14 / R41 …) appears exactly as specified — none
normalised.

**Spec artifact handled:** the structural spec assigns some posts several facts that are the
*same* assertion (identical type, subject, predicate, value). Rendering one sentence per fact
would be padding and would make traceability ambiguous. Identical assertions are rendered
**once**, with every matching fact id mapping to that single occurrence — the truthful
relationship. No fact is dropped.

## 5. Duplication — PASS
0 unexpected exact-duplicate bodies. Intentional noise boilerplate (the "Random test words …
velvet bicycle …" line) is retained as the designed exact-token/duplicate fixture.
**Unique-sentence ratio 1.000** — no full sentence repeats inside any post.

## 6. Naturalized golden questions — PASS
240 retained · **202 Cohort-A applicable** (chosen mechanically from `minimum_cohort`) ·
0 category prefixes remaining · 0 metadata changes · exact tokens preserved in all 15
exact-token cases. Original wording kept as `question_template`.

## 7. Production chunker
`multitenant-rag/lambdas/ingest_worker/chunker.py::chunk_markdown`, **imported, not
reimplemented** (asserted by test), `max_tokens=500, overlap_tokens=50` — exactly what
`ingest_worker` uses.

## 8. Chunk count
450 posts → **2165 chunks**. mean **4.811**/post ·
p50 5.0 · p95 6 · max 7.

Distribution: {"1_chunk_posts": 20, "2_chunk_posts": 12, "3_chunk_posts": 2, "4_chunk_posts": 30, "5_chunk_posts": 325, "6+_chunk_posts": 61}

## 9. Chunk sizes
Estimated tokens: min 12 · mean 219.6 ·
p50 228 · p95 414 ·
p99 489 · max 499.
Chars: min 48 · mean 880.1 · p95 1657 ·
max 1997.

**Soft-limit outliers (>500 est. tokens): 0.** The chunker's
max is soft, so outliers were expected; none occurred because generated sections sit well
inside the window.

## 10. Chunks by category
| adversarial | 163 |
| ai_ml_swe | 450 |
| eng_notes | 324 |
| job_search | 900 |
| noise | 58 |
| travel_food | 270 |

## 11. Chunks by user
min 28 · mean 86.6 · p50 90 ·
p95 108 · max 108.
Top 5: U019=108, U020=108, U021=108, U001=90, U002=90

## 12. Linear projection
**8660 chunks at 100 users — LINEAR PROJECTION ONLY.** Later
cohorts share the difficulty composition but not the content, so the real figure is only
knowable once they exist.

## 13. Chunk-density gate — **DECISION REQUIRED: TOO HIGH**
Gate is 1,000–1,600. Actual **2165**. See the accompanying response for the
root cause, options and recommendation. **No post length was changed**, per instruction.

## 14. Manual quality review — PARTIAL
12 posts reviewed (2 per category). Facts render correctly, identifiers are exact, structure
is category-appropriate, nothing is evaluation-aware, and no test markers appear.

**One honest weakness:** narrative variety is only fragment-level. No full sentence repeats,
but a single *opening fragment* recurs a mean of **9.5** times per post (max 20 — e.g. "The
morning began with" ×20). The prose reads more mechanically than a human blog. It does not
affect fact fidelity or retrieval ground truth, but it is a realism limitation worth fixing in
the same regeneration if the density decision requires one.

## 15. Reproducibility
```
corpus fingerprint  3f54d0eb2dad5cc5d0a4e87c2052f4ffb601b19e4f07338dd3815b1d357abbdf
manifest            20752b2cc63df4fcf993295a874a2e96
fact_trace          37942dd39b07b7da5212846492d6aad0
chunk_stats         f15ff386b1931fb2291b514731a6a631
eval naturalized    2aa2760cc38bd127b49850e997f58bfa
eval applicable     742dbed14576d93039ca617458afa230
```
Regeneration into a temp directory produced a **byte-identical** fingerprint. No generated
file contains a timestamp.

## 16. Status
Cohort A: **GENERATED / VALIDATED / CHUNK-MEASURED / NOT INGESTED**.
Cohorts B/C/D: **NOT GENERATED**. AWS: **UNCHANGED**.
