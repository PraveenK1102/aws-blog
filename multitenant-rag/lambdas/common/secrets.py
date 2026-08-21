"""Cached AWS Secrets Manager access.

Fetches secrets once per Lambda cold start, caches in memory.
Reduces both latency and API calls (Secrets Manager is not free per-call).
"""

import functools
import json
import os

import boto3


_sm = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


@functools.lru_cache(maxsize=8)
def get_secret(secret_id: str) -> dict:
    """
    Retrieve a JSON-encoded secret. Cached for the lifetime of the Lambda
    execution environment (survives across warm invocations).

    To rotate credentials, you must redeploy or wait for the container to
    cycle. Acceptable trade-off for learning; in production use rotation
    hooks.
    """
    resp = _sm.get_secret_value(SecretId=secret_id)
    return json.loads(resp["SecretString"])


def get_groq_key() -> str:
    # Local dev override: a GROQ_API_KEY env var wins over Secrets Manager, so a
    # local .env (e.g. an alternate account's key) needs no code or cloud change.
    env = os.environ.get("GROQ_API_KEY")
    if env:
        return env
    return get_secret("multitenant/groq")["api_key"]


def get_qdrant() -> tuple[str, str]:
    """Return (url, api_key) for Qdrant Cloud.

    Local dev override: QDRANT_URL + QDRANT_API_KEY env vars win over Secrets
    Manager (so local runs need no AWS Secrets call).
    """
    url = os.environ.get("QDRANT_URL")
    key = os.environ.get("QDRANT_API_KEY")
    if url and key:
        return url, key
    creds = get_secret("multitenant/qdrant")
    return creds["url"], creds["api_key"]


def get_jwt_secret() -> str:
    """HMAC secret for signing JWTs. Local dev override via JWT_SECRET env."""
    env = os.environ.get("JWT_SECRET")
    if env:
        return env
    return get_secret("multitenant/jwt")["secret"]


def get_langsmith_key() -> str | None:
    """LangSmith API key for observability tracing.

    Local dev override via LANGSMITH_API_KEY env; prod via Secrets Manager
    `multitenant/langsmith` (JSON with an `api_key` field). Unlike the Groq/
    Qdrant/JWT helpers this NEVER raises and returns None when no key is
    resolvable — tracing is optional and must never break the request path, so
    a missing secret simply disables tracing rather than failing a cold start.
    """
    env = os.environ.get("LANGSMITH_API_KEY")
    if env:
        return env
    try:
        return get_secret("multitenant/langsmith").get("api_key")
    except Exception:
        # Secret not created yet / no access — tracing stays disabled. This is a
        # supported state (the AWS secret + IAM are a separate deployment step).
        return None
