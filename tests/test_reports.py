"""Phase 3 — reporting, history, dashboard, nudge, report card.

The reconciliation tests insert task_instances + task_events directly (with
chosen timestamps) so completion timing, reopen, and cancel are deterministic,
then assert the API numbers fold the event chain correctly.
"""

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.timeutil import org_day_bounds, org_day_span
from app.models import Membership, Notification, Organization, TaskEvent, TaskInstance
from tests.conftest import AdminSession


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _rand_phone() -> str:
    return "+9198" + "".join(random.choices("0123456789", k=8))


@pytest.fixture
def org_ctx(client, unique_email, cleanup):
    reg = client.post(
        "/api/v1/auth/register-org",
        json={"org_name": "Ops Co", "name": "Adam Admin", "email": unique_email,
              "password": "supersecret1", "timezone": "Asia/Kolkata"},
    ).json()
    cleanup["orgs"].append(uuid.UUID(reg["org"]["id"]))
    cleanup["users"].append(uuid.UUID(reg["user"]["id"]))
    # Pre-billing reporting tests use several boards; run on Pro so plan caps
    # (Phase 4) don't interfere — limits are covered in test_phase4.
    _db = AdminSession()
    try:
        _org = _db.get(Organization, uuid.UUID(reg["org"]["id"]))
        _org.plan = "pro"
        _db.commit()
    finally:
        _db.close()
    admin_token = reg["access_token"]

    def invite_join(name):
        inv = client.post(
            "/api/v1/org/members/invite", headers=_auth(admin_token),
            json={"name": name, "phone": _rand_phone(), "role": "member"},
        ).json()
        cleanup["users"].append(uuid.UUID(inv["user_id"]))
        token = inv["invite_url"].rsplit("/join/", 1)[1]
        sess = client.post("/api/v1/auth/verify", json={"token": token}).json()
        return inv["user_id"], sess["access_token"]

    bob_id, bob_token = invite_join("Bob")
    cara_id, cara_token = invite_join("Cara")
    board = client.post(
        "/api/v1/boards", headers=_auth(admin_token),
        json={"name": "Ops", "visibility": "public"},
    ).json()
    return {
        "admin_token": admin_token, "admin_id": reg["user"]["id"], "org_id": reg["org"]["id"],
        "bob_id": bob_id, "bob_token": bob_token,
        "cara_id": cara_id, "cara_token": cara_token,
        "board_id": board["id"],
    }


def _insert_task(db, *, org_id, board_id, creator_id, title, due_at,
                 assignee_id=None, status="open", completed_at=None,
                 completed_by=None, pass_count=0, events=()):
    inst = TaskInstance(
        org_id=uuid.UUID(org_id), board_id=uuid.UUID(board_id), title=title,
        assignee_id=uuid.UUID(assignee_id) if assignee_id else None,
        due_at=due_at, status=status, completed_at=completed_at,
        completed_by=uuid.UUID(completed_by) if completed_by else None,
        pass_count=pass_count, created_by=uuid.UUID(creator_id),
    )
    db.add(inst)
    db.flush()
    for etype, actor, at, payload in events:
        db.add(TaskEvent(
            org_id=uuid.UUID(org_id), instance_id=inst.id,
            actor_id=uuid.UUID(actor) if actor else None,
            event_type=etype, created_at=at, payload=payload,
        ))
        db.flush()
    return inst.id


