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

from common.auth import (
    AuthError, create_token, hash_password, verify_password, verify_token,
    bearer_from_headers,
)
from common import chats as chatstore
from common import semcache
from common.context import ContextError, get_context_from_headers
from common.logger import get_logger
from common.posts import PostError, create_post as create_post_shared
from common.secrets import get_qdrant
from llm import stream_answer


log = get_logger("ask")

REGION = os.environ.get("AWS_REGION", "ap-south-1")
TENANTS_TABLE = os.environ["TENANTS_TABLE"]
USERS_TABLE = os.environ.get("USERS_TABLE", "multitenant-users")
POSTS_TABLE = os.environ.get("POSTS_TABLE", "multitenant-posts")
USAGE_TABLE = os.environ["USAGE_TABLE"]
S3_CONTENT_BUCKET = os.environ.get("S3_CONTENT_BUCKET", "praveen-multitenant-content")
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "multitenant_chunks")
TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
SCORE_THRESHOLD = 0.3
# Low retrieval floor: below this the top chunk is clearly unrelated (e.g. a
# question about a city the author never mentioned), so we decline WITHOUT an
# LLM call (saves a Groq request). Above it, we hand the retrieved context to
# the LLM and let IT judge whether the content genuinely answers the question.
RETRIEVAL_FLOOR = float(os.environ.get("RETRIEVAL_FLOOR", "0.15"))
TOP_K = 5

ddb = boto3.client("dynamodb", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)

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
    tenant_id: str  # the profile whose AI to ask (may be anyone's; asker just needs to be logged in)
    chat_id: str | None = None  # optional saved-chat session for conversation memory


class NewChatRequest(BaseModel):
    tenant_id: str
    profile_user_id: str | None = None


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    domain: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class CreatePostRequest(BaseModel):
    title: str
    content: str


def _ndjson(event: dict) -> bytes:
    return (json.dumps(event) + "\n").encode("utf-8")


@app.get("/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------------------
# Auth — signup / login / me. Identity everywhere else comes from the JWT.
# ---------------------------------------------------------------------------

@app.post("/api/auth/signup")
def signup(req: SignupRequest):
    email = (req.email or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse(status_code=400, content={"error": "valid email required"})
    if not req.password or len(req.password) < 8:
        return JSONResponse(status_code=400, content={"error": "password must be at least 8 characters"})

    # Email must be unique (no verification, but no duplicates)
    existing = ddb.query(
        TableName=USERS_TABLE, IndexName="by_email",
        KeyConditionExpression="email = :e",
        ExpressionAttributeValues={":e": {"S": email}},
    )
    if existing.get("Items"):
        return JSONResponse(status_code=409, content={"error": "email already registered"})

    user_id = f"user_{uuid.uuid4().hex[:12]}"
    tenant_id = f"tenant_{uuid.uuid4().hex[:12]}"
    display = (req.display_name or email.split("@")[0]).strip()
    now = int(time.time())

    # Signup creates the user AND their tenant/workspace
    ddb.put_item(TableName=TENANTS_TABLE, Item={
        "tenant_id": {"S": tenant_id},
        "display_name": {"S": display},
        "domain": {"S": (req.domain or "general")},
        "active": {"BOOL": True},
        "created_at": {"N": str(now)},
    })
    ddb.put_item(TableName=USERS_TABLE, Item={
        "user_id": {"S": user_id},
        "email": {"S": email},
        "password_hash": {"S": hash_password(req.password)},
        "tenant_id": {"S": tenant_id},
        "display_name": {"S": display},
        "active": {"BOOL": True},
        "created_at": {"N": str(now)},
    }, ConditionExpression="attribute_not_exists(user_id)")

    token = create_token(user_id, tenant_id, email)
    log.info("signup", user_id=user_id, tenant_id=tenant_id)
    return {"token": token, "user": {
        "user_id": user_id, "tenant_id": tenant_id, "email": email, "display_name": display,
    }}


@app.post("/api/auth/login")
def login(req: LoginRequest):
    email = (req.email or "").strip().lower()
    resp = ddb.query(
        TableName=USERS_TABLE, IndexName="by_email",
        KeyConditionExpression="email = :e",
        ExpressionAttributeValues={":e": {"S": email}},
    )
    items = resp.get("Items", [])
    # Same generic message whether email or password is wrong (no user enumeration)
    if not items or not verify_password(req.password, items[0].get("password_hash", {}).get("S", "")):
        return JSONResponse(status_code=401, content={"error": "invalid email or password"})

    u = items[0]
    user_id = u["user_id"]["S"]
    tenant_id = u["tenant_id"]["S"]
    token = create_token(user_id, tenant_id, email)
    log.info("login", user_id=user_id, tenant_id=tenant_id)
    return {"token": token, "user": {
        "user_id": user_id, "tenant_id": tenant_id, "email": email,
        "display_name": u.get("display_name", {}).get("S", ""),
    }}


@app.get("/api/auth/me")
def me(request: Request):
    token = bearer_from_headers(dict(request.headers))
    if not token:
        return JSONResponse(status_code=401, content={"error": "not authenticated"})
    try:
        claims = verify_token(token)
    except AuthError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})
    return {"user": {
        "user_id": claims.get("sub"), "tenant_id": claims.get("tenant_id"),
        "email": claims.get("email"),
    }}


