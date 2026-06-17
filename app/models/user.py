"""Global user model. A user exists once; org membership lives in memberships."""

from sqlalchemy import Boolean, Text, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPKMixin


class User(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable: phone-only staff onboarded via invite link may have neither set yet.
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True, nullable=True)
    # Globally-unique login handle for staff without email. App-layer validated.
    username: Mapped[str | None] = mapped_column(CITEXT, unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)  # E.164
    # Null for users who haven't set one yet (email invite); set for everyone else.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True until the user sets their own password (email invite = first password;
    # bulk staff = forced change off the admin-set temp). Drives the first-login gate.
    must_set_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, name={self.name!r})>"
