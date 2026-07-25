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
    return get_secret("multitenant/groq")["api_key"]


def get_qdrant() -> tuple[str, str]:
    """Return (url, api_key) for Qdrant Cloud."""
    creds = get_secret("multitenant/qdrant")
    return creds["url"], creds["api_key"]
