# Curated Corpus — Ingestion + Legacy Cleanup Audit

**Status: PHASE A PASS · PHASE B COMPLETE**
**Date:** 2026-08-26

> **Data classification.** These 25 accounts are **curated seeded demo users**
> (the *curated 25-user corpus*). They are realistic synthetic personas for testing —
> **not** real users, customers, or organically acquired traffic. No "fake"/"test"
> label was added to their public profile fields.

## 1. Source + fingerprint
```
file:              friends_realistic_blog_corpus_25_users_268_posts.md
source SHA-256:    6e2f76b74c0c598f884d0185d6cc426efaa2450576bba26e8d02402741cd6f9b
ingest-region SHA: ed8ecb6680d8e655c6eb00a18e6cf384f4070fa75700219d9712ff27d16c1f1b
```
The task named `...(1).md`; only the un-suffixed file exists — same corpus, browser
duplicate-naming aside. The source file was **not modified**.

**§1 boundary:** 145,159 of 147,090 chars ingested; 1,931 excluded. `Research Basis`,
`Short Claude Ingestion Prompt` and the trailing prompt block are provably absent from
the ingest region. The parser initially **refused to run** because `BEGIN_INGEST` appears
twice — the second is a quoted mention inside a fenced block *after* the real `END_INGEST`,
so first-BEGIN/first-END is unambiguous. A second BEGIN *inside* the region is still rejected.

## 2. Architect decisions applied
| # | Decision | Implementation |
|---|---|---|
| 1 | Identity | `email = <exact-corpus-username>@example.com`; corpus username preserved verbatim in the email local part, the manifest and this audit. **No username schema field added** — deferred to the profile/UI task. Age/origin/gender/content-type remain corpus metadata only; no profile fields were added for seed fidelity. |
| 1A | Passwords | `HMAC-SHA256(master_secret, "curated-corpus-v1:" + username)`, derived **in memory only**. The master secret came from a hidden prompt via env — never printed, logged, persisted, committed, or placed on a command line. No password appears in any artifact. |
| 2 | Dates | Corpus `YYYY-MM-DD` → `created_at` at **00:00:00 UTC**, applied as a narrow conditional DynamoDB patch pinned to `tenant_id` + `post_id` + `content_hash`. |
| 2 | Tags | Exact ordered list stored as an optional DynamoDB **list attribute**. No key/GSI/LSI/IAM/API-Gateway/Qdrant schema change. |
| 2 | Body | S3 Markdown is the **exact corpus body** — no Date line, Tags line, metadata or seed marker injected. Verified by SHA-256 on 268/268 objects. |
| 3 | Cleanup | `tools/cleanup_legacy_seed.py`, dry-run default, `--apply` required, exact allowlist from `SEED-MANIFEST.json`. No "delete everything not in corpus" rule exists. |
| 4 | Unknown accounts | All five untouched, and hard-blocked from the delete set in code. |

## 3. Preflight findings
Architecture confirmed as expected (CreatePost → S3 + DynamoDB → SQS FIFO → ingest worker →
chunk → Titan → BM25 → Qdrant → `indexed`). Four gaps found and handled:
1. **No `username` field** — users are keyed by email (Decision 1).
2. **No `tags` field** — added as an optional attribute (Decision 2).
3. **`created_at` is stamped at ingest time** — patched to the corpus date (Decision 2).
4. **No post/user deletion path exists anywhere** — only chats/follows/group-members have
   deletes, so Phase B required the purpose-built tool with a documented order (Decision 3).

The per-tenant `content_hash` dedup was verified harmless: 84 duplicate bodies exist but
**all are across different users**, so 0 posts were silently dropped.

## 4-5. Ingestion
Users created **25**, reused 0, conflicts **0**, failures **0**.
Posts created **268**, reused 0, conflicts **0**, failures **0**, metadata patched **268**.

## 6. Async indexing
Indexed **268/268**, pending 0, failed 0. Queue fully drained (0 visible / 0 in-flight).

**Titan throttling occurred and self-recovered.** 8 `ThrottlingException` events on
`InvokeModel`; affected messages returned to the FIFO queue and succeeded on retry
(`VisibilityTimeout` 300 s). Only 3 tenants were briefly blocked. **0 permanent failures,
0 poison messages.** No queue configuration was changed. `RedrivePolicy` is still **null** —
the ingestion DLQ remains the top open P0 and is a separate task.

## 7. Per-user verification
| User | Expected | Actual | Indexed | Status | Failures / Retries |
|---|---:|---:|---:|---|---|
| kavin.raj25 | 10 | 10 | 10 | OK | 0 |
| vignesh.k25 | 10 | 10 | 10 | OK | 0 |
| gokul.krishnan25 | 10 | 10 | 10 | OK | 0 |
| janani.raman25 | 10 | 10 | 10 | OK | 0 |
| aishwarya.selvam25 | 10 | 10 | 10 | OK | 0 |
| naveen.kumar25 | 10 | 10 | 10 | OK | 0 |
| divya.rajan25 | 10 | 10 | 10 | OK | 0 |
| nithin.k25 | 10 | 10 | 10 | OK | 0 |
| karthik.raj25 | 10 | 10 | 10 | OK | 0 |
| saikiran.reddy25 | 10 | 10 | 10 | OK | 0 |
| nandhini.k25 | 10 | 10 | 10 | OK | 0 |
| pavithra.selvan25 | 10 | 10 | 10 | OK | 0 |
| madhan.kumar25 | 10 | 10 | 10 | OK | 0 |
| deepika.chandran25 | 10 | 10 | 10 | OK | 0 |
| anusha.reddy25 | 10 | 10 | 10 | OK | 0 |
| vishnu.priyan25 | 18 | 18 | 18 | OK | 0 |
| priyadharshini.m25 | 28 | 28 | 28 | OK | 0 |
| swathi.raj25 | 10 | 10 | 10 | OK | 0 |
| ashwin.raj25 | 6 | 6 | 6 | OK | 0 |
| dinesh.k25 | 6 | 6 | 6 | OK | 0 |
| yogesh.k25 | 6 | 6 | 6 | OK | 0 |
| meena.lakshmi25 | 6 | 6 | 6 | OK | 0 |
| dharani.vel25 | 6 | 6 | 6 | OK | 0 |
| aravind.k25 | 16 | 16 | 16 | OK | 0 |
| abinaya.raj25 | 16 | 16 | 16 | OK | 0 |

