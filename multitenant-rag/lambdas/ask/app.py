"""askLambda — streaming via Lambda Web Adapter (FastAPI + LWA).

Python's managed Lambda runtime has no native response streaming, so we run a
FastAPI app and let the Lambda Web Adapter bridge a RESPONSE_STREAM Function URL
to it. FastAPI's StreamingResponse maps to the streamed Lambda response.

Streams NDJSON, one event per line:
  {"type":"content","text":"..."}   per token
  {"type":"done","citations":[...]} final event
"""

import json
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastembed import SparseTextEmbedding
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, Fusion, FusionQuery, MatchValue,
    Prefetch, SparseVector,
)

from common.context import ContextError, get_context_from_headers
from common.logger import get_logger
from common.secrets import get_qdrant
from llm import stream_answer


log = get_logger("ask")

REGION = os.environ.get("AWS_REGION", "ap-south-1")
TENANTS_TABLE = os.environ["TENANTS_TABLE"]
USERS_TABLE = os.environ.get("USERS_TABLE", "multitenant-users")
POSTS_TABLE = os.environ.get("POSTS_TABLE", "multitenant-posts")
USAGE_TABLE = os.environ["USAGE_TABLE"]
COLLECTION_NAME = "multitenant_chunks"
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
SCORE_THRESHOLD = 0.3
TOP_K = 5

ddb = boto3.client("dynamodb", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

_qdrant: QdrantClient | None = None
_bm25: SparseTextEmbedding | None = None

app = FastAPI()

# CORS: prod is same-origin (behind CloudFront) so this is a no-op there; it
# only matters for local dev where the Vite server (5173) calls this app (8080).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://d261g450savmee.cloudfront.net",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str


def _ndjson(event: dict) -> bytes:
    return (json.dumps(event) + "\n").encode("utf-8")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/users")
def list_users():
    """List personas (users) with their tenant display info — powers the UI picker."""
    resp = ddb.scan(TableName=USERS_TABLE)
    users = []
    for it in resp.get("Items", []):
        uid = it["user_id"]["S"]
        tid = it.get("tenant_id", {}).get("S", "")
        tenant = _get_tenant(tid) if tid else None
        users.append({
            "user_id": uid,
            "tenant_id": tid,
            "display_name": (tenant or {}).get("display_name", uid),
            "domain": (tenant or {}).get("domain", ""),
        })
    users.sort(key=lambda u: u["display_name"])
    return {"users": users}


@app.get("/api/tenants/{tenant_id}/posts")
def list_posts(tenant_id: str):
    """List a tenant's posts (newest first)."""
    resp = ddb.query(
        TableName=POSTS_TABLE,
        KeyConditionExpression="tenant_id = :t",
        ExpressionAttributeValues={":t": {"S": tenant_id}},
    )
    posts = [
        {
            "post_id": i["post_id"]["S"],
            "title": i.get("title", {}).get("S", ""),
            "status": i.get("ingestion_status", {}).get("S", ""),
            "created_at": int(i.get("created_at", {}).get("N", "0")),
        }
        for i in resp.get("Items", [])
    ]
    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return {"posts": posts}


@app.post("/ask")
@app.post("/api/ask")
async def ask(req: AskRequest, request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()

    # Resolve identity/tenant server-side (never trust tenant from client)
    try:
        user_id, tenant_id, _ = get_context_from_headers(dict(request.headers))
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})

    tenant = _get_tenant(tenant_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"error": f"tenant {tenant_id} not found"})

    question = (req.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "question is required"})

    log.info("query start", user_id=user_id, tenant_id=tenant_id, request_id=request_id)

    # Everything below runs inside the streamed generator. Starlette iterates a
    # sync generator in a threadpool, so blocking calls (Bedrock, Qdrant, Groq)
    # are fine and don't block the event loop.
    def event_stream():
        results = _hybrid_search(question, tenant_id)

        if not results or results[0].score < SCORE_THRESHOLD:
            msg = f"{tenant['display_name']} hasn't written about this topic."
            yield _ndjson({"type": "content", "text": msg})
            yield _ndjson({"type": "done", "citations": []})
            _log_usage(tenant_id, user_id, request_id, question, 0, 0, "empty", started_at)
            return

        system_prompt = _build_system_prompt(tenant, results)

        answer_parts: list[str] = []
        total_input = 0
        total_output = 0
        try:
            for ev in stream_answer(system_prompt, question):
                if ev["type"] == "content":
                    answer_parts.append(ev["text"])
                    yield _ndjson(ev)
                elif ev["type"] == "usage":
                    total_input = ev["input_tokens"]
                    total_output = ev["output_tokens"]
        except Exception as e:
            log.error("llm stream failed", error=str(e), request_id=request_id)
            yield _ndjson({"type": "content", "text": "\n\n[Error while generating response]"})

        # Citation refinement: if the model refused (context didn't answer the
        # question), don't cite unrelated sources. Otherwise cite distinct posts
        # (deduped, best score each) rather than repeated chunks.
        answer = "".join(answer_parts).lower()
        refused = "hasn't written about" in answer
        citations = [] if refused else _dedupe_citations(results)
        result_type = "refused" if refused else "answered"

        yield _ndjson({"type": "done", "citations": citations})
        _log_usage(tenant_id, user_id, request_id, question, total_input, total_output, result_type, started_at)

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


