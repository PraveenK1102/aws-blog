# Production-Readiness & Scale Review — MultiTenantRAG

*Paste this into a fresh Claude Code session opened at the repo root
(`/Users/praveen-16349/Documents/Personal/Learnings/AWS - Blog`). It is self-contained.*

---

## Your role

You are a **senior/staff engineer running an adversarial production-readiness and scale
review** of a live multi-tenant RAG SaaS. Be rigorous, specific, and honest — this is a
learning project, but review it as if it must carry real paying tenants. Do **not** be
reassuring; your job is to find what will break, leak, or bankrupt us before it does.

**Read-only review.** Do not modify code. Produce findings, not fixes.

## The system

A signup-gated, multi-tenant blogging platform with a per-profile RAG chatbot, 100%
serverless on AWS (ap-south-1). Visitors browse an author directory, read posts, and ask an
author's AI, which answers **only from that author's posts** (tenant-isolated retrieval).

**Orient yourself first (read in this order), then verify everything against the actual code —
docs may lag the code:**
- `multitenant-rag/MASTER-CONTEXT.md` — self-contained overview + resource names.
- `multitenant-rag/ARCHITECTURE.md` — data models (has a "SHIPPED-STATE NOTE" at top with diffs).
- `multitenant-rag/CODE-GUIDE.md` — code walkthrough (has a "2026-07-26 update" note; earlier
  sections describe the ORIGINAL design and are stale — trust the code).
- `multitenant-rag/STATUS.md` — current live state.
- Code: `multitenant-rag/lambdas/**` (`ask/app.py`, `ask/llm.py`, `ingest_worker/`, `create_post/`,
  `common/*`), frontend `blog-frontend/src/App.jsx` + `src/api.js`.
- Infra-as-reality: there is **no Terraform** — infra lives in the AWS account. CI is
  `.github/workflows/build-lambdas.yml` (builds 3 images; deploy is a manual `update-function-code`).

## Target scale — review against THIS bar

- **1,000,000 users**, **20,000,000 posts** (~20 posts/user **on average, heavily skewed** — assume
  some tenants have 10K–100K posts and are "hot", while most have <10).
- Derive the implications yourself and check the system against them, e.g.:
  - Posts × chunks/post (~5–15) ⇒ **~100M–300M vectors** in the vector store.
  - Even 0.1–1% concurrently-active users ⇒ **thousands of concurrent `ask` requests**.
  - Ingestion of bursts of posts ⇒ sustained embedding + upsert throughput.
- For every subsystem, answer: *does the current design hold at this scale, and if not, exactly
  where and how does it fall over (throttle, hot-partition, cost, latency, quota, correctness)?*

## Scope

**In scope:** correctness bugs; security & tenant-isolation flaws; scale bottlenecks (hot
partitions, low-cardinality GSIs, unbounded growth, N+1 calls, serialization ceilings); reliability
gaps (retries, DLQ, idempotency, partial failures, swallowed errors); cost blowups at target
scale; data-lifecycle/consistency (edit/delete/orphaned data, GDPR erase); auth hardening;
observability/ops gaps; best-practice violations that bite at scale.

**Out of scope:** net-new product features; UI/visual polish; and simply *restating* the known
deferred items below — only raise them if they concretely break at the target scale.

## Known deliberate tradeoffs — JUDGE whether each survives at 1M/20M (don't just restate)

- `ask` is **buffered** (LWA buffered via API Gateway), not true edge streaming.
- **Groq** (free tier, rate-limited) for the LLM; **Bedrock Titan V2** for embeddings (one text/call).
- **Qdrant Cloud free tier (1GB)** as the vector store; **no reranker**.
- **No email verification**; Google login deferred; single-user-per-tenant.
- **LocalStack dev ignores IAM** (so IAM gaps only surface in prod).
- Secrets cached per warm container (stale on rotation).

## Areas to scrutinize (leads, not conclusions — verify each in code and prove it)

1. **Tenant isolation & security.** Is `tenant_id` ever trusted from the client? Trace it from JWT
   (`common/context.py`, `common/auth.py`) → Qdrant filter in `ask/app.py` `_hybrid_search`. Is the
   Qdrant filter a **pre-filter** on every path (retrieval, semantic-cache lookup, cache
   invalidation)? Can one tenant read another's posts (`get_post`), chats, or **cached answers**?
   JWT secret handling, expiry, algorithm pinning, token revocation.
2. **DynamoDB modeling at scale.** `posts` PK=`tenant_id` (hot partition for large tenants?);
   the **`by_status` GSI** PK=`ingestion_status` — how many distinct values? (low-cardinality GSI PK
   ⇒ hot GSI partitions/throttling at scale). `chats`: `list_chats` Query on `user_id` + in-memory
   filter, and `create_chat`/`set_status` recount by listing then filtering — cost per op and
   unbounded chats-per-user across many profiles. Any **Scans** or FilterExpressions that grow with
   data. `messages` stored as one JSON blob (400KB item cap).
3. **Vector store at scale.** ~100M–300M vectors vs Qdrant free-tier 1GB — quantify the gap. Single
   collection for all tenants; payload-index/filter performance with a huge multi-tenant collection;
   HNSW memory; per-tenant deletes. Is a single shared collection the right multi-tenant shape at
   this scale (vs per-tenant collections / sharding / a managed alternative)?
