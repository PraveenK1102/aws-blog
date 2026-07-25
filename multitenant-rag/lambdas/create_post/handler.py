"""createPostLambda — handles POST /posts.

Thin adapter: parse the API Gateway event, resolve identity, then delegate to
common.posts.create_post (shared with the ask app's dev route so the create
flow is identical in dev and prod).
"""

import base64
import json

from common.context import ContextError, get_context
from common.logger import get_logger
from common.posts import PostError, create_post
from common.responses import error_response, json_response


log = get_logger("create_post")


def handler(event, _context):
    try:
        user_id, tenant_id, _ = get_context(event)
    except ContextError as e:
        return error_response(401, "Unauthorized", str(e))

    try:
        body = _parse_body(event)
    except ValueError as e:
        return error_response(400, "Bad request", str(e))

    try:
        result = create_post(user_id, tenant_id, body.get("title"), body.get("content"), log=log)
    except PostError as e:
        return error_response(e.status, "Bad request" if e.status < 500 else "Internal error", e.message)

    # 200 when dedup returned an existing post, else 201
    status_code = 200 if result.get("message") else 201
    return json_response(status_code, result)


def _parse_body(event: dict) -> dict:
    raw = event.get("body")
    if not raw:
        raise ValueError("empty body")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("body must be valid JSON")
    if "title" not in body or "content" not in body:
        raise ValueError("body must include 'title' and 'content'")
    return body
