#!/usr/bin/env bash
# Build multitenant-rag-current.zip under TWO independent controls, failing closed.
#
# WHY THIS SCRIPT EXISTS
# On 2026-08-24 a hand-written `zip` invocation swept `multitenant-rag/local/.env`
# — a REAL Qdrant API key and the dev JWT signing secret — into a committed,
# publicly-pushed archive. The ad-hoc content scan reported CLEAN because it only
# matched QUOTED assignments and a handful of known key prefixes.
#
# CONTROL 1 (primary, path-based):   release_guard.py — NO .env may enter, ever.
#                                    Cannot be defeated by an unknown key format.
# CONTROL 2 (secondary, content):    secret_scan.py — run on the EXTRACTED archive,
#                                    not the source tree, so it sees exactly what
#                                    would ship.
# Either control failing DELETES the archive.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
OUT="multitenant-rag-current.zip"
PY="${PY:-multitenant-rag/.venv/bin/python}"
TOOLS="multitenant-rag/tools"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

fail() { rm -f "$OUT"; echo "ARCHIVE DELETED — $1" >&2; exit 1; }

rm -f "$OUT"
zip -qr "$OUT" multitenant-rag \
  -x '*/.venv/*' '*/__pycache__/*' '*.pyc' '*/node_modules/*' '*/.git/*' \
     '*/output/*' '*/.pytest_cache/*' \
     '*/.env' '*.env' '*/.env.*' '*.pem' '*.key' '*/credentials' '*/credentials.*' \
     '*.p12' '*.pfx' '*id_rsa*' '*.keystore' '*.jks' '*/.netrc' '*/.pgpass' '*/.npmrc'
# .env.example is intentionally shareable (placeholders only) and is re-added.
# It is still subject to BOTH controls below.
zip -q "$OUT" multitenant-rag/local/.env.example 2>/dev/null || true

echo "--- CONTROL 1: forbidden paths (primary) ---"
"$PY" "$TOOLS/release_guard.py" "$OUT" || fail "forbidden file present in archive"

echo "--- CONTROL 2: content scan of the EXTRACTED archive (secondary) ---"
unzip -qq "$OUT" -d "$STAGE"
"$PY" "$TOOLS/secret_scan.py" "$STAGE" || fail "secret scan failed"

ENV_COUNT="$(unzip -l "$OUT" | grep -cE '/\.env$|^\s*[0-9]+.*\s\.env$' || true)"
echo "--- SUMMARY ---"
echo "  files:        $(unzip -l "$OUT" | tail -1 | awk '{print $2}')"
echo "  .env entries: ${ENV_COUNT}  (must be 0; .env.example is not a .env)"
[ "${ENV_COUNT}" = "0" ] || fail ".env present in archive"
echo "  archive OK:   $OUT"
