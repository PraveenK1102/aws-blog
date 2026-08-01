"""User groups — a named set of members, used for group chat + group search.

Tables:
  multitenant-groups         PK=group_id                     (name, owner_id, created_at)
  multitenant-group-members  PK=group_id, SK=user_id         (tenant_id, added_at)
     GSI by_member           PK=user_id, SK=group_id         ("my groups")

We denormalize each member's tenant_id onto the membership row so a group ask can
resolve members -> tenant_ids for the retrieval filter with no extra user lookups.
"""

import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError


REGION = os.environ.get("AWS_REGION", "ap-south-1")
GROUPS_TABLE = os.environ.get("GROUPS_TABLE", "multitenant-groups")
MEMBERS_TABLE = os.environ.get("GROUP_MEMBERS_TABLE", "multitenant-group-members")

_ddb = boto3.client("dynamodb", region_name=REGION)


class GroupError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def create_group(owner_id: str, owner_tenant_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise GroupError(400, "group name required")
    group_id = f"grp_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    _ddb.put_item(TableName=GROUPS_TABLE, Item={
        "group_id": {"S": group_id}, "name": {"S": name},
        "owner_id": {"S": owner_id}, "created_at": {"N": str(now)}})
    _add_member_item(group_id, owner_id, owner_tenant_id)  # owner is a member
    return {"group_id": group_id, "name": name, "owner_id": owner_id, "created_at": now}


def get_group(group_id: str) -> dict | None:
    try:
        it = _ddb.get_item(TableName=GROUPS_TABLE, Key={"group_id": {"S": group_id}}).get("Item")
    except ClientError:
        return None
    if not it:
        return None
    return {"group_id": it["group_id"]["S"], "name": it.get("name", {}).get("S", ""),
            "owner_id": it.get("owner_id", {}).get("S", ""),
            "created_at": int(it.get("created_at", {}).get("N", "0"))}


def _add_member_item(group_id: str, user_id: str, tenant_id: str) -> None:
    _ddb.put_item(TableName=MEMBERS_TABLE, Item={
        "group_id": {"S": group_id}, "user_id": {"S": user_id},
        "tenant_id": {"S": tenant_id or ""}, "added_at": {"N": str(int(time.time()))}})


def add_member(group_id: str, actor_id: str, user_id: str, tenant_id: str) -> None:
    g = get_group(group_id)
    if not g:
        raise GroupError(404, "group not found")
    if g["owner_id"] != actor_id:
        raise GroupError(403, "only the group owner can add members")
    _add_member_item(group_id, user_id, tenant_id)


def remove_member(group_id: str, actor_id: str, user_id: str) -> None:
    g = get_group(group_id)
    if not g:
        raise GroupError(404, "group not found")
    if g["owner_id"] != actor_id and actor_id != user_id:
        raise GroupError(403, "only the owner, or the member themselves, can remove")
    _ddb.delete_item(TableName=MEMBERS_TABLE,
                     Key={"group_id": {"S": group_id}, "user_id": {"S": user_id}})


def list_members(group_id: str) -> list[dict]:
    """Return [{user_id, tenant_id}] for a group (paginated internally)."""
    out: list[dict] = []
    kw = {"TableName": MEMBERS_TABLE,
          "KeyConditionExpression": "group_id = :g",
          "ExpressionAttributeValues": {":g": {"S": group_id}}}
    while True:
        r = _ddb.query(**kw)
        out += [{"user_id": i["user_id"]["S"], "tenant_id": i.get("tenant_id", {}).get("S", "")}
                for i in r.get("Items", [])]
        lek = r.get("LastEvaluatedKey")
        if not lek:
            break
        kw["ExclusiveStartKey"] = lek
    return out


def member_tenant_ids(group_id: str) -> list[str]:
    return [m["tenant_id"] for m in list_members(group_id) if m["tenant_id"]]


def list_my_groups(user_id: str) -> list[dict]:
    """Groups the user belongs to (via the by_member GSI).

    Note: N+1 (a get_group per membership). Fine while a user is in few groups;
    denormalize the group name onto the membership row if this ever gets hot.
    """
    out: list[dict] = []
    kw = {"TableName": MEMBERS_TABLE, "IndexName": "by_member",
          "KeyConditionExpression": "user_id = :u",
          "ExpressionAttributeValues": {":u": {"S": user_id}}}
    while True:
        r = _ddb.query(**kw)
        for i in r.get("Items", []):
            g = get_group(i["group_id"]["S"])
            if g:
                out.append(g)
        lek = r.get("LastEvaluatedKey")
        if not lek:
            break
        kw["ExclusiveStartKey"] = lek
    return out


def is_member(group_id: str, user_id: str) -> bool:
    try:
        r = _ddb.get_item(TableName=MEMBERS_TABLE,
                          Key={"group_id": {"S": group_id}, "user_id": {"S": user_id}})
        return "Item" in r
    except ClientError:
        return False


def join_group(group_id: str, user_id: str, tenant_id: str) -> dict:
    """Self-subscribe: any logged-in user can join a group (idempotent)."""
    g = get_group(group_id)
    if not g:
        raise GroupError(404, "group not found")
    _add_member_item(group_id, user_id, tenant_id)
    return g


def list_all_groups(limit: int = 100) -> list[dict]:
    """All groups, for discovery. A Scan — fine at small scale; swap for a
    name-sorted directory index (like the users directory) when it grows."""
    out: list[dict] = []
    try:
        r = _ddb.scan(TableName=GROUPS_TABLE, Limit=limit)
    except ClientError:
        return out
    for it in r.get("Items", []):
        out.append({"group_id": it["group_id"]["S"], "name": it.get("name", {}).get("S", ""),
                    "owner_id": it.get("owner_id", {}).get("S", ""),
                    "created_at": int(it.get("created_at", {}).get("N", "0"))})
    out.sort(key=lambda g: g["name"].lower())
    return out
