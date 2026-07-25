"""askLambda — handles POST /ask (buffered response).

v1 is buffered: the full answer is collected server-side and returned as one
JSON payload. True token-by-token streaming is a planned enhancement via the
Lambda Web Adapter (managed Python runtime has no native response streaming).

Flow:
  1. Resolve user_id -> tenant_id
  2. Load tenant metadata (display_name, domain)
  3. Embed question (dense via Titan + sparse via BM25)
  4. Hybrid Qdrant search with tenant_id filter (RRF fusion)
  5. If empty/weak results -> polite "hasn't written" answer
  6. Assemble prompt (universal template + retrieved chunks)
  7. Collect the full answer from Groq
  8. Return {answer, citations} as JSON
  9. Log usage (best-effort, doesn't block the response)

Response body:
  {"answer": "...", "citations": [{"post_id","title","chunk_index","header_path","score"}, ...]}
"""

import json
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError
from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, Fusion, FusionQuery, MatchValue,
    Prefetch, SparseVector,
)

from common.context import ContextError, get_context
from common.logger import get_logger
from common.responses import error_response, json_response
from common.secrets import get_qdrant
from llm import stream_answer


log = get_logger("ask")

REGION = os.environ.get("AWS_REGION", "ap-south-1")
TENANTS_TABLE = os.environ["TENANTS_TABLE"]
USAGE_TABLE = os.environ["USAGE_TABLE"]
COLLECTION_NAME = "multitenant_chunks"
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
SCORE_THRESHOLD = 0.3
TOP_K = 5

ddb = boto3.client("dynamodb", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

_qdrant: QdrantClient | None = None
_bm25: SparseTextEmbedding | None = None


def handler(event, _context):
    """Buffered Lambda handler (API Gateway v2 / Function URL BUFFERED)."""
    request_id = str(uuid.uuid4())
    started_at = time.time()

    try:
        user_id, tenant_id, _ = get_context(event)
    except ContextError as e:
        return error_response(401, "Unauthorized", str(e))

    body = _parse_body(event)
    if body is None:
        return error_response(400, "Bad request", "invalid JSON body")

    question = (body.get("question") or "").strip()
    if not question:
        return error_response(400, "Bad request", "question is required")

    tenant = _get_tenant(tenant_id)
    if not tenant:
        return error_response(404, "Not found", f"tenant {tenant_id} not found")

    log.info("query start", user_id=user_id, tenant_id=tenant_id, request_id=request_id)

    # Retrieve
    results = _hybrid_search(question, tenant_id)

    # No content or too weak -> polite refusal (skip the LLM call)
    if not results or results[0].score < SCORE_THRESHOLD:
        msg = f"{tenant['display_name']} hasn't written about this topic."
        _log_usage(tenant_id, user_id, request_id, question, 0, 0, "empty", started_at)
        return json_response(200, {"answer": msg, "citations": []})

    # Build prompt + collect the full answer from Groq
    system_prompt = _build_system_prompt(tenant, results)

    answer_parts: list[str] = []
    total_input = 0
    total_output = 0
    try:
        for ev in stream_answer(system_prompt, question):
            if ev["type"] == "content":
                answer_parts.append(ev["text"])
            elif ev["type"] == "usage":
                total_input = ev["input_tokens"]
                total_output = ev["output_tokens"]
    except Exception as e:
        log.error("llm call failed", error=str(e), request_id=request_id)
        _log_usage(tenant_id, user_id, request_id, question, 0, 0, "llm_error", started_at)
        return error_response(502, "Upstream error", "failed to generate an answer")

    answer = "".join(answer_parts).strip()

    citations = [
        {
            "post_id": r.payload["post_id"],
            "title": r.payload["title"],
            "chunk_index": r.payload["chunk_index"],
            "header_path": r.payload.get("header_path", ""),
            "score": round(r.score, 4),
        }
        for r in results
    ]

    _log_usage(tenant_id, user_id, request_id, question, total_input, total_output, "answered", started_at)

    return json_response(200, {"answer": answer, "citations": citations})


def _parse_body(event: dict) -> dict | None:
    raw = event.get("body")
    if not raw:
        return None
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _get_tenant(tenant_id: str) -> dict | None:
    try:
        resp = ddb.get_item(
            TableName=TENANTS_TABLE,
            Key={"tenant_id": {"S": tenant_id}},
        )
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
    """Dense + sparse hybrid retrieval with tenant filter."""
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
    resp = bedrock.invoke_model(
        modelId=TITAN_MODEL_ID,
        body=body,
        contentType="application/json",
    )
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
    """Best-effort usage log. Failures don't block the response."""
    now = int(time.time())
    latency_ms = int((time.time() - started_at) * 1000)
    date_key = time.strftime("%Y-%m-%d", time.gmtime(now))
    expires_at = now + 30 * 86400  # 30-day TTL

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
