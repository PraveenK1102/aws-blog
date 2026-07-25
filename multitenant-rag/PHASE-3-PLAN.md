# Phase 3 — Deployment Plan

Deploy the 3 Lambdas + wire up API Gateway + CloudFront routing.

**Realistic time:** 4-8 hours split across 3 sessions.
**Do not attempt in one go** — Docker builds + platform issues will burn time.

---

## Session A (~90 min) — ECR + IAM + Docker Build

### 3A.1 Set Up ECR Repositories

Create three ECR repos, one per Lambda:

```bash
for repo in multitenant-createpost multitenant-ingestworker multitenant-ask; do
  aws ecr create-repository \
    --repository-name $repo \
    --image-scanning-configuration scanOnPush=true \
    --region ap-south-1
done
```

Login Docker to ECR:
```bash
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin 557690605487.dkr.ecr.ap-south-1.amazonaws.com
```

### 3A.2 Create IAM Roles

Three roles needed — one per Lambda. Each with minimum permissions.

**createPostLambda role:**
- Trust: lambda.amazonaws.com
- Managed: AWSLambdaBasicExecutionRole
- Inline: allow dynamodb PutItem/Query on multitenant-posts + multitenant-users
        S3 PutObject/DeleteObject on praveen-multitenant-content
        SQS SendMessage on multitenant-ingestion.fifo

**ingestWorkerLambda role:**
- Trust: lambda.amazonaws.com
- Managed: AWSLambdaBasicExecutionRole
- Inline: S3 GetObject on praveen-multitenant-content
        DynamoDB GetItem/UpdateItem on multitenant-posts, GetItem on multitenant-users
        Bedrock InvokeModel on amazon.titan-embed-text-v2
        SecretsManager GetSecretValue on multitenant/qdrant
        SQS ReceiveMessage/DeleteMessage on ingestion queue (for SQS trigger)

**askLambda role:**
- Trust: lambda.amazonaws.com
- Managed: AWSLambdaBasicExecutionRole
- Inline: DynamoDB GetItem on multitenant-tenants, multitenant-users
        DynamoDB PutItem on multitenant-usage-logs
        Bedrock InvokeModel on amazon.titan-embed-text-v2
        SecretsManager GetSecretValue on multitenant/qdrant, multitenant/groq

Create these via Console (Roles → Create role → AWS service → Lambda), OR via CLI (write JSON policy files first).

Detailed IAM JSON examples in Appendix A below.

### 3A.3 Docker Build — The Hard Part

This is where you may hit platform issues. Your Mac is Apple Silicon (ARM64) but Lambda runs x86_64.

**Approach 1: Docker buildx (if installed)**
```bash
docker buildx version   # verify installed
cd multitenant-rag
docker buildx build --platform linux/amd64 -t multitenant-createpost:latest -f lambdas/create_post/Dockerfile lambdas/ --load
```

**Approach 2: Standard docker build with --platform**
```bash
cd multitenant-rag
docker build --platform linux/amd64 -t multitenant-createpost:latest -f lambdas/create_post/Dockerfile lambdas/
```

Note: build context is `lambdas/` because Dockerfile does `COPY common/` and `COPY create_post/`.

**If build fails:**
- Common issue: fastembed on ARM Mac fails to compile onnxruntime dependencies
- Solution: build on EC2 (as we did in Stage 4) — spin up t3.medium x86 briefly
- Alternative: use GitHub Actions runner (ubuntu-latest is x86)

### 3A.4 Push to ECR

```bash
# Tag with ECR URL
ACCOUNT_ID=557690605487
REGION=ap-south-1
REGISTRY=${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

for name in createpost ingestworker ask; do
  docker tag multitenant-${name}:latest ${REGISTRY}/multitenant-${name}:v1
  docker push ${REGISTRY}/multitenant-${name}:v1
done
```

**Verify:**
```bash
aws ecr describe-images --repository-name multitenant-createpost --region ap-south-1
```

### 3A.5 Expected Issues in Session A

1. **Docker buildx not installed** → `brew install docker-buildx` or fall back to plain build with --platform
2. **fastembed compile fails on ARM** → build on EC2 or use GitHub Actions runner
3. **ECR push permission denied** → verify Docker login step
4. **Image too large** (>10 GB) → shouldn't happen, but if so trim fastembed cache

**Stop when:** all 3 images visible in ECR console.

---

