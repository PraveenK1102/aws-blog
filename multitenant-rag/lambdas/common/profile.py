"""Mutable public profile attributes: username and email.

IDENTITY MODEL (architect-fixed)
--------------------------------
STABLE, never changed here:   user_id, tenant_id
MUTABLE profile attributes:   username (new), email, display_name

`username` is a PUBLIC PROFILE ATTRIBUTE ONLY. It is never a DynamoDB primary
key, never the tenant identity, never Qdrant scope, never post ownership, and
never the JWT subject. Renaming therefore moves no posts, rewrites no S3 paths,
touches no Qdrant points, and leaves user_id/tenant_id untouched.

UNIQUENESS WITHOUT NEW INFRASTRUCTURE
-------------------------------------
The users table has a single HASH key `user_id` and one GSI `by_email`. A
read-then-write on a GSI is NOT race-safe (GSIs are eventually consistent), so
uniqueness is enforced with a RESERVATION ITEM in the SAME table:

    user_id = "USERNAME#<normalized>"   ->  { owner_user_id, claimed_at }

Claiming is a conditional PutItem on `attribute_not_exists(user_id)`, which is
an atomic compare-and-set. No new table, no new GSI, no key-schema change, no
infrastructure mutation.

Reservation rows are invisible to the app: `GET /api/users` skips any item whose
`tenant_id` does not resolve to a tenant, and reservations carry no `tenant_id`.
`ReservationsAreInvisible` in the tests pins that behaviour.

RENAME ORDER: claim the new name FIRST; only release the old one after the new
claim succeeds. A failure can therefore leave a harmless orphaned reservation,
never two users sharing a username.
"""
import os
import re
import time

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "ap-south-1")
USERS_TABLE = os.environ.get("USERS_TABLE", "multitenant-users")

_ddb = boto3.client("dynamodb", region_name=REGION)

USERNAME_RE = re.compile(r"^[a-z0-9._]{3,30}$")
USERNAME_PREFIX = "USERNAME#"
EMAIL_PREFIX = "EMAIL#"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


class ProfileError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ------------------------------------------------------------------ username
def normalize_username(username: str) -> str:
    """Deterministic case-folding. 'Kavin.Raj25' and 'kavin.raj25' are the SAME
    username, so case can never create duplicates."""
    return (username or "").strip().lower()


def validate_username(username: str) -> str:
    """Return the normalized username or raise. Conservative, corpus-compatible:
    a-z 0-9 . _ , 3-30 chars (so `kavin.raj25` and `priyadharshini.m25` fit)."""
    norm = normalize_username(username)
    if not norm:
        raise ProfileError(400, "username cannot be empty")
    if not USERNAME_RE.match(norm):
        raise ProfileError(
            400, "username must be 3-30 characters using only a-z, 0-9, dot or underscore")
    if norm.startswith(".") or norm.endswith("."):
        raise ProfileError(400, "username cannot start or end with a dot")
    if ".." in norm:
        raise ProfileError(400, "username cannot contain consecutive dots")
    return norm


def _reservation_key(norm: str) -> dict:
    return {"user_id": {"S": USERNAME_PREFIX + norm}}


def username_owner(norm: str) -> str | None:
    """user_id currently holding this username, or None. Strongly consistent."""
    try:
        r = _ddb.get_item(TableName=USERS_TABLE, Key=_reservation_key(norm),
                          ConsistentRead=True)
    except ClientError:
        return None
    it = r.get("Item")
    return it.get("owner_user_id", {}).get("S") if it else None


def is_username_available(username: str, for_user_id: str | None = None) -> bool:
    norm = validate_username(username)
    owner = username_owner(norm)
    return owner is None or owner == for_user_id


