"""P0-BE-06 done-when: events written from a flow; activation_funnel returns funnel."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text

from app.models import (
    AnalyticsEvent,
    Board,
    Organization,
    TaskEvent,
    TaskInstance,
    User,
)
from tests.conftest import AdminSession


def _now():
    return datetime.now(UTC)


def test_register_invite_join_emit_analytics(client, unique_email, cleanup):
    # Register an org (org_registered), invite a member (member_invited),
    # then redeem the invite (member_joined).
    reg = client.post(
        "/api/v1/auth/register-org",
        json={"org_name": "A Co", "name": "Admin", "email": unique_email,
              "password": "supersecret1", "timezone": "Asia/Kolkata"},
    ).json()
    org_id = uuid.UUID(reg["org"]["id"])
    cleanup["orgs"].append(org_id)
    cleanup["users"].append(uuid.UUID(reg["user"]["id"]))

    inv = client.post(
        "/api/v1/org/members/invite",
        headers={"Authorization": f"Bearer {reg['access_token']}"},
        json={"name": "Staffer", "phone": "+919800009999", "role": "member"},
    ).json()
    cleanup["users"].append(uuid.UUID(inv["user_id"]))
    token = inv["invite_url"].rsplit("/join/", 1)[1]
    client.post("/api/v1/auth/verify", json={"token": token})

    db = AdminSession()
    try:
        events = set(
            db.scalars(
                select(AnalyticsEvent.event).where(AnalyticsEvent.org_id == org_id)
            )
        )
    finally:
        db.close()
    assert {"org_registered", "member_invited", "member_joined"} <= events


def test_activation_funnel_reflects_assign_then_complete():
    """An org that assigns-to-other and completes is 'activated'; one that only
    assigns is not."""
    db = AdminSession()
    org_ids, user_ids = [], []
    try:
        def build_org(name):
            org = Organization(name=f"act-{name}-{uuid.uuid4()}")
            db.add(org)
            db.flush()
            org_ids.append(org.id)
            kc = User(name="kc", email=f"kc-{uuid.uuid4().hex[:8]}@example.com")
            staff = User(name="staff", email=f"staff-{uuid.uuid4().hex[:8]}@example.com")
            db.add_all([kc, staff])
            db.flush()
            user_ids.extend([kc.id, staff.id])
            board = Board(org_id=org.id, name="B", created_by=kc.id, owner_id=kc.id)
            db.add(board)
            db.flush()
            inst = TaskInstance(org_id=org.id, board_id=board.id, title="t", created_by=kc.id)
            db.add(inst)
            db.flush()
            return org, kc, staff, inst

        # Org 1: kc assigns to staff (other), then it's completed -> activated.
        org1, kc1, staff1, inst1 = build_org("done")
        db.add(TaskEvent(org_id=org1.id, instance_id=inst1.id, actor_id=kc1.id,
                         event_type="assigned", payload={"to": str(staff1.id)},
                         created_at=_now() - timedelta(hours=2)))
        db.add(TaskEvent(org_id=org1.id, instance_id=inst1.id, actor_id=staff1.id,
                         event_type="completed", created_at=_now() - timedelta(hours=1)))

        # Org 2: kc assigns to staff but it's never completed -> not activated.
        org2, kc2, staff2, inst2 = build_org("pending")
        db.add(TaskEvent(org_id=org2.id, instance_id=inst2.id, actor_id=kc2.id,
                         event_type="assigned", payload={"to": str(staff2.id)},
                         created_at=_now() - timedelta(hours=2)))
        db.commit()

        rows = {
            r._mapping["org_id"]: r._mapping
            for r in db.execute(
                text("SELECT org_id, activated, first_activation_at "
                     "FROM activation_funnel WHERE org_id = ANY(:ids)"),
                {"ids": [org1.id, org2.id]},
            )
        }
        assert rows[org1.id]["activated"] is True
        assert rows[org1.id]["first_activation_at"] is not None
        assert rows[org2.id]["activated"] is False
    finally:
        for oid in org_ids:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in user_ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
        db.close()
