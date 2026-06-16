"""Single-use auth tokens: magic links, invites, OTP, refresh tokens.

Only the SHA-256 hash of the raw token is stored. used_at marks consumption —
single use is enforced atomically (UPDATE ... WHERE used_at IS NULL).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPKMixin


class AuthToken(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Set for invite tokens (which org the invite joins) and refresh tokens
    # (which org context the session continues in).
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('magic_link', 'otp', 'invite', 'refresh')", name="purpose_valid"
        ),
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
    )
