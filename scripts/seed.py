"""Seed the demo org — the shared dev fixture (plan §7.8).

Idempotent: wipes and recreates the demo org by name. Run:
    python -m scripts.seed
"""

from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    Board,
    BoardMember,
    Membership,
    Organization,
    TaskEvent,
    TaskInstance,
    User,
)

IST = timezone(timedelta(hours=5, minutes=30))
DEMO_ORG_NAME = "SHANA Ops (demo)"


def _now() -> datetime:
    return datetime.now(UTC)


def _today_at(hour: int, minute: int = 0) -> datetime:
    """UTC datetime for today at the given org-local (IST) time."""
    local = datetime.now(IST).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local.astimezone(UTC)


DEMO_EMAILS = ["kc@demo.trackbit.app", "priya@demo.trackbit.app"]
DEMO_PHONES = ["+919800000001", "+919800000002"]


def wipe_demo(db: Session) -> None:
    org = db.scalar(select(Organization).where(Organization.name == DEMO_ORG_NAME))
    if org:
        # FK cascades clean up boards/tasks/events/memberships under the org.
        db.execute(delete(Organization).where(Organization.id == org.id))
        db.flush()
    # Users are global — remove the demo people too so re-seeding is idempotent.
    db.execute(delete(User).where(User.email.in_(DEMO_EMAILS)))
    db.execute(delete(User).where(User.phone.in_(DEMO_PHONES)))
    db.flush()