def _dedupe_citations(results) -> list[dict]:
    """Collapse chunk-level hits into distinct source posts, best score each."""
    best: dict[str, dict] = {}
    for r in results:
        pid = r.payload["post_id"]
        score = round(r.score, 4)
        if pid not in best or score > best[pid]["score"]:
            best[pid] = {
                "post_id": pid,
                "title": r.payload["title"],
                "header_path": r.payload.get("header_path", ""),
                "score": score,
            }
    return sorted(best.values(), key=lambda c: c["score"], reverse=True)


def _get_tenant(tenant_id: str) -> dict | None:
    try:
        resp = ddb.get_item(TableName=TENANTS_TABLE, Key={"tenant_id": {"S": tenant_id}})
    except ClientError:
        return None
    item = resp.get("Item")
    if not item:
        return None
    return {
        "tenant_id": item["tenant_id"]["S"],
        "display_name": item.get("display_name", {}).get("S", tenant_id),
        "domain": item.get("domain", {}).get("S", "general"),
    }


def _hybrid_search(question: str, tenant_id: str):
    dense = _embed_dense(question)
    sparse = _embed_sparse(question)
    qdrant = _get_qdrant_client()
    result = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        prefetch=[
            Prefetch(query=dense, using="dense", limit=20),
            Prefetch(
                query=SparseVector(indices=sparse["indices"], values=sparse["values"]),
                using="sparse",
                limit=20,
            ),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        query_filter=Filter(must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
        ]),
        limit=TOP_K,
        with_payload=True,
    )
    return result.points


def _embed_dense(text: str) -> list[float]:
    body = json.dumps({"inputText": text[:8000]})
    resp = bedrock.invoke_model(modelId=TITAN_MODEL_ID, body=body, contentType="application/json")
    return json.loads(resp["body"].read())["embedding"]


def _embed_sparse(text: str) -> dict:
    global _bm25
    if _bm25 is None:
        _bm25 = SparseTextEmbedding("Qdrant/bm25")
    emb = list(_bm25.query_embed(text))[0]
    return {"indices": emb.indices.tolist(), "values": emb.values.tolist()}


def _build_system_prompt(tenant: dict, results) -> str:
    context_blocks = "\n\n---\n\n".join([
        f"[Source: {r.payload['title']}]\n{r.payload['chunk_text']}"
        for r in results
    ])
    return f"""You are a helpful AI assistant answering questions about {tenant['display_name']}, whose primary domain is: {tenant['domain']}.

Below are excerpts from {tenant['display_name']}'s own documents:

{context_blocks}

Rules:
1. Answer using ONLY the context above. Cite sources by title in your answer.
2. If the context doesn't contain the answer, say: "{tenant['display_name']} hasn't written about this."
3. Match {tenant['display_name']}'s tone from their writing.
4. If the topic is medical, legal, or financial, add appropriate disclaimers (consult a professional; not personalized advice).
5. Be concise. Prefer direct answers over long preambles.
"""


def _get_qdrant_client() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        url, api_key = get_qdrant()
        _qdrant = QdrantClient(url=url, api_key=api_key)
    return _qdrant


def _log_usage(tenant_id: str, user_id: str, request_id: str, query: str,
               input_tokens: int, output_tokens: int, result_type: str,
               started_at: float) -> None:
    now = int(time.time())
    latency_ms = int((time.time() - started_at) * 1000)
    date_key = time.strftime("%Y-%m-%d", time.gmtime(now))
    expires_at = now + 30 * 86400
    try:
        ddb.put_item(
            TableName=USAGE_TABLE,
            Item={
                "tenant_date": {"S": f"{tenant_id}#{date_key}"},
                "timestamp_req": {"S": f"{now}#{request_id}"},
                "user_id": {"S": user_id},
                "query": {"S": query[:500]},
                "tokens_input": {"N": str(input_tokens)},
                "tokens_output": {"N": str(output_tokens)},
                "latency_ms": {"N": str(latency_ms)},
                "result_type": {"S": result_type},
                "expires_at": {"N": str(expires_at)},
            },
        )
    except ClientError as e:
        log.error("usage log failed", error=str(e), request_id=request_id)
