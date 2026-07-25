# Stage 5: Serverless — Lambda + SQS + API Gateway + CloudFront

**Status:** ✅ Complete (2026-07-26)
**Vehicle:** Built the **MultiTenantRAG** app — a signup-gated, multi-tenant blogging
platform with a per-profile RAG chatbot, deployed 100% serverless. Live at
`https://d261g450savmee.cloudfront.net`. (GenAI details tracked in Obsidian
`/genai multitenant-rag`; this file captures the **AWS/serverless** learnings.)

---

## What got built (all serverless, ap-south-1)

```
Browser
  │  https://d261g450savmee.cloudfront.net   (CloudFront dist EOV3277U5A8CF)
  ▼
CloudFront
  ├─ /*        → S3  praveen-blog-frontend   (React/Vite static site)
  └─ /api/*    → API Gateway HTTP API  (multitenant-api, pdp1o70aug)
                   ├─ POST /api/posts          → createpost Lambda
                   └─ $default (everything else)→ ask Lambda   (auth, chats, ask, users, tenants, read post)
                                                       │
createpost ── writes ──► S3 + DynamoDB ── enqueues ──► SQS FIFO (multitenant-ingestion.fifo)
                                                       │  (event source mapping, batch 1)
                                                       ▼
                                              ingest_worker Lambda
                                              chunk → Bedrock Titan (dense) + fastembed BM25 (sparse) → Qdrant Cloud
```

- **3 Lambdas, all container images** (`multitenant-createpost`, `multitenant-ingestworker`,
  `multitenant-ask`) — container (not zip) because fastembed/onnxruntime (~200MB) is painful as a Layer.
- **DynamoDB** (on-demand): `multitenant-users` (+`by_email` GSI for login), `-tenants`, `-posts`
  (SK post_id, GSI by_status), `-usage-logs` (TTL), `-chats`.
- **S3** `praveen-multitenant-content` (raw markdown), `praveen-blog-frontend` (the SPA).
- **SQS FIFO** with content-based dedup, `MessageGroupId=tenant_id` (per-tenant serialization).
- **Secrets Manager**: `multitenant/groq`, `multitenant/qdrant`, `multitenant/jwt`.
- **Bedrock** Titan Text v2 (embeddings). **Groq** Llama 3.3 70B (prod LLM), 8B (dev).
- **Qdrant Cloud** (vectors, hybrid dense+sparse RRF) — outside AWS, so Lambda stays outside a VPC.
- **CI/CD**: GitHub Actions builds the 3 images on native x86 runners → ECR (OIDC, no stored keys).
- **Auth**: custom JWT (email+password, bcrypt) — chosen over Cognito so it's testable in the LocalStack dev env.

## Commands Used (the ones worth keeping)

```bash
# ECR + build via GitHub Actions (native x86 avoids ARM->x86 cross-build pain)
aws ecr create-repository --repository-name multitenant-ask --image-scanning-configuration scanOnPush=true
# CI: aws-actions/configure-aws-credentials (OIDC) + amazon-ecr-login + docker build --platform linux/amd64

# Lambda from a container image
aws lambda create-function --function-name multitenant-ask --package-type Image \
  --code ImageUri=<acct>.dkr.ecr.ap-south-1.amazonaws.com/multitenant-ask:v1 \
  --role arn:...:role/multitenant-ask-role --timeout 60 --memory-size 2048
aws lambda update-function-code --function-name multitenant-ask --image-uri <...>:v1   # redeploy
aws lambda update-function-configuration --function-name multitenant-ask --environment "Variables={...}"

# SQS -> Lambda trigger
aws lambda create-event-source-mapping --function-name multitenant-ingestworker \
  --event-source-arn arn:aws:sqs:...:multitenant-ingestion.fifo --batch-size 1

# API Gateway HTTP API: a $default route catches every path not matched by a specific route
aws apigatewayv2 create-route --api-id <id> --route-key '$default' --target integrations/<ask_int>
aws lambda add-permission --function-name multitenant-createpost --principal apigateway.amazonaws.com \
  --action lambda:InvokeFunction --source-arn 'arn:aws:execute-api:...:<api>/*/*'

# Add a GSI to an existing table (online, backfills; a few min even when empty)
aws dynamodb update-table --table-name multitenant-users \
  --attribute-definitions AttributeName=email,AttributeType=S \
  --global-secondary-index-updates '[{"Create":{"IndexName":"by_email","KeySchema":[{"AttributeName":"email","KeyType":"HASH"}],"Projection":{"ProjectionType":"ALL"}}}]'

# Front a Lambda Function URL with CloudFront (streaming path) — needs OAC signing
aws cloudfront create-origin-access-control --origin-access-control-config \
  '{"Name":"...","SigningProtocol":"sigv4","SigningBehavior":"always","OriginAccessControlOriginType":"lambda"}'
```

## Things That Broke & How I Fixed Them (the real learning)

1. **Dockerfile COPY context.** `COPY requirements.txt` vs `COPY common/` assumed different build
   contexts → build failed on the first COPY. Fix: one consistent context (`lambdas/`) and path every
   COPY from it (`COPY ask/requirements.txt`). Lesson: pin the build context and be consistent.

2. **Structured logger rejected kwargs.** `get_logger()` returned a stdlib `logging.Logger`, but every
   handler called `log.info("msg", user_id=...)`. Stdlib rejects arbitrary kwargs → `TypeError` on the
   first log. Fix: a thin adapter routing kwargs into `extra={}`. Caught on the very first invoke — **test
   by invoking, not just syntax-checking.**