4. **Semantic cache** (`common/semcache.py`). Is the cache **tenant-scoped on read AND write**
   (poisoning/leak risk)? TTL is enforced **app-side at read time** — stale points accumulate
   forever (unbounded growth, no server expiry). Invalidation is a **full per-tenant delete** on
   every new post — cost and correctness at high write rates. Cosine ≥0.95 threshold — false-hit
   risk. Does it cache/return anything user-specific?
5. **Empty-retrieval / overview path & prompt injection.** In `ask/app.py`, when nothing clears the
   floor, a **small-model** (`GROQ_MODEL_SMALL`) call gets the "profile card" = author's **post
   titles** (`_tenant_post_titles`, `_build_profile_prompt`). Post titles are **user-controlled** —
   assess prompt-injection / jailbreak via a malicious title. Decline reliability on 8B (it must emit
   an exact line). Extra LLM call cost/latency at scale.
6. **Concurrency, throughput, cold starts, backpressure.** Lambda reserved/unreserved concurrency vs
   thousands of concurrent asks (account limit, 70B answers + 8B decisions + Bedrock embeds per
   request). `fastembed` BM25 cold-start (~2–3s) on the ask path. **SQS FIFO with
   MessageGroupId=tenant_id serializes ingestion per tenant** — throughput ceiling for a hot tenant;
   ingest_worker concurrency; Titan **one-embed-per-call** (N calls/post). Backpressure/queue depth.
7. **Reliability & failure handling.** SQS **DLQ**? Redrive? Idempotency of ingest (delete-then-
   insert) under retries/duplicates. `create_post` partial-failure path (S3 ok, DDB/SQS fail).
   **Swallowed errors** (best-effort try/except that hide failures — e.g. usage logging, semcache,
   get_post). What happens on Bedrock/Groq/Qdrant outage or throttle mid-request?
8. **Auth hardening.** No email verification (abuse/spam signups at scale). Login/signup **rate
   limiting**? Password policy, bcrypt cost. JWT expiry/rotation/revocation. Enumeration via
   login/signup error differences.
9. **Cost model at target scale.** Build a back-of-envelope monthly cost at 1M/20M: Groq (rate
   limits/paid tier), Bedrock embeds (20M posts × chunks), Lambda GB-s + concurrency, DynamoDB
   RCU/WCU + storage, Qdrant tier, CloudFront, S3, CloudWatch. Where does the "~$1–3/mo" model break
   and by how much?
10. **Data lifecycle & consistency.** Post **edit** (chunk churn) and **delete** — are old chunks +
    S3 objects + cache entries cleaned up, or orphaned? Is there even a delete-post path? **GDPR /
    delete-a-user** (erase across DynamoDB, S3, Qdrant, chats, cache)? `ingestion_status="failed"`
    handling / stuck-pending recovery.
11. **Frontend.** `renderMarkdown` in `App.jsx` — **XSS** via user post content? (React escapes text,
    but verify no `dangerouslySetInnerHTML`/raw HTML path.) CloudFront **SPA 403/404→index.html**
    masks real API errors (this already caused a silent 404-instead-of-500 bug). Token storage
    (localStorage) and its risks. Client trusts nothing from server it shouldn't.
12. **Observability & ops.** Structured logs exist — but are there **metrics, alarms, tracing**
    beyond a $5 budget alarm? Can you detect throttling, error spikes, DLQ growth, cache hit-rate,
    p99 latency, per-tenant cost? Deploy is a manual `update-function-code` by SHA with no
    rollback/canary — assess.

## How to work

- **Verify every finding in the actual code**; cite `path:line`. No hallucinated APIs, files, or line
  numbers — if you can't point to it, don't claim it.
- Give each finding a **concrete failure scenario**: specific input/state/scale → what breaks
  (throttle, leak, wrong answer, crash, $ blowup), not a vague "could be an issue."
- **Classify** each: severity (Critical / High / Medium / Low) and type (security · correctness ·
  scale · reliability · cost · data-integrity · observability · best-practice).
- Separate **"broken now"** from **"won't scale to 1M/20M"** from **"best-practice gap."**
- Prefer depth over breadth on the highest-severity items; prove them.

## Output format

1. **Executive summary** (≤10 lines): overall production-readiness verdict for the target scale.
2. **Dimension scorecard** — a table rating each area (security, tenant-isolation, DynamoDB/scale,
   vector-store/scale, reliability, cost, data-lifecycle, auth, observability, frontend) Red/Amber/Green
   with one-line justification.
3. **Findings** — prioritized Critical→Low. For each: `ID · Title · Severity · Type · Location
   (file:line) · Failure scenario · Impact at scale · Recommended fix · Rough effort (S/M/L)`.
4. **Top 5 must-fix before scaling** — the ordered shortlist.
5. **Quick wins** (optional) — high-value, low-effort fixes.

## Optional: run it as a multi-agent workflow

If you have multi-agent orchestration available, consider fanning out one reviewer per dimension
(security, DynamoDB-scale, vector-store, reliability, cost, data-lifecycle, frontend), then
**adversarially verify** each finding with an independent skeptic before it makes the report
(kill findings that can't be proven in code). Otherwise, do it single-threaded but with the same
verify-before-report discipline.
