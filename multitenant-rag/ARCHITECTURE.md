# MultiTenantRAG — Architecture (Locked v1)

Last updated: 2026-06-16
Status: Design LOCKED — implementation starts with Phase 0 cleanup

> **⚠️ SHIPPED-STATE NOTE (2026-07-26).** This is the *original* locked design. The app shipped
> to prod with several deliberate changes — read `STATUS.md` / `MASTER-CONTEXT.md` for what's
> actually live. Key diffs from this doc: **auth is custom JWT** (not the `X-User-Id` header
> described below); **the product model is "visit a profile, ask THEIR AI"** (a directory, not
> per-post); **`ask` is buffered via API Gateway** (LWA buffered), not a streaming Function URL
> (Python has no native streaming; true streaming deferred); **saved chats + conversation memory**
> were added (`common/chats.py`, `multitenant-chats` table); **relevance is LLM-as-judge** with a
> 0.20 dense floor. Everything below is otherwise accurate (data models, hybrid retrieval, cost).

---

## What We're Building

A multi-tenant RAG chatbot deployed on AWS. Each user has a profile page; visitors chat with an AI that knows only that user's content. 5-10 mock tenants across different domains (doctor, chef, coder, etc.). Complex retrieval (hybrid dense + sparse), streaming responses, tenant isolation enforced server-side.

Interview positioning: depth over scale. Complex retrieval + isolation + streaming, not "scales to a million."

---

## Non-Negotiable Decisions (Locked)

### Compute + API Layer
- **API Gateway HTTP API** for `POST /posts` (synchronous, no streaming needed)
- **Lambda Function URL with response streaming** for `POST /ask` (streaming required for chat UX)
- **CloudFront** in front, routes by path pattern to each origin
- **All backend Lambdas run as container images** (fastembed dependencies force this)

### Storage
- **DynamoDB** for metadata, users, tenants, posts, usage logs
- **S3** (`praveen-multitenant-content`) for raw post markdown
- **Qdrant Cloud** for vector store (free tier, 1GB)
- **Secrets Manager** for Groq + Qdrant API keys

### Models
- **LLM**: Groq Llama 3.3 70B (free tier, rate-limited)
- **Embeddings**: Bedrock Titan Text V2 (1024 dims)
- **Sparse (BM25)**: fastembed library, runs inside Lambda
- **NO reranker** in v1 (dense + sparse hybrid is enough)

### Async Processing
- **SQS FIFO queue** for ingestion, MessageGroupId = tenant_id (per-tenant serialization)
- Ingestion is fire-and-forget from user's perspective; user does not wait for embedding

### Frontend
- Existing React + Vite app on S3 + CloudFront
- URLs: `/u/{user_id}/`, `/u/{user_id}/chat`, `/u/{user_id}/write`
- Tenant identity flows through user context, never from URL body/param

### Chunking
- **Structural (markdown-aware)** chunking, not fixed-length, not pure semantic
- Priority: headers → paragraphs → sentences
- Chunk size: 500 tokens, overlap: 50
- Header path prepended to each chunk for context

### Retrieval
- **Hybrid dense + sparse via Qdrant native fusion** (RRF)
- Filter always applied server-side: `tenant_id = derived_from_user_id`
- Top-K: 5
- If top-1 score below threshold (0.3): return polite tenant-named no-info message

### Authentication
- **v1**: No real auth. `X-User-Id` header from URL context.
- **v1.5**: Cognito with tenant claim
- Code uses `get_context(event)` abstraction — swap implementation without touching business logic

### Tenant Isolation
- `user_id` from request → look up in `users` DynamoDB table → derive `tenant_id`
- Never trust `tenant_id` from request body or URL
- Qdrant search is **pre-filtered** by tenant_id (never post-filter)

### Domain Handling
- `tenant.domain` is a **hint string** in the system prompt, NOT a retrieval filter
- No hardcoded `DOMAIN_PROMPTS` dictionary
- No per-post domain classification
- Vector search naturally handles multi-domain users

### Response Behavior
- Streaming tokens through Lambda Function URL
- Empty result response: `"{tenant.display_name} hasn't written about this topic."`
- Citations appended after stream ends

