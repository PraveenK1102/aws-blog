# Ingestion DLQ / Redrive — Pre-Deployment Plan

**Status: PLANNED / NOT YET APPLIED. Zero AWS mutations in this task.**
**Date:** 2026-08-27

> Read-only AWS inspection only. No queue, policy, mapping, alarm or SNS resource was
> created or modified. Applying this plan requires explicit approval.

## 1. Existing queue state — verified against AWS, not assumed

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

Every expected value in the brief matched AWS. **No DLQ exists** — the account has exactly
one SQS queue.

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

**Honest note on retry nesting.** boto3's default retry mode already retries `InvokeModel`
inside a single invocation — the observed log read *"reached max retries: 4"*. So the real
budget is roughly *5 SQS deliveries × boto3's internal attempts*, not 5 total HTTP calls. No
application-level retry loop exists in the worker (verified), and none is being added, per the
instruction not to create nested uncontrolled retries. The nesting that exists is boto3's and
is pre-existing.

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

**Deduplication caveat:** the source has `ContentBasedDeduplication=true` with a 5-minute
dedup window. A redriven message whose body is byte-identical to one accepted within the last
5 minutes can be silently deduplicated. Operators should therefore not redrive immediately
after a failed attempt of the same message — wait out the window.

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
4. **Wait out the 5-minute dedup window** if the same message failed recently.
5. **Redrive with the supported mechanism:**
   ```
   aws sqs start-message-move-task \
     --source-arn arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion-dlq.fifo \
     --destination-arn arn:aws:sqs:ap-south-1:557690605487:multitenant-ingestion.fifo \
     --region ap-south-1
   ```
6. **Confirm** the post reaches `ingestion_status = indexed` and the DLQ returns to empty.

No custom replay service is built, and nothing is redriven automatically — a message reaches
the DLQ precisely because it needs a human decision.

## 10. CloudWatch alarms — defined, NOT created
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
| Actions | **empty** | **empty** |

Alarm A means *anything reached the DLQ*. Alarm B catches the case a DLQ cannot: a group
blocked but still under its receive limit, or a genuine backlog.

## 11. Alert-destination state
**An alert destination EXISTS:** `arn:aws:sns:ap-south-1:557690605487:blog-alarms`, with
**1 confirmed email subscription**. It is already the action target for two alarms
(`blog-backend-5xx-errors`, `blog-backend-unhealthy`), both currently `INSUFFICIENT_DATA`
because the ALB they watch was torn down in the serverless migration.

So this is **not** "no alert destination configured" — a working topic exists. But attaching
it was not authorised, so `AlarmActions` is left empty and the topic is reported for decision.
**Recommendation:** attach `blog-alarms` when the alarms are approved; a DLQ alarm nobody
receives is only marginally better than no alarm. Worth noting separately that the two stale
ALB alarms are now meaningless and could be retired.

## 12. Exact proposed AWS mutations — NOT APPLIED
| # | Action | Target | Detail |
|---|---|---|---|
| 1 | `sqs:CreateQueue` | `multitenant-ingestion-dlq.fifo` | FIFO, content-based dedup, 14-day retention |
| 2 | `sqs:SetQueueAttributes` | DLQ | `RedriveAllowPolicy` byQueue, one source ARN |
| 3 | `sqs:SetQueueAttributes` | source | `RedrivePolicy` → DLQ ARN, `maxReceiveCount 5` |
| 4 | `lambda:UpdateEventSourceMapping` | `60e4e50a-…` | `ScalingConfig.MaximumConcurrency = 2` |
| 5 | `cloudwatch:PutMetricAlarm` ×2 | two alarms | **separate approval**; actions empty |

Order matters: the DLQ must exist (1) before the source can point at it (3).

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
5. Delete the alarms if they were created.

Steps 2–3 are attribute clears and take effect immediately; no redeploy is involved.

## 14. Cost / new resources
This plan **would create billable AWS resources** — stated plainly rather than glossed:
* one additional SQS FIFO queue (charged per request; an idle DLQ is effectively free, and
  `GetQueueAttributes` polling by alarms is billable API traffic),
* two CloudWatch alarms (standard alarms are billed per alarm-month, with a free-tier
  allowance),
* SNS delivery charges if alarm actions are later attached.

`MaximumConcurrency = 2` and the redrive policies carry no direct charge; the concurrency cap
should *reduce* Lambda and Bedrock spend during ingestion by limiting fan-out.

## 15. Stress-corpus ingestion gate
**AWS ingestion of the 100-user / 1,800-post stress corpus is GATED on steps 1–4 being
applied.** The corpus is roughly 7× the posts and ~4× the tenant groups of the run that
already produced 8 throttles; without a DLQ, one poison message blocks a tenant group for
4 days and burns quota ~1,150 times.

Cohort A (25 users / 450 posts) may be generated as prose while this remains unapplied —
generation touches no AWS.

## 16. Deployment status
**PLANNED / NOT APPLIED.** Zero AWS mutations. Awaiting explicit approval to execute steps
1–4, and a separate decision on step 5 and whether to attach `blog-alarms`.