3. **Python Lambda has NO native response streaming.** `streamifyResponse` is Node-only. Our
   `handler(event, response_stream)` crashed (`LambdaContext has no attribute write`). Options for Python:
   Lambda Web Adapter (LWA) or a custom runtime. We used **LWA** (FastAPI + uvicorn, plain base image;
   LWA ships the Runtime Interface Client). `AWS_LWA_INVOKE_MODE=response_stream` for a streaming Function
   URL; `buffered` to run behind API Gateway. We ship buffered + a client-side typewriter.

4. **Function URL public access is blocked at the account level.** A correct `Principal:"*"` policy still
   returned 403 (account "Block Public Access" — a good default). Went to **AWS_IAM** auth. To let
   CloudFront reach an IAM-auth Function URL you use **OAC** — but **OAC + Lambda + POST needs the client to
   send `x-amz-content-sha256` (body hash)**; CloudFront won't compute it → `InvalidSignatureException`.
   Because we're buffered, we sidestepped all of it: route `/api/ask` through **API Gateway** (no OAC/body
   hash). The Function URL + OAC path is only needed for true edge streaming (a future item).

5. **Relevance gate: RRF score is a rank, not a relevance.** Thresholding the RRF fused score let
   keyword-only matches through ("chennai" → coffee). Fix: gate on **absolute dense cosine** (a real 0..1
   similarity). Then made it an **LLM-as-judge in one call** with a low floor (0.20) that only skips the LLM
   for clearly-irrelevant queries; the prompt does answer / clarify / decline.

6. **dev/prod IAM parity gap (the sharpest one).** Listing posts worked in prod but *reading* one post
   404'd. The ask role had `dynamodb:Query` on posts but not `GetItem` (read-one uses get_item). AccessDenied
   got swallowed as "not found", and CloudFront's SPA 404→/index.html masked it as the app shell.
   **LocalStack ignores IAM, so it worked in dev.** Fix: add `GetItem`; and make the handler log + return 500
   on a ClientError instead of masquerading as 404. Lesson: **least-privilege gaps only surface on real AWS —
   audit every DynamoDB action (get vs query vs scan) per handler when promoting.**

## What I Learned (in my own words)

- **Split Lambdas on trigger + resource profile, not on routes.** createpost = fast sync API-GW function
  (512MB); ingest_worker = memory/CPU-heavy async SQS consumer (2048MB) so the writer never waits on embedding;
  ask = the query path. Not one-Lambda-per-URL (nanoservices).
- **Lambda memory is the only performance dial — it scales CPU too.** ~1769MB ≈ 1 vCPU. The 2048MB
  functions were for CPU (embedding math), not RAM (they used ~230MB). More memory can be *cheaper* for
  CPU-bound work because GB-seconds drop if it finishes faster.
- **Async decouple with SQS** = fast write path + serialized-per-tenant ingest (`MessageGroupId`).
- **API Gateway `$default` route** is the clean way to send "everything else" to one Lambda while keeping a
  couple of specific routes (POST /api/posts → createpost).
- **OIDC for CI** beats stored access keys — GitHub exchanges a short-lived token; the role is pinned to the
  `main` branch.
- **LocalStack** gives a faithful, free, isolated dev mirror of SQS/DynamoDB/S3 (pin `3.8.1` — newer images
  demand a paid token even for core services). But it **does not enforce IAM** and can't emulate Bedrock/Cognito
  — so keep an eye on those in the promotion.
- **Tune thresholds from data, not guesses.** The relevance floor (`RETRIEVAL_FLOOR`, 0.15) is
  a cheap pre-filter; the LLM is the real judge, so bias the floor LOW (a false-negative there
  hard-declines a real question with no recourse). Every query logs `top_dense` + `result_type`,
  so calibrate empirically once there's traffic — set the floor just below the lowest score of an
  answered query:  `filter msg="relevance" and result_type="answered" | stats min(top_dense)`.
- **Idle cost of pure serverless ≈ $0** (only Secrets Manager ~$1.20/mo for 3 secrets). vs the old
  ALB+RDS+EC2 stack that was ~$10/mo even idle. That cost floor is why we deleted the whole VPC/ECS stack.

## Containers vs Serverless (when to use each)

| | Containers (ECS Fargate — Stage 4) | Serverless (Lambda — this stage) |
|---|---|---|
| Billing | per running task (idle still costs) | per request + GB-second (idle ≈ $0) |
| Cold start | none (always warm) | yes (fastembed load ~2.5s here) |
| Long/streaming/websocket | natural | awkward (needs LWA / custom runtime) |
| Ops | you manage the service/scaling | fully managed, event-driven |
| Best for | steady traffic, long connections, heavy deps | spiky/occasional traffic, event-driven, demo apps |

For an occasionally-demoed portfolio app, **serverless wins on cost** — the deciding factor here.

## Questions That Came Up (answered)

- *One LLM call or two for "LLM-as-judge"?* **One** — relevance judgment is baked into the same system
  prompt; the model decides + answers/declines in a single generation. (A separate grader call would double cost.)
- *Is inlining an example one-shot prompting?* It's zero-shot with an inline hint; a domain-specific example
  in a **universal** prompt biases other tenants — keep the relevance rule domain-neutral.
- *Prod model?* 70B (`llama-3.3-70b-versatile`); dev 8B for speed/limits — one `GROQ_MODEL` env var.
