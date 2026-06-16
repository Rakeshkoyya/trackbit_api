"""The event writer — the spine (plan §7.1).

Every task state change goes through here: the event is appended AND the
instance mutated within the same request transaction. task_events is
append-only; never UPDATE/DELETE. Reports derive net state from these events.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TaskEvent, User


def append_event(
    db: Session,
    *,
    org_id: uuid.UUID,
    instance_id: uuid.UUID,
    event_type: str,
    actor_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> TaskEvent:
    ev = TaskEvent(
        org_id=org_id,
        instance_id=instance_id,
        actor_id=actor_id,
        event_type=event_type,
        payload=payload,
    )
    db.add(ev)
    db.flush()
    return ev


def resolve_user_names(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Batch-resolve user ids to display names for rendering event chains."""
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()
    return {uid: name for uid, name in rows}
