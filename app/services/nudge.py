"""Manual nudge (plan P3-BE-03, F7/§3.6).

A nudge is the admin's one-tap "still waiting on you 🙂" — never "you failed."
It lists the member's currently-overdue tasks, dedupes within 4h so it can't be
used to pester, and rides the normal channel ladder (push → email).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.context import CurrentMember
from app.core.exceptions import NotFoundError
from app.models import Membership, Notification, TaskInstance, User
from app.schemas.report import NudgeResponse
from app.services import dispatcher, notifications

_DEDUPE_HOURS = 4


class NudgeService:
    def __init__(self, db: Session):
        self.db = db

    def nudge(self, admin: CurrentMember, user_id: uuid.UUID) -> NudgeResponse:
        target = self.db.scalar(
            select(Membership).where(
                Membership.org_id == admin.org_id,
                Membership.user_id == user_id,
                Membership.status == "active",
            )
        )
        if target is None:
            raise NotFoundError("Member")

        now = datetime.now(UTC)
        overdue = list(
            self.db.scalars(
                select(TaskInstance).where(
                    TaskInstance.org_id == admin.org_id,
                    TaskInstance.assignee_id == user_id,
                    TaskInstance.status == "open",
                    TaskInstance.due_at.isnot(None),
                    TaskInstance.due_at < now,
                )
            )
        )
        if not overdue:
            return NudgeResponse(sent=False, overdue_count=0, reason="nothing_overdue")

        recent = self.db.scalar(
            select(Notification.id).where(
                Notification.org_id == admin.org_id,
                Notification.user_id == user_id,
                Notification.notif_type == "nudge",
                Notification.created_at >= now - timedelta(hours=_DEDUPE_HOURS),
            )
        )
        if recent is not None:
            return NudgeResponse(sent=False, overdue_count=len(overdue), reason="recently_nudged")

        user = self.db.get(User, user_id)
        first = user.name.split()[0] if user and user.name else "there"
        titles = [t.title for t in overdue[:5]]
        listed = "\n".join(f"• {t}" for t in titles)
        more = len(overdue) - len(titles)
        if more > 0:
            listed += f"\n• …and {more} more"
        n_word = "task" if len(overdue) == 1 else "tasks"
        bucket = int(now.timestamp()) // (_DEDUPE_HOURS * 3600)
        notif = notifications.enqueue(
            self.db,
            org_id=admin.org_id,
            user_id=user_id,
            instance_id=None,
            notif_type="nudge",
            channel="push",
            payload={
                "subject": "A gentle nudge",
                "body": f"Hi {first} — {len(overdue)} {n_word} waiting on you:\n{listed}",
            },
            dedupe_key=f"nudge:{user_id}:{bucket}",
        )
        if notif is None:  # lost a race against the 4h bucket key
            return NudgeResponse(sent=False, overdue_count=len(overdue), reason="recently_nudged")
        dispatcher.send_now(self.db, notification_id=notif.id)
        return NudgeResponse(sent=True, overdue_count=len(overdue))
