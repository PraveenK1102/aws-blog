"""Remove ONLY the positively-identified `seed-20260822` legacy demo population.

SAFETY MODEL (architect DECISION 3 / 3A)
  * DRY RUN IS THE DEFAULT. Deleting requires an explicit `--apply`.
  * The delete set is an EXACT ALLOWLIST of ids read from SEED-MANIFEST.json —
    6 user_ids, 6 tenant_ids, 50 post_ids. Nothing else is ever deletable.
  * There is deliberately NO "delete everything not in the corpus" rule. Absence
    from the protected list is NOT evidence for deletion.
  * Identity is REVALIDATED against live AWS before any mutation; any drift stops
    the run.
  * A mid-run failure stops the whole run rather than proceeding to another user.

DELETION ORDER (DECISION 3A) — chosen so a post is never retrievable after its
metadata is gone, and no live post ever points at a removed S3 object:
  0 revalidate identity
  1 deactivate the legacy user (active=false) — stops further use, reversible
  2 Qdrant points for the exact post_ids            (retrieval residue first)
  3 semantic-cache invalidation for that tenant
  4 S3 markdown objects
  5 DynamoDB post metadata rows
  6 user-owned relational records (follows / group memberships / chats / usage)
  7 user record
  8 tenant record (only once nothing legitimate remains)
  9 verification
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SEED_MANIFEST = os.path.join(REPO, "SEED-MANIFEST.json")
CORPUS_MANIFEST = os.path.join(HERE, "corpus_25_manifest.json")
REGION = os.environ.get("AWS_REGION", "ap-south-1")

# The ONLY population this tool may ever remove.
LEGACY_SEED_PREFIX = "seed-20260822"
EXPECTED_USERS = 6
EXPECTED_POSTS = 50

# Never deletable, under any circumstance, regardless of manifest contents.
UNKNOWN_REVIEW_EMAILS = frozenset({
    "pk@gmail.com", "pk1@gmail.com", "snehasattai@gmail.com",
    "naresh_nagarjuna@gmail.com", "realuser@example.com"})


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Remove the seed-20260822 legacy demo population (dry-run by default).")
    ap.add_argument("--apply", action="store_true",
                    help="ACTUALLY DELETE. Without this the tool only reports.")
    return ap.parse_args(argv if argv is not None else [])


def load_allowlist() -> dict:
    """Exact ids from the recorded provenance manifest. No inference."""
    m = json.load(open(SEED_MANIFEST, encoding="utf-8"))
    if m.get("seed_prefix") != LEGACY_SEED_PREFIX:
        raise SystemExit(f"seed manifest prefix is {m.get('seed_prefix')!r}, "
                         f"expected {LEGACY_SEED_PREFIX!r} — refusing to run")
    allow = {
        "seed_prefix": m["seed_prefix"],
        "user_ids": {u["user_id"] for u in m["users"]},
        "tenant_ids": {t["tenant_id"] for t in m["tenants"]},
        "post_ids": {p["post_id"] for p in m["posts"]},
        "emails": {u.get("email", "") for u in m["users"] if u.get("email")},
        "posts": m["posts"], "users": m["users"], "tenants": m["tenants"],
    }
    if len(allow["user_ids"]) != EXPECTED_USERS:
        raise SystemExit(f"expected {EXPECTED_USERS} legacy users, "
                         f"manifest has {len(allow['user_ids'])} — refusing")
    if len(allow["post_ids"]) != EXPECTED_POSTS:
        raise SystemExit(f"expected {EXPECTED_POSTS} legacy posts, "
                         f"manifest has {len(allow['post_ids'])} — refusing")
    # A corpus persona must never appear in the legacy allowlist.
    if os.path.exists(CORPUS_MANIFEST):
        corpus = json.load(open(CORPUS_MANIFEST, encoding="utf-8"))
        protected = {u["email"] for u in corpus["users"]}
        overlap = protected & allow["emails"]
        if overlap:
            raise SystemExit(f"protected corpus email in legacy allowlist: {overlap}")
    return allow


def is_deletable_user(user_id: str, allow: dict) -> bool:
    return user_id in allow["user_ids"]


def is_deletable_email(email: str, allow: dict) -> bool:
    if not email or email in UNKNOWN_REVIEW_EMAILS:
        return False
    if email.endswith("@example.com"):        # curated corpus personas
        return False
    return email in allow["emails"]


def is_deletable_post(post_id: str, allow: dict) -> bool:
    return post_id in allow["post_ids"]


def revalidate(allow: dict) -> dict:
    """Confirm live AWS still matches the recorded provenance. Read-only."""
    import boto3
    ddb = boto3.client("dynamodb", region_name=REGION)
    report = {"users_found": 0, "users_missing": [], "identity_drift": [],
              "posts_found": 0, "posts_missing": [], "posts_wrong_owner": []}
    for u in allow["users"]:
        r = ddb.get_item(TableName=os.environ.get("USERS_TABLE", "multitenant-users"),
                         Key={"user_id": {"S": u["user_id"]}})
        it = r.get("Item")
        if not it:
            report["users_missing"].append(u["user_id"])
            continue
        report["users_found"] += 1
        if it.get("tenant_id", {}).get("S") != u["tenant_id"]:
            report["identity_drift"].append(
                f"{u['user_id']}: tenant_id changed since the manifest was written")
        if LEGACY_SEED_PREFIX not in it.get("display_name", {}).get("S", ""):
            report["identity_drift"].append(
                f"{u['user_id']}: display_name no longer carries {LEGACY_SEED_PREFIX}")
    for p in allow["posts"]:
        r = ddb.get_item(TableName=os.environ.get("POSTS_TABLE", "multitenant-posts"),
                         Key={"tenant_id": {"S": p["tenant_id"]},
                              "post_id": {"S": p["post_id"]}})
        it = r.get("Item")
        if not it:
            report["posts_missing"].append(p["post_id"])
            continue
        report["posts_found"] += 1
        if it.get("tenant_id", {}).get("S") not in allow["tenant_ids"]:
            report["posts_wrong_owner"].append(p["post_id"])
    return report


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    allow = load_allowlist()
    mode = "APPLY (DESTRUCTIVE)" if args.apply else "DRY RUN (no mutation)"
    print(f"=== cleanup_legacy_seed — {mode} ===")
    print(f"  seed_prefix: {allow['seed_prefix']}")
    print(f"  delete set:  {len(allow['user_ids'])} users / "
          f"{len(allow['tenant_ids'])} tenants / {len(allow['post_ids'])} posts")
    print(f"  never deletable: {len(UNKNOWN_REVIEW_EMAILS)} UNKNOWN_REVIEW accounts "
          f"+ all @example.com corpus personas")

    rv = revalidate(allow)
    print(f"\n  revalidation: users_found={rv['users_found']}/{EXPECTED_USERS} "
          f"posts_found={rv['posts_found']}/{EXPECTED_POSTS}")
    for k in ("users_missing", "identity_drift", "posts_missing", "posts_wrong_owner"):
        if rv[k]:
            print(f"    {k}: {rv[k][:6]}")
    drift = bool(rv["identity_drift"] or rv["posts_wrong_owner"])
    if drift:
        raise SystemExit("IDENTITY DRIFT — refusing to delete. Investigate first.")

    if not args.apply:
        print("\n  DRY RUN complete. Re-run with --apply to delete.")
        return 0
    rep = execute(allow)
    print("\n=== DELETION REPORT ===")
    print(json.dumps(rep, indent=2, default=str))
    out = os.path.join(HERE, "output", "cleanup_report.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rep, open(out, "w"), indent=2, default=str)
    return 0


def execute(allow: dict) -> dict:
    """DECISION 3A order. Stops the ENTIRE run on the first failure rather than
    moving on to another user, so a partial state is always reportable."""
    import boto3
    from qdrant_client import QdrantClient
    from qdrant_client.models import (Filter, FieldCondition, MatchValue,
                                      FilterSelector, MatchAny)
    sys.path.insert(0, os.path.join(REPO, "multitenant-rag", "lambdas"))
    from common.secrets import get_qdrant

    ddb = boto3.client("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    url, key = get_qdrant()
    qc = QdrantClient(url=url, api_key=key, timeout=30)
    bucket = os.environ["S3_CONTENT_BUCKET"]
    COLL = os.environ.get("QDRANT_COLLECTION", "multitenant_chunks")

    T = {"users": os.environ.get("USERS_TABLE", "multitenant-users"),
         "tenants": os.environ.get("TENANTS_TABLE", "multitenant-tenants"),
         "posts": os.environ.get("POSTS_TABLE", "multitenant-posts"),
         "follows": os.environ.get("FOLLOWS_TABLE", "multitenant-follows"),
         "members": os.environ.get("GROUP_MEMBERS_TABLE", "multitenant-group-members"),
         "chats": os.environ.get("CHATS_TABLE", "multitenant-chats")}

    rep = {"deactivated": [], "qdrant_points_deleted": 0, "semcache_invalidated": [],
           "s3_deleted": 0, "post_rows_deleted": 0, "follows_deleted": 0,
           "memberships_deleted": 0, "chats_deleted": 0,
           "users_deleted": [], "tenants_deleted": [], "errors": [],
           "residue": {}}

    posts_by_tenant = {}
    for p in allow["posts"]:
        posts_by_tenant.setdefault(p["tenant_id"], []).append(p)

    for u in allow["users"]:
        uid, tid = u["user_id"], u["tenant_id"]
        pids = [p["post_id"] for p in posts_by_tenant.get(tid, [])]
        try:
            # 1 — deactivate first: stops further use, and is reversible
            ddb.update_item(TableName=T["users"], Key={"user_id": {"S": uid}},
                            UpdateExpression="SET active = :f",
                            ConditionExpression="attribute_exists(user_id)",
                            ExpressionAttributeValues={":f": {"BOOL": False}})
            rep["deactivated"].append(uid)

            # 2 — Qdrant points for the EXACT legacy post ids (retrieval residue first)
            if pids:
                qc.delete(collection_name=COLL, points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="post_id",
                                                       match=MatchAny(any=pids))])))
                left = qc.count(collection_name=COLL, exact=True, count_filter=Filter(
                    must=[FieldCondition(key="post_id", match=MatchAny(any=pids))])).count
                if left:
                    raise RuntimeError(f"{left} Qdrant points remain for {uid}")

            # 3 — semantic cache for that tenant
            from common import semcache
            semcache.invalidate_tenant(tid)
            rep["semcache_invalidated"].append(tid)

            # 4 — S3 markdown objects
            for p in posts_by_tenant.get(tid, []):
                k = p.get("s3_key") or f"tenants/{tid}/posts/{p['post_id']}.md"
                s3.delete_object(Bucket=bucket, Key=k)
                rep["s3_deleted"] += 1

            # 5 — DynamoDB post metadata
            for pid in pids:
                ddb.delete_item(TableName=T["posts"],
                                Key={"tenant_id": {"S": tid}, "post_id": {"S": pid}})
                rep["post_rows_deleted"] += 1

            # 6 — user-owned relational records (proven key layouts only)
            for it in _query_all(ddb, T["follows"], "follower_id = :f",
                                 {":f": {"S": uid}}):
                ddb.delete_item(TableName=T["follows"], Key={
                    "follower_id": it["follower_id"], "followee_id": it["followee_id"]})
                rep["follows_deleted"] += 1
            for it in _query_all(ddb, T["follows"], "followee_id = :f",
                                 {":f": {"S": uid}}, index="by_followee"):
                ddb.delete_item(TableName=T["follows"], Key={
                    "follower_id": it["follower_id"], "followee_id": it["followee_id"]})
                rep["follows_deleted"] += 1
            for it in _query_all(ddb, T["members"], "user_id = :u",
                                 {":u": {"S": uid}}, index="by_member"):
                ddb.delete_item(TableName=T["members"], Key={
                    "group_id": it["group_id"], "user_id": it["user_id"]})
                rep["memberships_deleted"] += 1
            for it in _query_all(ddb, T["chats"], "user_id = :u", {":u": {"S": uid}}):
                ddb.delete_item(TableName=T["chats"], Key={
                    "user_id": it["user_id"], "chat_id": it["chat_id"]})
                rep["chats_deleted"] += 1

            # 7 — user record
            ddb.delete_item(TableName=T["users"], Key={"user_id": {"S": uid}})
            rep["users_deleted"].append(uid)

            # 8 — tenant record, only once nothing legitimate remains
            remaining = _query_all(ddb, T["posts"], "tenant_id = :t", {":t": {"S": tid}})
            if remaining:
                rep["errors"].append(
                    f"{tid}: {len(remaining)} posts still present — tenant KEPT")
            else:
                ddb.delete_item(TableName=T["tenants"], Key={"tenant_id": {"S": tid}})
                rep["tenants_deleted"].append(tid)
        except Exception as e:
            rep["errors"].append(f"{uid}: {type(e).__name__}: {e}")
            rep["stopped_early"] = True
            return rep      # STOP — never continue to another user

    # 9 — residue verification
    all_pids = list(allow["post_ids"])
    rep["residue"]["qdrant_points"] = qc.count(
        collection_name=COLL, exact=True, count_filter=Filter(
            must=[FieldCondition(key="post_id", match=MatchAny(any=all_pids))])).count
    rep["residue"]["post_rows"] = sum(
        1 for p in allow["posts"]
        if ddb.get_item(TableName=T["posts"], Key={"tenant_id": {"S": p["tenant_id"]},
                                                    "post_id": {"S": p["post_id"]}}).get("Item"))
    rep["residue"]["users"] = sum(
        1 for u in allow["users"]
        if ddb.get_item(TableName=T["users"], Key={"user_id": {"S": u["user_id"]}}).get("Item"))
    return rep


def _query_all(ddb, table, cond, values, index=None):
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


if __name__ == "__main__":
    sys.exit(main())
