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

## 17. PROSE QUALITY REGENERATION (2026-08-27)

The first Cohort A prose was rejected: a single opening fragment recurred up to
**20x** inside one post. The generator was rebuilt around a context-aware,
pattern-based sentence engine. Ground truth was NOT touched — same seed family,
same post/fact assignments, same word-range classes, same fact values.

### 17.1 What changed in the generator
| Change | Reason |
|---|---|
| `PATTERNS` banks per category (30/30/27/28/23/28) | replaces a handful of reused fragments |
| `ctx()` context slots (company, role, tech, place, month) | sentences vary with the post, not at random |
| opening budgets shared across fact + filler sentences | repetition bounded over the WHOLE post |
| two-pass fill, then on-demand top-up | word range is frozen, so running short is not an option |
| sibling spacing (same template >= 8 sentences apart) | template siblings differ only by slot fill |
| `t1`/`t2` sampled without replacement | fixed "depth in X and breadth across X" |
| widened `measurement` fact bank (3 -> 8) | posts can carry more facts than a 3-way bank can open distinctly |
| title qualifier on bank re-cycle | an author no longer reuses a title across 18 posts |

### 17.2 Within-post repetition — FIXED
| Metric | Before | After |
|---|---|---|
| worst 4-word opening repeat in a post | **20x** | **2x** |
| mean worst-per-post | 2.44 | **1.04** |
| substantial non-noise posts exceeding 2 | many | **0** |
| duplicate titles within one author | 2 per author | **0** |
| same-value contrast slots (`t1 == t2`) | present | **0** |

Noise posts are exempt by design (SS11/SS27 specify low-signal, repetitive
boilerplate); their worst opening repeat is **6x** and is reported, not gated.

### 17.3 Cross-post duplication — NOT fixed, and worse than first reported
An earlier figure in this workstream ("98 duplicate sentences") was measured
per-post and was wrong as a corpus-wide statistic. Measured properly:

| Metric | Value |
|---|---|
| sentences corpus-wide | 23,240 |
| **distinct** sentences | **1,992** |
| duplicate instances | 21,248 (**91.4%**) |
| most-repeated single sentence | **177x** |
| distinct sentences appearing under **more than one author** | **694 / 1,992 (34.8%)** |

Per category: noise 99.1% dup, travel_food 95.9%, adversarial 94.8%,
ai_ml_swe 89.9%, eng_notes 89.9%, job_search 88.8%.

This is a property of a finite template bank spread over 450 posts. It is
**material to RAG evaluation**: identical sentences under different authors
produce near-duplicate chunks, which weakens cross-user attribution and
high-overlap discrimination testing. It is raised as DECISION REQUIRED — the
fix is a corpus-design change, not an implementation detail.

### 17.4 Question quality pass
Stem diversity (4-word), 240 cases:

| Metric | Before | After |
|---|---|---|
| worst stem frequency | 35 (14.6%) | **11 (4.6%)** |
| distinct 4-word stems | 116 | **138** |

Defects found by manual review of 60 questions and fixed (wording only):
- **`target_role` and other raw schema field names** reached retrieval in 7 questions.
- **Double genitives** — "Arjun Balan's their manager's name" — across `unanswerable`.
- **"Two things -"** used for questions carrying 3, 4 and 5 needs.
- All 35 compound cases opened with the identical preamble "Could you tell me".

Metadata drift vs frozen ground truth: **0 cases**. Rare exact tokens preserved: **0 violations**.

Not fixed (DECISION REQUIRED): **7 question texts are duplicated with
CONFLICTING ground truth** — the same wording maps to different expected facts,
so an evaluator would mark a correct answer wrong.

### 17.5 Chunk density rebaseline
The 1,000-1,600 gate is **RETIRED**; 1,800-2,500 is now informational only.

Total chunks **1,752** (below the informational range, not a failure).
Heading counts were NOT reduced to chase a projection.

| Headings | Posts | Mean chunks |
|---|---|---|
| 0 | 36 (8.0%) | 1.78 |
| 3 | 36 (8.0%) | 3.39 |
| 4 | 324 (72.0%) | 4.00 |
| 5+ | 54 (12.0%) | 5.00 |

Same-post concentration: **12.7% of posts reach 5 chunks**, **0% reach 6**, max
**5** — so a top-5 retrieval can be filled entirely from one post, but no post
can exceed the window on its own.

### 17.6 Gates
`validate_rag_stress_cohort.py`: **PASS** (0 problems) — now also gating
paragraph-opening repetition and question-stem diversity/hygiene.
`test_corpus_generator.py`: **25 passed**.
Determinism: regeneration to a temp directory is **byte-identical**,
fingerprint `8741890b4a25a4963930365fe544b90b`.

## 18. Status
Cohort A: **QUALITY-REGENERATED / VALIDATED / CHUNK-MEASURED / NOT INGESTED**.
Cohorts B/C/D: **NOT GENERATED**. AWS: **UNCHANGED**.
