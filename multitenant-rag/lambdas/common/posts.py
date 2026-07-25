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
import uuid

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
    existing = _find_existing_by_hash(posts_table, tenant_id, content_hash)
    if existing:
        return {
            "post_id": existing["post_id"],
            "status": existing["ingestion_status"],
            "message": "identical content already exists",
        }

    post_id = f"post_{uuid.uuid4().hex[:12]}"
    s3_key = f"tenants/{tenant_id}/posts/{post_id}.md"
    now = int(time.time())

    # 1. content -> S3
    try:
        _s3.put_object(Bucket=bucket, Key=s3_key, Body=content.encode("utf-8"),
                       ContentType="text/markdown")
    except ClientError as e:
        if log:
            log.error("s3 put failed", error=str(e), tenant_id=tenant_id, post_id=post_id)
        raise PostError(500, "failed to save content")

    # 2. metadata -> DynamoDB (rollback S3 on failure)
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
        })
    except ClientError as e:
        if log:
            log.error("dynamodb put failed", error=str(e), post_id=post_id)
        try:
            _s3.delete_object(Bucket=bucket, Key=s3_key)
        except ClientError:
            pass
        raise PostError(500, "failed to save metadata")

    # 3. enqueue async ingestion (per-tenant FIFO ordering)
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
            log.error("sqs send failed", error=str(e), post_id=post_id)
        return {"post_id": post_id, "status": "pending",
                "warning": "ingestion queue send failed — needs manual retry"}

    if log:
        log.info("post created", user_id=user_id, tenant_id=tenant_id, post_id=post_id)
    return {"post_id": post_id, "status": "pending"}


def _find_existing_by_hash(posts_table: str, tenant_id: str, content_hash: str) -> dict | None:
    try:
        resp = _ddb.query(
            TableName=posts_table,
            KeyConditionExpression="tenant_id = :t",
            FilterExpression="content_hash = :h",
            ExpressionAttributeValues={":t": {"S": tenant_id}, ":h": {"S": content_hash}},
            Limit=1,
        )
    except ClientError:
        return None
    items = resp.get("Items", [])
    if not items:
        return None
    return {"post_id": items[0]["post_id"]["S"], "ingestion_status": items[0]["ingestion_status"]["S"]}
