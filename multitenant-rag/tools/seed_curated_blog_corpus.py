"""Seed the curated 25-user / 268-post corpus through the SUPPORTED application path.

Modes:
  --validate   parse + validate the corpus, write the manifest. No network.
  --plan       resolve what would be created vs reused. Read-only AWS.
  --apply      create/reuse personas and posts. MUTATES production.
  --verify     verify DynamoDB / S3 / Qdrant against the manifest. Read-only.

Design constraints honoured:
  * Posts are created via the application's HTTP API (signup/login + POST
    /api/posts), so S3/DynamoDB/SQS invariants and authorization are the
    application's, not this script's (§8).
  * The S3 Markdown body is the EXACT corpus post body. No Date line, no Tags
    line, no seed marker is ever injected (DECISION 2).
  * After a post is created, exactly two metadata fields are patched on that one
    DynamoDB item under a condition tied to tenant_id + post_id + content_hash:
    `created_at` (corpus date at 00:00:00 UTC) and `tags` (exact corpus list).
  * Idempotent and resumable: identity by (owner, title, date, body_sha256).
  * The master secret and derived passwords are never written anywhere.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import boto3
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_parser as CP
from corpus_dates import corpus_date_to_epoch
from corpus_identity import derive_email, derive_password, get_master_secret

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
MANIFEST = os.path.join(HERE, "corpus_25_manifest.json")
STATE = os.path.join(OUT, "seed_state.json")

API = os.environ.get("CORPUS_API_BASE",
                     "https://pdp1o70aug.execute-api.ap-south-1.amazonaws.com")
REGION = os.environ.get("AWS_REGION", "ap-south-1")
POSTS_TABLE = os.environ.get("POSTS_TABLE", "multitenant-posts")
USERS_TABLE = os.environ.get("USERS_TABLE", "multitenant-users")
TIMEOUT = 30
POST_PACING_S = float(os.environ.get("CORPUS_POST_PACING_S", "0.35"))

_ddb = boto3.client("dynamodb", region_name=REGION)


class Conflict(RuntimeError):
    pass


# ------------------------------------------------------------------ manifest
def build_manifest(corpus_path: str) -> dict:
    raw = open(corpus_path, encoding="utf-8").read()
    parsed = CP.parse(raw)
    problems = CP.validate(parsed)
    if problems:
        raise SystemExit("CORPUS VALIDATION FAILED:\n  " + "\n  ".join(problems))
    man = {"source_file": os.path.basename(corpus_path),
           "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
           "ingest_region_sha256": parsed["ingest_sha256"],
           "data_classification": "curated seeded demo users / curated 25-user corpus",
           "expected_users": CP.EXPECTED_USERS, "expected_posts": CP.EXPECTED_POSTS,
           "users": [], "posts": []}
    for u in parsed["users"]:
        un = u["fields"]["Username"]
        man["users"].append({
            "username": un, "display_name": u["display_name"],
            "email": derive_email(un),
            "expected_post_count": len(u["posts"]),
            "corpus_age": u["fields"].get("Age"),
            "corpus_origin": u["fields"].get("Origin"),
            "corpus_gender": u["fields"].get("Gender"),
            "corpus_content_type": u["fields"].get("Content Type")})
        for p in u["posts"]:
            man["posts"].append({
                "username": un, "title": p["title"], "date": p["date"],
                "created_at_epoch": corpus_date_to_epoch(p["date"]),
                "body_sha256": p["body_sha256"], "tags": p["tags"],
                "body_chars": len(p["body"])})
    return man, parsed


def bodies_by_key(parsed) -> dict:
    """(username, title, date) -> exact body. Held in memory only; never written."""
    out = {}
    for u in parsed["users"]:
        un = u["fields"]["Username"]
        for p in u["posts"]:
            out[(un, p["title"], p["date"])] = p["body"]
    return out


# --------------------------------------------------------------------- state
def load_state() -> dict:
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"users": {}, "posts": {}}


def save_state(s: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


# ----------------------------------------------------------------- app calls
def api(method: str, path: str, **kw):
    return requests.request(method, f"{API}{path}", timeout=TIMEOUT, **kw)


def ensure_persona(u: dict, master: str, state: dict) -> dict:
    """Login if the persona exists, otherwise sign it up. Never logs the secret."""
    email = u["email"]
    pw = derive_password(u["username"], master)          # in memory only
    try:
        r = api("POST", "/api/auth/login", json={"email": email, "password": pw})
        if r.status_code == 200:
            d = r.json()
            return {"action": "reused", "token": d["token"],
                    "user_id": d["user"]["user_id"], "tenant_id": d["user"]["tenant_id"]}
        r2 = api("POST", "/api/auth/signup", json={
            "email": email, "password": pw, "display_name": u["display_name"]})
        if r2.status_code == 200:
            d = r2.json()
            return {"action": "created", "token": d["token"],
                    "user_id": d["user"]["user_id"], "tenant_id": d["user"]["tenant_id"]}
        if r2.status_code == 409:
            # Email taken but our derived password did not authenticate it.
            raise Conflict(
                f"{email}: address already registered but does not belong to this "
                f"corpus operation (login rejected). Refusing to overwrite.")
        raise Conflict(f"{email}: signup failed status={r2.status_code}")
    finally:
        pw = None


def tenant_posts(tenant_id: str) -> dict:
    """content_hash -> item, for idempotency. Read-only."""
    out, kw = {}, {"TableName": POSTS_TABLE,
                   "KeyConditionExpression": "tenant_id = :t",
                   "ExpressionAttributeValues": {":t": {"S": tenant_id}}}
    while True:
        r = _ddb.query(**kw)
        for it in r.get("Items", []):
            out[it.get("content_hash", {}).get("S", "")] = it
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return out


def patch_post_metadata(tenant_id: str, post_id: str, content_hash: str,
                        created_at: int, tags: list) -> None:
    """DECISION 2: narrow conditional metadata patch on ONE post item.

    The condition pins tenant_id, post_id AND content_hash, so this can never
    touch a different record even if an id were mistyped. DynamoDB is schemaless,
    so `tags` is an attribute extension — no key, GSI, LSI, IAM, API Gateway or
    Qdrant schema change.
    """
    _ddb.update_item(
        TableName=POSTS_TABLE,
        Key={"tenant_id": {"S": tenant_id}, "post_id": {"S": post_id}},
        UpdateExpression="SET created_at = :c, tags = :g, updated_at = :u",
        ConditionExpression="attribute_exists(post_id) AND content_hash = :h",
        ExpressionAttributeValues={
            ":c": {"N": str(created_at)},
            ":g": {"L": [{"S": t} for t in tags]},
            ":u": {"N": str(int(time.time()))},
            ":h": {"S": content_hash}})


# ------------------------------------------------------------------ apply
def apply_corpus(man: dict, bodies: dict, state: dict, limit_users=None) -> dict:
    master = get_master_secret()
    rep = {"users_created": 0, "users_reused": 0, "user_conflicts": [],
           "posts_created": 0, "posts_reused": 0, "post_conflicts": [],
           "post_failures": [], "patched": 0, "patch_failures": []}
    posts_by_user = {}
    for p in man["posts"]:
        posts_by_user.setdefault(p["username"], []).append(p)

    users = man["users"][:limit_users] if limit_users else man["users"]
    for u in users:
        un = u["username"]
        try:
            res = ensure_persona(u, master, state)
        except Conflict as e:
            rep["user_conflicts"].append(str(e))
            print(f"  [CONFLICT] {un}: {e}")
            continue
        rep["users_created" if res["action"] == "created" else "users_reused"] += 1
        state["users"][un] = {"user_id": res["user_id"], "tenant_id": res["tenant_id"],
                              "email": u["email"], "display_name": u["display_name"]}
        save_state(state)
        token = res["token"]
        tid = res["tenant_id"]
        existing = tenant_posts(tid)
        print(f"  {un:22} {res['action']:<8} tenant={tid[:18]} existing_posts={len(existing)}")

        for p in posts_by_user.get(un, []):
            key = f"{un}|{p['title']}|{p['date']}"
            body = bodies[(un, p["title"], p["date"])]
            bh = p["body_sha256"]
            hit = existing.get(bh)
            if hit is not None:
                if hit.get("title", {}).get("S") != p["title"]:
                    rep["post_conflicts"].append(
                        f"{key}: body matches an existing post titled "
                        f"{hit.get('title',{}).get('S','')!r}")
                    continue
                pid = hit["post_id"]["S"]
                rep["posts_reused"] += 1
            else:
                # same title+date already present with a DIFFERENT body -> CONFLICT
                clash = [it for it in existing.values()
                         if it.get("title", {}).get("S") == p["title"]]
                if clash:
                    rep["post_conflicts"].append(
                        f"{key}: title exists with a different body hash — refusing to overwrite")
                    continue
                r = api("POST", "/api/posts",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"title": p["title"], "content": body})
                if r.status_code not in (200, 201):
                    rep["post_failures"].append(f"{key}: HTTP {r.status_code}")
                    continue
                d = r.json()
                pid = d["post_id"]
                if d.get("message"):
                    rep["posts_reused"] += 1
                else:
                    rep["posts_created"] += 1
                existing[bh] = {"post_id": {"S": pid}, "title": {"S": p["title"]},
                                "content_hash": {"S": bh}}
                time.sleep(POST_PACING_S)     # gentle on the FIFO queue + Titan

            try:
                patch_post_metadata(tid, pid, bh, p["created_at_epoch"], p["tags"])
                rep["patched"] += 1
            except Exception as e:
                rep["patch_failures"].append(f"{key}: {type(e).__name__}")
            state["posts"][key] = {"post_id": pid, "tenant_id": tid}
        save_state(state)
    return rep


# ------------------------------------------------------------------ verify
def verify(man: dict) -> dict:
    s3 = boto3.client("s3", region_name=REGION)
    bucket = os.environ.get("S3_CONTENT_BUCKET")
    state = load_state()
    rows, tot = [], {"expected": 0, "actual": 0, "indexed": 0, "tag_ok": 0,
                     "date_ok": 0, "s3_ok": 0, "chunks": 0}
    posts_by_user = {}
    for p in man["posts"]:
        posts_by_user.setdefault(p["username"], []).append(p)

    for u in man["users"]:
        un = u["username"]
        st = state["users"].get(un)
        exp = u["expected_post_count"]
        tot["expected"] += exp
        if not st:
            rows.append({"username": un, "expected": exp, "actual": 0, "indexed": 0,
                         "status": "USER MISSING"})
            continue
        items = tenant_posts(st["tenant_id"])
        want = {p["body_sha256"]: p for p in posts_by_user[un]}
        actual = indexed = tag_ok = date_ok = s3_ok = chunks = 0
        for bh, p in want.items():
            it = items.get(bh)
            if not it:
                continue
            actual += 1
            if it.get("ingestion_status", {}).get("S") == "indexed":
                indexed += 1
            chunks += int(it.get("chunk_count", {}).get("N", "0"))
            if int(it.get("created_at", {}).get("N", "0")) == p["created_at_epoch"]:
                date_ok += 1
            got = [t.get("S") for t in it.get("tags", {}).get("L", [])]
            if got == p["tags"]:
                tag_ok += 1
            if bucket:
                try:
                    k = it.get("s3_key", {}).get("S", "")
                    body = s3.get_object(Bucket=bucket, Key=k)["Body"].read()
                    if hashlib.sha256(body).hexdigest() == bh:
                        s3_ok += 1
                except Exception:
                    pass
        for k, v in (("actual", actual), ("indexed", indexed), ("tag_ok", tag_ok),
                     ("date_ok", date_ok), ("s3_ok", s3_ok), ("chunks", chunks)):
            tot[k] += v
        rows.append({"username": un, "expected": exp, "actual": actual,
                     "indexed": indexed, "tag_ok": tag_ok, "date_ok": date_ok,
                     "s3_ok": s3_ok, "chunks": chunks,
                     "status": "OK" if (actual == exp and indexed == exp) else "INCOMPLETE"})
    return {"rows": rows, "totals": tot}


def main():
    ap = argparse.ArgumentParser(description="Seed the curated 25-user corpus.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--validate", action="store_true")
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--verify", action="store_true")
    ap.add_argument("--corpus", default=os.environ.get("CORPUS_PATH", ""))
    ap.add_argument("--limit-users", type=int, default=None)
    a = ap.parse_args()

    if a.validate or a.plan or a.apply:
        if not a.corpus:
            raise SystemExit("--corpus PATH (or CORPUS_PATH) required")
        man, parsed = build_manifest(a.corpus)
        json.dump(man, open(MANIFEST, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    else:
        man = json.load(open(MANIFEST, encoding="utf-8"))
        parsed = None

    if a.validate:
        print(f"VALIDATE ok: {len(man['users'])} users / {len(man['posts'])} posts")
        print(f"  source sha256: {man['source_sha256']}")
        print(f"  ingest sha256: {man['ingest_region_sha256']}")
        return
    if a.plan:
        state = load_state()
        print(f"PLAN: {len(man['users'])} personas, {len(man['posts'])} posts")
        print(f"  known personas in state: {len(state['users'])}")
        print(f"  emails: {man['users'][0]['email']} ... {man['users'][-1]['email']}")
        print("  no mutation performed")
        return
    if a.apply:
        state = load_state()
        rep = apply_corpus(man, bodies_by_key(parsed), state, a.limit_users)
        os.makedirs(OUT, exist_ok=True)
        json.dump(rep, open(os.path.join(OUT, "seed_apply_report.json"), "w"), indent=2)
        print(json.dumps(rep, indent=2)[:2000])
        return
    if a.verify:
        v = verify(man)
        json.dump(v, open(os.path.join(OUT, "seed_verify_report.json"), "w"), indent=2)
        t = v["totals"]
        print(f"VERIFY expected={t['expected']} actual={t['actual']} indexed={t['indexed']} "
              f"date_ok={t['date_ok']} tag_ok={t['tag_ok']} s3_ok={t['s3_ok']} chunks={t['chunks']}")
        for r in v["rows"]:
            if r["status"] != "OK":
                print(f"  {r['username']}: {r}")


if __name__ == "__main__":
    main()
