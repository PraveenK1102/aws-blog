"""Shared create-post logic.

Used by BOTH the createpost Lambda handler (prod) and the ask app's dev
POST /api/posts route, so the create flow is exercised identically in dev and
prod. Writes raw markdown to S3, metadata to DynamoDB, and enqueues async
ingestion on SQS (per-tenant FIFO). Content-hash dedup per tenant.
"""

import hashlib
import json
import os
import time

import boto3
from botocore.exceptions import ClientError


REGION = os.environ.get("AWS_REGION", "ap-south-1")

_s3 = boto3.client("s3", region_name=REGION)
_ddb = boto3.client("dynamodb", region_name=REGION)
_sqs = boto3.client("sqs", region_name=REGION)


class PostError(Exception):
    """Create-post failure with an HTTP-ish status + message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def create_post(user_id: str, tenant_id: str, title: str, content: str, log=None) -> dict:
    """Create a post for a tenant. Returns {post_id, status[, message]}.

    Dedup and create are ONE idempotent step: post_id is derived from the content
    hash, so identical content maps to the same id and a conditional PutItem
    (attribute_not_exists) rejects a duplicate atomically. This replaces the old
    query+Limit=1 check, which applied Limit BEFORE the content_hash filter and so
    read one arbitrary post and almost never caught the duplicate (and was racy —
    two fast double-clicks could both pass the query). The conditional write is
    correct and race-safe with no extra read.

    If any step after the S3 write fails, we roll back so no orphaned or
    half-created post ("pending" with no queued work) is ever left behind.

    Raises PostError on validation/storage failure.
    """
    bucket = os.environ["S3_CONTENT_BUCKET"]
    posts_table = os.environ["POSTS_TABLE"]
    queue_url = os.environ["INGESTION_QUEUE_URL"]

    title = (title or "").strip()
    if not title:
        raise PostError(400, "title cannot be empty")
    if not content or not content.strip():
        raise PostError(400, "content cannot be empty")

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # Deterministic id from (tenant, content) — identical content => same id => the
    # conditional PutItem below dedups it.
    post_id = "post_" + hashlib.sha256(
        f"{tenant_id}|{content_hash}".encode("utf-8")).hexdigest()[:16]
    s3_key = f"tenants/{tenant_id}/posts/{post_id}.md"
    now = int(time.time())

    # 1. content -> S3 (same id -> same key; a duplicate just rewrites identical bytes)
    try:
        _s3.put_object(Bucket=bucket, Key=s3_key, Body=content.encode("utf-8"),
                       ContentType="text/markdown")
    except ClientError as e:
        if log:
            log.error("s3 put failed", error=str(e), tenant_id=tenant_id, post_id=post_id)
        raise PostError(500, "failed to save content")

    # 2. metadata -> DynamoDB, conditional on the post not already existing (dedup).
    try:
        _ddb.put_item(TableName=posts_table, Item={
            "tenant_id": {"S": tenant_id},
            "post_id": {"S": post_id},
            "user_id": {"S": user_id},
            "title": {"S": title},
            "s3_key": {"S": s3_key},
            "ingestion_status": {"S": "pending"},
            "content_hash": {"S": content_hash},
            "chunk_count": {"N": "0"},
            "created_at": {"N": str(now)},
            "updated_at": {"N": str(now)},
        }, ConditionExpression="attribute_not_exists(post_id)")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            # Identical content already exists for this tenant -> return it, don't re-ingest.
            existing = _ddb.get_item(
                TableName=posts_table,
                Key={"tenant_id": {"S": tenant_id}, "post_id": {"S": post_id}},
            ).get("Item", {})
            status = existing.get("ingestion_status", {}).get("S", "pending")
            return {"post_id": post_id, "status": status,
                    "message": "identical content already exists"}
        if log:
            log.error("dynamodb put failed", error=str(e), post_id=post_id)
        try:
            _s3.delete_object(Bucket=bucket, Key=s3_key)
        except ClientError:
            pass
        raise PostError(500, "failed to save metadata")

    # 3. enqueue async ingestion (per-tenant FIFO). If this fails, roll BOTH stores
    #    back and fail the request — never leave a post stuck "pending" with no worker.
    try:
        _sqs.send_message(
            QueueUrl=queue_url,
            MessageGroupId=tenant_id,
            MessageBody=json.dumps({
                "tenant_id": tenant_id, "user_id": user_id,
                "post_id": post_id, "s3_key": s3_key, "action": "index",
            }),
        )
    except ClientError as e:
        if log:
            log.error("sqs send failed — rolling back post", error=str(e), post_id=post_id)
        try:
            _ddb.delete_item(TableName=posts_table,
                             Key={"tenant_id": {"S": tenant_id}, "post_id": {"S": post_id}})
        except ClientError:
            pass
        try:
            _s3.delete_object(Bucket=bucket, Key=s3_key)
        except ClientError:
            pass
        raise PostError(503, "couldn't start indexing — please try again")

    if log:
        log.info("post created", user_id=user_id, tenant_id=tenant_id, post_id=post_id)
    return {"post_id": post_id, "status": "pending"}
