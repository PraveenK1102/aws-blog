# Curated 25-User / 268-Post Corpus — Production Rollback

**Status: COMPLETE. Corpus fully removed from production.**
**Date:** 2026-08-26

## 1. Objective
Exact data rollback of one seeding operation — the curated 25-user / 268-post synthetic
demo corpus. Not a general cleanup: deletion was driven solely by manifest identity, never
by count, email pattern, creation date, or "not on a keep list".

## 2. Source corpus fingerprint
```
source SHA-256:    6e2f76b74c0c598f884d0185d6cc426efaa2450576bba26e8d02402741cd6f9b
ingest-content SHA: ed8ecb6680d8e655c6eb00a18e6cf384f4070fa75700219d9712ff27d16c1f1b
```
Both matched the values recorded in the task; the tool refuses to run on a mismatch.

## 3. Manifest identity
`multitenant-rag/tools/corpus_25_manifest.json` — 25 personas (each `<username>@example.com`)
and 268 posts with per-post `body_sha256`. Content hashes, not email patterns, decided
ownership: each live post's DynamoDB `content_hash` had to be in that persona's manifest set.

## 4. Pre-delete production inventory (read-only)
```
users 30 · tenants 30 · posts 271
username/email reservation rows: 0   (the UI/profile work is NOT deployed — verified, not assumed)
```

## 5. Exact target users — reconciliation PASS
All 25 personas matched exactly: correct email → `user_id` → `tenant_id`, expected post
count, every live content hash present in the manifest.

**Zero identity drift. Zero extra posts. Zero missing posts.** No `DECISION REQUIRED` was
triggered, and no account had been used beyond the seed.

Per-user counts confirmed live before deletion: 15 × 10, `vishnu.priyan25` 18,
`priyadharshini.m25` 28, `swathi.raj25` 10, 5 noise users × 6, `aravind.k25` 16,
`abinaya.raj25` 16 — **268 total**.

## 6. Exact target posts
268 posts, addressed by exact `(tenant_id, post_id)`, with S3 keys taken from each record's
stored `s3_key`.

## 7. Dry run
```
deletable corpus identities: 25      target posts: 268
retained accounts excluded:    PASS
non-manifest users excluded:   PASS
non-manifest posts excluded:   PASS
```

## 8. Qdrant cleanup
Deleted by a filter requiring **both** `tenant_id` = that corpus tenant **and** `post_id` ∈
the exact post-id list — never a collection-wide delete. Each user's deletion verified to
zero before moving on.

```
collection points before: 274
corpus points deleted:    268
corpus points remaining:  0 (by post_id) / 0 (by tenant_id)
collection points after:  6   (all belonging to retained accounts)
```

## 9. Semantic cache invalidation
`semcache.invalidate_tenant(tid)` per deleted tenant — **25 tenant-scoped invalidations, no
global flush**, so retained tenants' caches were untouched.

## 10. S3 cleanup
268 objects deleted by **exact recorded key**. No prefix deletion and no `delete_objects`
batch — the tool contains neither (asserted by test). Corpus prefixes now return 0 keys.

## 11. DynamoDB cleanup
268 post rows deleted by exact composite key; 25 user rows; 25 tenant rows. A tenant row was
only deleted after proving no posts remained under it.

## 12. User / tenant cleanup
Each account was **deactivated first** (`active=false`) so nothing could be written
mid-delete, then removed at the end of its own sequence.

## 13. Relationship cleanup — measured, not assumed
```
follows deleted:            0
group memberships deleted:  0
saved chats deleted:        0
usage log rows deleted:     0
```
The corpus personas were only ever written to; they never followed anyone, joined a group,
saved a chat, or issued a query, so there were no edges to remove. **No group was deleted** —
only membership edges were ever in scope, and there were none. Usage rows are keyed
`tenant_id#date` and already carry a 30-day TTL; ownership is provable from the key prefix so
they were in scope, but none existed.

## 14. Partial-failure / resume behaviour
Progress is recorded by stable username in `output/corpus_rollback_state.json` after every
user. A failure **stops the run** and reports the last completed user and the partial one; it
never continues into the next account. A rerun skips completed users, treats already-absent
resources as success, and can never broaden the deletion set. In this run no failure
occurred — 25/25 completed in order.

## 15. Retained accounts — untouched
| Account | Status | Posts |
|---|---|---|
| `pk@gmail.com` | PRESENT | 0 |
| `pk1@gmail.com` | PRESENT | 1 |
| `snehasattai@gmail.com` | PRESENT | 0 |
| `naresh_nagarjuna@gmail.com` | PRESENT | 1 |
| `realuser@example.com` | PRESENT | 1 |

5/5 present, 3/3 posts intact, and no retained `user_id`/`tenant_id` appears anywhere in the
deletion set. They are hard-blocked in code: `is_deletable_email` rejects them **before**
consulting the manifest, so even a forged manifest containing one cannot delete it (tested).

## 16. Post-delete inventory (measured, not assumed)
```
users   5
tenants 5
posts   3
Qdrant collection points 6
S3 post objects 3
```
No new legitimate data had appeared, so the measured totals match the expectation. The older
`seed-20260822` population remains absent.

## 17. Retrieval-residue checks (§29)
LLM-free: the entire collection was scrolled and payloads inspected directly, so **no
embedding call was made and no Titan usage incurred**.

```
points backed by a deleted corpus post_id: 0
points under a deleted corpus tenant_id:   0
```
Distinctive corpus phrases — `velvet bicycle`, `Dhanushkodi`, `1964 cyclone`,
`shared expense and team-budget tracker`, `visual-defect detector`, `Rayar's Mess`,
`idempotency keys`, `Moonseed` — return **0 matches backed by a deleted corpus post**.
All 6 surviving points belong to retained accounts.

## 18. AWS mutations
Data-plane only: 268 Qdrant point deletions (scoped), 25 semantic-cache invalidations,
268 S3 object deletions, 268 + 25 + 25 DynamoDB row deletions, 25 `active=false` updates.

**No infrastructure change:** no Lambda code/config, API Gateway, CloudFront, SQS, queue
concurrency, DLQ, table schema/index, IAM, Secrets Manager, ECR image, Router V2, LangGraph,
Groq/Titan configuration, Qdrant collection schema or LangSmith. The new UI/profile work was
neither modified nor deployed.

## 19. Security
No credential was requested, printed, logged or stored. No password hash, JWT, API key or
secret appears in any plan, report or audit output.

## 20. Final status
**CURATED 25-USER / 268-POST CORPUS REMOVED FROM PRODUCTION.**

The corpus remains available as a reproducible synthetic **test fixture**: the manifest,
parser, seed utility and rollback tool are all retained. It is no longer production content.