@app.post("/api/posts")
def create_post(req: CreatePostRequest, request: Request):
    """Create a post (dev route; prod routes POST /api/posts to the createpost
    Lambda — both call the same common.posts.create_post)."""
    try:
        user_id, tenant_id, _ = get_context_from_headers(dict(request.headers))
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    try:
        result = create_post_shared(user_id, tenant_id, req.title, req.content, log=log)
    except PostError as e:
        return JSONResponse(status_code=e.status, content={"error": e.message})
    return JSONResponse(status_code=200 if result.get("message") else 201, content=result)


@app.get("/api/posts")
def list_posts(request: Request):
    """List the authenticated user's own posts (tenant from the JWT), newest first."""
    try:
        _user_id, tenant_id, _ = get_context_from_headers(dict(request.headers))
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
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


def _require_login(request: Request):
    """Any valid JWT — used to gate the profile directory / profile pages."""
    return get_context_from_headers(dict(request.headers))


@app.get("/api/users")
def list_profiles(request: Request):
    """Directory of all profiles (you browse these and ask their AIs)."""
    try:
        me_id, my_tenant, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    resp = ddb.scan(TableName=USERS_TABLE)
    profiles = []
    for it in resp.get("Items", []):
        tid = it.get("tenant_id", {}).get("S", "")
        tenant = _get_tenant(tid) if tid else None
        if not tenant:
            continue
        profiles.append({
            "user_id": it["user_id"]["S"],
            "tenant_id": tid,
            "display_name": tenant.get("display_name", ""),
            "domain": tenant.get("domain", ""),
            "is_me": tid == my_tenant,
        })
    profiles.sort(key=lambda p: p["display_name"].lower())
    return {"users": profiles}


@app.get("/api/users/{user_id}")
def get_profile(user_id: str, request: Request):
    """One profile by user_id (for loading a /u/:userId page directly)."""
    try:
        _me_id, my_tenant, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    try:
        item = ddb.get_item(TableName=USERS_TABLE, Key={"user_id": {"S": user_id}}).get("Item")
    except ClientError:
        item = None
    if not item:
        return JSONResponse(status_code=404, content={"error": "profile not found"})
    tid = item.get("tenant_id", {}).get("S", "")
    tenant = _get_tenant(tid) if tid else None
    if not tenant:
        return JSONResponse(status_code=404, content={"error": "profile not found"})
    return {
        "user_id": user_id, "tenant_id": tid,
        "display_name": tenant.get("display_name", ""),
        "domain": tenant.get("domain", ""),
        "is_me": tid == my_tenant,
    }


@app.get("/api/tenants/{tenant_id}/posts")
def list_profile_posts(tenant_id: str, request: Request):
    """A profile's posts (any logged-in user can view)."""
    try:
        _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
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


@app.get("/api/tenants/{tenant_id}/posts/{post_id}")
def get_post(tenant_id: str, post_id: str, request: Request):
    """Read one post's full content (metadata from DynamoDB, body from S3)."""
    try:
        _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    try:
        item = ddb.get_item(
            TableName=POSTS_TABLE,
            Key={"tenant_id": {"S": tenant_id}, "post_id": {"S": post_id}},
        ).get("Item")
    except ClientError as e:
        # Don't silently masquerade an infra error (e.g. IAM AccessDenied) as
        # "not found" — log it so it's diagnosable.
        log.error("get_post ddb error", error=str(e), post_id=post_id, tenant_id=tenant_id)
        return JSONResponse(status_code=500, content={"error": "could not load post"})
    if not item:
        return JSONResponse(status_code=404, content={"error": "post not found"})

    content = ""
    s3_key = item.get("s3_key", {}).get("S")
    if s3_key:
        try:
            content = s3.get_object(Bucket=S3_CONTENT_BUCKET, Key=s3_key)["Body"].read().decode("utf-8")
        except ClientError:
            content = ""
    return {
        "post_id": post_id,
        "title": item.get("title", {}).get("S", ""),
        "content": content,
        "status": item.get("ingestion_status", {}).get("S", ""),
        "created_at": int(item.get("created_at", {}).get("N", "0")),
    }


