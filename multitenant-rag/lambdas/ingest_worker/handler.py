"""ingestWorkerLambda — consumes SQS messages, chunks + embeds posts.

Flow per message:
  1. Read content from S3
  2. Chunk (markdown-aware)
  3. Dense embed via Bedrock Titan V2
  4. Sparse embed via fastembed BM25
  5. Delete existing chunks for this post_id in Qdrant (idempotent re-ingest)
  6. Upsert new chunks with payload
  7. Update DynamoDB post status → "indexed"

If a message fails, the exception propagates. SQS will retry via its
built-in visibility timeout / redrive policy.
"""

import json
import os
import time
from typing import Iterator

import boto3
from botocore.exceptions import ClientError
from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, FilterSelector, MatchValue,
    PointStruct, SparseVector,
)

from common.context import _fetch_user  # for user metadata if needed
from common.logger import get_logger
from common.secrets import get_qdrant


log = get_logger("ingest_worker")

REGION = os.environ.get("AWS_REGION", "ap-south-1")
S3_BUCKET = os.environ["S3_CONTENT_BUCKET"]
POSTS_TABLE = os.environ["POSTS_TABLE"]
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "multitenant_chunks")
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
DENSE_DIMS = 1024

s3 = boto3.client("s3", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

# Initialized lazily to keep cold start light
_qdrant: QdrantClient | None = None
_bm25: SparseTextEmbedding | None = None


def handler(event, _context):
    """
    SQS trigger: event["Records"] is a list of messages.
    For FIFO, they're delivered in MessageGroupId order.
    """
    results = {"processed": 0, "failed": 0}

    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            _process_message(body)
            results["processed"] += 1
        except Exception as e:
            log.error(
                "message processing failed",
                error=str(e),
                error_type=type(e).__name__,
                message_id=record.get("messageId"),
            )
            results["failed"] += 1
            raise  # let SQS retry

    log.info("batch complete", **results)
    return results


def _process_message(msg: dict) -> None:
    tenant_id = msg["tenant_id"]
    post_id = msg["post_id"]
    user_id = msg.get("user_id")
    s3_key = msg["s3_key"]

    log.info("ingest start", tenant_id=tenant_id, post_id=post_id)

    # 1. Fetch content from S3
    obj = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
    content = obj["Body"].read().decode("utf-8")
    log.info("content loaded", post_id=post_id, size_bytes=len(content))

    # 2. Load post metadata (for title, etc.)
    post_meta = _get_post_meta(tenant_id, post_id)
    title = post_meta.get("title", "Untitled")

    # 3. Chunk
    from chunker import chunk_markdown
    chunks = chunk_markdown(content, max_tokens=500, overlap_tokens=50)
    log.info("chunked", post_id=post_id, chunk_count=len(chunks))

    if not chunks:
        _mark_indexed(tenant_id, post_id, 0)
        return

    # 4. Dense embed (batched)
    dense_vectors = _embed_dense_batch([c.text for c in chunks])
    log.info("dense embedded", post_id=post_id, count=len(dense_vectors))

    # 5. Sparse embed (BM25)
    sparse_vectors = _embed_sparse_batch([c.text for c in chunks])
    log.info("sparse embedded", post_id=post_id, count=len(sparse_vectors))

    # 6. Delete existing chunks for this post_id (idempotent re-ingest)
    qdrant = _get_qdrant_client()
    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="post_id", match=MatchValue(value=post_id))]
        )),
    )

    # 7. Upsert new chunks
    points = []
    for i, (chunk, dense, sparse) in enumerate(zip(chunks, dense_vectors, sparse_vectors)):
        chunk_id = f"{post_id}_{i}"
        points.append(PointStruct(
            id=_point_id(chunk_id),
            vector={
                "dense": dense,
                "sparse": SparseVector(indices=sparse["indices"], values=sparse["values"]),
            },
            payload={
                "tenant_id": tenant_id,
                "user_id": user_id or "",
                "post_id": post_id,
                "chunk_id": chunk_id,
                "chunk_index": i,
                "chunk_text": chunk.text,
                "title": title,
                "header_path": chunk.header_path,
                "source_s3_key": s3_key,
                "created_at": int(time.time()),
            },
        ))

    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
    log.info("qdrant upsert complete", post_id=post_id, points=len(points))

    # 8. Update DynamoDB status
    _mark_indexed(tenant_id, post_id, len(chunks))
    log.info("ingest complete", post_id=post_id)


def _get_post_meta(tenant_id: str, post_id: str) -> dict:
    resp = ddb.get_item(
        TableName=POSTS_TABLE,
        Key={"tenant_id": {"S": tenant_id}, "post_id": {"S": post_id}},
    )
    item = resp.get("Item", {})
    return {k: (v.get("S") or v.get("N")) for k, v in item.items()}


def _mark_indexed(tenant_id: str, post_id: str, chunk_count: int) -> None:
    now = int(time.time())
    try:
        ddb.update_item(
            TableName=POSTS_TABLE,
            Key={"tenant_id": {"S": tenant_id}, "post_id": {"S": post_id}},
            UpdateExpression="SET ingestion_status = :s, chunk_count = :c, updated_at = :u",
            ExpressionAttributeValues={
                ":s": {"S": "indexed"},
                ":c": {"N": str(chunk_count)},
                ":u": {"N": str(now)},
            },
        )
    except ClientError as e:
        log.error("failed to update status", error=str(e), post_id=post_id)


def _embed_dense_batch(texts: list[str]) -> list[list[float]]:
    """
    Bedrock Titan V2 embeds one text per API call. Loop over texts.
    For large volumes, consider Bedrock's InvokeModel with async wrappers.
    """
    results: list[list[float]] = []
    for text in texts:
        body = json.dumps({"inputText": text[:8000]})  # 8k char safety cap
        resp = bedrock.invoke_model(
            modelId=TITAN_MODEL_ID,
            body=body,
            contentType="application/json",
        )
        data = json.loads(resp["body"].read())
        results.append(data["embedding"])
    return results


def _embed_sparse_batch(texts: list[str]) -> list[dict]:
    """Compute BM25 sparse vectors via fastembed."""
    global _bm25
    if _bm25 is None:
        log.info("loading BM25 model")
        _bm25 = SparseTextEmbedding("Qdrant/bm25")

    embeddings = list(_bm25.embed(texts))
    return [
        {"indices": e.indices.tolist(), "values": e.values.tolist()}
        for e in embeddings
    ]


def _get_qdrant_client() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        url, api_key = get_qdrant()
        _qdrant = QdrantClient(url=url, api_key=api_key)
    return _qdrant


def _point_id(chunk_id: str) -> int:
    """
    Qdrant point IDs must be int or UUID. Hash the chunk_id to a stable int.
    Using hash truncated to 63 bits (positive) to fit signed int64.
    """
    import hashlib
    h = hashlib.sha256(chunk_id.encode()).digest()
    return int.from_bytes(h[:8], "big") & 0x7FFFFFFFFFFFFFFF
