# RAG Stress Corpus v1 — Generation Plan

**Plan only. No prose was generated and nothing was ingested.**

## 1. Ordering principle
Structured ground truth already exists (`rag_stress_facts_v1.json`, 7,270 facts with stable
IDs). Prose is written **around** those facts, never the reverse. A generation batch receives
only its assigned facts and is forbidden from inventing, renaming, merging or dropping one —
so the evaluation set stays valid no matter how the prose turns out.

## 2. Batch structure
**10 users per batch → 10 batches of 180 posts.** Batches follow cohort order (A: 1–3,
B: 4–5(+), C, D) so the 25-user scale stage can be assembled and tested before the rest exists.

Each batch receives exactly:
* its 10 user records,
* its 180 post records (title slug, date, topic, tags, `target_word_range`),
* the fact records referenced by those posts,
* for adversarial users, the rare tokens **and** their confusable siblings, with an explicit
  instruction that a sibling token must never appear in a post that does not own it.

It does **not** receive the golden evaluation set. Prose must never be written to satisfy a
known question — that would make the benchmark measure itself.

## 3. Per-batch contract
A generated post must:
1. contain every assigned fact, expressed naturally, with `subject`, `predicate` and `value`
   recoverable by a human reader;
2. fall inside `target_word_range`;
3. use only its own `rare_tokens`;
4. add no fact that contradicts another user's ground truth;
5. carry no hidden hints ("the correct retrieval answer is…"), no provider-specific phrasing;
6. contain no real personal data, credential or secret.

Noise posts are the exception: they carry keyword overlap and **zero** assigned facts.

## 4. Mechanical validation per batch
Before a batch is accepted:
* every assigned `fact_id` is traceable to its post;
* word counts fall inside range (report distribution, do not silently truncate);
* no confusable sibling token leaks into a post that does not own it;
* no duplicate title within a user;
* boilerplate repetition appears only in noise posts;
* a secret scan runs over the generated prose.

A batch that fails is regenerated — never hand-patched, which would break determinism.

## 5. Assembly and ingestion (separate, later, authorised task)
1. Assemble validated batches into a corpus file with the same `BEGIN_INGEST` / `END_INGEST`
   boundary convention the previous corpus used.
2. Fingerprint the source and the ingest region (SHA-256).
3. Run `validate_rag_stress_spec.py` plus a corpus-vs-manifest reconciliation.
4. Seed **only into a non-production environment** unless production seeding is separately
   authorised, using the existing supported application path.
5. Ingest cohort by cohort so the 25 / 50 / 75 / 100 stages can each be measured.

**Ingestion cost signal from the previous corpus:** 268 posts produced 8 Titan
`ThrottlingException` events that recovered only because SQS redelivered them. 1,800 posts
with ~3,400–5,000 chunks is roughly an order of magnitude more embedding work, across 100
FIFO message groups. **The ingestion DLQ — still absent, `RedrivePolicy` null — should be in
place before a run of this size**, otherwise a poison message blocks its tenant group for the
full 4-day retention. This is a prerequisite recommendation, not a change made here.

## 6. Measurement plan
**Layer 1 (retrieval, no generation):** all 240 cases at each of the four scales. Records
Evidence Recall@5, expected-source hit rate, MRR, wrong-user rate, cross-scope leakage,
exact-token hit rate, noise displacement, and per-stage Titan / BM25 / Qdrant-probe /
Qdrant-RRF / merge latency plus actual post and chunk counts.

**Layer 2 (routed answer):** the 60–80 case subset, run once at full scale, exercising Router
V2, decomposition, merge, context cap and citation parity.

The headline comparison is the **202 fixed-ground-truth cases across 25 → 50 → 75 → 100
users**: same questions, same correct answers, four index sizes. The 38 scale-dependent cases
are reported separately and never averaged into that number.

## 7. What must not happen
No architecture change to test the corpus. No prose tuned to the current models. No evaluation
question leaked into generation. No production seeding without separate authorisation. No
regeneration of the deleted 25-user corpus.