# ---- P3-BE-01: board report reconciliation ----------------------------
def test_board_report_reconciles_with_reopen_and_cancel(client, org_ctx):
    now = datetime.now(UTC)
    org, board = org_ctx["org_id"], org_ctx["board_id"]
    admin, bob, cara = org_ctx["admin_id"], org_ctx["bob_id"], org_ctx["cara_id"]

    db = AdminSession()
    try:
        # A: due -1h, completed -2h (before due) -> done, on-time. assignee bob.
        _insert_task(db, org_id=org, board_id=board, creator_id=admin, title="A on-time",
                     due_at=now - timedelta(hours=1), assignee_id=bob, status="done",
                     completed_at=now - timedelta(hours=2), completed_by=bob,
                     events=[("created", admin, now - timedelta(hours=3), None),
                             ("completed", bob, now - timedelta(hours=2), None)])
        # B: due -2h, completed -1h (after due) -> done, late. assignee bob.
        _insert_task(db, org_id=org, board_id=board, creator_id=admin, title="B late",
                     due_at=now - timedelta(hours=2), assignee_id=bob, status="done",
                     completed_at=now - timedelta(hours=1), completed_by=bob,
                     events=[("created", admin, now - timedelta(hours=3), None),
                             ("completed", bob, now - timedelta(hours=1), None)])
        # C: due -1h, open -> overdue. assignee bob.
        _insert_task(db, org_id=org, board_id=board, creator_id=admin, title="C open",
                     due_at=now - timedelta(hours=1), assignee_id=bob, status="open",
                     events=[("created", admin, now - timedelta(hours=3), None)])
        # D: due -1h, open -> overdue. assignee cara.
        _insert_task(db, org_id=org, board_id=board, creator_id=admin, title="D open",
                     due_at=now - timedelta(hours=1), assignee_id=cara, status="open",
                     events=[("created", admin, now - timedelta(hours=3), None)])
        # E: completed then REOPENED -> net open -> overdue, not done. assignee bob.
        _insert_task(db, org_id=org, board_id=board, creator_id=admin, title="E reopened",
                     due_at=now - timedelta(hours=1), assignee_id=bob, status="open",
                     events=[("created", admin, now - timedelta(hours=3), None),
                             ("completed", bob, now - timedelta(hours=3), None),
                             ("reopened", bob, now - timedelta(hours=2), None)])
        # F: CANCELLED -> excluded from everything.
        _insert_task(db, org_id=org, board_id=board, creator_id=admin, title="F cancelled",
                     due_at=now - timedelta(hours=1), assignee_id=bob, status="cancelled",
                     events=[("created", admin, now - timedelta(hours=3), None),
                             ("cancelled", admin, now - timedelta(hours=2), None)])
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/v1/boards/{board}/report?range=week",
                   headers=_auth(org_ctx["admin_token"])).json()

    assert r["total"] == 5          # A,B,C,D,E (F excluded)
    assert r["done"] == 2           # A,B
    assert r["completion_pct"] == 40
    assert r["on_time"] == 1        # A only (B was late)
    assert r["on_time_pct"] == 50   # of the 2 done
    assert r["overdue"] == 3        # C,D,E (E reopened back to open)

    bars = {b["name"]: b for b in r["members"]}
    assert bars["Bob"]["total"] == 4 and bars["Bob"]["done"] == 2 and bars["Bob"]["on_time"] == 1
    assert bars["Cara"]["total"] == 1 and bars["Cara"]["done"] == 0

    assert sum(p["done"] for p in r["trend"]) == 2  # A,B; E's completion was undone
    assert len(r["trend"]) == 14


def test_board_report_visible_to_member_404_for_non_viewer(client, org_ctx):
    priv = client.post("/api/v1/boards", headers=_auth(org_ctx["admin_token"]),
                       json={"name": "Secret", "visibility": "private"}).json()
    # Bob is not on the private board -> report is 404 (no existence leak).
    assert client.get(f"/api/v1/boards/{priv['id']}/report",
                      headers=_auth(org_ctx["bob_token"])).status_code == 404


# ---- P3-BE-04: history dot calendar -----------------------------------
def test_history_dot_states_and_run(client, org_ctx):
    org, board, bob = org_ctx["org_id"], org_ctx["board_id"], org_ctx["bob_id"]
    start, _, dates = org_day_span("Asia/Kolkata", 70)
    # noon org-local on a given offset-day, as UTC
    def at(day_offset, *, done):
        d = dates[-1 - day_offset]
        # 06:30 UTC == 12:00 IST -> safely inside that org-local day
        due = datetime(d.year, d.month, d.day, 6, 30, tzinfo=UTC)
        return due

    db = AdminSession()
    try:
        # today: done -> all ; yesterday: done -> all ; 2d ago: none ;
        # 3d ago: done -> all ; 4d ago: NOT done -> partial
        for off in (0, 1, 3):
            due = at(off, done=True)
            _insert_task(db, org_id=org, board_id=board, creator_id=bob, title=f"done-{off}",
                         due_at=due, assignee_id=bob, status="done",
                         completed_at=due, completed_by=bob,
                         events=[("created", bob, due, None), ("completed", bob, due, None)])
        due4 = at(4, done=False)
        _insert_task(db, org_id=org, board_id=board, creator_id=bob, title="open-4",
                     due_at=due4, assignee_id=bob, status="open",
                     events=[("created", bob, due4, None)])
        db.commit()
    finally:
        db.close()

    h = client.get("/api/v1/me/history", headers=_auth(org_ctx["bob_token"])).json()
    by_date = {d["date"]: d for d in h["dots"]}
    assert by_date[dates[-1].isoformat()]["state"] == "all"
    assert by_date[dates[-2].isoformat()]["state"] == "all"
    assert by_date[dates[-3].isoformat()]["state"] == "none"
    assert by_date[dates[-4].isoformat()]["state"] == "all"
    assert by_date[dates[-5].isoformat()]["state"] == "partial"
    # run skips the quiet day (2d ago), counts today+yesterday+3d-ago, stops at partial.
    assert h["current_run"] == 3
    assert h["total_completed"] == 3
    assert h["personal_best"] >= 1
    assert len(h["dots"]) == 70


