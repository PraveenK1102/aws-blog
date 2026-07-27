# Infra fixes — scale-review "Fix now" items that live in AWS, not the repo

Companion to the code changes on branch `fix/scale-review-batch`. These are the parts that
can't be fixed by editing code — they're AWS config. Each has: what/why, commands (review the
placeholders against your account), and how to verify. **Nothing here is applied yet.**

Account `557690605487` · region `ap-south-1` · CloudFront `EOV3277U5A8CF` · API GW `pdp1o70aug`
· Lambdas `multitenant-ask` / `multitenant-ingestworker` / `multitenant-createpost` · queue
`multitenant-ingestion.fifo`.

---

## 1. CloudFront error-masking on `/api/*`  (High)

**Why:** the SPA deep-link fallback (`403/404 → /index.html`, status 200) is configured as
**distribution-wide `CustomErrorResponses`**, so it *also* rewrites real `/api/*` errors into
`200 + index.html`. (The client-side safety net is already in `api.js` — it now throws if a 2xx
body is HTML — but the proper fix is to stop CloudFront masking in the first place.)

**Key gotcha:** `CustomErrorResponses` are **distribution-level**, not per-behavior — you can't
just "scope them to the SPA behavior." The correct fix is to do SPA routing with a **CloudFront
Function** on the default behavior and **remove** the blanket error responses.

**Fix:**
1. Create a CloudFront Function (viewer-request) that rewrites only page routes:
   ```js
   // spa-routing  (viewer request)
   function handler(event) {
     var req = event.request;
     var uri = req.uri;
     if (uri.startsWith('/api/')) return req;   // never touch API calls
     if (uri.includes('.')) return req;         // real asset (has an extension)
     req.uri = '/index.html';                   // SPA route -> app shell
     return req;
   }
   ```
   ```bash
   aws cloudfront create-function --name spa-routing \
     --function-config Comment="SPA routing, leaves /api/* alone",Runtime=cloudfront-js-2.0 \
     --function-code fileb://spa-routing.js
   aws cloudfront publish-function --name spa-routing --if-match <ETag-from-create>
   ```
2. Attach it to the **default** cache behavior as a `viewer-request` function association, and
   **delete the `CustomErrorResponses`** (the 403/404→/index.html entries) — this is a
   `get-distribution-config` → edit JSON → `update-distribution --if-match <ETag>` flow.

**Verify:** `curl -i https://<dist>/api/does-not-exist` should return the **real** 404 (JSON),
not 200 + HTML. Deep link `https://<dist>/u/<someUser>` should still load the app.

---

## 2. SQS dead-letter queue + redrive  (High)

**Why:** today a poison message retries forever and, on the FIFO queue, blocks every later post
for that tenant until the 4-day retention drops it — silently. A DLQ parks it after N tries.

```bash
# 1. Create the DLQ (FIFO, to match the source).
aws sqs create-queue --queue-name multitenant-ingestion-dlq.fifo \
  --attributes FifoQueue=true

DLQ_ARN=$(aws sqs get-queue-attributes --queue-url <dlq-url> \
  --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

# 2. Point the main queue's redrive policy at it (maxReceiveCount=5).
aws sqs set-queue-attributes --queue-url <main-queue-url> \
  --attributes '{"RedrivePolicy":"{\"deadLetterTargetArn\":\"'$DLQ_ARN'\",\"maxReceiveCount\":\"5\"}"}'
```
Also consider **partial-batch responses** if batch size ever > 1: enable `ReportBatchItemFailures`
on the event source mapping and return `{"batchItemFailures":[...]}` from the handler.

**Verify:** send a message referencing a missing S3 key; after 5 receives it should land in the DLQ.

---

## 3. CloudWatch alarms  (High — highest-value ops upgrade)

**Why:** today the only alarm is the $5 budget (a *lagging cost* signal). You can't see errors or
throttles. Add these (all free-tier-friendly). Route to an SNS topic → your email.

