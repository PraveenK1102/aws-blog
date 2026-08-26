"""Deterministic identity derivation for the curated 25-user corpus.

DECISION 1 / 1A (architect-approved 2026-08-25):
  email    = "<exact-corpus-username>@example.com"   (example.com = synthetic,
             non-deliverable domain; the corpus username is preserved verbatim as
             the local part because the production user model has NO username
             field — a real mutable profile username is a separate UI task)
  password = HMAC-SHA256(master_secret, "curated-corpus-v1:" + exact_username)

The master secret is read from the environment (entered once via a hidden shell
prompt) and is NEVER printed, logged, persisted, committed, placed in a command
line, or written to any manifest/report/archive. Derived passwords live only in
memory for the duration of one request and are discarded immediately.

Properties this construction guarantees:
  * deterministic  — a resumed run derives the same password, so it can log in
  * unique         — a different username yields a different password
  * non-reversible — the manifest/report can never leak a password because none
                     is ever written anywhere
"""
import base64
import hashlib
import hmac
import os

DOMAIN = "example.com"
DERIVATION_CONTEXT = "curated-corpus-v1:"
MASTER_ENV = "CORPUS_SEED_MASTER"


class MasterSecretMissing(RuntimeError):
    """The master secret was not supplied. Never prompt inside library code."""


def derive_email(username: str) -> str:
    """Exact corpus username as the local part. No normalisation, no lowercasing
    beyond what the corpus already provides — §4 forbids normalising usernames."""
    u = (username or "").strip()
    if not u:
        raise ValueError("empty username")
    return f"{u}@{DOMAIN}"


def get_master_secret() -> str:
    m = os.environ.get(MASTER_ENV)
    if not m:
        raise MasterSecretMissing(
            f"{MASTER_ENV} is not set. Enter it via a hidden prompt; never pass it "
            "on a command line.")
    return m


def derive_password(username: str, master: str | None = None) -> str:
    """Per-persona password, derived in memory only.

    urlsafe-base64 of the HMAC digest, trimmed to 32 chars — comfortably above
    the application's >=8 requirement, and drawn from base64's alphabet so it
    survives JSON/HTTP without escaping.
    """
    key = (master if master is not None else get_master_secret()).encode("utf-8")
    msg = (DERIVATION_CONTEXT + (username or "").strip()).encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:32]