# ---------------------------------------------------------------------------
# Saved chats — conversation memory, up to MAX_ACTIVE per user
# ---------------------------------------------------------------------------

def _chat_summary(c: dict) -> dict:
    return {
        "chat_id": c["chat_id"], "tenant_id": c["tenant_id"],
        "profile_user_id": c.get("profile_user_id", ""), "profile_name": c["profile_name"],
        "title": c["title"], "status": c["status"], "updated_at": c["updated_at"],
        "message_count": len(c["messages"]),
    }


@app.post("/api/chats")
def new_chat(req: NewChatRequest, request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    tenant = _get_tenant(req.tenant_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"error": "profile not found"})
    try:
        chat = chatstore.create_chat(user_id, req.tenant_id, tenant["display_name"], req.profile_user_id or "")
    except chatstore.ChatLimitError as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    return chat


@app.get("/api/chats")
def get_chats(request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    return {"chats": [_chat_summary(c) for c in chatstore.list_chats(user_id, "active")]}


@app.get("/api/chats/trash")
def get_trash(request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    return {"chats": [_chat_summary(c) for c in chatstore.list_chats(user_id, "trashed")]}


@app.get("/api/chats/{chat_id}")
def get_chat_detail(chat_id: str, request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    chat = chatstore.get_chat(user_id, chat_id)
    if not chat:
        return JSONResponse(status_code=404, content={"error": "chat not found"})
    return chat


@app.delete("/api/chats/{chat_id}")
def trash_chat(chat_id: str, request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    chatstore.set_status(user_id, chat_id, "trashed")
    return {"ok": True}


@app.post("/api/chats/{chat_id}/restore")
def restore_chat(chat_id: str, request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    try:
        chatstore.set_status(user_id, chat_id, "active")
    except chatstore.ChatLimitError as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    return {"ok": True}


@app.delete("/api/chats/{chat_id}/permanent")
def delete_chat_permanent(chat_id: str, request: Request):
    try:
        user_id, _, _ = _require_login(request)
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})
    chatstore.delete_permanent(user_id, chat_id)
    return {"ok": True}


@app.post("/ask")
@app.post("/api/ask")
async def ask(req: AskRequest, request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()

    # Must be logged in to ask at all (identity of the *visitor*)...
    try:
        asker_id, _asker_tenant, _ = get_context_from_headers(dict(request.headers))
    except ContextError as e:
        return JSONResponse(status_code=401, content={"error": "Unauthorized", "detail": str(e)})

    # ...but the AI you ask is the *profile you're visiting* (any tenant).
    tenant_id = (req.tenant_id or "").strip()
    if not tenant_id:
        return JSONResponse(status_code=400, content={"error": "tenant_id (profile) is required"})

    tenant = _get_tenant(tenant_id)
    if not tenant:
        return JSONResponse(status_code=404, content={"error": "profile not found"})

    question = (req.question or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "question is required"})

    log.info("query start", asker_id=asker_id, tenant_id=tenant_id, request_id=request_id)

    # Conversation memory: load prior turns from the saved chat (if any).
    chat_id = (req.chat_id or "").strip() or None
    history = []  # for the LLM: [{"role","content"}]
    if chat_id:
        chat = chatstore.get_chat(asker_id, chat_id)
        if chat and chat["status"] == "active" and chat["tenant_id"] == tenant_id:
            for m in chat["messages"][-8:]:  # last few turns keeps token cost bounded
                history.append({"role": m["role"], "content": m.get("text", "")})

    # Multi-turn retrieval: for short follow-ups ("yes", "tell me more"), fold in
    # the previous user question so retrieval still finds the right content.
    retrieval_query = question
    prev_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), None)
    if prev_user and len(question.split()) <= 4:
        retrieval_query = f"{prev_user} {question}"

    # Everything below runs inside the streamed generator. Starlette iterates a
    # sync generator in a threadpool, so blocking calls (Bedrock, Qdrant, Groq)
    # are fine and don't block the event loop.
    def event_stream():
        # --- Semantic cache (single-turn only; conversation context would make a
        # cached answer wrong for follow-ups). Embed the question once and reuse
        # the vector for retrieval on a miss. ---
        q_dense = None
        if not history:
            q_dense = _embed_dense(question)
            hit = semcache.lookup(tenant_id, q_dense)
            if hit is not None:
                log.info("relevance", request_id=request_id, top_dense=hit["score"],
                         floor=RETRIEVAL_FLOOR, hits=len(hit["citations"]), result_type="cache_hit")
                yield _ndjson({"type": "content", "text": hit["answer"]})
                yield _ndjson({"type": "done", "citations": hit["citations"], "cache_hit": True})
                _log_usage(tenant_id, asker_id, request_id, question, 0, 0, "cache_hit", started_at)
                if chat_id:
                    chatstore.append_turn(asker_id, chat_id, question, hit["answer"], hit["citations"])
                return

        results, top_dense = _hybrid_search(retrieval_query, tenant_id, dense_vec=q_dense)

        def log_relevance(result_type):
            # One line per query carrying BOTH the score and the outcome, so the
            # floor can be calibrated from data: the floor should sit just below
            # the min(top_dense) of queries with result_type=answered. Tune via:
            #   filter msg="relevance" and result_type="answered" | stats min(top_dense)
            log.info("relevance", request_id=request_id, top_dense=round(top_dense, 3),
                     floor=RETRIEVAL_FLOOR, hits=len(results), result_type=result_type)

        # No relevant content retrieved. "who is he?" and "his take on quantum
        # physics?" BOTH land here for a fitness blogger — same empty retrieval,
        # opposite right answers, and the score can't tell them apart. So:
        #   0 posts total → honest "hasn't published anything yet" (no LLM)
        #   has posts     → ONE LLM call decides from the profile card: answer as
        #                   an overview if it's a who-are-they question, else
        #                   decline. No brittle keyword gate.
        if not results or top_dense < RETRIEVAL_FLOOR:
            titles = _tenant_post_titles(tenant_id)

            if not titles:
                msg = f"{tenant['display_name']} hasn't published any posts yet, so there's nothing for me to answer from."
                yield _ndjson({"type": "content", "text": msg})
                yield _ndjson({"type": "done", "citations": [], "cache_hit": False})
                log_relevance("empty_corpus")
                _log_usage(tenant_id, asker_id, request_id, question, 0, 0, "empty_corpus", started_at)
                if chat_id:
                    chatstore.append_turn(asker_id, chat_id, question, msg, [])
                return

            # Let the model decide overview-vs-decline, grounded in name + domain
            # + post titles. It answers identity/overview questions and declines
            # (with the canonical line) genuine topic misses.
            parts, tin, tout = [], 0, 0
            try:
                for ev in stream_answer(_build_profile_prompt(tenant, titles), question, history=history):
                    if ev["type"] == "content":
                        parts.append(ev["text"]); yield _ndjson(ev)
                    elif ev["type"] == "usage":
                        tin, tout = ev["input_tokens"], ev["output_tokens"]
            except Exception as e:
                log.error("profile stream failed", error=str(e), request_id=request_id)
                yield _ndjson({"type": "content", "text": "\n\n[Error while generating response]"})
            text = "".join(parts)
            rtype = "declined" if "hasn't written about this topic" in text.lower() else "overview"
            yield _ndjson({"type": "done", "citations": [], "cache_hit": False})
            log_relevance(rtype)
            _log_usage(tenant_id, asker_id, request_id, question, tin, tout, rtype, started_at)
            if chat_id:
                chatstore.append_turn(asker_id, chat_id, question, text, [])
            return

        system_prompt = _build_system_prompt(tenant, results)

        answer_parts: list[str] = []
        total_input = 0
        total_output = 0
        try:
            for ev in stream_answer(system_prompt, question, history=history):
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
        answer_text = "".join(answer_parts)
        refused = "hasn't written about" in answer_text.lower()
        citations = [] if refused else _dedupe_citations(results)
        result_type = "refused" if refused else "answered"
        log_relevance(result_type)

        # Cache only clean single-turn answers (not refusals/clarifications, not
        # follow-ups). Invalidated when the tenant writes (see ingest_worker).
        if result_type == "answered" and not history and q_dense is not None:
            semcache.store(tenant_id, question, q_dense, answer_text, citations)

        yield _ndjson({"type": "done", "citations": citations, "cache_hit": False})
        _log_usage(tenant_id, asker_id, request_id, question, total_input, total_output, result_type, started_at)
        if chat_id:
            chatstore.append_turn(asker_id, chat_id, question, answer_text, citations)

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


def _tenant_post_titles(tenant_id: str) -> list[str]:
    """Titles of everything a profile has published — the 'profile card' used to
    answer identity/overview questions when retrieval finds nothing relevant."""
    try:
        resp = ddb.query(
            TableName=POSTS_TABLE,
            KeyConditionExpression="tenant_id = :t",
            ExpressionAttributeValues={":t": {"S": tenant_id}},
            ProjectionExpression="title",
        )
    except ClientError:
        return []
    return [i["title"]["S"] for i in resp.get("Items", []) if i.get("title", {}).get("S")]


def _build_profile_prompt(tenant: dict, titles: list[str]) -> str:
    """Prompt for the empty-retrieval decision: given only the profile card
    (name, domain, post titles), the model either answers an identity/overview
    question or declines a genuine topic miss with the canonical line."""
    title_list = "\n".join(f"- {t}" for t in titles)
    return f"""You are {tenant['display_name']}'s AI. Retrieval found no post that closely matches the visitor's question, so choose ONE of two responses.

{tenant['display_name']}'s primary domain: {tenant['domain']}.
Titles of everything {tenant['display_name']} has published:
{title_list}

1. If the question is about WHO {tenant['display_name']} is, what they write about, or a general overview of them → answer in 2-4 warm, natural sentences describing who they are *based on what they write about* — their domain and the themes across these titles. Do NOT invent biographical facts (age, job, location, name meaning) the titles/domain don't support. If a title directly answers it, name it. End by inviting a follow-up on a specific topic they cover.

2. If the question is about a SPECIFIC topic these titles don't cover → reply with EXACTLY this line and nothing else: "{tenant['display_name']} hasn't written about this topic."

Don't list the titles mechanically and don't copy any bracket markers."""


def _hybrid_search(question: str, tenant_id: str, dense_vec: list | None = None):
    """Return (ranked_points, top_dense_cosine).

    Hybrid RRF gives good *ranking* + citations, but its fused score is a rank
    number (≈1.0 for the top hit even on a tiny blog), so it's useless as a
    relevance gate. We separately fetch the top *dense cosine similarity* — an
    absolute 0..1 measure of semantic closeness — and let the caller gate on it
    so tangential/keyword-only matches ("chennai", "food"→coffee) get declined.
    """
    dense = dense_vec if dense_vec is not None else _embed_dense(question)
    sparse = _embed_sparse(question)
    qdrant = _get_qdrant_client()
    tenant_filter = Filter(must=[
        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
    ])

    # Absolute dense relevance (cosine) — the real "is this on-topic?" signal.
    dense_res = qdrant.query_points(
        collection_name=COLLECTION_NAME, query=dense, using="dense",
        query_filter=tenant_filter, limit=1, with_payload=False,
    )
    top_dense = dense_res.points[0].score if dense_res.points else 0.0

    # Hybrid RRF for ranking + citations.
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
        query_filter=tenant_filter,
        limit=TOP_K,
        with_payload=True,
    )
    return result.points, top_dense


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

