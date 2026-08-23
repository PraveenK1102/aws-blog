# MultiTenantRAG — Code-Grounded Low-Level Design

Audit date 2026-08-23. Every claim below is read from source or from a read-only AWS describe.
**Zero inference or data-plane provider calls were made producing this document** (NVIDIA 0, Groq 0,
Titan 0, Qdrant queries 0, RAGAS/DeepEval 0). No AWS mutation. Nothing deployed.

Three systems are kept strictly separate throughout:

| | Label | Status |
|---|---|---|
| **A** | Current production | live on `d5af30e` |
| **B** | Offline-validated routed LangGraph v2 | frozen, never deployed |
| **C** | Desired production target | design only |

---

## 1. A — CURRENT PRODUCTION

Serverless, account `557690605487`, region `ap-south-1`.

```
client → CloudFront (EOV3277U5A8CF) → API Gateway HTTP API (pdp1o70aug)
                                          ├── multitenant-ask         (container Lambda, FastAPI)
                                          ├── multitenant-createpost  (container Lambda)
                                          └── multitenant-ingestworker(container Lambda, SQS-triggered)
```

Ask path (`lambdas/ask/app.py`, ~1,389 lines) is a **deterministic if/else DAG** — no LangGraph, no router.
Generation is Groq `openai/gpt-oss-120b` via a hand-rolled `requests.post` + SSE parser (`ask/llm.py`).
`MAX_LLM_CONTEXT_CHUNKS=5`. Stores: S3 (bodies), DynamoDB (7 tables), Qdrant Cloud (2 collections),
SQS FIFO (ingestion), Secrets Manager (groq/qdrant/jwt/langsmith/nvidia), CloudWatch, LangSmith tracing.

## 2. B — OFFLINE-VALIDATED ROUTED LANGGRAPH V2 (frozen, not deployed)

`evals/routed_graph_v2.py`. Frozen Router V2 → conditional edge → simple path, or decompose → `Send`
fan-out → parallel branch retrieval under `Semaphore(2)` → deferred coverage-aware merge → one generation.
Live result: 18/18 success, 34 logical branches, context coverage +13, answer coverage +10, zero
regressions. Exists only in `evals/`; absent from every Lambda image.

---

## 3. INGESTION — EXACT DATA OWNERSHIP

### 3.1 Write path

