"""Razorpay subscription billing (plan P4-BE-01).

Two plans only: Free and **Pro — ₹500/month flat per org** (no per-seat math).
The core loop is never paywalled; Pro lifts breadth caps and unlocks premium
surfaces. Webhooks are the source of truth for plan state — signature-verified,
idempotent, and non-destructive on downgrade (we re-limit, never delete).

Without Razorpay keys the service runs in **stub mode**: the upgrade flow is
wired end-to-end but checkout is disabled until keys are added.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.context import CurrentMember
from app.core.exceptions import AppError, ConflictError, ForbiddenError
from app.models import Invoice, Organization
from app.schemas.billing import BillingOut, CheckoutOut, InvoiceOut

logger = logging.getLogger("trackbit.billing")

PRO_AMOUNT_PAISE = 50000  # ₹500

_ACTIVATE = ("subscription.activated", "subscription.charged", "subscription.resumed", "subscription.authenticated")
_GRACE = ("subscription.halted", "subscription.pending", "payment.failed")
_END = ("subscription.cancelled", "subscription.completed", "subscription.expired")


def _razorpay_client():
    """Lazily build the SDK client. Returns None if the package isn't installed
    so stub mode degrades gracefully (add `razorpay` when going live)."""
    try:
        import razorpay  # noqa: PLC0415
    except ImportError:
        return None
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


class BillingService:
    def __init__(self, db: Session):
        self.db = db

    def get_billing(self, member: CurrentMember) -> BillingOut:
        org = member.org
        invoices = list(
            self.db.scalars(
                select(Invoice).where(Invoice.org_id == org.id)
                .order_by(Invoice.created_at.desc()).limit(24)
            )
        )
        return BillingOut(
            plan=org.plan,
            plan_status=org.plan_status,
            renews_at=org.plan_renews_at,
            grace_until=org.grace_until,
            configured=settings.billing_configured,
            key_id=settings.RAZORPAY_KEY_ID or None,
            amount=PRO_AMOUNT_PAISE,
            currency="INR",
            invoices=[
                InvoiceOut(
                    id=i.id, amount=i.amount, currency=i.currency, status=i.status,
                    paid_at=i.paid_at, created_at=i.created_at,
                )
                for i in invoices
            ],
        )

    def start_checkout(self, admin: CurrentMember) -> CheckoutOut:
        org = admin.org
        if org.plan == "pro" and org.plan_status == "active":
            raise ConflictError("This org is already on Pro.", code="already_pro")
        if not settings.billing_configured:
            return CheckoutOut(
                configured=False,
                message="Billing isn't configured yet. Add Razorpay keys to enable checkout.",
            )
        client = _razorpay_client()
        if client is None:
            raise AppError("Billing is temporarily unavailable.", code="billing_unavailable")
        sub = client.subscription.create({
            "plan_id": settings.RAZORPAY_PLAN_ID,
            "total_count": 120,  # up to 10 years of monthly cycles
            "customer_notify": 1,
            "notes": {"org_id": str(org.id)},
        })
        org.razorpay_subscription_id = sub["id"]
        self.db.flush()
        return CheckoutOut(
            configured=True,
            subscription_id=sub["id"],
            key_id=settings.RAZORPAY_KEY_ID,
            short_url=sub.get("short_url"),
        )

    # ---- webhook (the source of truth) --------------------------------
    def handle_webhook(self, raw: bytes, signature: str | None) -> dict:
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if secret:
            expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            if not signature or not hmac.compare_digest(expected, signature):
                raise ForbiddenError("Invalid webhook signature.", code="bad_signature")
        try:
            event = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise AppError("Malformed webhook body.", code="bad_payload") from exc

        etype = event.get("event", "")
        payload = event.get("payload", {}) or {}
        sub = (payload.get("subscription", {}) or {}).get("entity", {}) or {}
        org = self._resolve_org(sub)
        if org is None:
            return {"ok": True, "ignored": "no_org"}

        now = datetime.now(UTC)
        if etype in _ACTIVATE:
            org.plan = "pro"
            org.plan_status = "active"
            org.grace_until = None
            if sub.get("id"):
                org.razorpay_subscription_id = sub["id"]
            cur_end = sub.get("current_end")
            org.plan_renews_at = datetime.fromtimestamp(cur_end, UTC) if cur_end else None
            pay = (payload.get("payment", {}) or {}).get("entity")
            if pay:
                self._record_invoice(org, pay)
        elif etype in _GRACE:
            if org.plan == "pro":  # don't fabricate Pro from a stray failure
                org.plan_status = "grace"
                org.grace_until = now + timedelta(days=settings.PRO_GRACE_DAYS)
        elif etype in _END:
            self._downgrade(org)
        self.db.flush()
        return {"ok": True, "event": etype}

    def _resolve_org(self, sub: dict) -> Organization | None:
        sub_id = sub.get("id")
        if sub_id:
            org = self.db.scalar(
                select(Organization).where(Organization.razorpay_subscription_id == sub_id)
            )
            if org is not None:
                return org
        notes = sub.get("notes") or {}
        if notes.get("org_id"):
            try:
                return self.db.get(Organization, uuid.UUID(notes["org_id"]))
            except (ValueError, TypeError):
                return None
        return None

    def _record_invoice(self, org: Organization, pay: dict) -> None:
        pid = pay.get("id")
        if not pid:
            return
        if self.db.scalar(select(Invoice.id).where(Invoice.provider_id == pid)):
            return  # idempotent on webhook replay
        self.db.add(Invoice(
            org_id=org.id, provider_id=pid, amount=pay.get("amount", PRO_AMOUNT_PAISE),
            currency=pay.get("currency", "INR"), status="paid", paid_at=datetime.now(UTC),
        ))

    @staticmethod
    def _downgrade(org: Organization) -> None:
        """Non-destructive: drop to Free limits, keep every board/member/task."""
        org.plan = "free"
        org.plan_status = "none"
        org.grace_until = None
        org.plan_renews_at = None
