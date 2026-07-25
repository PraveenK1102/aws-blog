"""Qdrant-backed vector store for AskPraveen (Session 2+).

Replaces the pgvector implementation from Session 1. Function names and
signatures are kept stable so ask.py and ingest.py don't need to change.

Concepts:
- Point: one row (id + vector + payload).
- Payload: JSON metadata attached to a point (holds user_id, content, etc.).
- Payload index: makes filter queries fast (like a SQL index on a column).
"""
from __future__ import annotations

import os
import uuid
from typing import Iterable, List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .config import EMBED_DIM

QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "documents")

# Deterministic namespace for UUIDv5 — any fixed UUID works; keep it stable
# so re-ingest produces the same point IDs and updates rather than duplicates.
_UUID_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

_client: Optional[QdrantClient] = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            check_compatibility=False,
        )
    return _client


def ensure_collection() -> None:
    """Create the collection + payload index if they don't exist. Idempotent."""
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if QDRANT_COLLECTION not in existing:
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            hnsw_config=HnswConfigDiff(m=16, ef_construct=64),
        )
    # Payload indexes are needed for fast filtering.
    # Safe to call every time — Qdrant no-ops if the index already exists.
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="user_id",
        field_schema="keyword",
    )
    client.create_payload_index(
        collection_name=QDRANT_COLLECTION,
        field_name="post_id",
        field_schema="integer",
    )


def _point_id(user_id: str, post_id: int, chunk_index: int) -> str:
    """Deterministic UUID for a given (user, post, chunk). Stable re-ingest."""
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{user_id}:{post_id}:{chunk_index}"))


def upsert_chunks(rows: Iterable[dict]) -> int:
    """Upsert chunks into the collection.

    Each row must include: user_id, post_id, chunk_index, embedding, content.
    Optional: title, section_path, source_type, source_url.
    """
    points: List[PointStruct] = []
    for r in rows:
        payload = {
            "user_id": r["user_id"],
            "post_id": r["post_id"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "title": r.get("title", ""),
            "section_path": r.get("section_path"),
            "source_type": r.get("source_type", "blog_post"),
            "source_url": r.get("source_url"),
        }
        points.append(PointStruct(
            id=_point_id(r["user_id"], r["post_id"], r["chunk_index"]),
            vector=r["embedding"],
            payload=payload,
        ))
    if not points:
        return 0
    get_client().upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
    return len(points)


def vector_search(
    query_vec: List[float],
    top_k: int,
    user_id: Optional[str] = None,
) -> List[tuple]:
    """Nearest-neighbor search, optionally scoped to one user's chunks.

    Returns list of tuples matching the shape ask.py expects:
        (id, source_type, source_url, source_path, title, section_path,
         chunk_index, content, score)
    """
    qdrant_filter = None
    if user_id is not None:
        qdrant_filter = Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        )

    hits = get_client().query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vec,
        limit=top_k,
        query_filter=qdrant_filter,
        with_payload=True,
    ).points

    # Adapt Qdrant's response to the tuple shape ask.py's _format_context expects.
    # source_path is synthesized as "user_id/post_id" for citation display.
    out = []
    for h in hits:
        p = h.payload or {}
        source_path = f"{p.get('user_id', 'unknown')}/post-{p.get('post_id', 'unknown')}"
        out.append((
            str(h.id),
            p.get("source_type", "blog_post"),
            p.get("source_url"),
            source_path,
            p.get("title", ""),
            p.get("section_path"),
            p.get("chunk_index", 0),
            p.get("content", ""),
            float(h.score),
        ))
    return out


def count_documents(user_id: Optional[str] = None) -> int:
    qdrant_filter = None
    if user_id is not None:
        qdrant_filter = Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        )
    return get_client().count(
        collection_name=QDRANT_COLLECTION,
        count_filter=qdrant_filter,
        exact=True,
    ).count


def delete_by_post(user_id: str, post_id: int) -> int:
    """Delete all chunks belonging to a specific post. Used on post edit/delete."""
    result = get_client().delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                FieldCondition(key="post_id", match=MatchValue(value=post_id)),
            ]
        ),
        wait=True,
    )
    return 1 if result.status.name == "COMPLETED" else 0