`lambdas/create_post/handler.py` is a thin adapter (53 lines): parse event → `get_context` → delegate.
All storage logic lives in **`lambdas/common/posts.py:create_post()`** (shared by the prod Lambda and the
ask app's dev route, so dev and prod write identically).

**Dedup first** — `posts.py:50-57`: `content_hash = sha256(content)`, then
`_find_existing_by_hash()` (`posts.py:116-130`) does a DynamoDB `query` on `tenant_id` with a
`FilterExpression` on `content_hash`, `Limit=1`. A hit returns the existing `post_id` with HTTP 200 instead
of 201 — no new write, no re-ingestion.

### 3.2 S3 — the ONLY home of the full body

| Property | Value | Source |
|---|---|---|
| Bucket | `os.environ["S3_CONTENT_BUCKET"]` | `posts.py:40` |
| Key | **`tenants/{tenant_id}/posts/{post_id}.md`** | `posts.py:60` |
| Body | the complete raw markdown, UTF-8 | `posts.py:65-66` |
| ContentType | `text/markdown` | `posts.py:66` |
| `post_id` | `post_{uuid4().hex[:12]}` | `posts.py:59` |

S3 is written **first**; if the subsequent DynamoDB write fails the object is deleted
(`posts.py:89-92`) — a compensating rollback, not a transaction.

### 3.3 DynamoDB posts table — metadata only, **no body**

Written by `create_post` (`posts.py:74-85`), key `(tenant_id HASH, post_id RANGE)`:

| Field | Type | Written by | Role |
|---|---|---|---|
| `tenant_id` | S | create | partition key |
| `post_id` | S | create | sort key |
| `user_id` | S | create | author |
| `title` | S | create | metadata; later copied into every Qdrant payload |
| `s3_key` | S | create | **pointer to the body** |
| `ingestion_status` | S | create `"pending"` → worker `"indexed"` | **indexing status** |
| `content_hash` | S | create | dedup key |
| `chunk_count` | N | create `0` → worker actual | indexing status |
| `created_at` / `updated_at` | N | create / both | timestamps |

Updated by the worker in `_mark_indexed()` (`ingest_worker/handler.py:167-181`):
`SET ingestion_status = "indexed", chunk_count = :c, updated_at = :u`.

**The post body is never stored in DynamoDB.** Only `s3_key` points to it.

Note: `_mark_indexed` catches `ClientError` and only logs (`:180-181`), so a failed status update leaves the
row `pending` even though Qdrant already holds the chunks — a benign inconsistency, but real.

### 3.4 SQS enqueue

`posts.py:97-104`: `send_message(QueueUrl, MessageGroupId=tenant_id, MessageBody={tenant_id, user_id,
post_id, s3_key, action:"index"})`. **`MessageGroupId` is the `tenant_id`** — the ordering key (see §10).
No explicit `MessageDeduplicationId`: the queue has `ContentBasedDeduplication=true`, so the body hash is
the dedup id. A send failure returns HTTP 200 with `warning: "ingestion queue send failed — needs manual
retry"` (`posts.py:108-109`) — the post exists but is never indexed, and **nothing retries it
automatically**.

### 3.5 Qdrant — vectors + a copy of the chunk text

Worker flow, `ingest_worker/handler.py:79-155`: S3 get → DynamoDB `_get_post_meta` (for `title`) → chunk →
dense embed → sparse embed → **delete-by-filter** → upsert → semcache invalidate → mark indexed.

Point payload (`handler.py:132-143`):

| Payload field | Source |
|---|---|
| `tenant_id`, `user_id`, `post_id` | SQS message |
| `chunk_id` = `f"{post_id}_{i}"`, `chunk_index` | loop index |
| **`chunk_text`** | the chunk body — **a second copy of content, denormalised from S3** |
| `title` | DynamoDB metadata |
| `header_path` | chunker output |
| `source_s3_key` | pointer back to S3 |
| `created_at` | ingest timestamp |

Vectors (`handler.py:128-131`): named `{"dense": [...1024 floats], "sparse": SparseVector(indices, values)}`.

Collection config from `scripts/init_qdrant.py:92-106`:
- `dense`: `VectorParams(size=1024, distance=Distance.COSINE)`
- `sparse`: `SparseVectorParams(modifier=Modifier.IDF)` — **Qdrant computes IDF server-side**
- payload index on `tenant_id`

**Idempotency** (`handler.py:115-120`): before upsert, `qdrant.delete()` with a
`FilterSelector(post_id == this post)` removes every existing chunk for the post, so re-ingesting a post
replaces rather than duplicates. Point IDs are additionally deterministic — `_point_id()`
(`handler.py:224-231`) is `sha256(chunk_id)[:8]` masked to 63 bits — so the same chunk index maps to the
same ID across runs. Delete-then-upsert is the primary mechanism; stable IDs are a second line of defence.
The pair is **not atomic**: a crash between them leaves the post with zero chunks.

### 3.6 Responsibility boundary

| Store | Owns | Does NOT own |
|---|---|---|
| **S3** | the single source of truth for the full post body | no metadata, no vectors, no query path |
| **DynamoDB** | metadata, ownership, dedup hash, indexing status, chunk count, S3 pointer | the body; never read during retrieval ranking |
| **Qdrant** | vectors + a **denormalised copy of each chunk's text** + retrieval metadata | authoritative content — it is a derived index |

Consequence: Qdrant is rebuildable from S3 + DynamoDB. The reverse is not true.

---

## 4. CHUNKER — EXACT IMPLEMENTATION

`lambdas/ingest_worker/chunker.py` (190 lines), invoked as
`chunk_markdown(content, max_tokens=500, overlap_tokens=50)` (`handler.py:98`).

| Parameter | Value | Line |
|---|---|---|
| `max_tokens` | 500 → `max_chars = 2000` | `chunker.py:45` |
| `overlap_tokens` | 50 → `overlap_chars = 200` | `chunker.py:46` |
| Token estimation | **`chars / 4` heuristic**, no tokenizer | `chunker.py:12-14, 45` |

Boundary priority, in order:
1. **Markdown headers** H1–H6 — `HEADER_RE = ^(#{1,6})\s+(.+)$` (`:21`). `_split_by_headers` (`:58-99`)
   maintains a `header_stack`, popping levels `>=` the new one, producing a `" / "`-joined `header_path`.
2. **Horizontal rules** — `HR_RE = ^-{3,}\s*$` is **declared at `:22` but never used**. Documented
   intent, not implemented behaviour.
3. **Paragraph breaks** — `re.split(r"\n\s*\n", section)` (`:111`); paragraphs accumulate into a buffer and
   emit when `buffer_chars + para + 2 > max_chars` (`:142`).
4. **Sentence fallback** — a single paragraph over `max_chars` goes to `_chunk_by_sentences` (`:155-174`),
   splitting on `(?<=[.!?])\s+(?=[A-Z])`.

**There is no final hard character split.** If one "sentence" exceeds `max_chars` (no punctuation, e.g. a
long table or code block) it is emitted oversized. `max_tokens` is a **soft ceiling**.

Overlap (`_get_overlap`, `:177-190`) carries trailing whole paragraphs/sentences up to `overlap_chars`,
never mid-unit, and only **within a section** — headers reset it.

Every chunk is prefixed `f"[{header_path}]\n{text}"` (`:122`) so it is self-contained. That prefix is part
of `chunk_text` and therefore part of what is embedded, retrieved and shown to the model — which is why
retrieved contexts appear as `[Title] [Title]\n...`.

### Classification

**Custom deterministic hierarchical/structural chunking.**

- **Not semantic chunking** — no embedding, no similarity, no topic-shift detection. Boundaries come from
  markdown structure and character budgets only.
- **Not recursive character splitting** in the LangChain sense, though it shares the separator-cascade idea
  (headers → paragraphs → sentences). It differs by tracking header hierarchy and prepending it, which a
  generic recursive splitter does not.

Accurate label: *markdown-structure-first, character-budgeted, overlap-preserving deterministic chunker*.

### Why semantic chunking has not been adopted

1. It requires an embedding call **per candidate boundary** during ingestion — Titan is billable, and
   ingestion currently spends exactly one Titan call per final chunk (`_embed_dense_batch`,
   `handler.py:184-199`, one `invoke_model` per chunk in a loop).
2. The corpus is synthetic markdown with clean headers, so structural boundaries are already close to
   semantic ones — the expected gain is small.
3. It would invalidate every existing measurement: all baselines, the decomposition experiment and the live
   routed run share one embedding space and one chunking. Changing chunking changes the index.

A fair future A/B would need: re-chunk + re-embed into a **separate** Qdrant collection; the same 52/40-case
questions; identical `TOP_K`, `RETRIEVAL_FLOOR`, merge policy and application model; both arms measured with
the same deterministic coverage metric; and a Titan budget for the full re-embed of both arms. **Not
implemented, not recommended yet.**

---

## 5. QUERY RETRIEVAL — EXACT LOW-LEVEL FLOW

One logical branch, `_hybrid_search` (`ask/app.py:789-829`) / `_hybrid_search_multi` (`:1133-1154`):

```
question or subquestion
  │
  ├─(1) dense  = _embed_dense(q)      -> Bedrock Titan v2  : 1024 floats            [app.py:831-835]
  ├─(2) sparse = _embed_sparse(q)     -> local fastembed BM25 query_embed           [app.py:837-842]
  │                                      -> {indices:[int], values:[float]}
  ├─(3) PHYSICAL Qdrant call #1  query_points(query=dense, using="dense",
  │                                 query_filter=tenant, limit=1, with_payload=False)
  │        -> top_dense = points[0].score  (absolute cosine 0..1)                    [app.py:806-811]
  ├─(4) caller gates: if top_dense < RETRIEVAL_FLOOR (0.15) -> treat as no evidence
  ├─(5) PHYSICAL Qdrant call #2  query_points(prefetch=[dense, sparse],
  │                                 query=FusionQuery(RRF), limit=TOP_K,
  │                                 with_payload=True)                              [app.py:813-827]
  └─(6) ranked candidates -> _llm_context(...)[:MAX_LLM_CONTEXT_CHUNKS=5]           [app.py:709-717]
```

**1. What Titan returns** — `_embed_dense` (`:831-835`) posts `{"inputText": text[:8000]}` to
`amazon.titan-embed-text-v2:0` and returns `data["embedding"]`: a single 1024-float dense vector. One
`invoke_model` per call. The 8,000-character truncation is a safety cap.

**2. What BM25/fastembed returns** — `_embed_sparse` (`:837-842`) uses `SparseTextEmbedding("Qdrant/bm25")`
with **`query_embed`** (query-side, distinct from the worker's `embed`) and returns a **sparse** vector as
`{indices, values}` — term ids and weights, not a fixed-width vector. This runs **locally in the Lambda**;
there is no external call and no cost.

**3. How Qdrant uses both** — the collection declares two *named* vector spaces per point (§3.5). A query
selects a space with `using="dense"` or supplies a `SparseVector` for `"sparse"`. Both are supplied as
`Prefetch` branches and combined by `FusionQuery(fusion=Fusion.RRF)`; Qdrant runs each retrieval and fuses
server-side. Sparse scoring uses `Modifier.IDF`, so Qdrant applies IDF itself.

**4. What is stored for each** — dense: the 1024-float Titan embedding of `chunk_text` (cosine). Sparse: the
BM25 `indices`/`values` of the same text (IDF modifier). Both attach to the same point and the same payload.

**5. What the dense-only probe is for** — an **absolute relevance gate**. The docstring (`:791-797`) is
explicit: RRF's fused score is a *rank* number, ≈1.0 for the top hit even on a tiny corpus, so it cannot
say whether anything is actually on-topic. The probe fetches the single best **cosine** similarity, an
absolute 0..1 semantic-closeness measure, with `limit=1, with_payload=False` — deliberately the cheapest
possible query.

**6. Why RRF cannot replace cosine** — RRF scores are computed from *positions in a result list*. The best
document always ranks first regardless of quality, so a tangential keyword match ("chennai", "food"→coffee)
scores as high as a perfect match. RRF is ordinal and corpus-relative; cosine is absolute. Only the latter
supports a threshold.

**7. What `RETRIEVAL_FLOOR` is applied to** — **only `top_dense`**, the probe's cosine, never to RRF
scores. `RETRIEVAL_FLOOR = float(env, "0.15")` (`:61`). The comparison lives in the callers, e.g.
`decomp_graph.normal_retrieve`: `ctx = prod._llm_context(cands) if (cands and top >= prod.RETRIEVAL_FLOOR)
else []`.

**8. What the second query does** — hybrid RRF retrieval **for ranking and citations**, `with_payload=True`
so `chunk_text`, `title`, `post_id`, `tenant_id` come back.

**9/10. Candidate counts and single vs multi/group**

| | single (`_hybrid_search`) | multi/group (`_hybrid_search_multi`) |
|---|---|---|
| tenant filter | `MatchValue(tenant_id)` | `MatchAny(any=tenant_ids)` |
| dense prefetch limit | 20 | **30** |
| sparse prefetch limit | 20 | **30** |
| final `limit` | `TOP_K` = **5** | `TOP_K * 2` = **10** |
| probe | `limit=1, with_payload=False` | identical |

Multi/group deliberately retrieves **twice the breadth** because candidates are spread across several
tenants. Both are then narrowed to 5 by `_llm_context`, which is the single decision point for what the
model sees — and the same list feeds citations, so the system can never cite evidence the model did not get.

### LOGICAL vs PHYSICAL — the distinction that broke a budget

| Unit | Definition | Per branch |
|---|---|---|
| **Logical retrieval** | one `_retrieve(...)` for one question/subquestion | 1 |
| **Physical `query_points`** | one HTTP query to Qdrant | **2** |
| Titan `invoke_model` | one dense query embedding | 1 |
| BM25 encode | local, free | 1 |

A logical branch is **not** one Qdrant call. It is always two: the dense probe and the hybrid query. My
Phase A budget assumed 1:1 and under-counted Qdrant by exactly 2×; the architect corrected the bound to
≤108 physical for ≤54 logical.

---

## 6. LIVE LANGGRAPH CALL ACCOUNTING — verified, not assumed

Verified against `evals/output/routed_live_v2_cases.jsonl` and `routed_live_v2_counters.json`:

| Quantity | Derived from artifacts | Counter | Match |
|---|---|---|---|
| cases | 18 | 18 | ✅ |
| logical branches (Σ `branch_count`) | **34** | `qdrant_logical_branches` 34 | ✅ |
| Titan embeddings (1/branch) | 34 | `titan_embed` 34 | ✅ |
| BM25 local encodes (1/branch) | 34 | `bm25_encode` 34 | ✅ |
| Qdrant dense probes (1/branch) | 34 | `qdrant_dense_probe` 34 | ✅ |
| Qdrant hybrid RRF (1/branch) | 34 | `qdrant_hybrid_rrf` 34 | ✅ |
| **Qdrant physical `query_points`** (2/branch) | **68** | `qdrant_physical` 68 | ✅ |

Branch distribution: 14 cases × 2, 1 case × 3 (`case-030`), 3 cases × 1 (fallback) = 34.

### The 36 NVIDIA 20B calls, verified

| Call | Count | Verification |
|---|---|---|
| decomposition / analyzer | **18** | 18 cases carry `tokens.decompose_in` |
| `final_answer` (compound path) | **15** | 15 cases carry `node_latencies.final_answer_ms` |
| `normal_answer` (fallback path) | **3** | 3 cases carry `node_latencies.normal_answer_ms` |
| **total answer generations** | **18** | 15 + 3 |
| **TOTAL NVIDIA** | **36** | 18 + 18, matches `nvidia_total` 36 |

The architect's expected accounting is confirmed exactly. `nvidia_final_gen` reads 15 rather than 18 because
the 3 fallback cases generated through `normal_answer`; every case still received exactly one answer
generation. Router V2 calls were **0** — persisted verdicts were injected.

---

## 7. MERGE / COVERAGE-AWARE FAN-IN

`decomp_graph.merge_evidence` (`evals/decomp_graph.py:201-239`), reused unmodified by graph v2 and declared
`defer=True` so it fires once after **all** branches.

The interleave the architect describes — `A(Q1) D(Q2) B(Q1) E(Q2) C(Q1)` — is exactly what the code
produces, and **yes, the goal is precisely as stated**: guarantee that independently retrieved evidence from
each subquestion gets representation before one branch consumes the 5-chunk budget.

Mechanism, in code order:

1. **Sort branches** by index; take each branch's `eligible` list (already floor-filtered).
2. **Dedupe key** — `key(c) = (payload["post_id"], payload["chunk_text"][:80])`. Cross-branch, so the same
   chunk retrieved by two subquestions is kept once.
3. **Pass 1 — coverage guarantee** (`:216-223`): iterate branches in order and take **the single best
   not-yet-seen chunk from each**. This is the `A(Q1), D(Q2)` prefix. Breaks early if the cap is reached.
4. **Pass 2 — round-robin fill** (`:225-234`): `depth = 1`, then repeatedly walk every branch taking its
   next unseen chunk at increasing depth — `B(Q1), E(Q2), C(Q1)` — until the cap is hit or all pools are
   exhausted.
5. **Cap** — `cap = prod.MAX_LLM_CONTEXT_CHUNKS` (5); returns `merged[:cap]` plus a parallel
   `merged_context_map[:cap]` recording `{subquestion_index, rank_in_branch}` per slot.

**The critical property, from the docstring (`:202-203`): "Never compares RRF scores across branches."** RRF
scores come from different queries and are not comparable; a global re-sort would let one high-scoring branch
take all 5 slots and starve the other information need — the exact baseline failure decomposition exists to
fix.

**Context/citation invariant** — `merge_evidence` returns one `merged_context` list. `_blocks(state)`
(`:251-260`) renders exactly that list into the prompt, and `_generate` (`:262-276`) derives citations from
the same `state["merged_context"]` via `_dedupe_citations_attributed` (multi/group) or `_dedupe_citations`
(single). One list, both consumers. Verified empirically: all 18 live cases had exactly 5 chunks and
citations ≤ chunks.

---

## 8. ROUTER V2 CONTRACT

Read from `evals/router_v2.py`. **The architect's list is confirmed exactly — 5 values, code agrees.**

| `reason_code` | Meaning | Expectation | Generic example |
|---|---|---|---|
| `multiple_independent_retrieval_needs` | two or more separately retrievable needs; **the only code permitted with `needs_decomposition=true`** | **compound** | "What is X's measurement, and why did unrelated event Y happen?" |
| `single_retrieval_need` | one need, nothing more specific fits | simple | "What is the shutdown procedure for X?" |
| `single_entity_multi_attribute` | several attributes of one entity | simple | "How tall is X and what does it do?" |
| `single_event_multi_attribute` | several attributes of one event/state | simple | "What caused the outage and how much was recovered?" |
| `negative_or_scope_check` | verifying, denying or bounding one fact | simple | "Does rule X apply to Y?" |

Enforced in `parse_router_output`: unknown code → `reason_code_not_in_enum`; `true` with a simple code →
`compound_flag_with_simple_reason_code`; `false` with the compound code →
`simple_flag_with_compound_reason_code`; compound with <2 needs → `compound_needs_fewer_than_two`.

**`router_reason_code` is used for**: classification explanation, debugging, evaluation stratification
(per-category recall/specificity), and observability/telemetry.

**It is NOT used for**: retrieval, query construction, subquestion generation, scope resolution, ranking,
prompt content, or any control-flow decision. Only the boolean `needs_decomposition` routes. Likewise
Router V2's `information_needs` are carried as **diagnostic metadata only** and never become retrieval
queries — asserted by a test that injects sentinel needs and checks they never reach a query.

---

## 9. RETRY POLICY — current implementation

"Bounded retry" in this project means: **a fixed maximum attempt count, an explicit timeout per attempt, and
a terminating raise** — never an unbounded or indefinite loop.

| Component | Max attempts | Retryable | Backoff | Timeout | Source |
|---|---|---|---|---|---|
| **NVIDIA (eval only)** | `MAX_ATTEMPTS=4` (env) | timeouts, 429, 5xx | `BACKOFF` 30/60/120 + jitter; timeouts use `backoff/4 + rand(0,2)` | per-call `timeout` arg, default 180 s | `evals/nvidia_provider.py:9,54-90` |
| — plus | **circuit breaker**: 3 consecutive 429s → `CircuitOpen`, run stops | | | | `:11,81-84` |
| — plus | global pacing `MIN_INTERVAL=6 s` between any two requests | | | | `:8,38-41` |
| — plus | 401/403 → `NvidiaAuthError` immediately, **no retry** | | | | `:75-76` |
| **Groq (production)** | `MAX_RETRIES=4` | **429 only** | honours Groq's "try again in Xs" hint | `timeout=60` | `lambdas/ask/llm.py:25,28-47` |
| — note | any non-200, non-429 raises immediately — no retry on 5xx | | | | `:43-46` |
| **Bedrock / Titan** | **boto3 defaults** — no `botocore.Config` anywhere | boto3 standard | boto3 standard | boto3 default | grep: no `Config(`/`retries=` in `lambdas/` |
| **Qdrant** | **client defaults** — `QdrantClient(url, api_key)` with no timeout or retry args | client default | client default | client default | `app.py:889`, `ingest_worker:220`, `semcache.py:47` |
| **Ingestion / SQS** | **no application retry** — the worker re-raises (`handler.py:73`) and relies on SQS visibility-timeout redelivery | any exception | SQS redelivery after 300 s | Lambda timeout 300 s | `handler.py:60-76` |

**Desired production behaviour** (design only, §12/§14): explicit `botocore.Config(retries={"max_attempts":
3, "mode": "standard"}, read_timeout=…, connect_timeout=…)` for Bedrock and DynamoDB; an explicit Qdrant
client timeout; a per-request wall-clock budget for the ask path; retry on Groq 5xx as well as 429; and a
finite `maxReceiveCount` on the ingestion queue (§10).

---

## 10. SQS FIFO / DLQ / REDRIVE

### Current reality (read-only describes, 2026-08-23)

| Property | Value |
|---|---|
| Queue | `multitenant-ingestion.fifo` (`arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo`) |
| `FifoQueue` | **true** |
| `ContentBasedDeduplication` | **true** (no explicit `MessageDeduplicationId` is sent) |
| `DeduplicationScope` / `FifoThroughputLimit` | `queue` / `perQueue` |
| `VisibilityTimeout` | **300 s** (equals the Lambda timeout) |
| `MessageRetentionPeriod` | **345600 s = 4 days** |
| **`RedrivePolicy`** | **NOT SET — no DLQ, no `maxReceiveCount`** |
| `RedriveAllowPolicy` | NOT SET |
| Lambda ESM | `60e4e50a-…`, **Enabled**, `BatchSize=1`, `MaximumBatchingWindowInSeconds=0`, `MaximumConcurrency=null`, `FunctionResponseTypes=[]` |
| Lambda | timeout 300 s, memory 2048 MB, no reserved concurrency, no Lambda DLQ |

The temporary `MaximumConcurrency=2` used during seeding is no longer set.

### Ordering semantics — precisely

`MessageGroupId = tenant_id` (`posts.py:99`). Therefore:

- **Same tenant → one ordered stream.** All of a tenant's ingestion messages share a group id, so SQS FIFO
  delivers them strictly in order and will not release the next message in that group until the current one
  is deleted or its visibility timeout expires. Two posts from the same author cannot be indexed
  concurrently or out of order.
- **Different tenants → independent groups, processed concurrently.** Distinct `MessageGroupId`s have no
  ordering relationship, so Lambda may scale out across tenants in parallel. Tenant A's slow ingestion
  cannot reorder tenant B's, and a stuck message blocks **only its own tenant's** group.

With `BatchSize=1` each invocation handles exactly one message, so the worker's re-raise cannot fail a
sibling message.

### Consequence of the missing DLQ

With no `maxReceiveCount`, a permanently failing message is redelivered until the **4-day retention**
expires, then is silently discarded. It is retried roughly every 300 s — about 1,150 times — each attempt
paying S3 + DynamoDB + Titan + Qdrant work, and it **blocks its tenant's group for four days**. There is no
alarm and no operator surface.

Note the earlier description of this queue as retrying "indefinitely" was wrong and is corrected here: it is
bounded by retention, not infinite.

### DESIRED DLQ architecture — design only, NOT APPLIED

```
createPost ──► multitenant-ingestion.fifo  (source, FIFO)
                 │  visibility 300s  ==  worker timeout
                 │  redelivery on failure
                 ▼
              maxReceiveCount = 5
                 │
                 ▼
        multitenant-ingestion-dlq.fifo  (FIFO DLQ, retention 14 days)
                 │
                 ▼
        CloudWatch alarm on ApproximateNumberOfMessagesVisible >= 1
                 │
                 ▼
        investigate → fix root cause → StartMessageMoveTask (redrive) → source queue
```

Recommended configuration, stated separately from current reality:

| Setting | Recommended | Rationale |
|---|---|---|
| DLQ | `multitenant-ingestion-dlq.fifo` (must also be FIFO) | FIFO source requires a FIFO DLQ |
| `maxReceiveCount` | **5** | ~25 min of retries at a 300 s visibility timeout — enough for transient Bedrock/Qdrant faults, far short of 1,150 |
| DLQ retention | **14 days** (1209600 s) | outlives the source's 4 days so evidence survives |
| `RedriveAllowPolicy` on DLQ | `byQueue` → source ARN only | restricts redrive targets |
| CloudWatch alarm | `ApproximateNumberOfMessagesVisible >= 1`, 1 datapoint, SNS to the existing budget-alert email | any DLQ arrival is abnormal at this volume |
| Second alarm | source `ApproximateAgeOfOldestMessage > 900 s` | catches a stuck tenant group before the DLQ fills |
| ESM `FunctionResponseTypes` | `["ReportBatchItemFailures"]` | only meaningful if `BatchSize > 1`; harmless now, correct later |
| Redrive | manual `StartMessageMoveTask` after a fix | never automatic — a poison message would loop |
| Backpressure | `MaximumConcurrency` on the ESM (e.g. 2–5) | Titan is the constrained downstream; this is the throttle used during seeding |

---

## 11. LANGSMITH + OFFLINE EVALUATION

### Current reality

- Production tracing: **unchanged**. `lambdas/common/tracing.py` uses the LangSmith SDK directly
  (`RunTree`), feature-gated on `LANGSMITH_TRACING`, fail-open, `hide_inputs=True`/`hide_outputs=True`, and
  attaches only whitelisted scalar metadata via `_ALLOWED_META`. `Span.finish()` calls `client.flush()` so
  runs survive Lambda freeze. Project `multitenant-rag-prod`.
- RAGAS/DeepEval: calibration **stopped**; **no framework feedback is attached to LangSmith**.
- An offline evaluation integration already exists: `evals/run_baseline.py` and `run_nvidia_eval.py` use
  `langsmith.Client`, `list_examples(dataset_name=…)` and `c.evaluate(..., max_concurrency=1)` against a
  private synthetic dataset.

### Future path — can persisted artifacts become a LangSmith run WITHOUT re-inference?

**Yes.** The artifacts already contain everything an example and a run need: `question`,
`reference_answer`, `answer`, and the exact `retrieved_contexts` with a `context_sha256`
(`ragas-deepeval-eval-inputs.jsonl`). The implementation path, **documented only**:

1. Create/reuse a private dataset (e.g. `routed-rag-v2-offline`), one example per **case**:
   inputs `{question}`, outputs `{reference_answer}`.
2. For each of the 36 records create a **run** carrying the already-computed `answer` and
   `retrieved_contexts` as outputs, tagged `variant=baseline|routed` and `experiment=rag-routed-langgraph-v2-offline`.
   `client.create_run`/`create_example` accepts supplied values — **no application inference is triggered**,
   which is the whole point. `c.evaluate()` would re-run the target and must **not** be used here.
3. Attach scores with `client.create_feedback(run_id, key=…, score=…)` using the explicit namespaced keys
   `ragas_faithfulness`, `ragas_answer_relevancy`, `ragas_context_precision`, `ragas_context_recall`,
   `deepeval_faithfulness`, `deepeval_answer_relevancy`, `deepeval_contextual_precision`,
   `deepeval_contextual_recall` — never renamed to a generic `score`, never averaged across frameworks.
4. Also attach the deterministic metrics (`det_ctx_phrase_coverage`, `det_ans_phrase_coverage`) so the
   defensible measurements sit beside the framework ones.
5. Keep this in a **separate project** from `multitenant-rag-prod`. The corpus is private synthetic content,
   which is allowed; production tracing stays redacted and untouched.

This makes clear RAGAS/DeepEval are **offline evaluation frameworks, not production runtime components**.
Blocked only on a reliable evaluator (§15). No LangSmith call was made in this task.

---

## 12. C — DESIRED PRODUCTION GRAPH

The target flow, with an explicit split between infrastructure and graph. **The key design point: not every
box in the diagram belongs inside LangGraph.**

| Stage | Belongs in | Why |
|---|---|---|
| `validate_request` | **FastAPI/Lambda** | HTTP concern: auth, JWT, body shape, 401/422. Must fail before any graph state exists. |
| `resolve_scope` | **LangGraph** | Produces state every downstream node depends on; scope safety is a graph invariant (tested). |
| `load_history` | **LangGraph** | Reads DynamoDB into state; conditional on a chat id. |
| `fold_followup` | **LangGraph** | Rewrites the question from history — a model-dependent decision belonging with routing. |
| `semantic_cache_check` | **LangGraph** | Its outcome is a conditional edge (hit → skip everything). Modelling it outside would duplicate the branch. |
| cache hit → response | **LangGraph → END** | terminal edge |
| `route_question` | **LangGraph** | frozen Router V2; the conditional edge |
| `retrieve` (simple) | **LangGraph** | node wrapping existing hybrid retrieval |
| `decompose` → `fan_out` → branches → `merge` | **LangGraph** | this is exactly what `Send` + deferred fan-in + `Semaphore(2)` exist for; validated offline |
| `build_context` | **LangGraph** | the max-5 cap and the context==citation invariant |
| `generate` | **LangGraph** | one call, prompt built from state |
| `validate_answer` | **LangGraph** | empty/error answer must not poison the cache — already a production fix |
| `build_citations` | **LangGraph** | must derive from the same context list |
| `write_cache` | **LangGraph** | conditional on `validate_answer` |
| `save_chat` | **Lambda, post-graph** | persistence side effect, not a routing decision; keep out so a DynamoDB fault cannot fail the answer |
| `write_usage` | **Lambda, post-graph** | telemetry; same reasoning |
| streaming/SSE | **FastAPI/Lambda** | transport |
| tracing | **both** — root span in Lambda, child spans per node | |

Rule of thumb applied: **LangGraph owns anything whose outcome changes control flow or that must share
state; the Lambda owns transport, auth, and fire-and-forget side effects.**

## 13. ROLLOUT DECISION — documented only

There is no meaningful real-user traffic, so a percentage canary would measure nothing. Agreed approach:

1. Production code prepared behind a switch (env var, e.g. `ROUTED_RAG_ENABLED`, default off).
2. Deploy with the switch **off** — a pure no-op release; verify the deterministic path is unchanged.
3. Manual authenticated smoke tests: single-profile, multi-profile, group, a known compound question, a
   refusal/scope question, and a semantic-cache hit.
4. Enable the switch immediately (no staged percentages).
5. Monitor CloudWatch (errors, duration, throttles) and LangSmith (graph spans, route distribution,
   branch counts, empty-branch rate).
6. Rollback = flip the switch off; no redeploy.

**The flag exists for rollback safety, not gradual customer rollout.** Not deployed.

---

## 14. Known limitations carried forward (pre-existing, unchanged)

- Retrieved user content is interpolated into the group/single system prompt — a cross-tenant
  prompt-injection surface on the group path.
- Global search (`app.py:1356`) queries with **no tenant filter** by design.
- `_mark_indexed` failure leaves a post `pending` though its chunks are indexed.
- Chunker declares an unused horizontal-rule pattern and has no final hard split.
- Historical git commits may still contain the retired RDS credential (tip is clean).
- `case-041`: Router V2 routes it compound and its `information_needs` are correct, but the frozen
  decomposition analyzer returns zero subquestions and the graph falls back to simple retrieval.
  **LOW PRIORITY / BACKLOG — not a production blocker.**
