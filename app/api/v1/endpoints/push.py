"""Web Push: expose the VAPID public key, register/unregister device tokens."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.context import CurrentMember
from app.core.database import get_db
from app.core.dependencies import get_current_member
from app.models import DeviceToken
from app.schemas.common import MessageResponse
from app.services import notifications
from app.services.dispatcher import deliver

router = APIRouter()


class SubscribeRequest(BaseModel):
    subscription: dict  # the browser PushSubscription JSON


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/vapid-key")
def vapid_key() -> dict:
    return {"public_key": settings.VAPID_PUBLIC_KEY}


@router.post("/subscribe", response_model=MessageResponse)
def subscribe(
    body: SubscribeRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    token = json.dumps(body.subscription, sort_keys=True)
    existing = db.scalar(select(DeviceToken).where(DeviceToken.token == token))
    if existing:
        existing.user_id = member.user_id
        existing.last_seen_at = datetime.now(UTC)
    else:
        db.add(DeviceToken(user_id=member.user_id, platform="webpush", token=token,
                           last_seen_at=datetime.now(UTC)))
    db.flush()
    return MessageResponse(message="Subscribed to push.")


@router.post("/unsubscribe", response_model=MessageResponse)
def unsubscribe(
    body: UnsubscribeRequest,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    # Match by endpoint inside the stored subscription JSON.
    for tok in db.scalars(select(DeviceToken).where(DeviceToken.user_id == member.user_id)):
        try:
            if json.loads(tok.token).get("endpoint") == body.endpoint:
                db.execute(delete(DeviceToken).where(DeviceToken.id == tok.id))
        except (ValueError, TypeError):
            continue
    db.flush()
    return MessageResponse(message="Unsubscribed.")


@router.post("/test", response_model=MessageResponse)
def test_push(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Send a test notification to yourself (handy while wiring up push)."""
    n = notifications.enqueue(
        db, org_id=member.org_id, user_id=member.user_id, instance_id=None,
        notif_type="digest", channel="push",
        payload={"subject": "TrackBit test", "body": "Push is working 🎉"},
    )
    if n:
        deliver(db, n)
    return MessageResponse(message="Test sent.")
