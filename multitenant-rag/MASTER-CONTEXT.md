# MultiTenantRAG — Master Context

For pasting into a new Claude Code session or Claude chat.

Self-contained. If you paste this + `PHASE-3-PLAN.md`, a new session has
everything needed to continue.

---

## Who I Am

Praveen, developer at Zoho (Zoho Books division). Learning AWS + GenAI to
target GenAI developer roles at product companies. Zero cloud experience
before this project. I ask "why" a lot; want to understand, not just copy
commands. Be honest, critique my logic, push back when I'm wrong.

---

## Project: MultiTenantRAG

Multi-tenant RAG chatbot deployed on AWS. 5-10 mock tenants across
different domains (doctor, chef, coder, ...). Each user has a profile
page; visitors chat with an AI that knows only that user's content.

Interview positioning: depth over scale. Complex retrieval + isolation +
streaming, not "scales to a million users".

---

## Locked Architecture (v1)

### Compute
- Lambda container images for 3 functions (createPost, ingestWorker, ask)
- API Gateway HTTP API for POST /posts (synchronous)
- Lambda Function URL with response streaming for POST /ask
- CloudFront in front (existing distribution EOV3277U5A8CF)

### Storage
- DynamoDB: 4 tables (users, tenants, posts, usage-logs)
- S3: praveen-multitenant-content (raw post markdown)
- Qdrant Cloud: vectors (free tier, eu-west-2)
- Secrets Manager: Groq + Qdrant API keys

### Models
- LLM: Groq Llama 3.3 70B (free tier, streaming)
- Embeddings: Bedrock Titan Text V2 (1024 dims)
- Sparse: fastembed BM25 (in-process)

### Retrieval
- Hybrid dense + sparse via Qdrant RRF fusion
- Pre-filter by tenant_id (server-side lookup from user_id)
- Top-K 5, score threshold 0.3

### Chunking
- Markdown-aware structural (500 tokens, 50 overlap)
- Header hierarchy prepended to each chunk

### Auth (v1)
- No real auth. X-User-Id header from URL context.
- users table maps user_id → tenant_id
- Never trust tenant_id from client
- v1.5: swap to Cognito JWT (only common/context.py changes)

### Domain Handling
- tenant.domain is a hint string in system prompt (not a filter)
- No hardcoded DOMAIN_PROMPTS dict
- Vector search naturally handles multi-domain users

### Scope (Locked — Not in v1)
- No global/common shared content
- No PDF/image/table/SVG ingestion
- No OCR
- No query rewriting
- No HyDE
- No cross-encoder reranker
- No semantic cache
- No multi-user per tenant
- No Cognito auth

---

## Current AWS State (as of 2026-07-25)

**Deleted (Phase 0):**
- Old blog ECS, ALB, EC2, RDS, security groups, ECR blog-backend, IAM roles

**Created (Phase 1):**
- DynamoDB: multitenant-users, multitenant-tenants, multitenant-posts (GSI by_status), multitenant-usage-logs (TTL on expires_at)
- S3: praveen-multitenant-content (private, versioned)
- SQS: multitenant-ingestion.fifo (content-based dedup)
- Secrets Manager: multitenant/groq, multitenant/qdrant (both with real credentials)
- Qdrant Cloud cluster + `multitenant_chunks` collection (hybrid config)

**Preserved:**
- CloudFront distribution EOV3277U5A8CF
- S3 bucket praveen-blog-frontend
- OIDC provider + github-actions-deploy-role
- IAM user praveen-admin

**Verified working:**
- Bedrock Titan V2 (returns 1024-dim embeddings, ~5ms per call)
- Qdrant collection (green status, hybrid config with sparse+dense)

**Estimated current cost:** ~$0.80/month (Secrets Manager only)

Account: 557690605487
Region: ap-south-1

---

## What's Written (Phase 2 Complete)

All Lambda code in `multitenant-rag/lambdas/`:

```
lambdas/
├── common/
│   ├── logger.py            JSON structured logs
│   ├── secrets.py           Cached Secrets Manager
│   ├── context.py           user_id → tenant_id resolver
│   └── responses.py         HTTP response builders
├── create_post/
│   ├── handler.py           POST /posts → S3 + DDB + SQS
│   ├── requirements.txt
│   └── Dockerfile
├── ingest_worker/
│   ├── handler.py           SQS → chunk + embed + Qdrant
│   ├── chunker.py           Markdown-aware chunker
│   ├── requirements.txt
│   └── Dockerfile
└── ask/
    ├── handler.py           POST /ask streaming → Groq
    ├── llm.py               Groq streaming client
    ├── requirements.txt
    └── Dockerfile
```

All 11 files pass Python syntax check.

See `CODE-GUIDE.md` for detailed explanation of each file.

---

## What's Next (Phase 3 Deployment)

**Detailed plan:** `PHASE-3-PLAN.md`

**High-level:**
1. Create ECR repos + IAM roles (~30 min)
2. Docker build for x86 (may hit ARM/buildx issues) + push to ECR
3. Create Lambda functions (env vars + IAM)
4. Set up SQS trigger for ingestWorker
5. API Gateway HTTP API for POST /posts
6. Lambda Function URL for POST /ask (streaming)
7. Update CloudFront routing (/api/posts, /api/ask, /*)
8. Seed 5-10 mock tenants + users
9. End-to-end test (create post, wait for ingestion, ask questions)
10. Verify tenant isolation

**Estimated time:** 4-8 hours split across 2-3 sessions.

---

## Interview Talking Points (for later)

Lead with these when asked about the project:

1. **Tenant isolation:** server-side user_id → tenant_id lookup, pre-filtered ANN in Qdrant, never trust client.
2. **Hybrid search:** dense (Titan V2) + sparse (BM25 via fastembed) with RRF fusion, single Qdrant query.
3. **Multi-domain users:** vector search handles it naturally, no per-domain prompt library needed.
4. **Streaming:** Lambda Function URL RESPONSE_STREAM, NDJSON events, progressive rendering in browser.
5. **Chunking:** markdown-aware structural, not fixed-length, respects author's intent.
6. **Cost:** designed for <$3/mo running, Groq free tier + Qdrant free tier + AWS free tier eligibility.
7. **What I excluded from v1:** reranker, semantic cache, PDF, OCR, Cognito — all v1.5+ features. Minimum viable slice first.

---

## Files in This Folder (Reference)

| File | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Locked v1 architecture — decisions + data models |
| `STATUS.md` | Current progress + immediate next steps |
| `PHASE-3-PLAN.md` | Detailed deployment plan for Phase 3 |
| `CODE-GUIDE.md` | Explanation of every code file |
| `MASTER-CONTEXT.md` | This file — full context for new sessions |
| `CLEANUP-STEPS.md` | Phase 0 runbook (already executed) |
| `scripts/init_qdrant.py` | Qdrant collection initializer (already run) |
| `lambdas/**` | All backend Lambda code (Phase 2 complete) |

---

## How to Resume in a New Session

**In Claude Code:**
1. `cd multitenant-rag`
2. Say: "Continue MultiTenantRAG project — read MASTER-CONTEXT.md and STATUS.md"
3. Then say: "Start Phase 3A" — follow PHASE-3-PLAN.md

**In Claude chat (mobile/other):**
1. Paste this MASTER-CONTEXT.md
2. Ask questions or plan next steps

**Session model:**
- Do Phase 3 in 3 mini-sessions (A, B, C) — don't try in one go
- Each session bounded by clear stop criteria in PHASE-3-PLAN.md
- Fresh session = fresh debugging capacity when bugs appear
