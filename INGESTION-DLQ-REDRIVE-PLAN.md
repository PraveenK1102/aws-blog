# Ingestion DLQ / Redrive — Deployed Configuration

**Status: DEPLOYED AND VERIFIED IN PRODUCTION.**
**Applied:** 2026-08-27, after explicit user approval of the AWS charges.

> All six steps applied in order and verified against AWS. Every post-deployment check
> passed. The source queue's retention, visibility timeout, `BatchSize`, batching window and
> `FunctionResponseTypes`, and the worker's timeout/memory, are all unchanged.

## 1. Queue state BEFORE deployment — verified against AWS, not assumed

```
Queue      multitenant-ingestion.fifo
ARN        arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo
FifoQueue                     true
ContentBasedDeduplication     true
DeduplicationScope            queue
FifoThroughputLimit           perQueue
MessageRetentionPeriod        345600  (4 days)
VisibilityTimeout             300
RedrivePolicy                 null      <-- NO DLQ
RedriveAllowPolicy            null
Visible / InFlight            0 / 0

Event source mapping  60e4e50a-eb3b-4bae-b6db-91601c5e3730   State Enabled
BatchSize                     1
MaximumBatchingWindow         0
ScalingConfig                 null      <-- concurrency UNBOUNDED
FunctionResponseTypes         []        <-- ReportBatchItemFailures NOT enabled

Lambda multitenant-ingestworker   Timeout 300s   Memory 2048MB
DeadLetterConfig              null
ReservedConcurrentExecutions  null
```

Every expected value in the brief matched AWS. At that point **no DLQ existed** — the account
held exactly one SQS queue. Section 12 records the applied end state.

## 2. Prior failure evidence
The 268-post curated corpus ingestion produced **8 Titan `ThrottlingException` events** on
`InvokeModel`. All recovered — but only because SQS redelivered the failed messages. Three
tenant groups were briefly blocked and drained on the 300-second visibility cycle. Zero
permanent failures, zero poison messages.

**That recovery was luck of the failure mode, not a safety property.** The throttles were
transient; a genuinely poisonous message would have behaved completely differently.

**Why the redelivery works at all** — `ingest_worker/handler.py` logs the error and then
`raise  # let SQS retry`. The exception escapes the handler, so Lambda reports the batch as
failed and `ApproximateReceiveCount` increments. This is the precondition for a
`maxReceiveCount` policy to function; had the handler swallowed the exception, a DLQ would
never receive anything. **Verified in code, not assumed.**

## 3. Failure mode without a DLQ
With `RedrivePolicy` null, a permanently failing message is redelivered until source
retention expires: **4 days at a 300-second visibility timeout ≈ 1,150 attempts**. Because
the queue is FIFO and `MessageGroupId = tenant_id`, that message **blocks its entire tenant
group** for the whole period — every later post by that user stays unindexed — while
re-burning Titan quota on every attempt.

At 268 posts this was a tolerable risk. At **1,800 posts across ~100 tenant groups** it is
not: the exposure scales with both the number of messages and the number of independently
blockable groups.

## 4. DLQ design
```
Name        multitenant-ingestion-dlq.fifo
ARN         arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion-dlq.fifo
FifoQueue                   true            (mandatory: a FIFO source can only
                                             dead-letter to a FIFO target)
ContentBasedDeduplication   true            (mirrors the source)
MessageRetentionPeriod      1209600         (14 days)
```
DLQ retention (14 d) is deliberately **longer than source retention** (4 d) so the evidence
outlives the window in which the problem occurred.

## 5. `maxReceiveCount = 5` rationale
Five SQS deliveries before isolation. The prior run showed transient Titan throttles clearing
well inside that budget, so 5 is comfortably above the observed transient-failure depth while
still isolating a genuine poison message in **≈25 minutes** (5 × 300 s visibility) instead of
4 days.

**Two distinct retry layers — do not conflate them.**

| Layer | Budget | Controlled by |
|---|---|---|
| **SQS delivery budget** | up to **5 deliveries**, then the message moves to the DLQ | `maxReceiveCount` |
| **SDK-internal request retries** | several `InvokeModel` attempts *within a single Lambda invocation* | botocore/boto3 defaults |

