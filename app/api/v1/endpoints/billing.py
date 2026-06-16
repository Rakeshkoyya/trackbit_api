"""Billing endpoints (S9 + webhook). Pro = ₹500/month flat per org."""

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.dependencies import get_current_member, require_admin
from app.schemas.billing import BillingOut, CheckoutOut
from app.services.billing import BillingService

router = APIRouter()


@router.get("", response_model=BillingOut)
def get_billing(
    member: CurrentMember = Depends(get_current_member), db: Session = Depends(get_db)
) -> BillingOut:
    return BillingService(db).get_billing(member)


@router.post("/checkout", response_model=CheckoutOut)
def start_checkout(
    admin: CurrentMember = Depends(require_admin), db: Session = Depends(get_db)
) -> CheckoutOut:
    return BillingService(db).start_checkout(admin)


@router.post("/webhook")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    raw = await request.body()
    # get_db commits on success / rolls back on a raised signature error.
    return BillingService(db).handle_webhook(raw, x_razorpay_signature)
