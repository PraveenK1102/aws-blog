from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://askpraveen:askpraveen@localhost:5433/askpraveen",
)
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
BEDROCK_EMBED_MODEL_ID = os.environ.get(
    "BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"
)
BEDROCK_LLM_MODEL_ID = os.environ.get(
    "BEDROCK_LLM_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
TOP_K = int(os.environ.get("TOP_K", "5"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONTENT_ROOT = Path(os.environ.get("CONTENT_ROOT", str(REPO_ROOT))).resolve()

EMBED_DIM = 1024
