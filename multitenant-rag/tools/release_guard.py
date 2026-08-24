"""Path-based release-artifact guard — the PRIMARY control.

WHY THIS EXISTS SEPARATELY FROM secret_scan.py
----------------------------------------------
On 2026-08-24 a real `.env` (live Qdrant API key + dev JWT secret) entered a
committed, publicly-pushed archive. Content-based regex scanning is a
*secondary* net and it failed: the scanner only matched quoted assignments and a
few known key prefixes, and a Qdrant key has neither.

The primary rule is therefore about PATHS, not content, and it is absolute:

    NO .env FILE MAY ENTER THE RELEASE ARCHIVE AT ALL.

A path rule cannot be defeated by an unfamiliar key format, a new provider, a
base64 blob, or an unquoted line. `.env.example` is the single allowlisted
exception because it contains placeholders only — and it is itself content-scanned.

This module is pure and import-safe: it takes a list of archive entry names and
returns findings. That makes it directly testable without building an archive.
"""
import posixpath
import re

# Absolute path rules. Matched against each archive entry, case-insensitively,
# on the BASENAME and on the full posix path, so nesting depth is irrelevant.
FORBIDDEN_BASENAMES = (
    ".env",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".netrc",
    ".pgpass",
    ".htpasswd",
)

# Any basename matching one of these is forbidden regardless of directory.
FORBIDDEN_PATTERNS = (
    re.compile(r"(?i)^\.env($|\..*)"),        # .env, .env.local, .env.production
    re.compile(r"(?i).*\.env$"),              # foo.env, prod.env
    re.compile(r"(?i).*\.(pem|key|p12|pfx|keystore|jks|asc|ppk)$"),
    re.compile(r"(?i)^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$"),
    re.compile(r"(?i)^credentials(\..*)?$"),
    re.compile(r"(?i)^\.(netrc|pgpass|htpasswd|npmrc|pypirc)$"),
    re.compile(r"(?i).*\.(kdbx|ovpn)$"),
)

# The ONLY allowlisted exceptions. Deliberately tiny and exact — an allowlist
# that accepts patterns is how `.env.production` sneaks back in.
ALLOWED_EXACT = (
    "multitenant-rag/local/.env.example",
)


def _normalise(name: str) -> str:
    """Normalise an archive entry path.

    NOT `lstrip("./")` — that strips CHARACTERS, so a root-level ".env" became
    "env" and was allowed through, defeating the whole control. Only a literal
    leading "./" prefix is removed.
    """
    norm = name.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def is_forbidden(name: str) -> str | None:
    """Return the rule name that forbids this entry, or None if it is allowed."""
    norm = _normalise(name)
    if norm.endswith("/"):
        return None                                  # directory entry
    if norm in ALLOWED_EXACT:
        return None
    base = posixpath.basename(norm)
    if base.lower() in FORBIDDEN_BASENAMES:
        return f"forbidden-basename:{base}"
    for pat in FORBIDDEN_PATTERNS:
        if pat.match(base):
            return f"forbidden-pattern:{pat.pattern}"
    return None


def check_entries(names) -> list[tuple[str, str]]:
    """(path, rule) for every entry that must not be in a release artifact."""
    out = []
    for n in names:
        rule = is_forbidden(n)
        if rule:
            out.append((_normalise(n), rule))
    return out


def check_zip(path: str) -> list[tuple[str, str]]:
    import zipfile
    with zipfile.ZipFile(path) as z:
        return check_entries(z.namelist())


if __name__ == "__main__":
    import sys
    findings = check_zip(sys.argv[1])
    for p, rule in findings:
        print(f"  [FORBIDDEN] {p}  ({rule})")
    print(f"  forbidden entries: {len(findings)}")
    print("  RESULT: CLEAN" if not findings else "  RESULT: *** DO NOT SHIP ***")
    sys.exit(1 if findings else 0)
