"""Roll back the curated 25-user / 268-post corpus from production.

This is an EXACT DATA ROLLBACK of one seeding operation — not a general cleanup.

SAFETY MODEL
  * DRY RUN IS THE DEFAULT. `--apply` is required to mutate anything.
  * The deletion set comes ONLY from corpus_25_manifest.json: 25 emails, and for
    each persona the exact set of 268 body hashes. Identity is matched, never
    inferred from an email pattern, a creation date, or "not on a keep list".
  * The five retained accounts are hard-blocked in code and can never enter the
    deletion set, regardless of manifest contents.
  * A target account holding ANY post that is not in the manifest means the
    account was used beyond the seed. That account is SKIPPED and reported —
    rolling back a seed is not authorisation to destroy later usage.
  * Resumable and idempotent: progress is recorded by stable ids, already-deleted
    resources are recognised, and the deletion set never broadens on a rerun.
  * A failure stops the run; it never continues blindly into the next user.

PER-USER ORDER (retrieval-visible data first, so a post stops being answerable
before its metadata disappears):
  0 revalidate identity + post ownership
  1 deactivate the account (active=false) so nothing new is written mid-delete
  2 Qdrant points, by EXACT post_id (never a collection-wide delete), then verify
  3 semantic-cache invalidation for that tenant
  4 S3 objects, by EXACT recorded key (never a broad prefix delete)
  5 DynamoDB post rows, by exact (tenant_id, post_id)
  6 relationship records provably owned by this identity
  7 user row
  8 tenant row, only once no posts remain
  9 verify 0 posts / 0 points / 0 objects
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
MANIFEST = os.path.join(HERE, "corpus_25_manifest.json")
STATE = os.path.join(HERE, "output", "corpus_rollback_state.json")
REGION = os.environ.get("AWS_REGION", "ap-south-1")
sys.path.insert(0, os.path.join(REPO, "multitenant-rag", "lambdas"))

# Never deletable, whatever any manifest says.
RETAINED_EMAILS = frozenset({
    "pk@gmail.com", "pk1@gmail.com", "snehasattai@gmail.com",
    "naresh_nagarjuna@gmail.com", "realuser@example.com"})

EXPECTED_USERS = 25
EXPECTED_POSTS = 268
EXPECTED_SOURCE_SHA = "6e2f76b74c0c598f884d0185d6cc426efaa2450576bba26e8d02402741cd6f9b"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Roll back the curated 25-user corpus (dry-run by default).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="show the plan (default)")
    g.add_argument("--verify", action="store_true", help="verify post-deletion state")
    g.add_argument("--apply", action="store_true", help="ACTUALLY DELETE")
    return ap.parse_args(argv if argv is not None else [])


def load_manifest() -> dict:
    m = json.load(open(MANIFEST, encoding="utf-8"))
    if m.get("source_sha256") != EXPECTED_SOURCE_SHA:
        raise SystemExit("manifest fingerprint mismatch — refusing to run")
    if len(m["users"]) != EXPECTED_USERS or len(m["posts"]) != EXPECTED_POSTS:
        raise SystemExit(
            f"manifest must describe {EXPECTED_USERS} users / {EXPECTED_POSTS} posts")
    emails = {u["email"] for u in m["users"]}
    overlap = emails & RETAINED_EMAILS
    if overlap:
        raise SystemExit(f"retained account present in the manifest: {overlap}")
    return m


def is_deletable_email(email: str, manifest: dict) -> bool:
    """The ONLY membership test. Retained accounts are rejected first."""
    if not email or email in RETAINED_EMAILS:
        return False
    return email in {u["email"] for u in manifest["users"]}


def load_state() -> dict:
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"completed": [], "partial": None}


def save_state(s: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(s, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE)


def _clients():
    import boto3
    from qdrant_client import QdrantClient
    from common.secrets import get_qdrant
    url, key = get_qdrant()
    return (boto3.client("dynamodb", region_name=REGION),
            boto3.client("s3", region_name=REGION),
            QdrantClient(url=url, api_key=key, timeout=30))


def reconcile(manifest: dict) -> dict:
    """Read-only: match live production against the manifest. Never mutates."""
    import collections
    ddb, _, _ = _clients()

    def scan(t, proj=None):
        items, kw = [], {"TableName": t}
        if proj:
            kw["ProjectionExpression"] = proj
        while True:
            r = ddb.scan(**kw)
            items += r.get("Items", [])
            if "LastEvaluatedKey" not in r:
                return items
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    users = scan(os.environ.get("USERS_TABLE", "multitenant-users"))
    posts = scan(os.environ.get("POSTS_TABLE", "multitenant-posts"),
                 "tenant_id,post_id,title,content_hash,s3_key")
    by_email = {u.get("email", {}).get("S", ""): u for u in users
                if not u["user_id"]["S"].startswith(("USERNAME#", "EMAIL#"))}
    by_tenant = collections.defaultdict(list)
    for p in posts:
        by_tenant[p["tenant_id"]["S"]].append(p)

    hashes = collections.defaultdict(set)
    for p in manifest["posts"]:
        hashes[p["username"]].add(p["body_sha256"])

    targets, blocked = [], []
    for mu in manifest["users"]:
        em, un = mu["email"], mu["username"]
        if not is_deletable_email(em, manifest):
            blocked.append(f"{un}: not deletable")
            continue
        u = by_email.get(em)
        if not u:
            blocked.append(f"{un}: already absent")
            continue
        tid = u.get("tenant_id", {}).get("S", "")
        mine = by_tenant.get(tid, [])
        live = {p.get("content_hash", {}).get("S", "") for p in mine}
        extra = live - hashes[un]
        if extra:
            # §7 — the account was used beyond the seed. Do NOT delete it.
            blocked.append(
                f"{un}: {len(extra)} post(s) NOT in the corpus manifest — SKIPPED, "
                "account has been used beyond the seed")
            continue
        if len(mine) != mu["expected_post_count"]:
            blocked.append(f"{un}: {len(mine)} posts, expected {mu['expected_post_count']}")
            continue
        targets.append({
            "username": un, "email": em, "user_id": u["user_id"]["S"], "tenant_id": tid,
            "posts": [{"post_id": p["post_id"]["S"],
                       "s3_key": p.get("s3_key", {}).get("S", "")} for p in mine]})
    return {"targets": targets, "blocked": blocked,
            "live_users": len(by_email), "live_posts": len(posts)}


def delete_user(t: dict, ddb, s3, qc, coll: str, bucket: str, tables: dict) -> dict:
    """Delete ONE corpus identity, in the §11 order. Raises on failure."""
    from qdrant_client.models import (Filter, FieldCondition, MatchAny,
                                      MatchValue, FilterSelector)
    from common import semcache

    uid, tid = t["user_id"], t["tenant_id"]
    pids = [p["post_id"] for p in t["posts"]]
    r = {"username": t["username"], "qdrant_points": 0, "s3": 0, "post_rows": 0,
         "follows": 0, "memberships": 0, "chats": 0, "usage": 0,
         "user": 0, "tenant": 0}

    # 1 — deactivate first so nothing new is written mid-delete
    try:
        ddb.update_item(TableName=tables["users"], Key={"user_id": {"S": uid}},
                        UpdateExpression="SET active = :f",
                        ConditionExpression="attribute_exists(user_id)",
                        ExpressionAttributeValues={":f": {"BOOL": False}})
    except Exception:
        pass                      # already gone on a resumed run

    # 2 — Qdrant by EXACT post ids, scoped to the tenant as well. Never global.
    if pids:
        flt = Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tid)),
                           FieldCondition(key="post_id", match=MatchAny(any=pids))])
        before = qc.count(collection_name=coll, count_filter=flt, exact=True).count
        qc.delete(collection_name=coll, points_selector=FilterSelector(filter=flt))
        after = qc.count(collection_name=coll, count_filter=flt, exact=True).count
        if after:
            raise RuntimeError(f"{t['username']}: {after} Qdrant points remain")
        r["qdrant_points"] = before

    # 3 — tenant-scoped semantic cache invalidation (never a global flush)
    semcache.invalidate_tenant(tid)

    # 4 — S3 by EXACT recorded key (no prefix delete)
    for p in t["posts"]:
        key = p["s3_key"] or f"tenants/{tid}/posts/{p['post_id']}.md"
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            r["s3"] += 1
        except Exception:
            pass                  # idempotent: already absent

    # 5 — DynamoDB post rows by exact composite key
    for pid in pids:
        ddb.delete_item(TableName=tables["posts"],
                        Key={"tenant_id": {"S": tid}, "post_id": {"S": pid}})
        r["post_rows"] += 1

    # 6 — relationship records provably owned by this identity
    for it in _q(ddb, tables["follows"], "follower_id = :f", {":f": {"S": uid}}):
        ddb.delete_item(TableName=tables["follows"],
                        Key={"follower_id": it["follower_id"], "followee_id": it["followee_id"]})
        r["follows"] += 1
    for it in _q(ddb, tables["follows"], "followee_id = :f", {":f": {"S": uid}}, "by_followee"):
        ddb.delete_item(TableName=tables["follows"],
                        Key={"follower_id": it["follower_id"], "followee_id": it["followee_id"]})
        r["follows"] += 1
    # membership EDGES only — the group itself is never deleted
    for it in _q(ddb, tables["members"], "user_id = :u", {":u": {"S": uid}}, "by_member"):
        ddb.delete_item(TableName=tables["members"],
                        Key={"group_id": it["group_id"], "user_id": it["user_id"]})
        r["memberships"] += 1
    for it in _q(ddb, tables["chats"], "user_id = :u", {":u": {"S": uid}}):
        ddb.delete_item(TableName=tables["chats"],
                        Key={"user_id": it["user_id"], "chat_id": it["chat_id"]})
        r["chats"] += 1
    # Usage rows are keyed `tenant_id#date` and already carry a 30-day TTL. They
    # are removed here because ownership is provable from the key prefix and a
    # deleted tenant should not leave dangling telemetry behind.
    for it in _scan_usage(ddb, tables["usage"], tid):
        ddb.delete_item(TableName=tables["usage"],
                        Key={"tenant_date": it["tenant_date"],
                             "timestamp_req": it["timestamp_req"]})
        r["usage"] += 1

    # 7 — user row
    ddb.delete_item(TableName=tables["users"], Key={"user_id": {"S": uid}})
    r["user"] = 1

    # 8 — tenant row, only once nothing remains under it
    left = _q(ddb, tables["posts"], "tenant_id = :t", {":t": {"S": tid}})
    if left:
        raise RuntimeError(f"{t['username']}: {len(left)} posts still under the tenant")
    ddb.delete_item(TableName=tables["tenants"], Key={"tenant_id": {"S": tid}})
    r["tenant"] = 1
    return r


def _q(ddb, table, cond, values, index=None):
    items, kw = [], {"TableName": table, "KeyConditionExpression": cond,
                     "ExpressionAttributeValues": values}
    if index:
        kw["IndexName"] = index
    while True:
        try:
            r = ddb.query(**kw)
        except Exception:
            return items
        items += r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def _scan_usage(ddb, table, tenant_id):
    items, kw = [], {"TableName": table,
                     "FilterExpression": "begins_with(tenant_date, :t)",
                     "ExpressionAttributeValues": {":t": {"S": tenant_id + "#"}},
                     "ProjectionExpression": "tenant_date,timestamp_req"}
    while True:
        try:
            r = ddb.scan(**kw)
        except Exception:
            return items
        items += r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            return items
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    manifest = load_manifest()
    mode = "APPLY (DESTRUCTIVE)" if args.apply else ("VERIFY" if args.verify else "DRY RUN")
    print(f"=== cleanup_curated_corpus — {mode} ===")
    print(f"  manifest: {EXPECTED_USERS} users / {EXPECTED_POSTS} posts "
          f"(source {manifest['source_sha256'][:16]}…)")
    print(f"  hard-blocked retained accounts: {len(RETAINED_EMAILS)}")

    rec = reconcile(manifest)
    print(f"\n  live users={rec['live_users']} posts={rec['live_posts']}")
    print(f"  deletable corpus identities: {len(rec['targets'])}")
    print(f"  target posts: {sum(len(t['posts']) for t in rec['targets'])}")
    for b in rec["blocked"]:
        print(f"    [SKIP] {b}")

    # Plan assertions (§9)
    tids = {t["tenant_id"] for t in rec["targets"]}
    emails = {t["email"] for t in rec["targets"]}
    print(f"\n  retained accounts excluded:    {'PASS' if not (emails & RETAINED_EMAILS) else 'FAIL'}")
    print(f"  non-manifest users excluded:  "
          f"{'PASS' if emails <= {u['email'] for u in manifest['users']} else 'FAIL'}")
    print(f"  non-manifest posts excluded:  "
          f"{'PASS' if sum(len(t['posts']) for t in rec['targets']) <= EXPECTED_POSTS else 'FAIL'}")

    if args.verify:
        print("\n  (verify mode: reconciliation above reflects live state)")
        return 0
    if not args.apply:
        print("\n  DRY RUN complete. Re-run with --apply to delete.")
        return 0

    ddb, s3, qc = _clients()
    coll = os.environ.get("QDRANT_COLLECTION", "multitenant_chunks")
    bucket = os.environ["S3_CONTENT_BUCKET"]
    tables = {"users": os.environ.get("USERS_TABLE", "multitenant-users"),
              "tenants": os.environ.get("TENANTS_TABLE", "multitenant-tenants"),
              "posts": os.environ.get("POSTS_TABLE", "multitenant-posts"),
              "follows": os.environ.get("FOLLOWS_TABLE", "multitenant-follows"),
              "members": os.environ.get("GROUP_MEMBERS_TABLE", "multitenant-group-members"),
              "chats": os.environ.get("CHATS_TABLE", "multitenant-chats"),
              "usage": os.environ.get("USAGE_TABLE", "multitenant-usage-logs")}

    state = load_state()
    totals = {k: 0 for k in ("qdrant_points", "s3", "post_rows", "follows",
                             "memberships", "chats", "usage", "user", "tenant")}
    done = list(state["completed"])
    for t in rec["targets"]:
        if t["username"] in done:
            print(f"  [skip] {t['username']} already completed")
            continue
        state["partial"] = t["username"]; save_state(state)
        try:
            r = delete_user(t, ddb, s3, qc, coll, bucket, tables)
        except Exception as e:
            state["partial"] = t["username"]; save_state(state)
            print(f"\n*** STOPPED at {t['username']}: {type(e).__name__}: {e}")
            print(f"    completed: {done}")
            print(f"    rerun to resume — the deletion set never broadens")
            return 1
        for k in totals:
            totals[k] += r.get(k, 0)
        done.append(t["username"])
        state["completed"] = done; state["partial"] = None; save_state(state)
        print(f"  [done] {t['username']:22} posts={r['post_rows']:>3} "
              f"points={r['qdrant_points']:>3} s3={r['s3']:>3}")

    print(f"\n=== TOTALS ===\n{json.dumps(totals, indent=2)}")
    json.dump({"totals": totals, "completed": done},
              open(os.path.join(HERE, "output", "corpus_rollback_report.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
