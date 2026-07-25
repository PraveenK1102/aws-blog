from __future__ import annotations

import json
from typing import List

import boto3

from .config import AWS_REGION, BEDROCK_EMBED_MODEL_ID, EMBED_DIM

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _client


def embed_text(text: str) -> List[float]:
    body = json.dumps({"inputText": text, "dimensions": EMBED_DIM, "normalize": True})
    resp = _bedrock().invoke_model(
        modelId=BEDROCK_EMBED_MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(resp["body"].read())
    return payload["embedding"]


def embed_batch(texts: List[str]) -> List[List[float]]:
    # Titan doesn't expose a native batch endpoint; loop sequentially.
    # Boto3 keeps the connection warm within a single client instance.
    return [embed_text(t) for t in texts]
