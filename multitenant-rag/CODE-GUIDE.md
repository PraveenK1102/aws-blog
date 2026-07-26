# Code Walkthrough

Every file in `lambdas/`, what it does, and design choices.

Use this as reference while reading the code. Written to explain WHY, not just WHAT.

> **⚠️ 2026-07-26 update — READ THIS; several sections below describe the original design.**
> Files ADDED / CHANGED since this guide was first written:
> - `common/auth.py` — bcrypt + JWT (create/verify/bearer).
> - `common/context.py` — identity from the **JWT** (Authorization: Bearer), not `X-User-Id`.
> - `common/posts.py` — shared create-post logic (createpost handler + ask dev route).
> - `common/chats.py` — saved chats CRUD, soft+permanent delete. **Limit is PER-PROFILE**:
>   MAX_ACTIVE=5 counted by filtering active chats on `tenant_id` (create_chat + set_status).
> - `common/semcache.py` — semantic answer cache in Qdrant: `lookup` (cosine ≥0.95, 24h TTL),
>   `store` (single-turn clean answers only), `invalidate_tenant` (from ingest_worker after upsert),
>   `_ensure` (lazy collection create). All best-effort (degrade to no-cache on error).
> - `ask/handler.py` → **`ask/app.py`** (FastAPI served by uvicorn via the **Lambda Web Adapter**).
>   Hosts auth, /api/ask, chats, users, tenants, read-post. `/api/ask` flow now: semantic-cache
>   lookup (single-turn) → `_hybrid_search` → floor gate → **if below floor:** 0 posts → honest
>   message; else one **small-model** (`GROQ_MODEL_SMALL`) call on the profile card
>   (`_build_profile_prompt`, from `_tenant_post_titles`) decides overview-vs-decline → **else**
>   LLM-judge answer on 70B → cache clean answers. Result types logged: answered / refused /
>   below_floor / empty_corpus / overview / declined / cache_hit.
> - `ask/llm.py` — 429-retry, `history` for follow-ups, and a `model` override + `GROQ_MODEL_SMALL`
>   (model tiering: easy decision on 8B, answers on 70B).
> - `ingest_worker/handler.py` — after Qdrant upsert, calls `semcache.invalidate_tenant(tenant_id)`.
> - Frontend: `blog-frontend/src/App.jsx` (React Router + Tailwind, Medium-style "Inkwell" UI +
>   floating Ask-AI widget), `src/api.js`, `tailwind.config.js` (light theme, serif article body).
>
> Below, the `ask/handler.py`, `common/context.py`, and `common/responses.py` sections describe
> the ORIGINAL design (X-User-Id, streaming Function URL, 0.3 threshold) — kept for history.

---

## Directory Structure

```
lambdas/
├── common/                   Shared utilities (copied into each container)
├── create_post/              POST /posts handler
├── ingest_worker/            SQS-triggered ingester
└── ask/                      POST /ask streaming handler
```

Common utilities are copied into each Docker image so all Lambdas share the same code without needing a Lambda Layer. Simpler at small scale.

---

## common/logger.py

**Purpose:** JSON-structured logger.

**Why JSON logs:** CloudWatch Logs Insights can query JSON fields. Example:
```
fields @timestamp, level, msg, tenant_id
| filter level = "ERROR"
| sort @timestamp desc
```

**Usage in code:**
```python
from common.logger import get_logger
log = get_logger("create_post")
log.info("post created", user_id=user_id, post_id=post_id)
# Output: {"level":"INFO","msg":"post created","user_id":"rajesh","post_id":"post_abc"}
```

Extra kwargs become JSON fields — makes CloudWatch queries much easier than free-text logs.

---

## common/secrets.py

**Purpose:** Fetch API keys from Secrets Manager, cached per Lambda cold start.

**Why cache:** Secrets Manager API calls are not free. Fetching once per cold start (and reusing on warm invocations) reduces cost + latency.

**Usage:**
```python
from common.secrets import get_groq_key, get_qdrant

api_key = get_groq_key()
url, key = get_qdrant()
```

