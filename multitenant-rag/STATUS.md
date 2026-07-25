# MultiTenantRAG — Project Status

Last updated: 2026-07-26

---

## Where We Are

**🚀 SHIPPED TO PRODUCTION.** Live at **https://d261g450savmee.cloudfront.net**

A signup-gated, multi-tenant blogging platform with a per-profile RAG chatbot,
running 100% serverless on AWS (ap-south-1). Real users only — no mock data.

---

## What the product does

1. **Sign up / log in** (email + password, JWT). The app is unusable when logged out.
2. **Discover** — browse a directory of everyone's profiles.
3. **Visit a profile** — read their blog posts, and **chat with their AI**, which answers
   *only* from that person's posts (tenant-isolated retrieval). Off-topic → declines;
   vague → asks a clarifying question.
4. **Saved chats** — conversations have memory; keep up to 5, delete → trash → permanent delete.
5. **My blog** — write markdown posts (async chunk + embed + index).

## Live architecture

```
Browser → CloudFront (EOV3277U5A8CF)
  ├─ /*        → S3 praveen-blog-frontend (React/Vite + Tailwind SPA)
  └─ /api/*    → API Gateway (multitenant-api)
                   ├─ POST /api/posts → createpost Lambda → S3 + DynamoDB + SQS
                   └─ $default        → ask Lambda (auth, chats, ask, users, tenants, read-post)
       SQS FIFO (multitenant-ingestion.fifo) → ingest_worker Lambda
                   → chunk → Bedrock Titan (dense) + fastembed BM25 (sparse) → Qdrant Cloud
```

- **LLM:** Groq Llama 3.3 70B (prod) / 8B (dev) — `GROQ_MODEL` env var.
- **Auth:** custom JWT (bcrypt), `multitenant/jwt` secret. Identity from the token, not a header.
- **Relevance:** low retrieval floor (0.15, env-tunable) short-circuits clearly-irrelevant; else the LLM
  judges in one call (answer / clarify / decline). RRF hybrid used for ranking + citations.
- **`ask` is buffered** (LWA `buffered` mode via API Gateway) + a client-side typewriter.
  True edge streaming (LWA `response_stream` + Function URL + OAC) is a future item.

## Environments

| | Prod | Dev |
|---|---|---|
| Where | Real AWS (ap-south-1) | LocalStack `3.8.1` on colima |
| Data | real users only (wiped clean) | seeded mock (5 Tamil-named users, ~20 blogs) |
| LLM | Groq 70B | Groq 8B |
| Run dev | — | `multitenant-rag/local/run_local.sh` (:8080) + `dev_worker.py` + Vite (:5173); `local/bootstrap_dev.sh` seeds |

## Cost
~$1.20/mo idle (3 Secrets Manager secrets); everything else free-tier / pay-per-use.
$5/mo budget alarm (`multitenant-monthly`) → praveen.kr@zohocorp.com.

## Remaining / future
- Google login (OAuth) — auth structured as an add-on.
- True edge streaming for `ask` (LWA response_stream + Function URL OAC + client body-hash).
- Terraform/CDK (AWS learning Stage 6) to codify all this infra.

## Files in this folder
| File | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Design + data models (see the "Shipped state" note at top) |
| `STATUS.md` | This file |
| `CODE-GUIDE.md` | Walkthrough of the code (`lambdas/**`) |
| `MASTER-CONTEXT.md` | Self-contained context to paste into a new session |
| `PHASE-3-PLAN.md` | Original deployment runbook (historical) |
| `CLEANUP-STEPS.md` | Phase 0 old-infra teardown (historical) |
| `lambdas/**` | createpost, ingest_worker, ask (FastAPI+LWA), common/ (auth, chats, posts, context, secrets, logger) |
| `local/**` | LocalStack dev harness (bootstrap, run, worker, seed, test) |
| `scripts/init_qdrant.py` | Qdrant collection initializer |
