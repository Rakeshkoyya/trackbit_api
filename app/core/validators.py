"""Shared field validators (usernames, etc.)."""

import re

from app.core.config import settings
from app.core.exceptions import ValidationError

# lowercase letters, digits, and . _ - ; no '@' (keeps email-vs-username detection clean)
_USERNAME_RE = re.compile(r"^[a-z0-9._-]+$")


def normalize_username(raw: str) -> str:
    """Trim + lowercase, then validate. Raises ValidationError on bad input."""
    u = (raw or "").strip().lower()
    if not (settings.USERNAME_MIN_LENGTH <= len(u) <= settings.USERNAME_MAX_LENGTH):
        raise ValidationError(
            f"Username must be {settings.USERNAME_MIN_LENGTH}–{settings.USERNAME_MAX_LENGTH} characters.",
            code="invalid_username",
        )
    if not _USERNAME_RE.match(u):
        raise ValidationError(
            "Username can use lowercase letters, numbers, dot, dash, underscore.",
            code="invalid_username",
        )
    if u[0] in "._-" or u[-1] in "._-":
        raise ValidationError("Username can't start or end with a symbol.", code="invalid_username")
    return u
