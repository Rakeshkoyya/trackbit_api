"""Minimal product analytics (plan G7). A table and a helper, not a BI stack."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    props: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_analytics_events_event_time", "event", "created_at"),
        Index("ix_analytics_events_org_event", "org_id", "event"),
    )
