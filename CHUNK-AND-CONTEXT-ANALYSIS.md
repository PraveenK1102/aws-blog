# MultiTenantRAG — Chunk Analysis + Default Top-5 LLM Context

Date 2026-08-22 · analysis SHA `03fda30` · change deployed as `d5af30e`
**Inference used for this analysis: ZERO** — no Groq, no NVIDIA, no judge, no embedding calls.
All corpus numbers come from a read-only Qdrant scroll of already-indexed points.

---

## 1. Current chunker (actual code, `lambdas/ingest_worker/chunker.py`)

| Setting | Implemented value |
|---|---|
| `max_tokens` | **500** (soft ceiling, default arg of `chunk_markdown`) |
| `overlap_tokens` | **50** |
| Derived `max_chars` | 2000 (`max_tokens * 4`) |
| Derived `overlap_chars` | 200 |
| Token estimation | **`chars / 4` heuristic** — the code states a real tokenizer "would require the tokenizer, adding complexity without much gain" |
| Splitting | markdown/structural: header hierarchy respected, chunks prefixed with `[H1 / H2 / H3]`, paragraph split, sentence-level fallback for oversized paragraphs |

`max_tokens = 500` is a **ceiling, not the average**. §3–§5 measure the actual distribution.
No tokenizer-based estimate is reported: none was already installed, and one was **not** added
just for this measurement.

## 2. Corpus (read-only)

| Set | Points |
|---|---|
| Total in `multitenant_chunks` | **2,057** |
| `seed-20260822` (the evaluation corpus) | **2,053** across **50 posts / 6 tenants** |
| Legacy/original | 4 (mean ~184 est. tokens — a different, older shape) |

All statistics below are the **seed** corpus unless stated. Nothing was mutated, deleted or re-embedded.

## 3. Chunk size distribution (seed, N = 2,053)

| Measure | mean | p50 | p75 | p90 | p95 | p99 | max | min |
|---|---|---|---|---|---|---|---|---|
| characters | 1892.6 | 1957 | 1991 | 2013 | 2023 | 2031 | 2038 | 20 |
| words | 278.8 | 288 | 294 | 299 | 301 | 305 | 310 | 3 |
| **est. tokens (chars/4)** | **473.1** | **489.2** | 497.8 | 503.2 | **505.8** | 507.8 | **509.5** | 5.0 |

## 4. Token histogram (seed)

| Bucket (est. tokens) | Count | % |
|---|---|---|
| 0–100 | 54 | 2.6% |
| 101–200 | 15 | 0.7% |
| 201–300 | 9 | 0.4% |
| 301–400 | 6 | 0.3% |
| **401–500** | **1,587** | **77.3%** |
| **>500** | **328** | **16.0%** |

## 5. Near-maximum analysis — the key finding

| Threshold | Count | % of seed chunks |
|---|---|---|
| ≥ 400 | 1,969 | **95.9%** |
| ≥ 450 | 1,960 | **95.5%** |
| ≥ 475 | 1,774 | **86.4%** |
| ≥ 500 | 388 | 18.9% |

**Answer to the question posed:** 500 is **not** a rarely-reached ceiling — the chunker packs
essentially every chunk right up against it. The distribution is extremely tight (p50 489,
p95 506, max 509.5), and ~16–19% actually exceed 500 because the ceiling is a *soft*,
`chars/4`-approximated bound. The small 0–100 bucket (2.6%) is short trailing/heading fragments.

## 6. Chunks per post (seed)

| Measure | Value |
|---|---|
| posts | 50 |
| mean | **41.1** |
| p50 | 41 |
| p95 | 43 |
| min / max | 39 / 43 |

Per tenant (6 seeded authors): 245–413 chunks over 6–10 posts each; mean chunk size is remarkably
uniform across tenants (471.6 – 477.1 est. tokens), confirming the ceiling — not content — sets size.

## 7. THEORETICAL context budgets

Derived from the measured seed distribution × K. **These are THEORETICAL** — they are not
query-observed retrieval-context distributions.