```bash
SNS=$(aws sns create-topic --name multitenant-ops-alerts --query TopicArn --output text)
aws sns subscribe --topic-arn $SNS --protocol email --notification-endpoint praveen.kr@zohocorp.com

for FN in multitenant-ask multitenant-ingestworker multitenant-createpost; do
  aws cloudwatch put-metric-alarm --alarm-name "$FN-errors" \
    --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=$FN \
    --statistic Sum --period 300 --evaluation-periods 1 --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching \
    --alarm-actions $SNS
  aws cloudwatch put-metric-alarm --alarm-name "$FN-throttles" \
    --namespace AWS/Lambda --metric-name Throttles --dimensions Name=FunctionName,Value=$FN \
    --statistic Sum --period 300 --evaluation-periods 1 --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching \
    --alarm-actions $SNS
done

# DLQ depth (after step 2 exists):
aws cloudwatch put-metric-alarm --alarm-name "ingestion-dlq-not-empty" \
  --namespace AWS/SQS --metric-name ApproximateNumberOfMessagesVisible \
  --dimensions Name=QueueName,Value=multitenant-ingestion-dlq.fifo \
  --statistic Maximum --period 300 --evaluation-periods 1 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold --alarm-actions $SNS

# API Gateway 5xx rate:
aws cloudwatch put-metric-alarm --alarm-name "apigw-5xx" \
  --namespace AWS/ApiGateway --metric-name 5xx --dimensions Name=ApiId,Value=pdp1o70aug \
  --statistic Sum --period 300 --evaluation-periods 1 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching \
  --alarm-actions $SNS
```
**Later (~100K):** emit CloudWatch EMF from the existing `_log_usage`/`relevance` log lines to get
p95 latency, cache-hit rate, and 429 count as real metrics + one dashboard.

**Verify:** confirm the SNS email subscription, then check the alarms show `OK` in the console.

---

## 4. (Optional, when the user table grows) Globally-sorted directory GSI

The code fix denormalized `domain` onto the user row and paginates the scan, which removes the
N+1 and the 1 MB truncation. But **Scan order isn't sorted**, so the name-sort is per-page. For a
truly name-sorted, cheap directory at large scale, add a GSI and query it instead of scanning:
- GSI `by_directory`: PK = a constant bucket (e.g. `"DIRECTORY"` or a shard `"DIR#<0-9>"` to avoid
  a hot partition), SK = `display_name` (lowercased). Write it on the user row at signup.
- `list_profiles` then does `query(IndexName="by_directory", ...)` with the cursor — already sorted.
Not needed now (few users); revisit past ~10K users.

---

## 5. Account erasure / GDPR — scaffold (Critical, Large — design only for now)

Not built in this pass (it's a multi-store orchestration). Shape when you do it:
- `DELETE /api/account` → set the user `active=false` immediately (disappears + audit/grace window),
  then enqueue an erasure job.
- A **Step Functions** state machine (or an SQS worker) that, idempotently:
  1. `query` posts by `tenant_id` → delete each DDB row.
  2. delete all S3 objects under `tenants/{tenant_id}/posts/` — **including every version**
     (versioning is on; a plain delete only writes a delete-marker), so `list-object-versions`
     then delete each `VersionId`.
  3. Qdrant delete-by-filter `tenant_id` on **both** `multitenant_chunks` and `multitenant_query_cache`.
  4. delete usage-logs for the tenant; delete the tenant row and the user row.
  5. chats: needs a **GSI on `tenant_id`** for `multitenant-chats` (today it's keyed by the visitor),
     to find and scrub chats *others* held with this profile.
- Also expose a data-export endpoint (GDPR Art.20).

---

## 6. Safe deploys (Medium) — the stale pipeline is already removed in this branch

`deploy.yml` (dead ECS pipeline) is deleted on this branch. For safe Lambda deploys:
- Publish a version, point an alias `live` at it, shift traffic with **CodeDeploy** weighted
  (canary, e.g. 10% for 5 min) gated on the alarms in §3 for **automatic rollback**.
- Add a post-deploy smoke test: `GET /health` + one real `/api/ask`, before shifting 100%.
- Deploy strictly by immutable SHA; stop moving `:latest`/`:v1` for prod (drop those tags in
  `build-lambdas.yml` or reserve them for non-prod).
- Long-term: codify all of the above (and §1–§4) in **Terraform/CDK** (AWS-learning Stage 6) so
  the infra is reproducible, not tribal knowledge.
