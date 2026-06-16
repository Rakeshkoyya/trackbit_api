"""Notification enqueue helpers.

Rows are an outbox: the 2-min sweep delivers pending rows via the dispatcher.
Instant types (assigned/passed) are also pushed immediately (send-now) so they
don't wait for the sweep. dedupe_key gives at-most-once per type per instance.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, TaskInstance
from app.services import dispatcher


def enqueue(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    instance_id: uuid.UUID | None,
    notif_type: str,
    channel: str = "push",
    payload: dict | None = None,
    scheduled_at: datetime | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    if dedupe_key is not None:
        exists = db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key))
        if exists is not None:
            return None
    n = Notification(
        org_id=org_id,
        user_id=user_id,
        instance_id=instance_id,
        channel=channel,
        notif_type=notif_type,
        status="pending",
        payload=payload,
        scheduled_at=scheduled_at or datetime.now(UTC),
        dedupe_key=dedupe_key,
    )
    db.add(n)
    db.flush()
    return n


def enqueue_instant(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    instance_id: uuid.UUID,
    notif_type: str,
    actor_name: str | None = None,
) -> None:
    """Assigned / passed — record and attempt immediate delivery."""
    n = enqueue(
        db, org_id=org_id, user_id=user_id, instance_id=instance_id,
        notif_type=notif_type, payload={"actor_name": actor_name} if actor_name else None,
    )
    if n is not None:
        dispatcher.send_now(db, notification_id=n.id)


def enqueue_reminder(db: Session, instance: TaskInstance) -> None:
    """Schedule a reminder `remind_before_minutes` before due (plan §8.5)."""
    if instance.due_at is None or instance.assignee_id is None:
        return
    when = instance.due_at - timedelta(minutes=instance.remind_before_minutes or 30)
    enqueue(
        db, org_id=instance.org_id, user_id=instance.assignee_id, instance_id=instance.id,
        notif_type="reminder", scheduled_at=when, dedupe_key=f"reminder:{instance.id}",
    )


def reset_reminder(db: Session, instance: TaskInstance) -> None:
    """Drop a still-pending reminder and re-enqueue (after reassign / due change)."""
    from sqlalchemy import delete

    db.execute(
        delete(Notification).where(
            Notification.dedupe_key == f"reminder:{instance.id}",
            Notification.status == "pending",
        )
    )
    db.flush()
    enqueue_reminder(db, instance)


def enqueue_unassigned(
    db: Session, *, org_id: uuid.UUID, user_id: uuid.UUID, instance_id: uuid.UUID, reason: str
) -> None:
    """F9: a task left someone (board went private, or they left a board). Calm
    heads-up so work never silently vanishes from under them."""
    n = enqueue(
        db, org_id=org_id, user_id=user_id, instance_id=instance_id,
        notif_type="unassigned", payload={"reason": reason},
    )
    if n is not None:
        dispatcher.send_now(db, notification_id=n.id)


def enqueue_overdue_nudge(db: Session, instance: TaskInstance, *, when: datetime) -> None:
    """Auto gentle nudge, once, 1h after due (plan §9). Dedupe-keyed."""
    if instance.assignee_id is None:
        return
    enqueue(
        db, org_id=instance.org_id, user_id=instance.assignee_id, instance_id=instance.id,
        notif_type="overdue", scheduled_at=when, dedupe_key=f"overdue:{instance.id}",
    )