Totals — expected **268**, actual **268**, indexed **268**,
`created_at` matches corpus date **268/268**, tags exact **268/268**,
S3 body hash matches **268/268**, chunks **268**.

## 8. Functional smoke (Titan + Qdrant only; **Groq 0**)
| # | Query | Expected | Actual | Result |
|---|---|---|---|---|
| 1 | shared expense / team-budget tracker | Kavin Raj | `kavin.raj25` (top-3 all) | PASS |
| 2 | visual defect detector | Karthik Raj | `karthik.raj25` (top-3 all) | PASS |
| 3 | support assistant on internal docs | Nandhini Kumar | `nandhini.k25` (top-3 all) | PASS |
| 4 | Dhanushkodi / 1964 cyclone | Vishnu Priyan | `vishnu.priyan25` @ 0.472 | PASS |
| 5 | Rayar's Mess / Mylapore | Priyadharshini M | `priyadharshini.m25` (top-3 all) | PASS |
| 6 | idempotency keys / duplicate side effects | Aravind Kumar | `aravind.k25` @ 0.505 | PASS |
| 7 | exact phrase "velvet bicycle" | noise/test posts | `ashwin.raj25` "Random search phrase" | PASS |

**7/7 via the production hybrid path.** Check 7 initially failed when run **dense-only** —
that was the wrong instrument for an exact rare token, not an indexing fault. Re-run with
production hybrid RRF (dense + BM25 sparse) it returns the intended noise post; `"number 731"`
returns 5/5 noise users. This is a search/index availability check, **not** a RAG evaluation.

## 9. Cleanup dry-run
| Classification | Users | Posts | Action |
|---|---:|---:|---|
| PROTECT_CORPUS | 25 | 268 | kept |
| PROTECT_OWNER_ADMIN | 0 | 0 | not determined by design |
| KEEP_OTHER_TEST | 0 | 0 | kept |
| LEGACY_MOCK_CONFIRMED | 6 | 50 | deleted |
| UNKNOWN_REVIEW | 5 | 3 | untouched |

Legacy evidence was positive, never inferred: exact `user_id` **and** `tenant_id` in
`SEED-MANIFEST.json` (`seed_prefix: seed-20260822`), a literal `(seed-20260822)` display-name
suffix, and sequential `testuserpkN@gmail.com` identities. Revalidated immediately before
deletion: 6/6 users, 50/50 posts, **zero identity drift**.

## 10. Cleanup applied
Deleted: **6 users**, **6 tenants**, **50 post rows**, **50 S3 objects**, **12 group
memberships**, all legacy Qdrant points. Follows 0, chats 0. **Errors: 0.**

Order executed (DECISION 3A): revalidate → deactivate user → Qdrant points → semantic-cache
invalidation → S3 objects → DynamoDB post rows → relational records → user → tenant → verify.
Retrieval residue is removed *before* metadata, so a deleted post is never answerable while
its row is gone; the tenant row is deleted only after proving no posts remain.

Deleted personas (display names): Arjun Vale, Dev Iyer, Kian Rao, Mira Sen, Nila Roy, Tara Moss.
No password or token is recorded anywhere.

## 11. Post-cleanup verification
```
protected corpus users present:  25/25
protected corpus posts:         268/268   indexed 268/268
corpus Qdrant points:           268/268
legacy users / tenants / posts:   0 / 0 / 0
legacy Qdrant points:             0
legacy S3 objects:                0
UNKNOWN_REVIEW accounts intact:   5/5
Qdrant collection total:        274  (2,327 before cleanup)
```
No protected username appears in the deletion log.

## 12. Provider usage
Titan: 268 ingestion embeddings + 10 smoke queries. Groq **0**. NVIDIA **0**.
RAGAS **0**. DeepEval **0**. No paid tier, no quota circumvention.

## 13. AWS mutations
25 signups, 268 post creations, 268 narrow metadata patches (all via the supported path or a
pinned conditional update); legacy deletion as itemised in §10. No change to queue config,
Lambda config, API Gateway, CloudFront, IAM, Secrets Manager, or any table key/index.

## 14. Security
No credential was requested by me, printed, logged, stored, committed, or written to any
manifest/report/archive. Derived passwords existed only in process memory.

## 15. Production data status
**CURATED 25-USER / 268-POST SEEDED CORPUS ACTIVE.**
This is synthetic demo content — **not** verified real-user traffic.

Final population: **30 users / 30 tenants / 271 posts** = 25 curated corpus personas
(268 posts) + 5 retained UNKNOWN_REVIEW accounts (3 posts). The system does **not** contain
only 25 users.