**Cache duration:** Lifetime of the Lambda execution environment (typically 5-15 min of warm reuse, then container recycles).

**Rotation caveat:** If you rotate the secret in AWS, warm Lambdas serve stale creds until container cycles. Fine for learning; production uses rotation hooks.

---

## common/context.py

**Purpose:** Extract user identity from request, derive tenant_id via DynamoDB.

**Why this pattern:** Tenant_id must NEVER be trusted from client. In v1, we accept `X-User-Id` from a header (no real auth yet). In v1.5 we swap to reading JWT claims from Cognito. Business logic never touches this concern.

**Usage:**
```python
from common.context import get_context, ContextError

try:
    user_id, tenant_id, user_record = get_context(event)
except ContextError as e:
    return error_response(401, "Unauthorized", str(e))
```

**Caching:** Users are cached with `@lru_cache(maxsize=256)`. Warm Lambdas reuse the mapping. If a user's tenant changes mid-flight, warm Lambdas serve stale mapping until they cycle. Acceptable for learning.

**Migration to Cognito (v1.5):** Change only `_extract_user_id()` to parse JWT. Rest of function is identical.

---

## common/responses.py

**Purpose:** Standard JSON response builders for API Gateway HTTP API v2 format.

**Usage:**
```python
from common.responses import json_response, error_response

return json_response(201, {"post_id": "post_xxx"})
return error_response(400, "Bad request", "title cannot be empty")
```

---

## create_post/handler.py

**Purpose:** Handle POST /posts. Save content, create metadata, trigger async ingestion.

### Flow

```
1. Parse body (title, content)
2. Resolve user_id → tenant_id via get_context
3. Compute SHA256 hash of content
4. Query for existing post with same hash (dedup)
   → If found, return existing post_id (idempotent)
5. Generate new post_id (uuid short)
6. Write content → S3
7. Write metadata → DynamoDB (status="pending")
8. Send SQS message with MessageGroupId=tenant_id
9. Return 201 with post_id
```

### Design Choices

**Dedup by content_hash:**
Prevents duplicate embeddings if user submits same content twice. For learning volume, a query with FilterExpression is fine. At scale, add a GSI on `content_hash`.

**Rollback on DynamoDB failure:**
If S3 write succeeds but DynamoDB fails, we delete the S3 object best-effort. Not perfect transactional, but avoids orphaned S3 content.

**SQS MessageGroupId:**
Set to `tenant_id`. Ensures per-tenant FIFO ordering. If Priya submits 3 posts rapidly, they process in submission order — but doesn't block Rajesh's posts from processing in parallel.

**Warning on SQS send failure:**
If SQS send fails after S3+DDB succeed, we return 201 with a warning field. The post exists but won't be indexed. Admin can retrigger via a management API (not built in v1).

### Environment Variables Required

```
S3_CONTENT_BUCKET       — praveen-multitenant-content
POSTS_TABLE            — multitenant-posts
INGESTION_QUEUE_URL    — https://sqs.ap-south-1.amazonaws.com/557690605487/multitenant-ingestion.fifo
LOG_LEVEL              — INFO (optional)
```

---

## ingest_worker/handler.py

**Purpose:** SQS-triggered Lambda. Chunks post content, embeds it (dense + sparse), stores in Qdrant.

### Flow

```
For each SQS record:
  1. Fetch content from S3
  2. Load post metadata from DynamoDB (for title)
  3. Chunk via markdown-aware chunker
  4. Dense embed via Bedrock Titan V2 (one-at-a-time API call)
  5. Sparse embed via fastembed BM25 (batched in-process)
  6. Delete existing Qdrant chunks for this post_id (idempotent re-ingest)
  7. Upsert new chunks with payload (tenant_id, post_id, ...)
  8. Update DynamoDB status → "indexed"
```

### Design Choices

**Delete-then-insert for re-ingestion:**
When a post is edited, chunk boundaries may shift due to markdown-aware chunking. Instead of trying to diff chunks, we delete all chunks for that post_id and re-insert. Simpler, correct.

**Batch dense embedding:**
Titan V2 embeds one text per API call (no batch API). For a post with 10 chunks, we make 10 Bedrock calls. Sequential is fine for the small volume here.

