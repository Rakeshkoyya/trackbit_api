"""Minimal product analytics (plan G7).

`track()` appends a row; that's the whole API. Activation and funnels are SQL
views/queries over analytics_events + task_events, not a BI pipeline.
"""

import uuid

from sqlalchemy.orm import Session

from app.models import AnalyticsEvent

# Event names — the keys we actually query on. Phase 1 adds the task.* events.
ORG_REGISTERED = "org_registered"
MEMBER_INVITED = "member_invited"
MEMBER_JOINED = "member_joined"
MAGIC_LINK_REQUESTED = "magic_link_requested"
PASSWORD_RESET_REQUESTED = "password_reset_requested"
PASSWORD_SET = "password_set"
MEMBER_BULK_CREATED = "member_bulk_created"
SETUP_COMPLETED = "setup_completed"
# Reserved for Phase 1 wiring:
TASK_CREATED = "task_created"
TASK_ASSIGNED = "task_assigned"
TASK_COMPLETED = "task_completed"
TASK_CLAIMED = "task_claimed"
TASK_PASSED = "task_passed"
ALL_CLEAR_REACHED = "all_clear_reached"
FIRST_COMPLETION = "first_completion"


def track(
    db: Session,
    *,
    event: str,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    props: dict | None = None,
) -> None:
    db.add(AnalyticsEvent(org_id=org_id, user_id=user_id, event=event, props=props))
