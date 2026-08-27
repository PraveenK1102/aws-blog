"""Declarative, testable definition of the proposed ingestion DLQ / redrive change.

PLAN ONLY. This module contains NO mutating call and imports no AWS client at
module scope. It exists so the intended configuration is asserted by tests and
reviewable as code, and so the apply phase (a separate, explicitly approved task)
has an unambiguous source of truth to execute.
"""

ACCOUNT = "557690605487"
REGION = "ap-south-1"

SOURCE_QUEUE_NAME = "multitenant-ingestion.fifo"
SOURCE_QUEUE_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{SOURCE_QUEUE_NAME}"

DLQ_NAME = "multitenant-ingestion-dlq.fifo"
DLQ_ARN = f"arn:aws:sqs:{REGION}:{ACCOUNT}:{DLQ_NAME}"

# --- DLQ creation attributes -----------------------------------------------
# FifoQueue is mandatory: a FIFO source can only dead-letter to a FIFO target.
DLQ_ATTRIBUTES = {
    "FifoQueue": "true",
    "ContentBasedDeduplication": "true",   # mirrors the source queue
    "MessageRetentionPeriod": str(14 * 24 * 3600),   # 14 days
}

# --- source queue RedrivePolicy --------------------------------------------
# 5 SQS deliveries, then the message is isolated in the DLQ.
MAX_RECEIVE_COUNT = 5
SOURCE_REDRIVE_POLICY = {
    "deadLetterTargetArn": DLQ_ARN,
    "maxReceiveCount": MAX_RECEIVE_COUNT,
}

# --- DLQ RedriveAllowPolicy -------------------------------------------------
# byQueue + exactly one source ARN: no other queue may dead-letter here.
DLQ_REDRIVE_ALLOW_POLICY = {
    "redrivePermission": "byQueue",
    "sourceQueueArns": [SOURCE_QUEUE_ARN],
}

# --- event source mapping ---------------------------------------------------
ESM_UUID = "60e4e50a-eb3b-4bae-b6db-91601c5e3730"
PLANNED_MAXIMUM_CONCURRENCY = 2          # architect decision — exactly 2

# Values that MUST NOT change in this task.
UNCHANGED = {
    "BatchSize": 1,
    "MaximumBatchingWindowInSeconds": 0,
    "FunctionResponseTypes": [],          # no ReportBatchItemFailures
    "SourceMessageRetentionPeriod": 4 * 24 * 3600,   # 4 days
    "SourceVisibilityTimeout": 300,
    "WorkerTimeout": 300,
}

# --- CloudWatch alarms (definitions only; NOT created) ----------------------
EXISTING_SNS_TOPIC_ARN = f"arn:aws:sns:{REGION}:{ACCOUNT}:blog-alarms"

ALARMS = [
    {
        "AlarmName": "multitenant-ingestion-dlq-not-empty",
        "AlarmDescription": "A message was dead-lettered from multitenant-ingestion.fifo.",
        "Namespace": "AWS/SQS",
        "MetricName": "ApproximateNumberOfMessagesVisible",
        "Dimensions": [{"Name": "QueueName", "Value": DLQ_NAME}],
        "Statistic": "Maximum",
        "Period": 60,
        "EvaluationPeriods": 1,
        "Threshold": 1,
        "ComparisonOperator": "GreaterThanOrEqualToThreshold",
        "TreatMissingData": "notBreaching",
        "AlarmActions": [],          # intentionally empty until approved
    },
    {
        "AlarmName": "multitenant-ingestion-oldest-message-age",
        "AlarmDescription": "Ingestion backlog is ageing; a group may be blocked.",
        "Namespace": "AWS/SQS",
        "MetricName": "ApproximateAgeOfOldestMessage",
        "Dimensions": [{"Name": "QueueName", "Value": SOURCE_QUEUE_NAME}],
        "Statistic": "Maximum",
        "Period": 60,
        "EvaluationPeriods": 1,
        "Threshold": 900,            # seconds
        "ComparisonOperator": "GreaterThanThreshold",
        "TreatMissingData": "notBreaching",
        "AlarmActions": [],
    },
]


def proposed_mutations() -> list[dict]:
    """The exact, ordered mutation list for the future apply phase."""
    return [
        {"step": 1, "action": "sqs:CreateQueue", "target": DLQ_NAME,
         "attributes": DLQ_ATTRIBUTES, "reversible": "delete only if empty"},
        {"step": 2, "action": "sqs:SetQueueAttributes", "target": DLQ_NAME,
         "attributes": {"RedriveAllowPolicy": DLQ_REDRIVE_ALLOW_POLICY},
         "reversible": "clear attribute"},
        {"step": 3, "action": "sqs:SetQueueAttributes", "target": SOURCE_QUEUE_NAME,
         "attributes": {"RedrivePolicy": SOURCE_REDRIVE_POLICY},
         "reversible": "clear attribute — restores today's behaviour exactly"},
        {"step": 4, "action": "lambda:UpdateEventSourceMapping", "target": ESM_UUID,
         "attributes": {"ScalingConfig": {"MaximumConcurrency": PLANNED_MAXIMUM_CONCURRENCY}},
         "reversible": "remove ScalingConfig to restore unset"},
        {"step": 5, "action": "cloudwatch:PutMetricAlarm (x2, SEPARATE approval)",
         "target": [a["AlarmName"] for a in ALARMS],
         "attributes": {"AlarmActions": "empty until an alert destination is approved"},
         "reversible": "delete alarms"},
    ]
