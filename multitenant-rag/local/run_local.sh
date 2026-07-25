#!/usr/bin/env bash
# Run the ask FastAPI app locally with uvicorn (hot-reload).
# Same Bedrock (embeddings) + Qdrant + DynamoDB as prod; LLM = Groq (8b).
# Uses your local ~/.aws creds. Ctrl-C to stop.  ->  http://localhost:8080
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$DIR/.."
set -a
[ -f "$DIR/.env" ] && source "$DIR/.env"
set +a
cd "$ROOT/lambdas/ask"
exec env PYTHONPATH="..:." "$ROOT/.venv/bin/uvicorn" app:app \
  --host 0.0.0.0 --port 8080 --reload