**Sparse embedding batched in-process:**
`fastembed.SparseTextEmbedding.embed()` accepts a list. Faster and simpler than per-chunk.

**BM25 model lazy-loaded:**
Global `_bm25` initialized on first call, reused on warm invocations. First cold start takes 2-3 seconds to load ONNX runtime + model.

**Qdrant point IDs:**
Qdrant requires int or UUID. We hash chunk_id ("post_abc_0", "post_abc_1", ...) to int64. Deterministic, so re-ingestion produces same point IDs → clean overwrites.

**Truncate at 8000 chars:**
Titan V2 has a 50K char input limit but we chunk to ~2000 chars, so 8000 is a safety cap in case of oversized chunk.

### Environment Variables Required

```
S3_CONTENT_BUCKET       — praveen-multitenant-content
POSTS_TABLE            — multitenant-posts
LOG_LEVEL              — INFO
AWS_REGION             — ap-south-1 (auto-populated by Lambda)
```

Also implicitly reads from Secrets Manager: `multitenant/qdrant`.

---

## ingest_worker/chunker.py

**Purpose:** Markdown-aware structural chunker.

### Strategy

Priority order for chunk boundaries:
1. Markdown headers (H1-H6)
2. Horizontal rules (`---`)
3. Paragraph breaks (blank line)
4. Sentence boundaries (fallback for oversized paragraphs)

Each chunk includes the header hierarchy prepended (`"[H1 / H2 / H3]\n<content>"`) so chunks are self-contained context units for retrieval.

### Why This Beats Fixed-Length

Blog post:
```
# Amoxicillin

## Side Effects
Common: nausea.

## Dosage
Adult: 500mg.
```

Fixed-length (say 100 chars) would put "nausea" and "Adult: 500mg" in the same chunk, mixing topics. Header-aware keeps them separate and prepends the section context.

### Why Not Pure Semantic Chunking

Pure semantic chunking (embedding each sentence, finding boundary points by cosine distance) would work slightly better for prose without markdown, but requires ~10-50x more embedding calls per document. Not worth the cost for markdown content.

If you upgrade later to handle prose-heavy content, add pure semantic chunking as v2 within-section splitter.

### Token Approximation

We use `chars // 4` as a rough token count. Real tokenization would require the tokenizer, adding a dependency without meaningful accuracy gain here. Titan V2 charges by tokens; our 500-token chunks are ~2000 chars.

---

## ask/handler.py

**Purpose:** POST /ask handler with streaming response.

Runs on Lambda Function URL with `RESPONSE_STREAM` invoke mode.

### Flow

```
1. Extract user_id from X-User-Id header
2. Look up user → derive tenant_id
3. Load tenant metadata (display_name, domain)
4. Parse question from body
5. Embed question dense (Titan V2) + sparse (BM25)
6. Qdrant hybrid query with RRF fusion, tenant_id filter, top-5
7. If empty/weak results (top score < 0.3):
   → Emit "{tenant} hasn't written about this" + empty citations
   → End stream, log usage as "empty"
8. Otherwise:
   → Build system prompt with tenant hint + retrieved chunks
   → Stream from Groq via SSE
   → Write each token event to response_stream
   → After Groq completes, emit citations event
   → Log usage as "answered"
```

### Design Choices

**NDJSON streaming format:**
Each event on its own line, JSON-encoded:
```
{"type":"content","text":"Common side "}
{"type":"content","text":"effects include..."}
{"type":"done","citations":[{"post_id":"post_abc","title":"...","chunk_index":2,"score":0.87}]}
```

Frontend parses line-by-line, incrementally.

**Score threshold 0.3:**
Below this, retrieval quality is too poor to answer meaningfully. Emit polite refusal rather than hallucinate. Tune this threshold based on real query behavior.

**Tenant-specific refusal:**
Instead of generic "I don't know", we say "{tenant.display_name} hasn't written about this topic." Makes sense because the assistant is scoped to that tenant.