## Session B (~90 min) — Lambda Functions

### 3B.1 Get Resource ARNs

```bash
# ECR image URIs
aws ecr describe-repositories --region ap-south-1 --query "repositories[?contains(repositoryName,'multitenant')].{Name:repositoryName,Uri:repositoryUri}"

# SQS queue URL/ARN
aws sqs get-queue-url --queue-name multitenant-ingestion.fifo --region ap-south-1
aws sqs get-queue-attributes --queue-url <URL> --attribute-names QueueArn --region ap-south-1

# IAM role ARNs
aws iam get-role --role-name multitenant-createpost-role
aws iam get-role --role-name multitenant-ingestworker-role
aws iam get-role --role-name multitenant-ask-role
```

### 3B.2 Create Lambda Functions

**createPostLambda:**
```bash
aws lambda create-function \
  --function-name multitenant-createpost \
  --package-type Image \
  --code ImageUri=557690605487.dkr.ecr.ap-south-1.amazonaws.com/multitenant-createpost:v1 \
  --role arn:aws:iam::557690605487:role/multitenant-createpost-role \
  --timeout 30 \
  --memory-size 512 \
  --environment "Variables={
    S3_CONTENT_BUCKET=praveen-multitenant-content,
    POSTS_TABLE=multitenant-posts,
    USERS_TABLE=multitenant-users,
    INGESTION_QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/557690605487/multitenant-ingestion.fifo,
    LOG_LEVEL=INFO
  }" \
  --region ap-south-1
```

**ingestWorkerLambda:**
```bash
aws lambda create-function \
  --function-name multitenant-ingestworker \
  --package-type Image \
  --code ImageUri=557690605487.dkr.ecr.ap-south-1.amazonaws.com/multitenant-ingestworker:v1 \
  --role arn:aws:iam::557690605487:role/multitenant-ingestworker-role \
  --timeout 300 \
  --memory-size 2048 \
  --environment "Variables={
    S3_CONTENT_BUCKET=praveen-multitenant-content,
    POSTS_TABLE=multitenant-posts,
    USERS_TABLE=multitenant-users,
    LOG_LEVEL=INFO
  }" \
  --region ap-south-1
```

**askLambda:**
```bash
aws lambda create-function \
  --function-name multitenant-ask \
  --package-type Image \
  --code ImageUri=557690605487.dkr.ecr.ap-south-1.amazonaws.com/multitenant-ask:v1 \
  --role arn:aws:iam::557690605487:role/multitenant-ask-role \
  --timeout 60 \
  --memory-size 2048 \
  --environment "Variables={
    TENANTS_TABLE=multitenant-tenants,
    USERS_TABLE=multitenant-users,
    USAGE_TABLE=multitenant-usage-logs,
    GROQ_MODEL=llama-3.3-70b-versatile,
    LOG_LEVEL=INFO
  }" \
  --region ap-south-1
```

**Memory settings explained:**
- createPost: 512MB — light workload
- ingestWorker: 2048MB — needs RAM for embeddings + BM25 model
- ask: 2048MB — same reason + Groq HTTP client

### 3B.3 Wire SQS → ingestWorker Trigger

```bash
aws lambda create-event-source-mapping \
  --function-name multitenant-ingestworker \
  --event-source-arn arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo \
  --batch-size 1 \
  --region ap-south-1
```

Note: FIFO queues require batch-size 1 to preserve MessageGroupId ordering, OR batch-size up to 10 if `ReportBatchItemFailures` used.

### 3B.4 Test Each Lambda Directly

Before wiring API Gateway, test each Lambda via CLI.

**Test ingestWorker with a mock SQS event:**
```bash
cat > /tmp/sqs-event.json << 'EOF'
{
  "Records": [{
    "body": "{\"tenant_id\":\"test-tenant\",\"user_id\":\"test-user\",\"post_id\":\"post_test001\",\"s3_key\":\"tenants/test-tenant/posts/post_test001.md\"}",
    "messageId": "test-msg-1"
  }]
}
EOF

# First seed a test doc in S3:
echo "# Test Post

## Section A

Some content about testing." > /tmp/test-post.md
aws s3 cp /tmp/test-post.md s3://praveen-multitenant-content/tenants/test-tenant/posts/post_test001.md

# Also seed test tenant + post metadata
# (see 3F seed data section)

# Invoke Lambda
aws lambda invoke \
  --function-name multitenant-ingestworker \
  --payload file:///tmp/sqs-event.json \
  --region ap-south-1 \
  /tmp/response.json

cat /tmp/response.json
```