The observed throttle log read *"reached max retries: 4"*, which is the **SDK** layer, not the
SQS layer. It is therefore **incorrect** to describe the effective Titan attempt count as
simply "5 total Titan API attempts" — the true figure is 5 deliveries multiplied by whatever
the SDK does inside each one.

No application-level retry loop exists in the worker (verified in code), none was added, and
boto3 retry configuration was deliberately **not** changed in this task.

## 6. `MaximumConcurrency = 2` rationale
Currently unset, i.e. unbounded fan-out across message groups. With ~100 tenant groups in the
stress corpus, unbounded scaling would multiply concurrent `InvokeModel` calls against exactly
the Titan quota that already threw 8 throttles at 25 groups.

Two lets two different tenant groups progress in parallel while preserving **same-tenant FIFO
ordering**, and it matches the value already used successfully during the earlier 50-post seed.
This experiment measures retrieval quality at scale, not ingestion throughput, so bounded and
reliable beats fast. **Architect decision: exactly 2** — not chosen independently.

## 7. FIFO behaviour after redrive — stated precisely
When a message exceeds `maxReceiveCount` it is moved to the DLQ, and SQS can then deliver the
**next** message in that `MessageGroupId`. That is what unblocks the group.

What is **not** claimed: AWS provides no ordering guarantee that survives a DLQ round trip. A
message redriven back to the source re-enters at the tail, so it will be processed **after**
messages that were behind it originally. For this ingestion that is harmless — each message is
an independent `index this post` instruction, and the worker is already idempotent
(delete-by-`post_id` then upsert). Ordering matters here only in that a *blocked* group makes
no progress at all.

**Deduplication caveat:** the source has `ContentBasedDeduplication=true`, so FIFO applies a
deduplication interval to identical message bodies. Operators should understand that interval
when re-submitting the same payload. This is **not** a mandatory waiting period before
`StartMessageMoveTask` — see §9 for the corrected redrive semantics.

## 8. `RedriveAllowPolicy`
```json
{"redrivePermission": "byQueue",
 "sourceQueueArns": ["arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo"]}
```
Exactly one source ARN, no wildcard. No other queue can dead-letter into this DLQ.

## 9. Manual redrive procedure — operator-driven, never automatic
1. **Alarm fires** (`multitenant-ingestion-dlq-not-empty`).
2. **Inspect** — read message attributes and correlate `post_id` with CloudWatch
   `/aws/lambda/multitenant-ingestworker`. Do not dump payload bodies into a report.
3. **Diagnose and fix** the underlying cause (bad S3 key, oversized content, Titan quota,
   Qdrant outage).
4. **Redrive with the native SQS mechanism:**
   ```
   aws sqs start-message-move-task \
     --source-arn arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion-dlq.fifo \
     --destination-arn arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo \
     --region ap-south-1
   ```
5. **Confirm** the post reaches `ingestion_status = indexed` and the DLQ returns to empty.

No custom replay service is built, and nothing is redriven automatically — a message reaches
the DLQ precisely because it needs a human decision.

**Redrive semantics, stated correctly.** An earlier draft of this document said operators must
always wait five minutes before `StartMessageMoveTask`. **That was wrong and is retracted** —
it is not a mandatory prerequisite. What is actually true:

* FIFO content-based deduplication has a deduplication interval operators should be aware of
  when re-submitting identical message bodies.
* A DLQ move / redrive changes message identity and enqueue semantics.
* Native SQS redrive (`StartMessageMoveTask`) is the supported mechanism.
* Redriven messages may interleave with newly produced messages.
* **Original end-to-end ordering across a DLQ/redrive cycle must NOT be claimed.**

This is acceptable here because ingestion is idempotent by post identity — the worker deletes
by `post_id` and re-upserts — so a re-ordered replay converges to the same index state.