def seed() -> None:
    db = SessionLocal()
    try:
        wipe_demo(db)

        org = Organization(name=DEMO_ORG_NAME, timezone="Asia/Kolkata", plan="pro")
        db.add(org)
        db.flush()

        # --- People ---------------------------------------------------------
        kc = User(name="KC", email="kc@demo.trackbit.app", password_hash=hash_password("demo1234"))
        priya = User(name="Priya", email="priya@demo.trackbit.app",
                     password_hash=hash_password("demo1234"))
        ramesh = User(name="Ramesh", phone="+919800000001")  # phone-only staffer
        anil = User(name="Anil", phone="+919800000002")
        db.add_all([kc, priya, ramesh, anil])
        db.flush()

        for role, user in [("admin", kc), ("member", priya), ("member", ramesh), ("member", anil)]:
            db.add(Membership(org_id=org.id, user_id=user.id, org_role=role, last_active_at=_now()))
        db.flush()

        # --- Boards ---------------------------------------------------------
        daily = Board(org_id=org.id, name="Daily Ops", visibility="public",
                      category="tasks", created_by=kc.id, owner_id=kc.id)
        admissions = Board(org_id=org.id, name="Admissions", visibility="private",
                           category="tasks", created_by=kc.id, owner_id=kc.id)
        db.add_all([daily, admissions])
        db.flush()

        # Board membership. Owners are always board members (so a flip to private
        # never locks them out). Private "Admissions" = KC + Priya only.
        db.add_all([
            BoardMember(board_id=daily.id, user_id=kc.id),
            BoardMember(board_id=admissions.id, user_id=kc.id),
            BoardMember(board_id=admissions.id, user_id=priya.id),
        ])
        db.flush()

        instances: list[TaskInstance] = []

        def add_task(board, title, *, assignee=None, due=None, status="open", all_day=False,
                     pass_count=0, completed_by=None, completed_at=None, created_at=None,
                     events=None):
            created = created_at or (_now() - timedelta(hours=20))
            inst = TaskInstance(
                org_id=org.id, board_id=board.id, title=title,
                assignee_id=assignee.id if assignee else None,
                due_at=due, all_day=all_day, status=status, pass_count=pass_count,
                completed_by=completed_by.id if completed_by else None,
                completed_at=completed_at, created_by=kc.id, created_at=created,
            )
            db.add(inst)
            db.flush()
            # Every instance starts with a 'created' event, then its history.
            chain = [{"type": "created", "actor": kc.id, "when": created}] + (events or [])
            for ev in chain:
                db.add(TaskEvent(
                    org_id=org.id, instance_id=inst.id, actor_id=ev.get("actor"),
                    event_type=ev["type"], payload=ev.get("payload"),
                    created_at=ev.get("when", created),
                ))
            instances.append(inst)
            return inst

        yesterday = _now() - timedelta(days=1)

        # Overdue (one-time, surfaces in Overdue section)
        add_task(daily, "Email parent group", assignee=priya, due=yesterday,
                 events=[{"type": "assigned", "actor": kc.id,
                          "payload": {"to": str(priya.id)}, "when": yesterday}])

        # Due today (timed)
        add_task(daily, "Submit attendance", assignee=priya, due=_today_at(10, 0),
                 events=[{"type": "assigned", "actor": kc.id, "payload": {"to": str(priya.id)}}])
        add_task(daily, "Restock display", assignee=ramesh, due=_today_at(14, 0),
                 events=[{"type": "assigned", "actor": kc.id, "payload": {"to": str(ramesh.id)}}])
        # Passed task (shows "passed by Ramesh ↩")
        add_task(daily, "Reply to reviews", assignee=priya, due=_today_at(16, 0), pass_count=1,
                 events=[
                     {"type": "assigned", "actor": kc.id, "payload": {"to": str(ramesh.id)}},
                     {"type": "passed", "actor": ramesh.id,
                      "payload": {"from": str(ramesh.id), "to": str(priya.id)}},
                 ])

        # Anytime today (untimed, assigned — never missed, plan G5)
        add_task(daily, "Tidy storeroom", assignee=ramesh, due=None)

        # Claimable (unassigned, public board)
        add_task(daily, "Update pricing sheet", assignee=None, due=_today_at(17, 0))
        add_task(daily, "Check supplier emails", assignee=None, due=None)

        # Done today
        add_task(daily, "Open shop", assignee=ramesh, due=_today_at(9, 0), status="done",
                 completed_by=ramesh, completed_at=_today_at(9, 30),
                 events=[
                     {"type": "assigned", "actor": kc.id, "payload": {"to": str(ramesh.id)}},
                     {"type": "completed", "actor": ramesh.id, "when": _today_at(9, 30)},
                 ])
        add_task(daily, "Morning headcount", assignee=priya, due=_today_at(9, 15), status="done",
                 completed_by=priya, completed_at=_today_at(9, 12),
                 events=[
                     {"type": "assigned", "actor": kc.id, "payload": {"to": str(priya.id)}},
                     {"type": "completed", "actor": priya.id, "when": _today_at(9, 12)},
                 ])

        # Missed (one-time miss yesterday)
        add_task(daily, "Lock back gate", assignee=anil, due=yesterday, status="missed",
                 events=[
                     {"type": "assigned", "actor": kc.id, "payload": {"to": str(anil.id)}},
                     {"type": "missed", "actor": None, "when": yesterday + timedelta(hours=6)},
                 ])

        # Private board tasks (only KC + Priya can see)
        add_task(admissions, "Call shortlisted parents", assignee=priya, due=_today_at(11, 0),
                 events=[{"type": "assigned", "actor": kc.id, "payload": {"to": str(priya.id)}}])
        add_task(admissions, "Verify documents", assignee=priya, due=_today_at(15, 0),
                 status="done", completed_by=priya, completed_at=_today_at(13, 0),
                 events=[
                     {"type": "assigned", "actor": kc.id, "payload": {"to": str(priya.id)}},
                     {"type": "completed", "actor": priya.id, "when": _today_at(13, 0)},
                 ])

        db.commit()
        print(f"Seeded '{DEMO_ORG_NAME}': org={org.id}")
        print("  users=4 (admin KC, members Priya/Ramesh/Anil)")
        print("  boards=2 (Daily Ops public, Admissions private)")
        print(f"  instances={len(instances)}")
        print("  login: kc@demo.trackbit.app / demo1234")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
