"""Global user model. A user exists once; org membership lives in memberships."""

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPKMixin


class User(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable: phone-only staff onboarded via invite link may have neither set yet.
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)  # E.164
    # Null for passwordless users (magic-link / invite-link only).
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, name={self.name!r})>"