### Scope Constraints
v1 does NOT include:
- Global/common shared content (`scope="global"`)
- PDF, image, table, or SVG ingestion
- OCR (Textract)
- Query rewriting via LLM
- HyDE
- Cross-encoder reranker
- Semantic cache
- Per-tenant rate limiting UI
- Cognito real auth
- Multi-user per tenant
- Admin dashboard
- Cost dashboard UI

---

## Request Flow Diagrams

### Post Creation

```
Browser (/u/{user_id}/write)
  |
  |  POST /api/posts
  |  Headers: X-User-Id
  |  Body: { title, content }
  |
CloudFront (routes /api/posts to API Gateway origin)
  |
API Gateway HTTP API
  |
createPostLambda
  |
  |-- 1. get_context(event) -> (user_id, tenant_id) via users table lookup
  |-- 2. Compute content_hash (SHA256)
  |-- 3. Save content -> S3 (tenants/{tenant_id}/posts/{post_id}.md)
  |-- 4. Save metadata -> DynamoDB posts
  |-- 5. Send message -> SQS ingestion.fifo (MessageGroupId=tenant_id)
  |-- 6. Return 201 { post_id, status: "pending" }

Later, async:

SQS ingestion.fifo
  |
ingestWorkerLambda (max concurrency: 5)
  |
  |-- 1. Fetch content from S3
  |-- 2. Markdown-aware chunking
  |-- 3. Batch embed via Bedrock Titan V2 (dense)
  |-- 4. fastembed BM25 (sparse)
  |-- 5. Delete existing chunks for this post_id from Qdrant
  |-- 6. Upsert new chunks with payload (tenant_id, user_id, ...)
  |-- 7. Update DynamoDB posts.ingestion_status = "indexed"
```

### Chat Query

```
Browser (/u/{user_id}/chat)
  |
  |  POST /api/ask
  |  Headers: X-User-Id
  |  Body: { question }
  |  (fetch with ReadableStream)
  |
CloudFront (routes /api/ask to Lambda Function URL)
  |
Lambda Function URL (response streaming mode)
  |
askLambda
  |
  |-- 1. get_context(event) -> (user_id, tenant_id)
  |-- 2. Load tenant from DynamoDB (cached in Lambda memory)
  |-- 3. Embed question dense (Titan V2) + sparse (fastembed BM25)
  |-- 4. Qdrant hybrid query with RRF fusion + tenant_id filter, top-5
  |-- 5. If top-1 score < threshold: emit "{tenant} hasn't written about this"
  |-- 6. Build prompt (universal template + tenant.domain hint + retrieved chunks)
  |-- 7. Call Groq with stream=True
  |-- 8. Forward tokens via response_stream.write() progressively
  |-- 9. Emit citations event at end
  |-- 10. Log usage to DynamoDB usage_logs (async, best-effort)
```

---

## Data Models

### DynamoDB Tables

**multitenant-users**
```
PK: user_id (S)
Attributes: tenant_id, display_name, role ("admin"|"member"), active, created_at
Billing: PAY_PER_REQUEST
```

**multitenant-tenants**
```
PK: tenant_id (S)
Attributes: display_name, domain (hint string), created_at, active
Billing: PAY_PER_REQUEST
```

**multitenant-posts**
```
PK: tenant_id (S)
SK: post_id (S)
Attributes: user_id, title, s3_key, ingestion_status ("pending"|"indexed"|"failed"),
            content_hash, chunk_count, created_at, updated_at
Billing: PAY_PER_REQUEST
GSI: by_status
  PK: ingestion_status
  SK: updated_at
```

**multitenant-usage-logs**
```
PK: tenant_date (S)          -- format: "{tenant_id}#YYYY-MM-DD"
SK: timestamp_req (S)         -- format: "{timestamp}#{request_id}"
Attributes: user_id, query, tokens_input, tokens_output, latency_ms, context_source
TTL: expires_at (30 days from creation)
Billing: PAY_PER_REQUEST
```

### S3 Content Bucket

```
Bucket: praveen-multitenant-content
Region: ap-south-1
Access: private (block all public), versioning enabled

Structure:
  tenants/{tenant_id}/posts/{post_id}.md
```