## 10. CloudWatch alarms — CREATED
| | Alarm A | Alarm B |
|---|---|---|
| Name | `multitenant-ingestion-dlq-not-empty` | `multitenant-ingestion-oldest-message-age` |
| Namespace | `AWS/SQS` | `AWS/SQS` |
| Metric | `ApproximateNumberOfMessagesVisible` | `ApproximateAgeOfOldestMessage` |
| Dimension | `QueueName = multitenant-ingestion-dlq.fifo` | `QueueName = multitenant-ingestion.fifo` |
| Statistic / Period | Maximum / 60 s | Maximum / 60 s |
| Evaluation | 1 period | 1 period |
| Threshold | `>= 1` | `> 900` seconds |
| Missing data | `notBreaching` | `notBreaching` |
| Actions | `blog-alarms` SNS topic | `blog-alarms` SNS topic |

**How to read each alarm — they are not equivalent.**

**Alarm A** (`ApproximateNumberOfMessagesVisible >= 1` on the DLQ) means a message has been
dead-lettered and **requires operator attention**. This is the strong failure signal.

**Alarm B** (`ApproximateAgeOfOldestMessage > 900 s` on the source) means the source queue has
a **backlog or stall condition**. It does **not** by itself prove a poison message exists.
During stress-corpus ingestion, `MaximumConcurrency = 2` may create backlog **by design**, so
Alarm B firing during a large seed is expected and is not automatically a failure. Diagnose
with CloudWatch logs plus queue state plus DynamoDB `ingestion_status` before concluding
anything.

## 11. Alert destination — ATTACHED
**Both alarms are attached to the existing topic:** `arn:aws:sns:ap-south-1:557690605487:blog-alarms`, with
**1 confirmed email subscription**. It is already the action target for two alarms
(`blog-backend-5xx-errors`, `blog-backend-unhealthy`), both currently `INSUFFICIENT_DATA`
because the ALB they watch was torn down in the serverless migration.

`AlarmActions = ["arn:aws:sns:ap-south-1:557690605487:blog-alarms"]` on both new alarms.
**No new SNS topic was created, no subscription was modified, and nobody was unsubscribed.**

The two stale ALB alarms (`blog-backend-5xx-errors`, `blog-backend-unhealthy`) were **left
untouched** — they belong to the removed EC2/ALB architecture and their cleanup is
**DEFERRED to a separate task**, explicitly out of scope here.

## 12. AWS mutations — APPLIED AND VERIFIED
| # | Action | Target | Detail |
|---|---|---|---|
| 1 | `sqs:CreateQueue` | `multitenant-ingestion-dlq.fifo` | FIFO, content-based dedup, 14-day retention |
| 2 | `sqs:SetQueueAttributes` | DLQ | `RedriveAllowPolicy` byQueue, one source ARN |
| 3 | `sqs:SetQueueAttributes` | source | `RedrivePolicy` → DLQ ARN, `maxReceiveCount 5` |
| 4 | `lambda:UpdateEventSourceMapping` | `60e4e50a-…` | `ScalingConfig.MaximumConcurrency = 2` |
| 5 | `cloudwatch:PutMetricAlarm` ×2 | two alarms | actions → `blog-alarms` |

Order mattered: the DLQ had to exist (1) before the source could point at it (3).

**Applied result:**
```
DLQ  https://sqs.ap-south-1.amazonaws.com/557690605487/multitenant-ingestion-dlq.fifo
     arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion-dlq.fifo
     FifoQueue=true  ContentBasedDeduplication=true  MessageRetentionPeriod=1209600
     RedriveAllowPolicy {"redrivePermission":"byQueue","sourceQueueArns":[<source ARN>]}

SRC  RedrivePolicy {"deadLetterTargetArn":"<DLQ ARN>","maxReceiveCount":5}
     MessageRetentionPeriod 345600 UNCHANGED   VisibilityTimeout 300 UNCHANGED

ESM  60e4e50a-eb3b-4bae-b6db-91601c5e3730  State Enabled
     ScalingConfig {"MaximumConcurrency": 2}
     BatchSize 1 UNCHANGED  MaximumBatchingWindow 0 UNCHANGED  FunctionResponseTypes [] UNCHANGED

CW   multitenant-ingestion-dlq-not-empty        >= 1     -> blog-alarms
     multitenant-ingestion-oldest-message-age   > 900 s  -> blog-alarms
     both INSUFFICIENT_DATA at idle (expected: no traffic yet)

HEALTH  source visible 0 / inflight 0     DLQ visible 0 / inflight 0
```