Check CloudWatch logs if it fails.

### 3B.5 Expected Issues in Session B

1. **Cold start timeout** — first invocation takes 5-10s (model load)
   Fix: timeout should be at least 60s. Set higher initially.
2. **Missing env var** — will error at import time
   Fix: check CloudWatch log, add missing env var
3. **Permission denied on Bedrock/S3/DynamoDB** — IAM policy gap
   Fix: check CloudWatch, add missing permission
4. **fastembed model not found** — Dockerfile ENV mismatch
   Fix: verify FASTEMBED_CACHE_DIR in image = code expectation
5. **Qdrant connection timeout** — network latency Mumbai→London
   Fix: increase Lambda timeout to 120s for safety

**Stop when:** each Lambda invokable directly via CLI, ingestWorker successfully ingests a test doc into Qdrant.

---

## Session C (~2 hours) — API Gateway + CloudFront + Testing

### 3C.1 Create API Gateway HTTP API

```bash
aws apigatewayv2 create-api \
  --name multitenant-api \
  --protocol-type HTTP \
  --cors-configuration \
    'AllowOrigins=["https://d261g450savmee.cloudfront.net"],AllowMethods=["POST","GET","OPTIONS"],AllowHeaders=["Content-Type","X-User-Id"]' \
  --region ap-south-1
```

Save the `ApiId` and `ApiEndpoint` from response.

Create integration + route:
```bash
API_ID=<from above>
LAMBDA_ARN=arn:aws:lambda:ap-south-1:557690605487:function:multitenant-createpost

# Create integration
aws apigatewayv2 create-integration \
  --api-id $API_ID \
  --integration-type AWS_PROXY \
  --integration-uri $LAMBDA_ARN \
  --payload-format-version 2.0 \
  --region ap-south-1
# Save IntegrationId

# Grant API Gateway permission to invoke Lambda
aws lambda add-permission \
  --function-name multitenant-createpost \
  --statement-id apigw-invoke \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:ap-south-1:557690605487:$API_ID/*/*" \
  --region ap-south-1

# Create route
aws apigatewayv2 create-route \
  --api-id $API_ID \
  --route-key "POST /posts" \
  --target integrations/<IntegrationId> \
  --region ap-south-1

# Create default stage
aws apigatewayv2 create-stage \
  --api-id $API_ID \
  --stage-name '$default' \
  --auto-deploy \
  --region ap-south-1
```

### 3C.2 Create Lambda Function URL for ask

```bash
aws lambda create-function-url-config \
  --function-name multitenant-ask \
  --auth-type NONE \
  --invoke-mode RESPONSE_STREAM \
  --cors 'AllowOrigins=["https://d261g450savmee.cloudfront.net"],AllowMethods=["POST"],AllowHeaders=["Content-Type","X-User-Id"]' \
  --region ap-south-1
```

Save the `FunctionUrl` from response.

Also grant public invoke (for CloudFront to reach it):
```bash
aws lambda add-permission \
  --function-name multitenant-ask \
  --statement-id function-url-public \
  --action lambda:InvokeFunctionUrl \
  --principal '*' \
  --function-url-auth-type NONE \
  --region ap-south-1
```

### 3C.3 Update CloudFront

Add two new origins to distribution EOV3277U5A8CF:

1. API Gateway origin (for POST /posts)
2. Lambda Function URL origin (for POST /ask, streaming)

Behaviors:
- `/api/posts` → API Gateway origin (no cache, forward all)
- `/api/ask` → Lambda Function URL origin (no cache, streaming enabled)
- `/*` → S3 frontend (existing)

Because CloudFront config JSON is complex, do this via Python script or Console. See ARCHITECTURE.md for the pattern used in earlier stages.

**Critical settings for streaming (Lambda Function URL origin):**
- Origin request policy: Managed-AllViewerExceptHostHeader
- Cache policy: Managed-CachingDisabled
- Response headers policy: none (or CustomResponse without body modification)
- Origin protocol: HTTPS only
- Origin domain: `<function-url-id>.lambda-url.ap-south-1.on.aws` (no path, no scheme prefix)

### 3C.4 Seed Data

Create 5-10 mock tenants + users in DynamoDB.