def set_username(user_id: str, username: str) -> dict:
    """Claim `username` for `user_id`. Race-safe. Never touches user_id/tenant_id."""
    norm = validate_username(username)
    current = _current_username(user_id)
    if current == norm:
        return {"username": norm, "changed": False}

    # 1. claim the NEW name atomically — this is the race-safe step
    try:
        _ddb.put_item(
            TableName=USERS_TABLE,
            Item={"user_id": {"S": USERNAME_PREFIX + norm},
                  "owner_user_id": {"S": user_id},
                  "reservation_kind": {"S": "username"},
                  "claimed_at": {"N": str(int(time.time()))}},
            ConditionExpression="attribute_not_exists(user_id)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            if username_owner(norm) == user_id:
                _write_username_attr(user_id, norm)
                return {"username": norm, "changed": True}
            raise ProfileError(409, "username already taken")
        raise ProfileError(500, "could not claim username")

    # 2. point the user record at the new name
    try:
        _write_username_attr(user_id, norm)
    except Exception:
        try:                       # roll the claim back so it is not orphaned
            _ddb.delete_item(TableName=USERS_TABLE, Key=_reservation_key(norm))
        except ClientError:
            pass
        raise ProfileError(500, "could not update profile")

    # 3. release the OLD claim only now that the new one is committed
    if current:
        try:
            _ddb.delete_item(
                TableName=USERS_TABLE, Key=_reservation_key(current),
                ConditionExpression="owner_user_id = :u",
                ExpressionAttributeValues={":u": {"S": user_id}})
        except ClientError:
            pass               # someone else owns it, or it is already gone
    return {"username": norm, "changed": True}


def _current_username(user_id: str) -> str | None:
    r = _ddb.get_item(TableName=USERS_TABLE, Key={"user_id": {"S": user_id}},
                      ConsistentRead=True)
    it = r.get("Item")
    if not it:
        raise ProfileError(404, "user not found")
    v = it.get("username", {}).get("S")
    return v or None


def _write_username_attr(user_id: str, norm: str) -> None:
    _ddb.update_item(
        TableName=USERS_TABLE, Key={"user_id": {"S": user_id}},
        UpdateExpression="SET username = :n, updated_at = :t",
        ConditionExpression="attribute_exists(user_id)",
        ExpressionAttributeValues={":n": {"S": norm},
                                   ":t": {"N": str(int(time.time()))}})


# --------------------------------------------------------------------- email
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str:
    norm = normalize_email(email)
    if not norm or not EMAIL_RE.match(norm):
        raise ProfileError(400, "valid email required")
    return norm


def email_in_use(norm: str, exclude_user_id: str | None = None) -> bool:
    try:
        r = _ddb.query(TableName=USERS_TABLE, IndexName="by_email",
                       KeyConditionExpression="email = :e",
                       ExpressionAttributeValues={":e": {"S": norm}})
    except ClientError:
        raise ProfileError(500, "could not verify email")
    for it in r.get("Items", []):
        if it.get("user_id", {}).get("S") != exclude_user_id:
            return True
    return False


def set_email(user_id: str, email: str) -> dict:
    """Change the login email. user_id/tenant_id and all content ownership are
    untouched; only the login identifier moves.

    Uniqueness uses BOTH the existing `by_email` GSI query (which is what signup
    uses) AND an atomic EMAIL# reservation, so two concurrent CHANGES cannot
    collide. A change racing a brand-new signup keeps the same (pre-existing)
    window as today, because signup does not create reservations — recorded as a
    known limitation rather than silently claimed as fully race-safe.
    """
    norm = validate_email(email)
    current = _current_email(user_id)
    if current == norm:
        return {"email": norm, "changed": False}
    if email_in_use(norm, exclude_user_id=user_id):
        raise ProfileError(409, "email already registered")

    try:
        _ddb.put_item(
            TableName=USERS_TABLE,
            Item={"user_id": {"S": EMAIL_PREFIX + norm},
                  "owner_user_id": {"S": user_id},
                  "reservation_kind": {"S": "email"},
                  "claimed_at": {"N": str(int(time.time()))}},
            ConditionExpression="attribute_not_exists(user_id)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            r = _ddb.get_item(TableName=USERS_TABLE,
                              Key={"user_id": {"S": EMAIL_PREFIX + norm}},
                              ConsistentRead=True)
            if (r.get("Item") or {}).get("owner_user_id", {}).get("S") != user_id:
                raise ProfileError(409, "email already registered")
        else:
            raise ProfileError(500, "could not claim email")

    try:
        _ddb.update_item(
            TableName=USERS_TABLE, Key={"user_id": {"S": user_id}},
            UpdateExpression="SET email = :e, updated_at = :t",
            ConditionExpression="attribute_exists(user_id)",
            ExpressionAttributeValues={":e": {"S": norm},
                                       ":t": {"N": str(int(time.time()))}})
    except ClientError:
        try:
            _ddb.delete_item(TableName=USERS_TABLE,
                             Key={"user_id": {"S": EMAIL_PREFIX + norm}})
        except ClientError:
            pass
        raise ProfileError(500, "could not update email")

    if current:
        try:
            _ddb.delete_item(
                TableName=USERS_TABLE, Key={"user_id": {"S": EMAIL_PREFIX + current}},
                ConditionExpression="owner_user_id = :u",
                ExpressionAttributeValues={":u": {"S": user_id}})
        except ClientError:
            pass
    return {"email": norm, "changed": True}


def _current_email(user_id: str) -> str | None:
    r = _ddb.get_item(TableName=USERS_TABLE, Key={"user_id": {"S": user_id}},
                      ConsistentRead=True)
    it = r.get("Item")
    if not it:
        raise ProfileError(404, "user not found")
    return it.get("email", {}).get("S") or None


def get_profile(user_id: str) -> dict:
    r = _ddb.get_item(TableName=USERS_TABLE, Key={"user_id": {"S": user_id}})
    it = r.get("Item")
    if not it:
        raise ProfileError(404, "user not found")
    return {"user_id": it["user_id"]["S"],
            "tenant_id": it.get("tenant_id", {}).get("S", ""),
            "email": it.get("email", {}).get("S", ""),
            "display_name": it.get("display_name", {}).get("S", ""),
            "username": it.get("username", {}).get("S") or None}