| Context chunks | Mean-based | P50-based | P95-based | Worst-case (max) |
|---|---|---|---|---|
| **5** | **2,366** | 2,446 | 2,529 | 2,548 |
| 10 | 4,731 | 4,892 | 5,058 | 5,095 |
| 15 | 7,096 | 7,338 | 7,587 | 7,642 |

## 8. Prompt overhead (static measurement, no LLM)

| Component | chars | ~est. tokens |
|---|---|---|
| single-profile system template, 0 chunks | 1,848 | **462** |
| group system template, 0 chunks | 409 | **102** |
| overview/decline template (10 titles, **no chunks**) | 1,081 | 270 |
| per-chunk label overhead (single, `[Source: …]`) | — | ~4 / chunk |
| per-chunk label overhead (group, incl. writer attribution) | — | ~8 / chunk |

Realistic totals at the measured mean chunk (1,893 chars):

| Prompt | 5 chunks | 10 chunks |
|---|---|---|
| single-profile total | ~2,858 tok (retrieved ~2,365 + overhead ~493) | ~5,255 tok (overhead ~525) |
| group total | ~2,516 tok (retrieved ~2,365 + overhead ~151) | ~4,930 tok (overhead ~200) |

Non-retrieval overhead is therefore **~150–530 tokens** — small relative to retrieved context.
Conversation history adds up to 8 prior turns (`common/chats.py`) and is variable; not counted here.

## 9. Behaviour by flow — BEFORE this change (verified in code, not assumed)

| Flow | Candidate retrieval | RRF output | Sent to LLM | Citation source |
|---|---|---|---|---|
| single-profile ask | dense 20 + sparse 20 prefetch | `limit=TOP_K` = **5** | **5** | the same 5 |
| group ask | dense 30 + sparse 30 prefetch | `limit=TOP_K*2` = **10** | **10** ⚠ | all 10 |
| multi-profile (eval path) | dense 30 + sparse 30 prefetch | `limit=TOP_K*2` = **10** | **10** ⚠ | all 10 |
| overview / decline (small model) | n/a — title-only prompt | n/a | **0 chunks** | none |
| **global search** | dense, `limit=10` | — | **N/A — LLM context chunks = N/A, LLM calls = 0** | results only |

The group/multi paths were sending **twice** the single-profile context (~4.9k vs ~2.9k prompt tokens).

## 10. Behaviour AFTER — `MAX_LLM_CONTEXT_CHUNKS = 5`

| Flow | Candidate retrieval (UNCHANGED) | Sent to LLM | Citation source |
|---|---|---|---|
| single-profile ask | 20+20 → RRF 5 | **5** | the same 5 |
| group ask | 30+30 → RRF **10 candidates kept** | **5** | the same 5 |
| multi-profile (eval path) | 30+30 → RRF **10 candidates kept** | **5** | the same 5 |
| overview / decline | n/a | 0 chunks | none |
| global search | unchanged | **N/A (0 LLM calls)** | unchanged |

Implementation: `MAX_LLM_CONTEXT_CHUNKS = int(os.environ.get("MAX_LLM_CONTEXT_CHUNKS", "5"))`
plus a single `_llm_context()` helper — the one place that decides what the model sees. Deployed
with the env var explicitly set to `5`.

## 11. Candidate retrieval vs LLM context — deliberately separate

```
query → hybrid retrieval (dense 20–30 + sparse 20–30 prefetch)
      → RRF fusion (TOP_K or TOP_K*2 candidates)
      → relevance floor / dedupe / ranking      ← unchanged
      → take first MAX_LLM_CONTEXT_CHUNKS (5)   ← NEW, the only new step
      → prompt
```
Retrieval breadth was **not** reduced. The group/multi paths still fetch and rank 10 candidates;
only the prompt is capped. This is what makes the future 5→10→15 escalation cheap: the extra
candidates are already retrieved and ranked.

## 12. Citation consistency — verified

