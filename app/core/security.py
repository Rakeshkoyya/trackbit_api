"""Password hashing, token hashing, and JWT helpers."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode(), salt).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def generate_raw_token(nbytes: int = 32) -> str:
    """A URL-safe opaque token. Only its hash is persisted."""
    return secrets.token_urlsafe(nbytes)


def hash_token(raw: str) -> str:
    """SHA-256 of an opaque token (fast lookup; tokens are high-entropy)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_access_token(*, user_id: uuid.UUID, org_id: uuid.UUID, org_role: str,
                        token_version: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": org_role,
        "tv": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError on invalid/expired tokens."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
