#!/usr/bin/env bash
# Build multitenant-rag-current.zip and refuse to produce it if a secret is found.
#
# WHY THIS SCRIPT EXISTS
# On 2026-08-24 a hand-written `zip` invocation swept `multitenant-rag/local/.env`
# — which holds a REAL Qdrant API key and the dev JWT signing secret — into the
# committed archive, and the ad-hoc scanner reported CLEAN because it only matched
# QUOTED assignments and a few known key prefixes. The exclusion list and the scan
# are now one command that fails closed.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
OUT="multitenant-rag-current.zip"
PY="${PY:-multitenant-rag/.venv/bin/python}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

rm -f "$OUT"
zip -qr "$OUT" multitenant-rag \
  -x '*/.venv/*' '*/__pycache__/*' '*.pyc' '*/node_modules/*' '*/.git/*' \
     '*/output/*' '*/.pytest_cache/*' \
     '*/.env' '*.env' '*/.env.*' '*.pem' '*.key' '*/credentials' '*/credentials.*' \
     '*.p12' '*id_rsa*' '*.keystore'
# .env.example is intentionally shareable (placeholders only) and is re-added.
zip -q "$OUT" multitenant-rag/local/.env.example 2>/dev/null || true

unzip -qq "$OUT" -d "$STAGE"
if ! "$PY" multitenant-rag/tools/secret_scan.py "$STAGE"; then
  rm -f "$OUT"
  echo "ARCHIVE DELETED — secret scan failed. Fix the finding and re-run." >&2
  exit 1
fi
echo "archive OK: $OUT ($(unzip -l "$OUT" | tail -1 | awk '{print $2}') files)"