These excerpts are {tenant['display_name']}'s entire knowledge. Use the conversation so
far for context. If your previous message offered a topic and the user now affirms it
(e.g. "yes", "yeah", "sure", "ok", "go on", "please"), treat that as case A for that
topic — ANSWER it from the excerpts, do NOT ask again.

Otherwise decide which case the question falls into and respond accordingly:

A. DIRECTLY RELEVANT — the excerpts clearly answer it (directly, or a clearly-related
   angle). → Answer using ONLY the excerpts, cite sources by title, be concise. Do not
   hedge about what they "really" meant — just answer.

B. VAGUE / INCOMPLETE / ONLY LOOSELY RELATED — e.g. a bare keyword, or a broad question
   the excerpts only touch tangentially. → Do NOT force an answer and do NOT ramble. In
   one or two sentences: say {tenant['display_name']} hasn't written about exactly that,
   name the closest thing they HAVE written about (by title), and ask a short clarifying
   question. For example: "{tenant['display_name']} hasn't written about that exactly,
   but has written about <closest topic>. Want me to tell you about that?"

C. UNRELATED — a plainly different subject with nothing close in the excerpts. → Reply
   with EXACTLY this and nothing else: "{tenant['display_name']} hasn't written about this."

Write in natural prose. NEVER copy the bracketed "[Source: ...]" labels or the
"[... / ...]" section markers from the excerpts into your reply — when you cite, just
name the post title in a normal sentence (e.g. "in his post *The Best Filter Coffee in
Chennai*"). Always match {tenant['display_name']}'s tone. For medical, legal, or
financial topics add a brief disclaimer (not personalized advice; consult a professional).
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
