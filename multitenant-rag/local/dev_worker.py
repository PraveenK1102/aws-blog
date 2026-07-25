#!/usr/bin/env python
"""Dev ingest worker — polls the LocalStack SQS queue and runs the REAL
ingest_worker handler (same code as prod). This stands in for the prod
SQS -> Lambda event source mapping. Ctrl-C to stop.

    .venv/bin/python local/dev_worker.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAMBDAS = os.path.join(HERE, "..", "lambdas")


def _load_env(path):
    if not os.path.exists(path):
        return
    for raw in open(path):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()  # dev .env wins


# Load dev config BEFORE importing the handler (it reads env at import time)
_load_env(os.path.join(HERE, ".env"))

# Import the real ingest_worker handler (needs its dir + lambdas/ on path)
sys.path.insert(0, os.path.join(LAMBDAS, "ingest_worker"))
sys.path.insert(0, LAMBDAS)

import boto3
import handler as iw  # ingest_worker/handler.py

QUEUE_URL = os.environ["INGESTION_QUEUE_URL"]
sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "ap-south-1"))

print(f"[dev-worker] polling {QUEUE_URL}")
print(f"[dev-worker] collection={os.environ.get('QDRANT_COLLECTION')}  (Ctrl-C to stop)")

while True:
    resp = sqs.receive_message(
        QueueUrl=QUEUE_URL, MaxNumberOfMessages=1,
        WaitTimeSeconds=2, VisibilityTimeout=120,
    )
    for m in resp.get("Messages", []):
        event = {"Records": [{"body": m["Body"], "messageId": m.get("MessageId", "")}]}
        try:
            iw.handler(event, None)
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=m["ReceiptHandle"])
            body = json.loads(m["Body"])
            print(f"[dev-worker] ingested post {body.get('post_id')} (tenant {body.get('tenant_id')})")
        except Exception as e:
            print(f"[dev-worker] FAILED msg {m.get('MessageId', '')[:8]}: {e}")
            # leave it for retry after the visibility timeout
