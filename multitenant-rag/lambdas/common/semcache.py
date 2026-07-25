"""Semantic answer cache — cache (query embedding -> answer) per tenant.

When a near-identical question (same tenant) was answered before, return the
cached answer and skip BOTH retrieval and the LLM. Safeguards:
  - per-tenant filter (never serve tenant A's answer to tenant B)
  - HIGH cosine threshold (0.95) — only near-duplicate questions hit
  - TTL (24h default) — entries self-expire
  - invalidate on write — ingest busts the tenant's cache when content changes
  - single-turn only — the caller must skip the cache when there's chat history
    (a cached answer ignores conversation context and would be wrong for follow-ups)

Best-effort: any cache error degrades to a normal (uncached) query, never fails
the request.
"""

import hashlib
import json
import os
import time

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, FilterSelector, MatchValue,
    PayloadSchemaType, PointStruct, VectorParams,
)

from common.logger import get_logger
from common.secrets import get_qdrant


log = get_logger("semcache")

COLLECTION = os.environ.get("QDRANT_CACHE_COLLECTION", "multitenant_query_cache")
THRESHOLD = float(os.environ.get("SEMCACHE_THRESHOLD", "0.95"))
TTL_SECONDS = int(os.environ.get("SEMCACHE_TTL_SECONDS", str(24 * 3600)))
ENABLED = os.environ.get("SEMCACHE_ENABLED", "true").lower() in ("1", "true", "yes")
DIMS = 1024  # Titan Text v2

_client: QdrantClient | None = None
_ensured = False


def _c() -> QdrantClient:
    global _client
    if _client is None:
        url, key = get_qdrant()
        _client = QdrantClient(url=url, api_key=key)
    return _client


def _ensure():
    """Create the cache collection on first use (dense cosine + tenant_id index)."""
    global _ensured
    if _ensured:
        return
    c = _c()
    names = [x.name for x in c.get_collections().collections]
    if COLLECTION not in names:
        c.create_collection(collection_name=COLLECTION,
                            vectors_config=VectorParams(size=DIMS, distance=Distance.COSINE))
        c.create_payload_index(collection_name=COLLECTION, field_name="tenant_id",
                               field_schema=PayloadSchemaType.KEYWORD)
        log.info("created query cache collection", collection=COLLECTION)
    _ensured = True


def _point_id(tenant_id: str, question: str) -> int:
    # Deterministic: the exact same question for a tenant overwrites (refreshes).
    h = hashlib.sha256(f"{tenant_id}|{question.strip().lower()}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") & 0x7FFFFFFFFFFFFFFF


def lookup(tenant_id: str, question_dense: list[float]) -> dict | None:
    """Return {answer, citations, score} on a near-duplicate hit, else None."""
    if not ENABLED:
        return None
    try:
        _ensure()
        res = _c().query_points(
            collection_name=COLLECTION, query=question_dense,
            query_filter=Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]),
            limit=1, with_payload=True,
        )
        pts = res.points
        if not pts or pts[0].score < THRESHOLD:
            return None
        p = pts[0].payload or {}
        if time.time() - int(p.get("created_at", 0)) > TTL_SECONDS:
            return None
        return {"answer": p.get("answer", ""),
                "citations": json.loads(p.get("citations", "[]")),
                "score": round(pts[0].score, 4)}
    except Exception as e:
        log.error("semcache lookup failed", error=str(e))
        return None


def store(tenant_id: str, question: str, question_dense: list[float], answer: str, citations: list) -> None:
    if not ENABLED:
        return
    try:
        _ensure()
        _c().upsert(collection_name=COLLECTION, points=[PointStruct(
            id=_point_id(tenant_id, question), vector=question_dense,
            payload={"tenant_id": tenant_id, "question": question[:500], "answer": answer,
                     "citations": json.dumps(citations), "created_at": int(time.time())},
        )])
    except Exception as e:
        log.error("semcache store failed", error=str(e))


def invalidate_tenant(tenant_id: str) -> None:
    """Drop all cached answers for a tenant — call when their content changes."""
    if not ENABLED:
        return
    try:
        _ensure()
        _c().delete(collection_name=COLLECTION, points_selector=FilterSelector(filter=Filter(
            must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))])))
        log.info("semcache invalidated tenant", tenant_id=tenant_id)
    except Exception as e:
        log.error("semcache invalidate failed", error=str(e))