# ---- P3-BE-02: org dashboard excludes private boards ------------------
def test_org_dashboard_excludes_private_and_requires_admin(client, org_ctx):
    now = datetime.now(UTC)
    org, pub_board, admin = org_ctx["org_id"], org_ctx["board_id"], org_ctx["admin_id"]
    priv = client.post("/api/v1/boards", headers=_auth(org_ctx["admin_token"]),
                       json={"name": "Secret", "visibility": "private"}).json()

    db = AdminSession()
    try:
        _insert_task(db, org_id=org, board_id=pub_board, creator_id=admin, title="public-overdue",
                     due_at=now - timedelta(hours=1), assignee_id=admin, status="open",
                     events=[("created", admin, now - timedelta(hours=2), None)])
        _insert_task(db, org_id=org, board_id=priv["id"], creator_id=admin, title="private-overdue",
                     due_at=now - timedelta(hours=1), assignee_id=admin, status="open",
                     events=[("created", admin, now - timedelta(hours=2), None)])
        db.commit()
    finally:
        db.close()

    d = client.get("/api/v1/org/dashboard?range=week",
                   headers=_auth(org_ctx["admin_token"])).json()
    assert d["total"] == 1           # only the public task; private never counts
    assert d["overdue"] == 1
    assert "Secret" not in [b["name"] for b in d["boards"]]

    # non-admin is forbidden.
    assert client.get("/api/v1/org/dashboard",
                      headers=_auth(org_ctx["bob_token"])).status_code == 403


# ---- P3-BE-03: manual nudge -------------------------------------------
def test_nudge_dedupes_within_window(client, org_ctx):
    now = datetime.now(UTC)
    # Give Bob an overdue task.
    client.post("/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
                json={"board_id": org_ctx["board_id"], "title": "Bob overdue",
                      "assignee_id": org_ctx["bob_id"],
                      "due_at": (now - timedelta(hours=1)).isoformat()})

    first = client.post(f"/api/v1/org/nudge/{org_ctx['bob_id']}",
                        headers=_auth(org_ctx["admin_token"])).json()
    assert first["sent"] is True and first["overdue_count"] >= 1

    second = client.post(f"/api/v1/org/nudge/{org_ctx['bob_id']}",
                         headers=_auth(org_ctx["admin_token"])).json()
    assert second["sent"] is False and second["reason"] == "recently_nudged"

    # Cara has nothing overdue -> nothing to send.
    cara = client.post(f"/api/v1/org/nudge/{org_ctx['cara_id']}",
                       headers=_auth(org_ctx["admin_token"])).json()
    assert cara["sent"] is False and cara["reason"] == "nothing_overdue"

    # Members can't nudge.
    assert client.post(f"/api/v1/org/nudge/{org_ctx['cara_id']}",
                       headers=_auth(org_ctx["bob_token"])).status_code == 403


# ---- P3-BE-03: report card job ----------------------------------------
def test_report_card_job_queues_for_admin(client, org_ctx):
    from app.core.timeutil import org_now
    from app.services import jobs

    org_id = org_ctx["org_id"]
    # Due inside today's org-local window so the wrap-up always has real numbers,
    # regardless of wall-clock time. (The org is Asia/Kolkata; a naive "now - 1h"
    # lands in yesterday's org-day when this runs just after local midnight.)
    day_start, _, _ = org_day_bounds("Asia/Kolkata")
    due = day_start + timedelta(hours=12)
    client.post("/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
                json={"board_id": org_ctx["board_id"], "title": "Wrap me",
                      "assignee_id": org_ctx["admin_id"],
                      "due_at": due.isoformat()})

    db = AdminSession()
    try:
        org = db.get(Organization, uuid.UUID(org_id))
        org.report_card_hour = org_now(org.timezone).hour  # fire this hour
        db.commit()
    finally:
        db.close()

    jobs.run_report_card()  # opens its own session

    db = AdminSession()
    try:
        from sqlalchemy import select

        cards = list(db.scalars(
            select(Notification).where(
                Notification.org_id == uuid.UUID(org_id),
                Notification.notif_type == "report_card",
            )
        ))
        assert len(cards) >= 1
        body = cards[0].payload["body"]
        assert "Ops Co" in body and "%" in body
        # Only admins receive it.
        admin_ids = set(db.scalars(
            select(Membership.user_id).where(
                Membership.org_id == uuid.UUID(org_id),
                Membership.org_role == "admin",
            )
        ))
        assert all(c.user_id in admin_ids for c in cards)
    finally:
        db.close()
