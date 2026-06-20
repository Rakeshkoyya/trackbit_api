"""Phase 2 — recurrence engine + materializer/miss-marker/sweep invariants."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, select

from app.core.recurrence import next_occurrences, occurrences_in, occurs_on, validate_rule
from app.core.timeutil import org_due_at
from app.models import (
    Board,
    DeviceToken,
    Notification,
    Organization,
    TaskInstance,
    TaskTemplate,
    User,
)
from app.services import jobs
from tests.conftest import AdminSession


# ---- pure recurrence logic (no DB) ------------------------------------
def test_daily_and_weekdays():
    assert occurs_on(validate_rule({"freq": "daily"}), date(2026, 6, 13))
    wd = validate_rule({"freq": "weekdays"})
    assert occurs_on(wd, date(2026, 6, 12))  # Friday
    assert not occurs_on(wd, date(2026, 6, 13))  # Saturday


def test_weekly_specific_days():
    rule = validate_rule({"freq": "weekly", "days": ["mon", "fri"], "time": "10:00"})
    assert rule["days"] == ["mon", "fri"]
    assert occurs_on(rule, date(2026, 6, 15))  # Monday
    assert not occurs_on(rule, date(2026, 6, 17))  # Wednesday


def test_monthly_month_end_clamp():
    # "day 31" in April (30 days) lands on Apr 30, not skipped.
    rule = validate_rule({"freq": "monthly", "day": 31})
    apr = occurrences_in(rule, start=date(2026, 4, 1), end=date(2026, 4, 30))
    assert apr == [date(2026, 4, 30)]
    may = occurrences_in(rule, start=date(2026, 5, 1), end=date(2026, 5, 31))
    assert may == [date(2026, 5, 31)]


def test_next_occurrences_count():
    rule = validate_rule({"freq": "weekly", "days": ["mon"]})
    nxt = next_occurrences(rule, after=date(2026, 6, 13), count=3)
    assert len(nxt) == 3 and all(d.weekday() == 0 for d in nxt)


def test_org_due_at_all_day_vs_timed():
    due, all_day = org_due_at("Asia/Kolkata", date(2026, 6, 13), None)
    assert all_day is True
    due2, all_day2 = org_due_at("Asia/Kolkata", date(2026, 6, 13), time(10, 0))
    assert all_day2 is False
    # 10:00 IST == 04:30 UTC
    assert due2.astimezone(UTC).hour == 4 and due2.minute == 30


# ---- materializer + miss-marker (DB) ----------------------------------
def _mk_org(db, tz="Asia/Kolkata"):
    org = Organization(name=f"rec-{uuid.uuid4()}", timezone=tz)
    db.add(org)
    db.flush()
    u = User(name="owner", email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
    db.add(u)
    db.flush()
    board = Board(org_id=org.id, name="B", created_by=u.id, owner_id=u.id)
    db.add(board)
    db.flush()
    return org, u, board


def test_materializer_idempotent_and_tz():
    db = AdminSession()
    org_ids, user_ids = [], []
    try:
        from app.services.recurrence import RecurringService

        org, u, board = _mk_org(db, "Asia/Kolkata")
        org_ids.append(org.id)
        user_ids.append(u.id)
        tpl = TaskTemplate(
            org_id=org.id, board_id=board.id, title="Daily standup",
            recurrence_rule=validate_rule({"freq": "daily", "time": "09:00"}),
            active=True, created_by=u.id,
        )
        db.add(tpl)
        db.flush()

        svc = RecurringService(db)
        today = date.today()
        n1 = svc.materialize_org(org, [today, today + timedelta(days=1)])
        n2 = svc.materialize_org(org, [today, today + timedelta(days=1)])  # rerun
        db.commit()
        assert n1 == 2  # today + tomorrow
        assert n2 == 0  # idempotent, no duplicates
        count = len(list(db.scalars(
            select(TaskInstance.id).where(TaskInstance.template_id == tpl.id)
        )))
        assert count == 2
    finally:
        for oid in org_ids:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in user_ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
        db.close()


def test_miss_marker_flips_yesterday_open():
    db = AdminSession()
    org_ids, user_ids = [], []
    try:
        org, u, board = _mk_org(db)
        org_ids.append(org.id)
        user_ids.append(u.id)
        # An open instance due 2 days ago.
        old = TaskInstance(
            org_id=org.id, board_id=board.id, title="Late", assignee_id=u.id,
            due_at=datetime.now(UTC) - timedelta(days=2), status="open", created_by=u.id,
        )
        # A future instance — must NOT be marked.
        future = TaskInstance(
            org_id=org.id, board_id=board.id, title="Future", assignee_id=u.id,
            due_at=datetime.now(UTC) + timedelta(days=1), status="open", created_by=u.id,
        )
        db.add_all([old, future])
        db.commit()

        jobs.run_miss_marker()  # opens its own session/commit

        db.expire_all()
        assert db.get(TaskInstance, old.id).status == "missed"
        assert db.get(TaskInstance, future.id).status == "open"
    finally:
        for oid in org_ids:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in user_ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
        db.close()


def test_reminder_dedupe_and_sweep(monkeypatch):
    db = AdminSession()
    org_ids, user_ids = [], []
    try:
        org, u, board = _mk_org(db)
        org_ids.append(org.id)
        user_ids.append(u.id)
        u.email = f"rec-{uuid.uuid4().hex[:8]}@example.com"
        inst = TaskInstance(
            org_id=org.id, board_id=board.id, title="Remind me", assignee_id=u.id,
            due_at=datetime.now(UTC) - timedelta(minutes=1), status="open",
            remind_before_minutes=30, created_by=u.id,
        )
        db.add(inst)
        db.flush()

        from app.core.config import settings
        from app.services import notifications

        notifications.enqueue_reminder(db, inst)
        notifications.enqueue_reminder(db, inst)  # dedupe: still one

        # Reminders are push-only now (no email fallback), so the user must have a
        # device token to receive one. Force push stub mode for a deterministic win.
        monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", "")
        db.add(DeviceToken(
            user_id=u.id, platform="webpush",
            token=f'{{"endpoint": "https://example.com/{uuid.uuid4().hex}"}}',
        ))
        db.commit()
        rows = list(db.scalars(
            select(Notification).where(Notification.dedupe_key == f"reminder:{inst.id}")
        ))
        assert len(rows) == 1

        # Sweep delivers it over push (stub → success), marking it sent.
        jobs.run_sweep()
        db.expire_all()
        sent = db.get(Notification, rows[0].id)
        assert sent.status == "sent"
        assert sent.channel == "push"
    finally:
        for oid in org_ids:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in user_ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
        db.close()


def test_email_fallback_only_for_report_types():
    """Email fallback is restricted to overdue / digest / report_card. A push-only
    type (reminder) with no device token is NOT emailed; an email-eligible type
    (overdue) with no device token still reaches the user via email."""
    db = AdminSession()
    org_ids, user_ids = [], []
    try:
        org, u, board = _mk_org(db)
        org_ids.append(org.id)
        user_ids.append(u.id)
        u.email = f"gate-{uuid.uuid4().hex[:8]}@example.com"
        inst = TaskInstance(
            org_id=org.id, board_id=board.id, title="T", assignee_id=u.id,
            due_at=datetime.now(UTC) - timedelta(minutes=1), status="open", created_by=u.id,
        )
        db.add(inst)
        db.flush()

        from app.services import notifications

        past = datetime.now(UTC) - timedelta(minutes=1)
        # push-only type — no device token means it can't be delivered (no email).
        rem = notifications.enqueue(
            db, org_id=org.id, user_id=u.id, instance_id=inst.id,
            notif_type="reminder", scheduled_at=past, dedupe_key=f"reminder:{inst.id}",
        )
        # email-eligible type — delivered via the email stub fallback.
        over = notifications.enqueue(
            db, org_id=org.id, user_id=u.id, instance_id=inst.id,
            notif_type="overdue", scheduled_at=past, dedupe_key=f"overdue:{inst.id}",
        )
        db.commit()

        jobs.run_sweep()
        db.expire_all()

        # reminder: push-only + no device => not sent (requeued for retry).
        assert db.get(Notification, rem.id).status != "sent"
        # overdue: email fallback delivered it.
        over_row = db.get(Notification, over.id)
        assert over_row.status == "sent"
        assert over_row.channel == "email"
    finally:
        for oid in org_ids:
            db.execute(delete(Organization).where(Organization.id == oid))
        for uid in user_ids:
            db.execute(delete(User).where(User.id == uid))
        db.commit()
        db.close()
