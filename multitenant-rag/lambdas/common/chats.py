"""Saved chat sessions — conversation memory per (user, profile).

A chat belongs to a user (the visitor) and is with one profile (tenant). It
stores the message history so follow-ups have context. Users keep up to
MAX_ACTIVE active chats; delete soft-trashes; permanent delete removes it.
"""

import json
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError


REGION = os.environ.get("AWS_REGION", "ap-south-1")
CHATS_TABLE = os.environ.get("CHATS_TABLE", "multitenant-chats")
MAX_ACTIVE = int(os.environ.get("MAX_ACTIVE_CHATS", "5"))

_ddb = boto3.client("dynamodb", region_name=REGION)


class ChatLimitError(Exception):
    """Raised when a user already has MAX_ACTIVE active chats."""


def _unmarshal(item: dict) -> dict:
    return {
        "chat_id": item["chat_id"]["S"],
        "tenant_id": item.get("tenant_id", {}).get("S", ""),
        "profile_user_id": item.get("profile_user_id", {}).get("S", ""),
        "profile_name": item.get("profile_name", {}).get("S", ""),
        "title": item.get("title", {}).get("S", ""),
        "messages": json.loads(item.get("messages", {}).get("S", "[]")),
        "status": item.get("status", {}).get("S", "active"),
        "created_at": int(item.get("created_at", {}).get("N", "0")),
        "updated_at": int(item.get("updated_at", {}).get("N", "0")),
    }


def list_chats(user_id: str, status: str = "active") -> list[dict]:
    resp = _ddb.query(
        TableName=CHATS_TABLE,
        KeyConditionExpression="user_id = :u",
        FilterExpression="#s = :st",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":u": {"S": user_id}, ":st": {"S": status}},
    )
    chats = [_unmarshal(i) for i in resp.get("Items", [])]
    chats.sort(key=lambda c: c["updated_at"], reverse=True)
    return chats


def get_chat(user_id: str, chat_id: str) -> dict | None:
    try:
        item = _ddb.get_item(
            TableName=CHATS_TABLE,
            Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}},
        ).get("Item")
    except ClientError:
        return None
    return _unmarshal(item) if item else None


def create_chat(user_id: str, tenant_id: str, profile_name: str, profile_user_id: str = "") -> dict:
    # Limit is PER PROFILE: up to MAX_ACTIVE chats with each person, not global.
    active_here = [c for c in list_chats(user_id, "active") if c["tenant_id"] == tenant_id]
    if len(active_here) >= MAX_ACTIVE:
        raise ChatLimitError(f"You can keep up to {MAX_ACTIVE} chats with {profile_name} — delete one to start a new chat here.")
    chat_id = f"chat_{uuid.uuid4().hex[:12]}"
    now = int(time.time())
    _ddb.put_item(TableName=CHATS_TABLE, Item={
        "user_id": {"S": user_id}, "chat_id": {"S": chat_id},
        "tenant_id": {"S": tenant_id}, "profile_user_id": {"S": profile_user_id},
        "profile_name": {"S": profile_name},
        "title": {"S": "New chat"}, "messages": {"S": "[]"},
        "status": {"S": "active"}, "created_at": {"N": str(now)}, "updated_at": {"N": str(now)},
    })
    return {"chat_id": chat_id, "tenant_id": tenant_id, "profile_user_id": profile_user_id,
            "profile_name": profile_name, "title": "New chat", "messages": [],
            "status": "active", "created_at": now, "updated_at": now}


def append_turn(user_id: str, chat_id: str, user_text: str, assistant_text: str, citations: list) -> list | None:
    chat = get_chat(user_id, chat_id)
    if not chat:
        return None
    msgs = chat["messages"]
    msgs.append({"role": "user", "text": user_text})
    msgs.append({"role": "assistant", "text": assistant_text, "citations": citations})
    title = chat.get("title") or "New chat"
    if title in ("", "New chat"):
        title = user_text[:60]
    now = int(time.time())
    _ddb.update_item(
        TableName=CHATS_TABLE,
        Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}},
        UpdateExpression="SET messages = :m, title = :t, updated_at = :u",
        ExpressionAttributeValues={":m": {"S": json.dumps(msgs)}, ":t": {"S": title}, ":u": {"N": str(now)}},
    )
    return msgs


def set_status(user_id: str, chat_id: str, status: str) -> bool:
    # Restoring counts against the PER-PROFILE limit for that chat's tenant.
    if status == "active":
        chat = get_chat(user_id, chat_id)
        if chat:
            tid = chat["tenant_id"]
            active_here = [c for c in list_chats(user_id, "active") if c["tenant_id"] == tid]
            if len(active_here) >= MAX_ACTIVE:
                raise ChatLimitError(
                    f"You already have {MAX_ACTIVE} active chats with {chat.get('profile_name', 'this person')}.")
    try:
        _ddb.update_item(
            TableName=CHATS_TABLE,
            Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}},
            UpdateExpression="SET #s = :st",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":st": {"S": status}},
            ConditionExpression="attribute_exists(chat_id)",
        )
        return True
    except ClientError:
        return False


def delete_permanent(user_id: str, chat_id: str) -> None:
    _ddb.delete_item(TableName=CHATS_TABLE, Key={"user_id": {"S": user_id}, "chat_id": {"S": chat_id}})
