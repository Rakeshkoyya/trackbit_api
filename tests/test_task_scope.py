"""Per-board task privacy (task_scope='assigned').

The member request: on a privacy board, a regular member sees ONLY tasks
assigned to them — on the board table, the flat list, single-task detail, and
Home — gets no board report, can't claim, and when they add a task it lands on
themselves. The owner/admins still see everything.
"""

import uuid

import pytest

from app.models import Organization
from tests.conftest import AdminSession


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(client, unique_email, cleanup):
    """Admin (=board owner) + two members (bob, cara) in one org, on Pro so plan
    caps don't interfere. Returns tokens/ids; the board is created per-test."""
    reg = client.post(
        "/api/v1/auth/register-org",
        json={"org_name": "Priv Co", "name": "Ada Admin", "email": unique_email,
              "password": "supersecret1", "timezone": "Asia/Kolkata"},
    ).json()
    cleanup["orgs"].append(uuid.UUID(reg["org"]["id"]))
    cleanup["users"].append(uuid.UUID(reg["user"]["id"]))

    db = AdminSession()
    try:
        org = db.get(Organization, uuid.UUID(reg["org"]["id"]))
        org.plan = "pro"
        db.commit()
    finally:
        db.close()

    admin_token = reg["access_token"]

    def invite_join(name):
        inv = client.post(
            "/api/v1/org/members/invite", headers=_auth(admin_token),
            json={"name": name, "email": f"{name.lower()}-{uuid.uuid4().hex[:6]}@ex.com",
                  "role": "member"},
        ).json()
        cleanup["users"].append(uuid.UUID(inv["user_id"]))
        token = inv["invite_url"].rsplit("/join/", 1)[1]
        sess = client.post("/api/v1/auth/verify", json={"token": token}).json()
        return inv["user_id"], sess["access_token"]

    bob_id, bob_token = invite_join("Bob")
    cara_id, cara_token = invite_join("Cara")
    return {
        "client": client,
        "admin_token": admin_token, "admin_id": reg["user"]["id"],
        "bob_id": bob_id, "bob_token": bob_token,
        "cara_id": cara_id, "cara_token": cara_token,
    }


