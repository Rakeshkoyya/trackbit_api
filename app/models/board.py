"""Board and BoardMember.

Public board = whole org implicitly; board_members rows are meaningful for
PRIVATE boards (PRD D4/D5). board_role is UNUSED in v1 — reserved for a future
governance tier (PRD note: do not drop).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPKMixin


class Board(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "boards"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default="public")
    # Per-board task privacy (member request). 'all' = open model: every viewer
    # sees every task (default, legacy). 'assigned' = members see ONLY tasks
    # assigned to them; only the owner/admins see the full list + the report.
    # Orthogonal to `visibility` (who can open the board at all).
    task_scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="all")
    # View preset only — same data model either way (PRD §4.1).
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default="tasks")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Transfers to an org admin when the owner leaves (F9).
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    members: Mapped[list["BoardMember"]] = relationship(
        "BoardMember", back_populates="board", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("visibility IN ('public', 'private')", name="visibility_valid"),
        CheckConstraint("task_scope IN ('all', 'assigned')", name="task_scope_valid"),
        CheckConstraint("category IN ('tasks', 'checklist')", name="category_valid"),
        Index("ix_boards_org_visibility", "org_id", "visibility"),
    )

    def __repr__(self) -> str:
        return f"<Board(id={self.id}, name={self.name!r}, visibility={self.visibility})>"


class BoardMember(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "board_members"

    board_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    board_role: Mapped[str] = mapped_column(Text, nullable=False, server_default="member")

    board: Mapped["Board"] = relationship("Board", back_populates="members")

    __table_args__ = (UniqueConstraint("board_id", "user_id"),)


class BoardCategory(Base, UUIDPKMixin, CreatedAtMixin):
    """A named, colored group on a board (Monday-style). Tasks reference it by
    the free-text `category` tag; this table gives a category an identity, a
    color, an order, and lets it exist while empty."""

    __tablename__ = "board_categories"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    board_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str] = mapped_column(Text, nullable=False, server_default="#888780")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (UniqueConstraint("board_id", "name", name="uq_board_categories_board_name"),)