Invariant enforced: **LLM context chunks == citation-eligible chunks.** The same capped list is
passed to the prompt builder *and* to `_dedupe_citations` / `_dedupe_citations_attributed`, so the
system cannot cite evidence the model never received. Previously the group path cited from all 10
retrieved chunks while the model saw all 10 (consistent then, and still consistent now at 5).
Covered by tests (`test_single_citations_come_only_from_capped_context`,
`test_group_attributed_citations_come_only_from_capped_context`).

## 13. Chunk-size conclusion

### **B. Chunk size is likely too large and deserves an experiment.**

Evidence:
- The distribution is **pinned to the ceiling**, not spread below it: mean 473, p50 489, p95 506,
  and **95.9% of chunks ≥400 / 86.4% ≥475**. Variance is tiny (p50→max spans only ~20 tokens).
- That tightness means chunk boundaries are being decided by the **size limit**, not by markdown
  structure or semantics — the structural splitter rarely binds first, so chunks are very likely
  being cut mid-topic.
- Consequence for the 5-slot budget: 5 chunks ≈ 2,366 retrieved tokens but only **5 distinct
  passages**. Smaller chunks would buy more *diverse* evidence per slot at equal token cost.
- ~16% of chunks exceed the nominal 500 ceiling (max 509.5), because it is a `chars/4` approximation.

Honest limits of this conclusion: **quality impact is NOT measured.** No A/B of chunk size against
the eval set exists, and the corpus is synthetic filler-heavy (repetitive lines), which inflates
size uniformly and may not represent real posts. So this is "worth an experiment", not "known bad".
**Chunk size, overlap, splitter and embeddings were NOT changed.**

## 14. Future experiment — realistic prompt capacity

Test small/medium/large realistic prompts per provider before choosing an escalation ceiling.
Directly relevant evidence already gathered: the NVIDIA free endpoint served **~2.4k-token**
single-profile prompts fine (3/3 cases) but **stalled >120 s on ~4.5k-token** prompts. The 10-chunk
group path was producing ~4.9k-token prompts — i.e. exactly the size that stalled. Capping to 5
brings group prompts to ~2.5k, in the range that worked. This should be re-verified, not assumed.

## 15. Future agentic RAG (design note only — NOT implemented)

```
START
  ↓ retrieve candidate pool (already 10 for group/multi)
  ↓ select top 5  ← today's deterministic behaviour
  ↓ evaluate evidence sufficiency / query complexity
  ├── sufficient ──────────→ answer
  └── insufficient ──→ expand to 10 → reassess
                       └─ still insufficient → expand to 15
                                              → optional query rewrite
                                              → answer
```
The runtime decision could use measurable state: retrieval scores, score gaps, source diversity,
contradiction/conflict signals, question complexity, answerability. **The decision algorithm is
deliberately not chosen** — that is an architect decision once evaluation data exists.

Why this becomes legitimately agentic later:

| Today (deterministic) | Future (agentic) |
|---|---|
| retrieve fixed context → generate | retrieve → inspect state → decide whether more retrieval/rewrite is needed → conditionally act → stop when evidence suffices |

That runtime conditional branching is the reason LangGraph would eventually be justified.
**LangGraph = NOT IMPLEMENTED.** No query rewriting, complexity classifier, adaptive top-k,
10/15 escalation or model routing was implemented.

## 16. Limitations
- **No inference calls were used for this analysis** — sizes are static text measurements.
- Token counts are the production **`chars/4` approximation**, not true tokenizer counts; no
  tokenizer was installed for this task. Real token counts will differ (typically somewhat lower
  for prose, higher for punctuation/identifier-dense text).
- §7 budgets are **theoretical** (distribution × K), not observed per-query retrieval contexts.
- Corpus is a **synthetic, deliberately repetitive** 50-post seed; 4 legacy points are excluded from
  the headline statistics.
- History tokens (up to 8 turns) are not included in the prompt-overhead totals.
- Quality effects of chunk size, and of 5 vs 10 context chunks, are **unmeasured** — the paused
  Groq baseline and halted NVIDIA evaluation mean no like-for-like quality comparison exists yet.