**Batch write via CLI:**
```bash
cat > /tmp/seed-tenants.json << 'EOF'
{
  "multitenant-tenants": [
    {"PutRequest": {"Item": {"tenant_id":{"S":"doctor-rajesh"},"display_name":{"S":"Doctor Rajesh"},"domain":{"S":"healthcare"},"active":{"BOOL":true},"created_at":{"N":"1730000000"}}}},
    {"PutRequest": {"Item": {"tenant_id":{"S":"chef-priya"},"display_name":{"S":"Chef Priya"},"domain":{"S":"recipes"},"active":{"BOOL":true},"created_at":{"N":"1730000000"}}}},
    {"PutRequest": {"Item": {"tenant_id":{"S":"coder-arjun"},"display_name":{"S":"Coder Arjun"},"domain":{"S":"software"},"active":{"BOOL":true},"created_at":{"N":"1730000000"}}}}
  ]
}
EOF
aws dynamodb batch-write-item --request-items file:///tmp/seed-tenants.json --region ap-south-1

cat > /tmp/seed-users.json << 'EOF'
{
  "multitenant-users": [
    {"PutRequest": {"Item": {"user_id":{"S":"rajesh"},"tenant_id":{"S":"doctor-rajesh"},"display_name":{"S":"Rajesh"},"role":{"S":"admin"},"active":{"BOOL":true},"created_at":{"N":"1730000000"}}}},
    {"PutRequest": {"Item": {"user_id":{"S":"priya"},"tenant_id":{"S":"chef-priya"},"display_name":{"S":"Priya"},"role":{"S":"admin"},"active":{"BOOL":true},"created_at":{"N":"1730000000"}}}},
    {"PutRequest": {"Item": {"user_id":{"S":"arjun"},"tenant_id":{"S":"coder-arjun"},"display_name":{"S":"Arjun"},"role":{"S":"admin"},"active":{"BOOL":true},"created_at":{"N":"1730000000"}}}}
  ]
}
EOF
aws dynamodb batch-write-item --request-items file:///tmp/seed-users.json --region ap-south-1
```

Then create 3-5 posts per tenant via the API (once API Gateway is working).

### 3C.5 End-to-End Test

1. **Create a post as user "rajesh":**
   ```bash
   curl -X POST https://d261g450savmee.cloudfront.net/api/posts \
     -H "Content-Type: application/json" \
     -H "X-User-Id: rajesh" \
     -d '{
       "title": "Amoxicillin Notes",
       "content": "# Amoxicillin\n\n## Side Effects\n\nCommon: nausea, rash.\n\n## Dosage\n\nAdult: 500mg."
     }'
   ```
   Expected: `{"post_id":"post_xxx","status":"pending"}`

2. **Wait 30 seconds for ingestion.** Check status:
   ```bash
   aws dynamodb get-item \
     --table-name multitenant-posts \
     --key '{"tenant_id":{"S":"doctor-rajesh"},"post_id":{"S":"post_xxx"}}' \
     --region ap-south-1
   ```
   Expected: `ingestion_status: "indexed"`, `chunk_count > 0`

3. **Ask a question:**
   ```bash
   curl -X POST https://d261g450savmee.cloudfront.net/api/ask \
     -H "Content-Type: application/json" \
     -H "X-User-Id: rajesh" \
     -d '{"question":"What are the side effects of Amoxicillin?"}'
   ```
   Expected: Streaming NDJSON response with content chunks and final citations.

4. **Test tenant isolation:**
   Same question as user "priya":
   ```bash
   curl -X POST https://d261g450savmee.cloudfront.net/api/ask \
     -H "Content-Type: application/json" \
     -H "X-User-Id: priya" \
     -d '{"question":"What are the side effects of Amoxicillin?"}'
   ```
   Expected: "Chef Priya hasn't written about this topic."

5. **Direct Qdrant verification (from local Python):**
   ```python
   from qdrant_client import QdrantClient
   client = QdrantClient(url=<URL>, api_key=<KEY>)
   info = client.get_collection("multitenant_chunks")
   print(info.points_count)  # should be > 0
   
   # Verify no cross-tenant leakage
   from qdrant_client.models import Filter, FieldCondition, MatchValue
   results = client.scroll(
     "multitenant_chunks",
     scroll_filter=Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value="doctor-rajesh"))]),
     limit=100
   )
   for point in results[0]:
     assert point.payload["tenant_id"] == "doctor-rajesh"
   ```

