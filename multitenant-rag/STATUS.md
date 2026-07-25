# MultiTenantRAG — Project Status

Last updated: 2026-07-25

---

## Where We Are

**Phase 2 complete. Ready for Phase 3 (Deployment).**

All code written. All AWS foundational resources created. All credentials in place.
Next session picks up with Docker builds + Lambda deployment.

---

## Progress by Phase

### ✅ Phase 0 — Cleanup (Complete)

Deleted old blog infrastructure to reduce cost.

Deleted:
- ECS cluster `blog-cluster` + service
- ALB + listener + 2 target groups (saved ~$7/mo public IPv4)
- EC2 instance (terminated)
- RDS `blog-db` + subnet group
- 3 security groups (blog-alb-sg, blog-ecs-sg, blog-backend-sg, blog-rds-sg)
- ECR repo `blog-backend`
- CloudWatch log groups
- 3 IAM roles + instance profile

Preserved:
- CloudFront distribution `EOV3277U5A8CF`
- S3 bucket `praveen-blog-frontend`
- OIDC provider + `github-actions-deploy-role`

### ✅ Phase 1 — Foundation Setup (Complete)

AWS resources created:
- 4 DynamoDB tables: `multitenant-users`, `multitenant-tenants`, `multitenant-posts` (with GSI `by_status`), `multitenant-usage-logs` (with TTL)
- S3 bucket: `praveen-multitenant-content` (private, versioned)
- SQS FIFO queue: `multitenant-ingestion.fifo`
- Secrets Manager: `multitenant/groq`, `multitenant/qdrant` (both with real credentials)

External services:
- Qdrant Cloud cluster (eu-west-2, free tier, 1GB)
- Qdrant collection `multitenant_chunks` initialized with hybrid config:
  - Dense vectors: 1024 dims, cosine
  - Sparse vectors: BM25 with IDF modifier
  - Payload indexes: tenant_id, user_id, post_id
- Groq account with API key
- AWS Bedrock Titan Text Embeddings V2 (verified working — returns 1024-dim vectors)

### ✅ Phase 2 — Backend Code (Complete)

All Lambda code written in `multitenant-rag/lambdas/`:

```
lambdas/
├── common/
│   ├── logger.py            JSON structured logger
│   ├── secrets.py           Cached Secrets Manager access
│   ├── context.py           user_id → tenant_id resolver
│   └── responses.py         HTTP response builders
├── create_post/
│   ├── handler.py           POST /posts → save + queue ingestion
│   ├── requirements.txt
│   └── Dockerfile
├── ingest_worker/
│   ├── handler.py           SQS → chunk + embed + upsert Qdrant
│   ├── chunker.py           Markdown-aware chunker
│   ├── requirements.txt
│   └── Dockerfile
└── ask/
    ├── handler.py           POST /ask (streaming) → hybrid search → Groq stream
    ├── llm.py               Groq streaming client
    ├── requirements.txt
    └── Dockerfile
```

All 11 Python files parse cleanly (syntax verified).

### ⏳ Phase 3 — Deployment (Next Session)

**See `PHASE-3-PLAN.md` for detailed steps.**

At a high level:
- Build 3 Docker images (must use `--platform linux/amd64` for Lambda x86)
- Push to ECR
- Create Lambda functions with correct env vars + IAM roles
- API Gateway HTTP API for POST /posts
- Lambda Function URL (streaming) for POST /ask
- Update CloudFront to route `/api/posts` and `/api/ask`
- Seed 5-10 mock tenants + users
- End-to-end test

### ⏳ Phase 4 — Frontend (After Phase 3)

- Strip blog CRUD from existing React app
- Add profile pages (`/u/{user_id}/`)
- Add chat UI with streaming (`ReadableStream` API)
- Deploy to existing `praveen-blog-frontend` S3 bucket

### ⏳ Phase 5 — Testing + Polish (After Phase 4)

- Load test
- Cost verification
- Tenant isolation tests
- Streaming latency measurement
- Update AWS Budget alerts

---

## Current AWS State

**Resources active:**
- CloudFront distribution (Deployed)
- 2 S3 buckets: `praveen-blog-frontend`, `praveen-blog-uploads` (old), `praveen-multitenant-content`
- 4 DynamoDB tables (all ACTIVE)
- SQS FIFO queue
- 2 Secrets Manager secrets
- Qdrant Cloud cluster (external)

**Estimated monthly cost:** ~$0.80/month (mostly Secrets Manager)

**No compute running yet** (no Lambdas deployed).

---

## Locked Decisions (See ARCHITECTURE.md)

- Groq Llama 3.3 70B for LLM (free tier, streaming)
- Bedrock Titan V2 for embeddings (1024 dims)
- fastembed BM25 for sparse vectors
- Qdrant native hybrid retrieval with RRF fusion
- Structural markdown-aware chunking (500 tokens, 50 overlap)
- Tenant isolation via `user_id` → DynamoDB lookup → `tenant_id` filter
- No global/common content in v1 (private only)
- Streaming via Lambda Function URL (not API Gateway)
- Container image Lambdas (for fastembed dependencies)

---

## Files in This Folder

| File | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Locked v1 architecture — decisions + data models |
| `STATUS.md` | This file — where we are, what's next |
| `PHASE-3-PLAN.md` | Detailed deployment plan for next session |
| `CODE-GUIDE.md` | Explanation of each code file we wrote |
| `CLEANUP-STEPS.md` | Phase 0 runbook (already executed) |
| `scripts/init_qdrant.py` | Qdrant collection initializer (already run) |
| `lambdas/**` | All backend Lambda code |
| `.venv/` | Python dev environment (git-ignored) |

---

## To Resume in Next Session

1. `cd multitenant-rag` in Claude Code
2. Paste this file's content (or reference it) to bring session up to speed
3. Say "Start Phase 3A" 
4. Follow `PHASE-3-PLAN.md`

If in another chat (mobile), paste `MASTER-CONTEXT.md` for full background.
