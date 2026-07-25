"""Request context resolution.

Extracts user_id from the request and derives tenant_id via DynamoDB lookup.

Design:
  - v1: user_id comes from X-User-Id header (no auth yet)
  - v1.5: user_id comes from Cognito JWT claim (sub)

Swap only this file to migrate — business logic never touches this concern.

Never trust tenant_id from client. Always look it up.
"""

import functools
import os

import boto3
from botocore.exceptions import ClientError


_ddb = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))


class ContextError(Exception):
    """Raised when the request has no valid identity."""


def get_context(event: dict) -> tuple[str, str, dict]:
    """
    Resolve user identity and tenant from a Lambda proxy event.

    Returns:
        (user_id, tenant_id, user_record)

    Raises:
        ContextError: if the request has no user_id, or the user is unknown.
    """
    return get_context_from_headers(event.get("headers") or {})


def get_context_from_headers(headers: dict) -> tuple[str, str, dict]:
    """
    Resolve user identity and tenant from a plain headers dict.

    Identity comes from a signed JWT ('Authorization: Bearer <token>'). The
    token carries user_id + tenant_id (set at login), so no per-request DB
    lookup is needed and tenant_id is never trusted from the client body.
    """
    # Lazy import so modules that only need _fetch_user (not auth) don't pull
    # jwt/bcrypt into their deployment package.
    from common.auth import AuthError, bearer_from_headers, verify_token

    token = bearer_from_headers(headers)
    if not token:
        raise ContextError("Missing bearer token")

    try:
        claims = verify_token(token)
    except AuthError as e:
        raise ContextError(str(e))

    user_id = claims.get("sub")
    tenant_id = claims.get("tenant_id")
    if not user_id or not tenant_id:
        raise ContextError("Malformed token")

    return user_id, tenant_id, claims


def _extract_user_id(event: dict) -> str | None:
    """
    Pull user_id from headers. Header names arrive lowercased in Lambda
    proxy integration but keep case-insensitive lookup.

    For Lambda Function URL: headers is a flat dict.
    For API Gateway HTTP API v2: headers is a flat dict.
    """
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if key.lower() == "x-user-id":
            return value.strip() if value else None
    return None


@functools.lru_cache(maxsize=256)
def _fetch_user(user_id: str) -> dict | None:
    """
    Look up user in DynamoDB. Cached per-invocation env for the lifetime
    of the Lambda execution context.

    Cache invalidation caveat: if you change a user's tenant_id, old warm
    Lambdas will serve stale mapping until they cycle. For learning this
    is fine; in production add a TTL or explicit invalidation.
    """
    try:
        resp = _ddb.get_item(
            TableName="multitenant-users",
            Key={"user_id": {"S": user_id}},
            ConsistentRead=False,
        )
    except ClientError:
        return None

    item = resp.get("Item")
    if not item:
        return None

    return _unmarshal(item)


def _unmarshal(item: dict) -> dict:
    """Convert DynamoDB item shape into a plain dict."""
    result = {}
    for key, wrapped in item.items():
        # wrapped is like {"S": "value"} or {"BOOL": True} etc.
        (attr_type, val), = wrapped.items()
        if attr_type == "N":
            result[key] = int(val) if val.isdigit() else float(val)
        elif attr_type == "BOOL":
            result[key] = val
        elif attr_type == "NULL":
            result[key] = None
        else:
            result[key] = val
    return result
