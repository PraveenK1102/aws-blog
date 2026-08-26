"""Backfill `username` for the 25 curated corpus personas. DRY RUN BY DEFAULT.

Maps each manifest persona's email local part to its username:
    kavin.raj25@example.com  ->  kavin.raj25

Properties required by the architect:
  * idempotent   — a persona that already holds the target username is a no-op
  * conditional  — claims go through the same atomic reservation as the UI path
  * safe         — if a target username is already claimed by a DIFFERENT user,
                   the run STOPS rather than overwriting
  * auditable    — writes a per-user report

The five UNKNOWN_REVIEW accounts are never given an invented username: they are
not in the manifest, so they are never touched. Users without a username must
render fine in the UI (display_name fallback) — no username is ever persisted on
their behalf.

NOT RUN against AWS in the implementation task.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "corpus_25_manifest.json")
sys.path.insert(0, os.path.join(HERE, "..", "lambdas"))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Backfill curated usernames (dry-run by default).")
    ap.add_argument("--apply", action="store_true",
                    help="ACTUALLY WRITE. Without this the tool only reports.")
    return ap.parse_args(argv if argv is not None else [])


def planned_pairs() -> list[tuple[str, str]]:
    """(email, username) from the manifest. The username is the email local part,
    which by construction is the exact corpus username."""
    man = json.load(open(MANIFEST, encoding="utf-8"))
    out = []
    for u in man["users"]:
        local = u["email"].split("@")[0]
        if local != u["username"]:
            raise SystemExit(f"manifest inconsistency: {u['email']} vs {u['username']}")
        out.append((u["email"], u["username"]))
    return out


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    pairs = planned_pairs()
    mode = "APPLY (writes)" if args.apply else "DRY RUN (no mutation)"
    print(f"=== backfill_usernames — {mode} ===")
    print(f"  curated personas to backfill: {len(pairs)}")
    print(f"  UNKNOWN_REVIEW accounts touched: 0 (never given an invented username)")

    import boto3
    from common.profile import USERNAME_PREFIX, normalize_username
    ddb = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    table = os.environ.get("USERS_TABLE", "multitenant-users")

    report = {"would_set": [], "already_correct": [], "conflicts": [],
              "user_missing": [], "applied": []}
    for email, username in pairs:
        norm = normalize_username(username)
        r = ddb.query(TableName=table, IndexName="by_email",
                      KeyConditionExpression="email = :e",
                      ExpressionAttributeValues={":e": {"S": email}})
        items = r.get("Items", [])
        if not items:
            report["user_missing"].append(email)
            continue
        uid = items[0]["user_id"]["S"]
        have = items[0].get("username", {}).get("S")
        if have == norm:
            report["already_correct"].append(username)
            continue
        res = ddb.get_item(TableName=table,
                           Key={"user_id": {"S": USERNAME_PREFIX + norm}},
                           ConsistentRead=True).get("Item")
        owner = (res or {}).get("owner_user_id", {}).get("S")
        if owner and owner != uid:
            report["conflicts"].append(
                f"{username}: already claimed by a different user — STOPPING")
            break
        report["would_set"].append({"username": username, "user_id": uid})
        if args.apply:
            from common.profile import set_username
            set_username(uid, username)
            report["applied"].append(username)

    print(json.dumps({k: (len(v) if isinstance(v, list) else v)
                      for k, v in report.items()}, indent=2))
    if report["conflicts"]:
        print("\n  CONFLICTS — refusing to overwrite:")
        for c in report["conflicts"]:
            print(f"    {c}")
        return 1
    if not args.apply:
        print("\n  DRY RUN complete. Re-run with --apply after deployment is authorised.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