def _make_board(w, scope: str) -> str:
    r = w["client"].post(
        "/api/v1/boards", headers=_auth(w["admin_token"]),
        json={"name": f"B-{scope}", "visibility": "public", "task_scope": scope},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_scope"] == scope
    return body["id"]


def _new_task(w, token, board_id, title, assignee_id=None):
    payload = {"board_id": board_id, "title": title}
    if assignee_id is not None:
        payload["assignee_id"] = assignee_id
    return w["client"].post("/api/v1/tasks", headers=_auth(token), json=payload)


# ---- visibility on an 'assigned' board --------------------------------------

def test_member_sees_only_own_tasks_in_table_and_list(world):
    w = world
    board = _make_board(w, "assigned")
    _new_task(w, w["admin_token"], board, "bob-task", w["bob_id"])
    _new_task(w, w["admin_token"], board, "cara-task", w["cara_id"])

    # Bob's table: only his task; the seeded/unassigned + cara's are hidden.
    rows = w["client"].get(
        f"/api/v1/boards/{board}/table", headers=_auth(w["bob_token"])
    ).json()["rows"]
    titles = {r["title"] for r in rows}
    assert "bob-task" in titles
    assert "cara-task" not in titles
    assert all(r["assignee"] and r["assignee"]["id"] == w["bob_id"] for r in rows)

    # Flat list endpoint enforces the same filter.
    flat = w["client"].get(
        f"/api/v1/boards/{board}/tasks", headers=_auth(w["bob_token"])
    ).json()
    assert {t["title"] for t in flat} == {"bob-task"}

    # Owner sees everything (both, plus the two seeded starter tasks).
    owner_rows = w["client"].get(
        f"/api/v1/boards/{board}/table", headers=_auth(w["admin_token"])
    ).json()["rows"]
    owner_titles = {r["title"] for r in owner_rows}
    assert {"bob-task", "cara-task"} <= owner_titles


def test_member_cannot_open_someone_elses_task(world):
    w = world
    board = _make_board(w, "assigned")
    cara_t = _new_task(w, w["admin_token"], board, "cara-task", w["cara_id"]).json()
    bob_t = _new_task(w, w["admin_token"], board, "bob-task", w["bob_id"]).json()

    # Direct fetch of Cara's task by id → 404 for Bob (no leak), 200 for his own.
    assert w["client"].get(
        f"/api/v1/tasks/{cara_t['id']}", headers=_auth(w["bob_token"])
    ).status_code == 404
    assert w["client"].get(
        f"/api/v1/tasks/{bob_t['id']}", headers=_auth(w["bob_token"])
    ).status_code == 200
    # Owner can open either.
    assert w["client"].get(
        f"/api/v1/tasks/{cara_t['id']}", headers=_auth(w["admin_token"])
    ).status_code == 200


def test_member_gets_no_board_report_owner_does(world):
    w = world
    board = _make_board(w, "assigned")
    assert w["client"].get(
        f"/api/v1/boards/{board}/report", headers=_auth(w["bob_token"])
    ).status_code == 404
    assert w["client"].get(
        f"/api/v1/boards/{board}/report", headers=_auth(w["admin_token"])
    ).status_code == 200


# ---- creating / assigning on an 'assigned' board ----------------------------

def test_member_create_auto_assigns_to_self(world):
    w = world
    board = _make_board(w, "assigned")
    r = _new_task(w, w["bob_token"], board, "bob-made-it")
    assert r.status_code == 200, r.text
    assert r.json()["assignee"]["id"] == w["bob_id"]
    # And it shows up in Bob's own view.
    titles = {
        row["title"]
        for row in w["client"].get(
            f"/api/v1/boards/{board}/table", headers=_auth(w["bob_token"])
        ).json()["rows"]
    }
    assert "bob-made-it" in titles


def test_member_cannot_assign_task_to_others_on_create(world):
    w = world
    board = _make_board(w, "assigned")
    r = _new_task(w, w["bob_token"], board, "for-cara", w["cara_id"])
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "self_assign_only"


def test_member_cannot_reassign_on_privacy_board(world):
    w = world
    board = _make_board(w, "assigned")
    bob_t = _new_task(w, w["admin_token"], board, "bob-task", w["bob_id"]).json()
    r = w["client"].post(
        f"/api/v1/tasks/{bob_t['id']}/reassign", headers=_auth(w["bob_token"]),
        json={"to_user_id": w["cara_id"]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "assign_restricted"


def test_claim_is_disabled_on_privacy_board(world):
    w = world
    board = _make_board(w, "assigned")
    # Owner makes an unassigned task; Bob can't even see it, and claim is off.
    unassigned = _new_task(w, w["admin_token"], board, "floating").json()
    r = w["client"].post(
        f"/api/v1/tasks/{unassigned['id']}/claim", headers=_auth(w["bob_token"])
    )
    assert r.status_code in (403, 404)  # 404 if hidden, 403 if reached
    # Owner attempting claim hits the explicit disable.
    r2 = w["client"].post(
        f"/api/v1/tasks/{unassigned['id']}/claim", headers=_auth(w["admin_token"])
    )
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "claim_disabled"


def test_home_excludes_privacy_board_from_claimable(world):
    w = world
    board = _make_board(w, "assigned")
    _new_task(w, w["admin_token"], board, "floating")  # unassigned
    home = w["client"].get("/api/v1/me/today", headers=_auth(w["bob_token"]))
    # Whatever the route, claimable must not include the privacy board's task.
    assert home.status_code == 200, home.text
    claimable_boards = {t["board_id"] for t in home.json().get("claimable", [])}
    assert board not in claimable_boards


# ---- recurring tasks on an 'assigned' board ---------------------------------

def _new_recurring(w, token, board_id, title, assignee_id=None):
    payload = {"board_id": board_id, "title": title, "recurrence": {"freq": "daily"}}
    if assignee_id is not None:
        payload["default_assignee_id"] = assignee_id
    return w["client"].post("/api/v1/recurring", headers=_auth(token), json=payload)


def test_member_recurring_self_assign_and_list_filtered(world):
    w = world
    board = _make_board(w, "assigned")
    # Admin schedules one for Cara; Bob schedules one (auto-self).
    _new_recurring(w, w["admin_token"], board, "cara-daily", w["cara_id"])
    mine = _new_recurring(w, w["bob_token"], board, "bob-daily")
    assert mine.status_code == 200, mine.text
    assert mine.json()["default_assignee"]["id"] == w["bob_id"]

    # Bob's recurring list: only his.
    listing = w["client"].get(
        f"/api/v1/recurring?board_id={board}", headers=_auth(w["bob_token"])
    ).json()
    assert {t["title"] for t in listing} == {"bob-daily"}

    # Bob can't schedule recurring work onto Cara.
    bad = _new_recurring(w, w["bob_token"], board, "for-cara", w["cara_id"])
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "self_assign_only"


def test_member_cannot_view_or_mutate_others_recurring(world):
    w = world
    board = _make_board(w, "assigned")
    cara_tpl = _new_recurring(w, w["admin_token"], board, "cara-daily", w["cara_id"]).json()
    # History of Cara's template → 404 for Bob, 200 for the owner.
    assert w["client"].get(
        f"/api/v1/recurring/{cara_tpl['id']}/history", headers=_auth(w["bob_token"])
    ).status_code == 404
    assert w["client"].get(
        f"/api/v1/recurring/{cara_tpl['id']}/history", headers=_auth(w["admin_token"])
    ).status_code == 200
    # Toggle / delete are owner-only on a privacy board.
    assert w["client"].post(
        f"/api/v1/recurring/{cara_tpl['id']}/toggle?active=false",
        headers=_auth(w["bob_token"]),
    ).status_code == 403
    assert w["client"].delete(
        f"/api/v1/recurring/{cara_tpl['id']}", headers=_auth(w["bob_token"])
    ).status_code == 403


# ---- 'all' board keeps the open model (control) -----------------------------

def test_all_scope_board_unchanged(world):
    w = world
    board = _make_board(w, "all")
    _new_task(w, w["admin_token"], board, "cara-task", w["cara_id"])
    # Bob sees Cara's task on an open board (legacy behavior).
    titles = {
        r["title"]
        for r in w["client"].get(
            f"/api/v1/boards/{board}/table", headers=_auth(w["bob_token"])
        ).json()["rows"]
    }
    assert "cara-task" in titles
    # And members can pull the report.
    assert w["client"].get(
        f"/api/v1/boards/{board}/report", headers=_auth(w["bob_token"])
    ).status_code == 200
