"""Task domain: TaskTemplate, TaskInstance, TaskEvent, Attachment.

Template vs Instance (PRD §4.2, load-bearing):
- TaskTemplate exists only for recurring tasks (the definition).
- TaskInstance is every concrete unit of work; one-time tasks have template_id NULL.
- Instances are materialized in advance by the nightly job — never lazily.

task_events is APPEND-ONLY: it is both the reporting backbone and the
accountability chain. Never UPDATE or DELETE rows. All state changes must go
through the event writer (services/events.py, Phase 1) in one transaction.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPKMixin

EVENT_TYPES = (
    "created",
    "assigned",
    "claimed",
    "passed",
    "completed",
    "reopened",
    "missed",
    "commented",
    "attached",
    "edited",  # field-level diff in payload (plan G4)
    "cancelled",  # soft delete with audit trail (plan G3)
)


class TaskTemplate(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "task_templates"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    board_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form per-task tag (board-scoped distinct values). Inherited by instances.
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0=none, 1=low, 2=med, 3=high. Inherited by spawned instances.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Human presets serialized, e.g. {"freq":"weekly","days":["mon","fri"],"time":"10:00"}.
    # No time key => spawned instances are all-day.
    recurrence_rule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Null = spawn unassigned (claimable).
    default_assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Inherited by spawned instances (Track FL alarm reminders).
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Null = org default (30); inherited by spawned instances.
    remind_before_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    __table_args__ = (Index("ix_task_templates_org_active", "org_id", "active"),)


class TaskInstance(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "task_instances"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    board_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("boards.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_templates.id", ondelete="SET NULL"), nullable=True
    )
    # Org-local calendar date of a recurring occurrence; set by the materializer.
    # The (template_id, occurrence_date) unique index is its idempotency guard.
    occurrence_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form per-task tag (board-scoped distinct values); see TaskTemplate.category.
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0=none, 1=low, 2=med, 3=high; see TaskTemplate.priority.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Null = claimable (PRD D10: one assignee or none, never co-owners).
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # all_day: due_at holds org-local end-of-day; UI shows the date without a time.
    # due_at NULL = "anytime until done" — never missed (plan G5).
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Denormalized ↩ badge (source of truth is 'passed' events).
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    remind_before_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="30"
    )

    events: Mapped[list["TaskEvent"]] = relationship(
        "TaskEvent", back_populates="instance", order_by="TaskEvent.id"
    )

    __table_args__ = (
        CheckConstraint("status IN ('open', 'done', 'missed', 'cancelled')", name="status_valid"),
        Index("ix_task_instances_home", "org_id", "assignee_id", "status", "due_at"),
        Index("ix_task_instances_board", "board_id", "status", "due_at"),
        Index(
            "uq_task_instances_template_occurrence",
            "template_id",
            "occurrence_date",
            unique=True,
            postgresql_where=text("template_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<TaskInstance(id={self.id}, title={self.title!r}, status={self.status})>"


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_instances.id", ondelete="CASCADE"), nullable=False
    )
    # Null = system actor (materializer, miss-marker).
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    instance: Mapped["TaskInstance"] = relationship("TaskInstance", back_populates="events")

    __table_args__ = (
        CheckConstraint(
            "event_type IN ({})".format(", ".join(f"'{t}'" for t in EVENT_TYPES)),
            name="event_type_valid",
        ),
        Index("ix_task_events_instance", "instance_id", "id"),
        Index("ix_task_events_org_type_time", "org_id", "event_type", "created_at"),
    )


class Attachment(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "attachments"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_instances.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # note text
    file_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # object-storage URL

    __table_args__ = (CheckConstraint("kind IN ('note', 'photo')", name="kind_valid"),)