**Universal system prompt:**
NO hardcoded per-domain prompts. `tenant.domain` is a HINT string ("healthcare", "recipes") in the prompt. LLM adapts based on retrieved content. Simpler, handles multi-domain users naturally.

**Groq streaming via SSE:**
Uses OpenAI-compatible chat completions API. `stream=True` returns Server-Sent Events. We parse them into `{"type":"content","text":...}` events.

**Best-effort usage logging:**
DynamoDB write after streaming ends. Failure doesn't block user's response. TTL of 30 days auto-purges old logs.

### Environment Variables Required

```
TENANTS_TABLE          — multitenant-tenants
USERS_TABLE            — multitenant-users
USAGE_TABLE            — multitenant-usage-logs
GROQ_MODEL             — llama-3.3-70b-versatile (default if unset)
LOG_LEVEL              — INFO
AWS_REGION             — ap-south-1
```

Also reads from Secrets Manager: `multitenant/qdrant`, `multitenant/groq`.

---

## ask/llm.py

**Purpose:** Groq LLM streaming client. Provider abstraction — v1's implementation.

### API

```python
from llm import stream_answer

for event in stream_answer(system_prompt, user_question):
    if event["type"] == "content":
        # a token
        yield event
    elif event["type"] == "usage":
        # final metadata with token counts
        input_tokens = event["input_tokens"]
        output_tokens = event["output_tokens"]
```

### Design Choices

**OpenAI-compatible API:**
Groq exposes `/openai/v1/chat/completions` — same format as OpenAI. Easy to swap providers by changing the URL.

**Temperature 0.3:**
Low temperature for factual RAG answers. Reduces hallucination and makes answers more consistent.

**max_tokens 1024:**
Generous for chat responses. Bump if answers get truncated.

**Streaming via requests.iter_lines:**
Simple SSE parser. Groq sends `data: <json>\n\n` events, ending with `data: [DONE]`.

**Usage tracking:**
Groq embeds usage stats in the final chunk's `x_groq.usage` field. We extract and yield as a final `usage` event.

---

## How the Pieces Connect

```
User creates post:
  Browser → API Gateway → createPostLambda
    ├→ S3 (raw content)
    ├→ DynamoDB posts (metadata, status=pending)
    └→ SQS (async trigger)
       └→ ingestWorkerLambda (via SQS event source)
          ├→ S3 (read content)
          ├→ Bedrock Titan (dense embeddings)
          ├→ fastembed BM25 (sparse embeddings, in-process)
          ├→ Qdrant (upsert points)
          └→ DynamoDB posts (status=indexed)

User asks question:
  Browser → CloudFront → Lambda Function URL → askLambda
    ├→ DynamoDB users (user → tenant)
    ├→ DynamoDB tenants (display_name, domain)
    ├→ Bedrock Titan (query embedding)
    ├→ fastembed BM25 (query sparse embedding)
    ├→ Qdrant (hybrid search with tenant filter)
    ├→ Groq (streaming chat completion)
    ├→ Response stream ← tokens as they arrive
    └→ DynamoDB usage-logs (best-effort)
```

---

## Adding a Fourth Lambda Later (Optional)

If you need admin operations (list posts, delete post, reindex), add:

```
lambdas/
└── admin/
    ├── handler.py         # routes: GET /posts, DELETE /posts/{id}
    ├── requirements.txt
    └── Dockerfile
```

Same pattern. Shares common/. Add to API Gateway routes.

---

## Testing Locally (Optional)

Each Lambda can be tested locally with:
```bash
cd multitenant-rag
source .venv/bin/activate
pip install boto3 qdrant-client fastembed requests

# Set env vars
export S3_CONTENT_BUCKET=praveen-multitenant-content
export POSTS_TABLE=multitenant-posts
# ... etc

# Import and call handler manually
python -c "
import sys; sys.path.insert(0, 'lambdas')
from create_post.handler import handler
event = {'headers':{'x-user-id':'rajesh'}, 'body':'{\"title\":\"test\",\"content\":\"hi\"}'}
print(handler(event, None))
"
```

Local testing catches most bugs before deployment. Save yourself CloudWatch spelunking.