### 3C.6 Expected Issues in Session C

1. **CloudFront takes 5-15 min to deploy config changes** — patience
2. **Streaming buffered by CloudFront** — check origin/cache policies
3. **CORS errors from browser** — verify Allow-Origin includes CloudFront domain
4. **Lambda cold start on first ask** — first request takes 5-10s, subsequent are fast
5. **Qdrant returns empty results** — check filter syntax, verify data ingested

**Stop when:** end-to-end curl test works and tenant isolation verified.

---

## Success Criteria for Phase 3

- [ ] All 3 Lambdas exist and are Active
- [ ] SQS trigger fires ingestWorker on new messages
- [ ] Creating a post ingests it into Qdrant (check DynamoDB status = "indexed")
- [ ] Chat query returns answers from Qdrant chunks
- [ ] Tenant isolation verified (asking as A gets no B content)
- [ ] Streaming works via CloudFront (tokens appear progressively)
- [ ] Usage logged to DynamoDB usage-logs table
- [ ] Total AWS spend for testing: < $2

---

## Appendix A — IAM Policy JSON Examples

### createPostLambda inline policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["dynamodb:PutItem", "dynamodb:Query"],
      "Resource": "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-posts"
    },
    {
      "Effect": "Allow",
      "Action": "dynamodb:GetItem",
      "Resource": "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-users"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::praveen-multitenant-content/*"
    },
    {
      "Effect": "Allow",
      "Action": "sqs:SendMessage",
      "Resource": "arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo"
    }
  ]
}
```

### ingestWorkerLambda inline policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::praveen-multitenant-content/*"
    },
    {
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-posts"
    },
    {
      "Effect": "Allow",
      "Action": "dynamodb:GetItem",
      "Resource": "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-users"
    },
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:ap-south-1::foundation-model/amazon.titan-embed-text-v2:0"
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:ap-south-1:557690605487:secret:multitenant/qdrant*"
    },
    {
      "Effect": "Allow",
      "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
      "Resource": "arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo"
    }
  ]
}
```

### askLambda inline policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "dynamodb:GetItem",
      "Resource": [
        "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-tenants",
        "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-users"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "dynamodb:PutItem",
      "Resource": "arn:aws:dynamodb:ap-south-1:557690605487:table/multitenant-usage-logs"
    },
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:ap-south-1::foundation-model/amazon.titan-embed-text-v2:0"
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:ap-south-1:557690605487:secret:multitenant/qdrant*",
        "arn:aws:secretsmanager:ap-south-1:557690605487:secret:multitenant/groq*"
      ]
    }
  ]
}
```

---

## Debugging Checklist

If something breaks after deployment:

1. **Check CloudWatch logs first** — every Lambda writes structured JSON logs
   ```bash
   aws logs tail /aws/lambda/multitenant-createpost --follow --region ap-south-1
   ```

2. **Verify environment variables:**
   ```bash
   aws lambda get-function-configuration --function-name multitenant-createpost --region ap-south-1
   ```

3. **Test Lambda directly (bypass API Gateway):**
   ```bash
   aws lambda invoke --function-name multitenant-createpost \
     --payload '{"headers":{"x-user-id":"rajesh"},"body":"{\"title\":\"test\",\"content\":\"hello\"}"}' \
     --region ap-south-1 /tmp/resp.json
   cat /tmp/resp.json
   ```

4. **Check IAM permissions:**
   ```bash
   aws iam list-attached-role-policies --role-name multitenant-createpost-role
   aws iam list-role-policies --role-name multitenant-createpost-role
   ```

5. **Check DynamoDB reads:**
   ```bash
   aws dynamodb get-item --table-name multitenant-users --key '{"user_id":{"S":"rajesh"}}' --region ap-south-1
   ```

6. **Check Qdrant from local Python:**
   ```python
   from qdrant_client import QdrantClient
   c = QdrantClient(url=<URL>, api_key=<KEY>)
   print(c.get_collections())
   ```

7. **Check Bedrock is invokable:**
   ```bash
   echo '{"inputText":"test"}' > /tmp/body.json
   aws bedrock-runtime invoke-model \
     --model-id amazon.titan-embed-text-v2:0 \
     --body fileb:///tmp/body.json \
     --region ap-south-1 /tmp/resp.json
   ```
