"""Standard API Gateway HTTP response helpers."""

import json


def json_response(status_code: int, body: dict | list) -> dict:
    """Return an API Gateway HTTP API v2 response with JSON body."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body),
    }


def error_response(status_code: int, message: str, detail: str | None = None) -> dict:
    body = {"error": message}
    if detail:
        body["detail"] = detail
    return json_response(status_code, body)