### Qdrant Collection

```
Name: multitenant_chunks

Vector configs:
  dense:
    size: 1024
    distance: Cosine
  sparse:
    modifier: idf

HNSW index defaults.

Payload:
  tenant_id: str (indexed keyword)
  user_id: str (indexed keyword)
  post_id: str (indexed keyword)
  chunk_id: str
  chunk_index: int
  chunk_text: str
  title: str
  header_path: str (e.g., "H1 / H2 / H3")
  source_s3_key: str
  content_hash: str
  created_at: int
```

### Secrets Manager

```
multitenant/groq
  { "api_key": "gsk_..." }

multitenant/qdrant
  { "api_key": "...", "url": "https://xxx.qdrant.io" }
```

### SQS Queue

```
Name: multitenant-ingestion.fifo
Type: FIFO
ContentBasedDeduplication: true
VisibilityTimeout: 300s
MessageRetentionPeriod: 345600s (4 days)
```

### Lambda Functions

```
multitenant-createpost     (API Gateway trigger)
multitenant-ingestworker   (SQS trigger, concurrency: 5)
multitenant-ask            (Function URL, streaming)
```

### ECR Repositories

```
multitenant-createpost
multitenant-ingestworker
multitenant-ask
```

---

## Cost Envelope

Target: under $10/month hard cap. Realistic v1 spend: **$1-3/month**.

| Component              | Free Tier                | Expected Cost |
|------------------------|--------------------------|---------------|
| Lambda invocations     | 1M req/mo + 400K GB-s    | $0 |
| API Gateway HTTP API   | 1M req/mo (12 mo)        | $0 |
| DynamoDB on-demand     | 25GB + 25 units          | $0 |
| S3                     | 5GB + 20K GET + 2K PUT   | $0 |
| SQS                    | 1M req/mo                | $0 |
| Bedrock Titan V2       | none                     | ~$0.05/mo |
| Groq                   | none (rate-limited)      | $0 |
| Qdrant Cloud           | 1GB / 1 cluster          | $0 |
| CloudFront             | 1TB egress + 10M req     | $0 |
| Secrets Manager        | none                     | ~$0.80/mo (2 secrets) |
| CloudWatch Logs        | 5GB ingest               | $0 |

Alarm: AWS Budget at $5/month notifies via email.

---

## What Gets Deleted (Old Blog Infra)

Delete during Phase 0:
- ECS cluster `blog-cluster` + service
- ALB `blog-alb` + listener + target groups
- EC2 instance `i-092269a5892039994` (terminate)
- RDS `blog-db` + subnet group
- Related security groups
- ECR repo `blog-backend`
- CloudWatch log groups for old blog
- IAM roles: `ecsTaskExecutionRole`, `blog-backend-task-role`, `blog-ec2-cloudwatch-role`
- Instance profile `blog-ec2-cloudwatch-profile`

Keep (will reuse or modify):
- CloudFront distribution `EOV3277U5A8CF`
- S3 bucket `praveen-blog-frontend` (will replace content)
- OIDC provider for GitHub Actions
- GitHub Actions IAM role `github-actions-deploy-role`
- Cost budget + billing alerts

Expected monthly savings: ~$10 (mostly VPC public IPv4 charges from ALB and EC2).

---

## Interview Talking Points

Lead with these when asked about the project:

1. **"How do you enforce tenant isolation?"**
   Server-side: user_id from request → users DynamoDB lookup → derive tenant_id → apply as Qdrant pre-filter. Never trust tenant_id from client. Pre-filter (not post-filter) means Qdrant returns 0 wrong-tenant results even if they'd be top-scored.

2. **"Why hybrid search?"**
   Dense (semantic) catches paraphrased questions. Sparse (BM25) catches exact terminology like drug names, code function names, product SKUs. RRF fusion combines both without additional complexity. Qdrant natively supports hybrid — no separate BM25 index required.

3. **"How do you handle multi-domain users?"**
   Vector search does this implicitly. Semantically unrelated chunks don't score high, so they don't surface. tenant.domain is just a prompt hint for tone, not a retrieval filter. No pre-classification needed.

