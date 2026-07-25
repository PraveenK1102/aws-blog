"""Custom JWT auth — email+password, bcrypt hashing, signed JWT tokens.

Chosen over Cognito so auth is testable in the LocalStack dev env and identical
in prod. Google login is a later add-on (an OAuth flow that mints the same JWT).
"""

import os
import time

import bcrypt
import jwt  # PyJWT

from common.secrets import get_jwt_secret


JWT_ALG = "HS256"
JWT_TTL_SECONDS = 7 * 24 * 3600  # 7 days


class AuthError(Exception):
    """Raised for invalid/expired tokens or bad credentials."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user_id: str, tenant_id: str, email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALG)


def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALG])
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {e}")


def bearer_from_headers(headers: dict) -> str | None:
    """Extract the token from an 'Authorization: Bearer <token>' header."""
    for key, value in headers.items():
        if key.lower() == "authorization" and value:
            parts = value.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1]
    return None
