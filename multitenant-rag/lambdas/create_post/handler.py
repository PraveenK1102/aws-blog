"""createPostLambda — handles POST /posts.

Flow:
  1. Parse request body (title, content)
  2. Resolve user_id → tenant_id via context helper
  3. Compute content hash for dedup
  4. Save raw content to S3
  5. Save metadata to DynamoDB
  6. Send SQS message to trigger async ingestion
  7. Return 201 with post_id

Idempotency: if a post with identical content_hash already exists for this
tenant, return the existing post_id instead of creating a duplicate.
"""

import hashlib
import json
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError

from common.context import ContextError, get_context
from common.logger import get_logger
from common.responses import error_response, json_response


log = get_logger("create_post")

REGION = os.environ.get("AWS_REGION", "ap-south-1")
S3_BUCKET = os.environ["S3_CONTENT_BUCKET"]           # praveen-multitenant-content
POSTS_TABLE = os.environ["POSTS_TABLE"]               # multitenant-posts
SQS_QUEUE_URL = os.environ["INGESTION_QUEUE_URL"]     # ...ingestion.fifo

s3 = boto3.client("s3", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)


def handler(event, _context):
    try:
        user_id, tenant_id, _ = get_context(event)
    except ContextError as e:
        return error_response(401, "Unauthorized", str(e))

    try:
        body = _parse_body(event)
    except ValueError as e:
        return error_response(400, "Bad request", str(e))

    title = body["title"].strip()
    content = body["content"]

    if not title:
        return error_response(400, "Bad request", "title cannot be empty")
    if not content or not content.strip():
        return error_response(400, "Bad request", "content cannot be empty")

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Dedup: check if this tenant already has a post with the same content
    existing = _find_existing_by_hash(tenant_id, content_hash)
    if existing:
        log.info(
            "duplicate post detected — returning existing",
            user_id=user_id, tenant_id=tenant_id,
            post_id=existing["post_id"], content_hash=content_hash,
        )
        return json_response(200, {
            "post_id": existing["post_id"],
            "status": existing["ingestion_status"],
            "message": "identical content already exists",
        })

    post_id = f"post_{uuid.uuid4().hex[:12]}"
    s3_key = f"tenants/{tenant_id}/posts/{post_id}.md"
    now = int(time.time())

    # 1. Write content to S3
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
        )
    except ClientError as e:
        log.error("s3 put failed", error=str(e), tenant_id=tenant_id, post_id=post_id)
        return error_response(500, "Internal error", "failed to save content")

    # 2. Write metadata to DynamoDB
    try:
        ddb.put_item(
            TableName=POSTS_TABLE,
            Item={
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
            },
        )
    except ClientError as e:
        log.error("dynamodb put failed", error=str(e), post_id=post_id)
        # Best-effort cleanup: delete S3 object
        try:
            s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        except ClientError:
            pass
        return error_response(500, "Internal error", "failed to save metadata")

    # 3. Trigger async ingestion via SQS
    try:
        sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageGroupId=tenant_id,  # per-tenant FIFO serialization
            MessageBody=json.dumps({
                "tenant_id": tenant_id,
                "user_id": user_id,
                "post_id": post_id,
                "s3_key": s3_key,
                "action": "index",
            }),
        )
    except ClientError as e:
        # Metadata + content saved but ingestion won't fire. Log; user can
        # retrigger via admin API or resubmitting the post.
        log.error("sqs send failed", error=str(e), post_id=post_id)
        return json_response(201, {
            "post_id": post_id,
            "status": "pending",
            "warning": "ingestion queue send failed — will need manual retry",
        })

    log.info(
        "post created",
        user_id=user_id, tenant_id=tenant_id, post_id=post_id,
        content_hash=content_hash, title_length=len(title), content_length=len(content),
    )

    return json_response(201, {
        "post_id": post_id,
        "status": "pending",
    })


def _parse_body(event: dict) -> dict:
    """Extract JSON body from API Gateway event."""
    raw = event.get("body")
    if not raw:
        raise ValueError("empty body")

    # API Gateway can base64-encode body when isBase64Encoded=true
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("body must be valid JSON")

    if "title" not in body or "content" not in body:
        raise ValueError("body must include 'title' and 'content'")

    return body


def _find_existing_by_hash(tenant_id: str, content_hash: str) -> dict | None:
    """
    Scan tenant's posts for matching content_hash.
    For learning scale (<1000 posts/tenant) this is fine.
    For real scale, add a GSI on content_hash.
    """
    try:
        resp = ddb.query(
            TableName=POSTS_TABLE,
            KeyConditionExpression="tenant_id = :t",
            FilterExpression="content_hash = :h",
            ExpressionAttributeValues={
                ":t": {"S": tenant_id},
                ":h": {"S": content_hash},
            },
            Limit=1,
        )
    except ClientError:
        return None

    items = resp.get("Items", [])
    if not items:
        return None

    item = items[0]
    return {
        "post_id": item["post_id"]["S"],
        "ingestion_status": item["ingestion_status"]["S"],
    }
