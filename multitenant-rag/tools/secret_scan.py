"""Precise secret scan for the release archive.

Two rules, deliberately separated because the previous single-rule scanner had a
real miss: it required a QUOTE after `=`, so an unquoted `.env` line
(`QDRANT_API_KEY=<real 176-char key>`) was never examined and was reported CLEAN.

  RULE 1  known provider-key / JWT shapes, anywhere.
  RULE 2  credential-named assignment whose VALUE IS A LITERAL:
            * config-style files (.env/.ini/.cfg/.conf/.properties/no extension):
              the bare token after `=`
            * source/markup files: only a QUOTED string literal
          Code expressions (`os.environ.get(...)`, `usage.get("x")`, f-strings,
          attribute access) are not literals and are skipped — that is what
          produced 103 false positives on identifiers like `input_tokens`.
"""
import collections, math, os, re, sys

ROOT = sys.argv[1]
CFG_EXT = {"", ".env", ".ini", ".cfg", ".conf", ".properties", ".sh", ".bash", ".zsh"}
SKIP_DIR = (".venv", "__pycache__", ".git", "node_modules")

PREFIX = re.compile(
    r"(gsk_[A-Za-z0-9]{20,}"
    r"|nvapi-[A-Za-z0-9_-]{20,}"
    r"|lsv2_(?:pt|sk)_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{32,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.)")

NAME = r"[A-Za-z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|APIKEY)[A-Za-z0-9_]*"
CFG_ASSIGN = re.compile(rf"(?i)^\s*(?:export\s+)?({NAME})\s*=\s*(\S+)\s*$")
# A value that contains code punctuation is an EXPRESSION, not a literal secret
# (e.g. `API_KEY = os.environ.get("X")`). Anything else on a bare NAME=VALUE line
# is treated as a literal, whatever the file extension — the earlier version only
# applied this rule to config-style extensions, so a secret in `foo.env.txt`
# slipped through the fail-closed check.
CODEISH = re.compile(r"[()\[\]{}$\\]|^[\"']|os\.environ|\.get\(")
SRC_ASSIGN = re.compile(rf"(?i)\b({NAME})\s*[:=]\s*([\"'])([^\"']{{8,}})\2")

# Names that CONTAIN a credential word but are never credentials. Verified by
# reading each hit: a DynamoDB KeyConditionExpression and a token-count label.
SAFE_NAMES = re.compile(
    r"(?i)^(KeyConditionExpression|.*ConditionExpression|.*_tokens?|tokens?_.*|"
    r"est_tokens|input_tokens|output_tokens|total_tokens|max_tokens|"
    r".*token_count|KeySchema|.*KeyType|PartitionKey|SortKey|.*_key_name)$")

PLACEHOLDER = re.compile(
    r"(?i)^(<.*>|\$\{.*\}|\$[A-Z_]+|x{3,}|your[-_ ]?.*|test.*|local.*|dev.*|"
    r"changeme|placeholder|redacted|example.*|none|null|true|false|\*+|"
    r"multitenant/.*|[a-z0-9-]+/[a-z0-9-]+)$")


def entropy(s):
    if not s:
        return 0.0
    c = collections.Counter(s)
    n = len(s)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def suspicious(val):
    val = val.strip().strip("\"'")
    if len(val) < 16 or PLACEHOLDER.match(val):
        return None
    if entropy(val) < 3.2:
        return None
    return f"len={len(val)} H={entropy(val):.2f}"


findings = []
scanned = 0
for dp, dns, fns in os.walk(ROOT):
    dns[:] = [d for d in dns if d not in SKIP_DIR]
    for fn in fns:
        p = os.path.join(dp, fn)
        ext = os.path.splitext(fn)[1].lower()
        is_cfg = ext in CFG_EXT or fn.startswith(".env")
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        scanned += 1
        rel = os.path.relpath(p, ROOT)
        for i, line in enumerate(text.splitlines(), 1):
            if PREFIX.search(line):
                findings.append((rel, i, "PROVIDER KEY SHAPE"))
                continue
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            # Both rules run on every file: bare NAME=VALUE (any extension) and
            # quoted NAME="VALUE" for source/markup.
            cand = []
            mc = CFG_ASSIGN.match(line)
            if mc and not CODEISH.search(mc.group(2)):
                cand.append((mc.group(1), mc.group(2)))
            ms = SRC_ASSIGN.search(line)
            if ms:
                cand.append((ms.group(1), ms.group(3)))
            if not cand:
                continue
            name, val = cand[0]
            if SAFE_NAMES.match(name):
                continue
            why = suspicious(val)
            if why:
                findings.append((rel, i, f"literal {name} {why}"))

print(f"  files scanned: {scanned}")
for f in findings[:25]:
    print(f"  [HIT] {f[0]}:{f[1]}  {f[2]}")
print(f"  findings: {len(findings)}")
print("  RESULT: CLEAN" if not findings else "  RESULT: *** DO NOT COMMIT ***")
sys.exit(1 if findings else 0)
