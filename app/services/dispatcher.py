"""Notification dispatch: render content + deliver via the channel ladder.

Ladder (plan G10): push (if the user has device tokens) → email fallback.
Used by both the sweep and the send-now path for instant assign/pass.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import DeviceToken, Notification, TaskInstance, User
from app.services import email_templates
from app.services import push as push_adapter
from app.services.delivery import send_email

logger = logging.getLogger("trackbit.dispatch")

# Notification types allowed to fall back to email. Everything else is
# push-only (Chrome pop-up) — reminders / assigned / passed / unassigned / nudge
# must NOT land in anyone's inbox (product call). Push is still attempted for
# ALL types below; this gate only controls the email fallback.
EMAIL_ENABLED_TYPES = {"overdue", "digest", "report_card"}


def _render(db: Session, n: Notification) -> tuple[str, str, str]:
    """Return (title, body, url) for a notification, north-star calm copy."""
    inst = db.get(TaskInstance, n.instance_id) if n.instance_id else None
    title_task = inst.title if inst else "a task"
    url = f"{settings.FRONTEND_BASE_URL}/task/{n.instance_id}" if n.instance_id else settings.FRONTEND_BASE_URL
    p = n.payload or {}
    actor = p.get("actor_name")

    mapping = {
        "assigned": (
            "You've got a task",
            f"{actor + ' assigned you' if actor else 'Assigned to you'}: {title_task}",
        ),
        "passed": (
            "A task came your way",
            f"{actor + ' passed you' if actor else 'Passed to you'}: {title_task}",
        ),
        "reminder": ("Reminder", f"{title_task} is coming up."),
        "overdue": ("Still waiting", f"{title_task} is waiting for you 🙂"),
        "digest": (p.get("subject", "Your day on TrackBit"), p.get("body", "")),
        "report_card": (p.get("subject", "Today's report"), p.get("body", "")),
        "nudge": (
            p.get("subject", "A gentle nudge"),
            p.get("body", f"{title_task} is waiting for you 🙂"),
        ),
        "unassigned": (
            "A task moved off your list",
            f"{title_task} is no longer assigned to you — nothing you need to do.",
        ),
    }
    title, body = mapping.get(n.notif_type, ("TrackBit", title_task))
    return title, body, url


def deliver(db: Session, n: Notification) -> bool:
    """Deliver one notification. Updates its status/channel/sent_at. Returns success."""
    user = db.get(User, n.user_id)
    if user is None:
        n.status = "failed"
        return False
    title, body, url = _render(db, n)

    # Try push first if the user has any device tokens.
    tokens = list(db.scalars(select(DeviceToken).where(DeviceToken.user_id == n.user_id)))
    for tok in tokens:
        try:
            subscription = json.loads(tok.token)
        except (ValueError, TypeError):
            continue
        ok, gone = push_adapter.push_send(subscription=subscription, title=title, body=body, url=url)
        if gone:
            db.execute(delete(DeviceToken).where(DeviceToken.id == tok.id))
            continue
        if ok:
            _mark_sent(n, "push")
            return True

    # Email fallback — only for the report-style types (overdue / digest /
    # report_card). reminders, assigned, passed, unassigned and nudge are
    # push-only and never email. Push above already covered the Chrome pop-up
    # for every type.
    if n.notif_type in EMAIL_ENABLED_TYPES and user.email:
        # overdue is a task reminder → reminders@; digest/report_card are general
        # updates → the default hello@ sender.
        sender = (
            settings.RESEND_FROM_REMINDERS
            if n.notif_type == "overdue"
            else settings.RESEND_FROM
        )
        html, text = email_templates.notification(
            notif_type=n.notif_type, heading=title, message=body, url=url
        )
        if send_email(to=user.email, subject=title, body=text, html=html, sender=sender):
            _mark_sent(n, "email")
            return True

    n.status = "failed"
    return False


def _mark_sent(n: Notification, channel: str) -> None:
    n.status = "sent"
    n.channel = channel
    n.sent_at = datetime.now(UTC)


def send_now(db: Session, *, notification_id: uuid.UUID) -> None:
    """Best-effort immediate delivery (instant assign/pass). Never raises."""
    try:
        n = db.get(Notification, notification_id)
        if n and n.status == "pending":
            deliver(db, n)
    except Exception:  # noqa: BLE001 — delivery must never break the request
        logger.exception("send_now failed for %s", notification_id)