**A note on execution:** steps 2 and 3 failed on the first attempt with a CLI
*parameter-parsing* error — the `--attributes` shorthand cannot carry embedded JSON. No API
call was made and the source queue was verifiably unchanged; both were reapplied using JSON
file input and round-trip verified.

**Explicitly NOT changed:** `BatchSize` (stays 1), `FunctionResponseTypes` (no
`ReportBatchItemFailures`), source retention, source visibility timeout, worker timeout,
worker code, IAM, and anything in the RAG path.

## 13. Rollback
1. Stop any active ingestion test first.
2. Remove `ScalingConfig` from the mapping to restore unset (unbounded) concurrency.
3. Clear `RedrivePolicy` on the source **only if rollback is explicitly required** — this
   restores exactly today's behaviour, including the 4-day retry loop.
4. **Never delete a DLQ that contains messages.** Drain or preserve them first; those messages
   are the evidence of whatever failed.
5. Delete the two new alarms if reverting them is required. **Do not** touch the two
   pre-existing ALB alarms.

Steps 2–3 are attribute clears and take effect immediately; no redeploy is involved.

## 14. Cost / new resources
This deployment **created billable AWS resources** — stated plainly rather than glossed:
* one additional SQS FIFO queue (charged per request; an idle DLQ is effectively free, and
  `GetQueueAttributes` polling by alarms is billable API traffic),
* two CloudWatch alarms (standard alarms are billed per alarm-month, with a free-tier
  allowance),
* SNS delivery charges for alarm notifications.

`MaximumConcurrency = 2` and the redrive policies carry no direct charge; the concurrency cap
should *reduce* Lambda and Bedrock spend during ingestion by limiting fan-out.

**Measured impact: ~$0/month.** CloudWatch was billing $0 with 2 alarms and the free-tier
allowance covers 10, so 4 alarms remain free. Neither SQS nor SNS appears as a billed line
item at this volume. Account run rate stays ≈$2/month, dominated by Secrets Manager ($1.11).

## 15. Stress-corpus ingestion gate — LIFTED
The gate required steps 1–4; all four are applied and verified, so **AWS ingestion of the
100-user / 1,800-post stress corpus is no longer blocked on ingestion reliability**.

The risk it removed: the corpus is roughly 7× the posts and ~4× the tenant groups of the run
that already produced 8 throttles, and without a DLQ one poison message would block a tenant
group for 4 days while burning quota ~1,150 times. A poison message now isolates in ~25
minutes and raises an alarm.

The corpus itself remains **DESIGNED / NOT GENERATED / NOT INGESTED** — that is a separate
task and a separate authorisation.

## 16. Deployment status
**DEPLOYED / VERIFIED — 2026-08-27.**

All six steps applied after explicit user approval of the AWS charges. Every §10 verification
check passed: source `RedrivePolicy` correct with `maxReceiveCount 5`; DLQ FIFO with 14-day
retention and a single-source `RedriveAllowPolicy`; `MaximumConcurrency = 2` with `BatchSize`,
batching window and `FunctionResponseTypes` unchanged; worker timeout/memory unchanged; both
alarms present with the correct metric, threshold and SNS action; SNS subscription unchanged
at 1 confirmed.

**No poison message was injected.** Verification was structural only — the upcoming real
corpus ingestion will exercise the path naturally.

**Cost:** CloudWatch was billing $0 with 2 alarms and the free-tier allowance covers 10, so
4 alarms remain free. SQS and SNS are not billed line items at this volume. Expected marginal
cost ≈ **$0/month** against a current run rate of ~$2/month.

**Gate lifted:** AWS ingestion of the stress corpus is no longer blocked on ingestion
reliability. The corpus itself remains DESIGNED / NOT GENERATED / NOT INGESTED.
