# MultiTenantRAG — Master Context

For pasting into a new Claude Code session or Claude chat. Self-contained.
**Status: SHIPPED TO PRODUCTION (2026-07-26).** Live: https://d261g450savmee.cloudfront.net

---

## Who I am
Praveen, developer at Zoho, learning AWS + GenAI at production grade to move into senior
roles needing 30–40% AWS. Learning-mode: teach the concept while/ before implementing;
solid over fast (timelines are upper bounds, don't cut scope). Free-tier through 2027-05-31.

## What this project is
A **signup-gated, multi-tenant blogging platform with a per-profile RAG chatbot**, 100%
serverless on AWS (ap-south-1). You browse a directory of people, visit a profile, read
their blog, and chat with an AI that answers **only from that person's posts**. It's the
cloud sibling of the on-prem `ticket-manager` RAG.

## The product (as shipped)
- **Auth gate** — email+password (JWT, bcrypt). Nothing works logged out. Google login = future.
- **UI** — Medium/Substack-style blog ("Inkwell"): author directory → author page (post feed +
  serif article reader) → **floating "Ask their AI" widget** (bottom-right of every profile).
- **Ask** — tenant-isolated hybrid retrieval (dense+sparse, RRF) → Groq 70B. Relevance is
  LLM-as-judge in ONE call: answer / clarify (push back on vague) / decline. Dense cosine floor
  0.15 (env `RETRIEVAL_FLOOR`) skips the LLM for clearly-irrelevant queries.
- **Empty-retrieval handling** — if nothing clears the floor: 0 posts → honest "hasn't published
  anything yet" (no LLM); has posts → ONE LLM call on the **profile card** (name+domain+titles)
  decides overview-vs-decline ("who is he" → synthesized overview; off-topic → decline).
- **Model tiering** — that easy decision runs on 8B (`GROQ_MODEL_SMALL`); real answers stay 70B.
- **Semantic cache** — per-tenant Qdrant cache of clean single-turn answers (cosine ≥0.95, 24h
  TTL, invalidate-on-write); ~3-4× faster on hits. Zero-config (self-creates its collection).
- **Saved chats** — conversation memory; **up to 5 PER profile** (not global); delete → trash → permanent delete.
- **My blog** — write markdown → async chunk + Bedrock Titan embed + Qdrant.

## Architecture
```
CloudFront (EOV3277U5A8CF)
  /*     → S3 praveen-blog-frontend (React/Vite/Tailwind)
  /api/* → API Gateway (multitenant-api / pdp1o70aug)
             POST /api/posts → createpost Lambda → S3 + DynamoDB + SQS FIFO
             $default        → ask Lambda (auth, chats, ask, users, tenants, read-post) [FastAPI + LWA, buffered]
  SQS FIFO → ingest_worker Lambda → chunk → Titan(dense)+BM25(sparse) → Qdrant Cloud
```
- **3 Lambda container images** (createpost, ingestworker, ask). ECR + GitHub Actions (OIDC, x86).
- **DynamoDB**: users (+by_email GSI), tenants, posts (GSI by_status), usage-logs (TTL), chats.
- **Secrets Manager**: multitenant/groq, /qdrant, /jwt. **Bedrock** Titan v2. **Groq** 70B answers +
  8B (`GROQ_MODEL_SMALL`) for the empty-retrieval decision (prod); all-8B in dev.
- **Qdrant Cloud** collections: `multitenant_chunks` (post chunks) + `multitenant_query_cache`
  (semantic answer cache, self-created). Dev suffix `_dev`.
- Account 557690605487, region ap-south-1. Repo github.com/PraveenK1102/aws-blog (subdir multitenant-rag/, frontend blog-frontend/). Branch: main (feat/auth-dev-env merged).

## Dev environment (for iterating without touching prod)
LocalStack `3.8.1` on colima (SQS+DynamoDB+S3, isolated; pin 3.8.1 — newer needs a paid token).
Bedrock/Secrets/Qdrant are still real AWS/cloud (LocalStack can't emulate Bedrock).
```
colima start                       # runtime
docker run -d --name localstack -p 4566:4566 -e SERVICES=sqs,dynamodb,s3 localstack/localstack:3.8.1
multitenant-rag/local/bootstrap_dev.sh   # create dev tables/bucket/queue (no mock)
multitenant-rag/local/run_local.sh       # ask app on :8080 (uvicorn --reload)
python multitenant-rag/local/dev_worker.py   # SQS->ingest poller (stands in for the Lambda mapping)
python multitenant-rag/local/dev_seed.py     # 5 Tamil-named users + ~20 blogs (DEV ONLY)
cd blog-frontend && npm run dev              # Vite :5173 (proxies /api -> :8080)
```
Dev seeded users: karthikraja / anitharani / senthilkumar / divyabharathi / balamurugan
(email <name>@gmail.com, password <name>@password@123). Prod has NO mock data.

## Key code files (multitenant-rag/lambdas/)
- `common/auth.py` — bcrypt + JWT (create/verify/bearer). `common/context.py` — identity from JWT.
- `common/posts.py` — shared create-post logic (used by createpost handler + ask dev route).
- `common/chats.py` — saved chats (create/list/get/append/soft+permanent delete). **Limit is
  PER-PROFILE**: MAX_ACTIVE=5 counted filtered by tenant_id.
- `common/semcache.py` — semantic answer cache (lookup/store/invalidate_tenant, lazy _ensure()).
- `common/secrets.py` — cached secrets, env-var overrides for dev. `common/logger.py` — JSON logger.
- `ask/app.py` — FastAPI app: auth, /api/ask (LLM-judge, memory, semantic cache, empty-retrieval
  overview/decline via `_build_profile_prompt`), chats, users, tenants, read-post. Helpers:
  `_hybrid_search`, `_tenant_post_titles`, `_dedupe_citations`.
- `ask/llm.py` — Groq streaming client (429 retry, `history` for follow-ups, `model` override +
  `GROQ_MODEL_SMALL` for tiering).
- `create_post/handler.py` — thin adapter over common.posts. `ingest_worker/handler.py` (calls
  `semcache.invalidate_tenant` after upsert) + `chunker.py`.
Frontend: `blog-frontend/src/App.jsx` (React Router, Tailwind, floating Ask-AI widget), `src/api.js`,
`tailwind.config.js` (light "Inkwell" theme).

## How to promote dev → prod
Push to main → CI (`build-lambdas.yml`) builds 3 images tagged `<sha>`/`v1`/`latest` → deploy the
changed ones by **immutable SHA**: `aws lambda update-function-code --image-uri <ECR>/<fn>:<sha>`.
Config updates (env) must wait for the code update (`aws lambda wait function-updated`). Frontend:
`npm run build` → `aws s3 sync dist/ s3://praveen-blog-frontend --delete` → CloudFront invalidation.
Semantic cache needs NO infra (defaults + lazy collection create); tiering needs `GROQ_MODEL_SMALL`.
**Audit IAM per handler** (get vs query vs scan) — LocalStack doesn't enforce it (this bit us once:
ask role needed GetItem on posts). Prod-only infra already exists (by_email GSI, chats table, jwt secret,
widened IAM, API GW $default, CloudFront /api/*).

## Gotchas learned (see learnings/stage-5-serverless.md for the full list)
Python Lambda has no native streaming → LWA. Function URL public blocked by account BPA → IAM+OAC, but
OAC+POST needs a client body-hash → we route ask via API Gateway (buffered). Lambda memory = CPU dial.
CloudFront SPA 403/404→index.html masks API errors. LocalStack ignores IAM.

## References
- Obsidian task: `/genai multitenant-rag` (context/progress/changelog/SESSIONS/resume-and-interview).
- AWS learnings: `learnings/stage-5-serverless.md`, `learnings/INDEX.md`.
- Sibling: on-prem [[ticket-manager]] RAG.
