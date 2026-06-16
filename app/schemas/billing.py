"""Billing schemas (S9). Pro is ₹500/month flat for the whole org."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class InvoiceOut(BaseModel):
    id: uuid.UUID
    amount: int  # paise
    currency: str
    status: str
    paid_at: datetime | None = None
    created_at: datetime


class BillingOut(BaseModel):
    plan: str
    plan_status: str
    renews_at: datetime | None = None
    grace_until: datetime | None = None
    configured: bool  # are Razorpay keys present?
    key_id: str | None = None  # public key for the checkout widget
    amount: int = 50000  # ₹500 in paise
    currency: str = "INR"
    invoices: list[InvoiceOut] = []


class CheckoutOut(BaseModel):
    configured: bool
    subscription_id: str | None = None
    key_id: str | None = None
    short_url: str | None = None  # Razorpay-hosted checkout fallback
    message: str | None = None
