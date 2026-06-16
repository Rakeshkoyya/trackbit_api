"""Billing artifacts (plan P4-BE-01). Invoices are a local mirror of Razorpay
charges so the Settings/Billing screen (S9) can list them without a live API
round-trip."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import CreatedAtMixin, UUIDPKMixin


class Invoice(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "invoices"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Razorpay payment/invoice id; unique so webhook replays are idempotent.
    provider_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # paise (₹500 = 50000)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")
    status: Mapped[str] = mapped_column(Text, nullable=False)  # paid | failed
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_invoices_org_created", "org_id", text("created_at DESC")),
    )