4. **"How do you handle streaming?"**
   Lambda Function URL with RESPONSE_STREAM invoke mode. Groq streaming API forwards tokens to Lambda callback, which writes them progressively to the response stream. CloudFront forwards streaming without buffering. Client uses ReadableStream API.

5. **"Why not OpenSearch?"**
   Cost. OpenSearch Service starts at ~$75/mo. Qdrant Cloud free tier fits 1GB (>100K vectors of my size). At my scale (10 tenants, ~3000 chunks), it's the right tool. If scale grew beyond free tier limits, revisit — but adding complexity before need is wrong.

6. **"Why Groq over Bedrock?"**
   Groq's Llama 3.3 70B is free with rate limits sufficient for demo scale, and rivals Claude Haiku's quality. Architecture uses a provider abstraction so swapping to Bedrock is a config change. Interview-honest answer, not "AWS at all costs."

7. **"What did you consciously exclude from v1?"**
   Rerankers, semantic cache, PDF/OCR, Cognito, global content — all valid features, all cost time and money before proving core value. v1 is minimum viable slice: multi-tenant, hybrid retrieval, streaming, isolation. Adding features prematurely is the anti-pattern I avoid.

---

## Provider Abstraction

Two interfaces designed as swappable:

```python
# llm_provider.py
class LLMProvider(ABC):
    def stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]: ...

class GroqProvider(LLMProvider): ...       # v1 default
class BedrockProvider(LLMProvider): ...    # v1.5 option
class OpenAIProvider(LLMProvider): ...     # future

# embedding_provider.py
class EmbeddingProvider(ABC):
    def embed(self, texts: List[str]) -> List[List[float]]: ...
    def dimension(self) -> int: ...

class TitanEmbeddingProvider(EmbeddingProvider): ...  # v1
class CohereEmbeddingProvider(EmbeddingProvider): ... # future
```

**Warning:** swapping the embedding provider requires re-indexing all vectors (dimensions or embedding space changes). LLM provider swaps are free.

Environment variables control which provider is loaded:
- `LLM_PROVIDER=groq` (default) | `bedrock`
- `EMBEDDING_PROVIDER=titan` (default)

---

## Deployment (High-Level)

- Lambdas built as container images
- ECR repos: `multitenant-createpost`, `multitenant-ingestworker`, `multitenant-ask`
- GitHub Actions workflow (adapted from existing) builds images, pushes to ECR, updates Lambda function code
- Frontend deployed via same workflow: `npm run build`, `aws s3 sync`, `cloudfront create-invalidation`

---

## v1 Success Criteria

- [ ] Visit `/u/rajesh/` → see Rajesh's post list from DynamoDB
- [ ] Create post → S3 write + DynamoDB write + SQS message dispatched
- [ ] Async ingestion completes → post status becomes `"indexed"`
- [ ] Qdrant collection populated with chunks + payload
- [ ] Query as Rajesh → get answer streamed with citations from Rajesh's chunks
- [ ] Query as Priya (different tenant, same question) → different chunks or no-info message
- [ ] Streaming works: tokens visible in UI within 500ms of request
- [ ] Zero cross-tenant leakage verifiable via direct Qdrant queries
- [ ] Total AWS cost < $3/month at 500 test queries
- [ ] GitHub push → automatic redeploy of Lambdas via Actions

---

## Open Questions Deferred to v1.5

- Cognito integration for real auth
- Per-tenant rate limiting enforcement
- Reranker via Cohere Rerank v3 or self-hosted cross-encoder
- Semantic cache via DynamoDB with vector similarity
- Per-post domain classification for multi-domain tenants
- PDF/image/table ingestion pipeline
- Global/common shared knowledge base
- Cost dashboard UI

---

## References

- Qdrant Hybrid Search: https://qdrant.tech/documentation/concepts/hybrid-queries/
- Bedrock Titan V2 docs: https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html
- Lambda Response Streaming: https://docs.aws.amazon.com/lambda/latest/dg/configuration-response-streaming.html
- Groq API: https://console.groq.com/docs/
- fastembed BM25: https://github.com/qdrant/fastembed
