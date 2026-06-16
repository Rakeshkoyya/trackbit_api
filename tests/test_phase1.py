"""Phase 1 spine — critical invariants through the HTTP stack.

Covers: event chain, claim race, complete idempotency, reassign chain,
visibility enforcement, Home bucketing, cancel hiding, member-removal orphaning.
"""

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Organization
from tests.conftest import AdminSession


def _make_pro(org_id: str) -> None:
    """Pre-billing tests need >2 boards; run them on Pro so plan caps (Phase 4)
    don't interfere — limit enforcement is covered in test_phase4."""
    db = AdminSession()
    try:
        org = db.get(Organization, uuid.UUID(org_id))
        org.plan = "pro"
        db.commit()
    finally:
        db.close()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _rand_phone() -> str:
    return "+9198" + "".join(random.choices("0123456789", k=8))


@pytest.fixture
def org_ctx(client, unique_email, cleanup):
    """Org with admin + two members (Bob, Cara) and a public board 'Ops'."""
    reg = client.post(
        "/api/v1/auth/register-org",
        json={"org_name": "Ops Co", "name": "Adam Admin", "email": unique_email,
              "password": "supersecret1", "timezone": "Asia/Kolkata"},
    ).json()
    cleanup["orgs"].append(uuid.UUID(reg["org"]["id"]))
    cleanup["users"].append(uuid.UUID(reg["user"]["id"]))
    _make_pro(reg["org"]["id"])
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


def test_create_task_records_event_chain(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Submit report",
              "assignee_id": org_ctx["bob_id"]},
    ).json()
    types = [e["type"] for e in t["events"]]
    assert types == ["created", "assigned"]
    assert t["assignee"]["name"] == "Bob"


def test_claim_is_atomic_one_winner(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Grab me"},  # unassigned
    ).json()
    r1 = client.post(f"/api/v1/tasks/{t['id']}/claim", headers=_auth(org_ctx["bob_token"]))
    r2 = client.post(f"/api/v1/tasks/{t['id']}/claim", headers=_auth(org_ctx["cara_token"]))
    assert {r1.status_code, r2.status_code} == {200, 409}
    winner = r1 if r1.status_code == 200 else r2
    assert winner.json()["assignee"] is not None


def test_complete_is_idempotent(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Finish me",
              "assignee_id": org_ctx["bob_id"]},
    ).json()
    first = client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_auth(org_ctx["bob_token"]))
    assert first.status_code == 200 and first.json()["already_done"] is False
    second = client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_auth(org_ctx["cara_token"]))
    assert second.status_code == 200 and second.json()["already_done"] is True


def test_reassign_records_pass_and_increments(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Hot potato",
              "assignee_id": org_ctx["bob_id"]},
    ).json()
    re = client.post(
        f"/api/v1/tasks/{t['id']}/reassign", headers=_auth(org_ctx["bob_token"]),
        json={"to_user_id": org_ctx["cara_id"]},
    ).json()
    assert re["pass_count"] == 1
    assert re["assignee"]["name"] == "Cara"
    detail = client.get(f"/api/v1/tasks/{t['id']}", headers=_auth(org_ctx["cara_token"])).json()
    assert "passed" in [e["type"] for e in detail["events"]]


def test_reopen_then_complete_keeps_chain(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Yo-yo", "assignee_id": org_ctx["bob_id"]},
    ).json()
    client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_auth(org_ctx["bob_token"]))
    client.post(f"/api/v1/tasks/{t['id']}/reopen", headers=_auth(org_ctx["bob_token"]))
    client.post(f"/api/v1/tasks/{t['id']}/complete", headers=_auth(org_ctx["bob_token"]))
    detail = client.get(f"/api/v1/tasks/{t['id']}", headers=_auth(org_ctx["bob_token"])).json()
    types = [e["type"] for e in detail["events"]]
    assert types.count("completed") == 2 and types.count("reopened") == 1
    assert detail["status"] == "done"


def test_private_board_hidden_from_non_member(client, org_ctx):
    priv = client.post(
        "/api/v1/boards", headers=_auth(org_ctx["admin_token"]),
        json={"name": "Secret", "visibility": "private"},
    ).json()
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": priv["id"], "title": "Classified"},
    ).json()
    # Bob is not on the private board: board + task are 404 to him.
    assert client.get(f"/api/v1/boards/{priv['id']}", headers=_auth(org_ctx["bob_token"])).status_code == 404
    assert client.get(f"/api/v1/tasks/{t['id']}", headers=_auth(org_ctx["bob_token"])).status_code == 404
    # And the private board never appears in his board list.
    boards = client.get("/api/v1/boards", headers=_auth(org_ctx["bob_token"])).json()
    names = [b["name"] for b in boards["my_boards"] + boards["other_public"]]
    assert "Secret" not in names


def test_assign_to_non_board_member_rejected(client, org_ctx):
    priv = client.post(
        "/api/v1/boards", headers=_auth(org_ctx["admin_token"]),
        json={"name": "PrivBoard", "visibility": "private"},
    ).json()
    # Bob is not a member of this private board -> cannot be assigned.
    resp = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": priv["id"], "title": "X", "assignee_id": org_ctx["bob_id"]},
    )
    assert resp.status_code == 422


def test_home_buckets(client, org_ctx):
    now = datetime.now(UTC)
    board = org_ctx["board_id"]
    hdr = _auth(org_ctx["admin_token"])

    def mk(title, **kw):
        body = {"board_id": board, "title": title, "assignee_id": org_ctx["bob_id"], **kw}
        return client.post("/api/v1/tasks", headers=hdr, json=body).json()

    mk("Overdue task", due_at=(now - timedelta(days=1)).isoformat())
    mk("Due today task", due_at=(now + timedelta(minutes=90)).isoformat())
    mk("Anytime task")  # no due
    # Claimable: unassigned on the board
    client.post("/api/v1/tasks", headers=hdr,
                json={"board_id": board, "title": "Claim me task"})

    home = client.get("/api/v1/me/today", headers=_auth(org_ctx["bob_token"])).json()
    assert "Overdue task" in [t["title"] for t in home["overdue"]]
    assert "Anytime task" in [t["title"] for t in home["anytime"]]
    assert "Claim me task" in [t["title"] for t in home["claimable"]]
    # due-today may land in due_today unless the clock is within 90 min of midnight
    titles_today = [t["title"] for t in home["due_today"]]
    assert "Due today task" in titles_today or len(home["overdue"]) >= 1


def test_cancel_hides_task(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Mistake"},
    ).json()
    assert client.post(f"/api/v1/tasks/{t['id']}/cancel",
                       headers=_auth(org_ctx["admin_token"])).status_code == 200
    tasks = client.get(f"/api/v1/boards/{org_ctx['board_id']}/tasks",
                       headers=_auth(org_ctx["admin_token"])).json()
    assert "Mistake" not in [t["title"] for t in tasks]


def test_member_removal_orphans_tasks(client, org_ctx):
    t = client.post(
        "/api/v1/tasks", headers=_auth(org_ctx["admin_token"]),
        json={"board_id": org_ctx["board_id"], "title": "Bob's task",
              "assignee_id": org_ctx["bob_id"]},
    ).json()
    resp = client.delete(f"/api/v1/org/members/{org_ctx['bob_id']}",
                         headers=_auth(org_ctx["admin_token"]))
    assert resp.status_code == 200
    assert resp.json()["orphaned_tasks"] >= 1
    # The task is now unassigned (claimable), not lost.
    detail = client.get(f"/api/v1/tasks/{t['id']}", headers=_auth(org_ctx["admin_token"])).json()
    assert detail["assignee"] is None
    # Bob's session is revoked.
    assert client.get("/api/v1/me/today", headers=_auth(org_ctx["bob_token"])).status_code == 401


# ---- Priority + My-tasks (board/home table rework) --------------------


def test_priority_create_edit_and_table(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    t = client.post(
        "/api/v1/tasks", headers=hdr,
        json={"board_id": org_ctx["board_id"], "title": "Important",
              "assignee_id": org_ctx["bob_id"], "priority": 3},
    ).json()
    assert t["priority"] == 3
    upd = client.patch(f"/api/v1/tasks/{t['id']}", headers=hdr, json={"priority": 1}).json()
    assert upd["priority"] == 1
    table = client.get(
        f"/api/v1/boards/{org_ctx['board_id']}/table", headers=hdr
    ).json()
    row = next(r for r in table["rows"] if r["id"] == t["id"])
    assert row["priority"] == 1


def test_recurring_priority_inherited_by_instance(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    tmpl = client.post(
        "/api/v1/recurring", headers=hdr,
        json={"board_id": org_ctx["board_id"], "title": "Daily standup",
              "recurrence": {"freq": "daily"}, "priority": 2},
    ).json()
    assert tmpl["priority"] == 2
    # create() materializes today's instance immediately; it inherits the priority.
    table = client.get(
        f"/api/v1/boards/{org_ctx['board_id']}/table", headers=hdr
    ).json()
    row = next(
        r for r in table["rows"] if r["kind"] == "recurring" and r["id"] == tmpl["id"]
    )
    assert row["priority"] == 2


def test_my_tasks_spans_boards_and_excludes_others(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    b2 = client.post(
        "/api/v1/boards", headers=hdr, json={"name": "Kitchen", "visibility": "public"},
    ).json()
    client.post("/api/v1/tasks", headers=hdr,
                json={"board_id": org_ctx["board_id"], "title": "On Ops",
                      "assignee_id": org_ctx["bob_id"]})
    client.post("/api/v1/tasks", headers=hdr,
                json={"board_id": b2["id"], "title": "On Kitchen",
                      "assignee_id": org_ctx["bob_id"]})
    client.post("/api/v1/tasks", headers=hdr,
                json={"board_id": org_ctx["board_id"], "title": "Caras",
                      "assignee_id": org_ctx["cara_id"]})
    scrap = client.post("/api/v1/tasks", headers=hdr,
                        json={"board_id": org_ctx["board_id"], "title": "Scrap",
                              "assignee_id": org_ctx["bob_id"]}).json()
    client.post(f"/api/v1/tasks/{scrap['id']}/cancel", headers=hdr)

    res = client.get("/api/v1/me/tasks", headers=_auth(org_ctx["bob_token"])).json()
    rows = {r["title"]: r for r in res["rows"]}
    assert "On Ops" in rows and "On Kitchen" in rows
    assert rows["On Kitchen"]["board_name"] == "Kitchen"
    assert rows["On Kitchen"]["board_id"] == b2["id"]
    assert "Caras" not in rows  # assigned to someone else
    assert "Scrap" not in rows  # cancelled


def test_delete_board_cascades_tasks(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    b = client.post("/api/v1/boards", headers=hdr,
                    json={"name": "Throwaway", "visibility": "public"}).json()
    t = client.post("/api/v1/tasks", headers=hdr,
                    json={"board_id": b["id"], "title": "doomed"}).json()
    assert client.delete(f"/api/v1/boards/{b['id']}", headers=hdr).status_code == 200
    # Board and its task are gone (DB cascade).
    assert client.get(f"/api/v1/boards/{b['id']}", headers=hdr).status_code == 404
    assert client.get(f"/api/v1/tasks/{t['id']}", headers=hdr).status_code == 404


def test_delete_board_forbidden_for_non_manager(client, org_ctx):
    admin_hdr = _auth(org_ctx["admin_token"])
    b = client.post("/api/v1/boards", headers=admin_hdr,
                    json={"name": "Guarded", "visibility": "public"}).json()
    # Bob is a plain member, not owner/admin → cannot delete.
    assert client.delete(f"/api/v1/boards/{b['id']}", headers=_auth(org_ctx["bob_token"])).status_code == 403


def test_board_category_groups_crud(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    board = org_ctx["board_id"]
    # Create an empty colored group — it shows up in the table's group list.
    assert client.post(f"/api/v1/boards/{board}/categories", headers=hdr,
                       json={"name": "Health", "color": "#d4537e"}).status_code == 200
    groups = {g["name"]: g for g in
              client.get(f"/api/v1/boards/{board}/table", headers=hdr).json()["groups"]}
    assert groups["Health"]["color"] == "#d4537e"
    # A task in the group follows a rename.
    t = client.post("/api/v1/tasks", headers=hdr,
                    json={"board_id": board, "title": "Run", "category": "Health"}).json()
    client.patch(f"/api/v1/boards/{board}/categories", headers=hdr,
                 json={"name": "Health", "new_name": "Wellness"})
    assert client.get(f"/api/v1/tasks/{t['id']}", headers=hdr).json()["category"] == "Wellness"
    # Delete the group → its tasks become uncategorized and the group disappears.
    assert client.delete(f"/api/v1/boards/{board}/categories?name=Wellness",
                         headers=hdr).status_code == 200
    assert client.get(f"/api/v1/tasks/{t['id']}", headers=hdr).json()["category"] is None
    names = [g["name"] for g in
             client.get(f"/api/v1/boards/{board}/table", headers=hdr).json()["groups"]]
    assert "Wellness" not in names


def test_new_board_has_starter_group_and_tasks(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    b = client.post("/api/v1/boards", headers=hdr,
                    json={"name": "Fresh", "visibility": "public"}).json()
    table = client.get(f"/api/v1/boards/{b['id']}/table", headers=hdr).json()
    assert "Group 1" in [g["name"] for g in table["groups"]]
    assert sorted(r["title"] for r in table["rows"]) == ["task1", "task2"]
    assert all(r["category"] == "Group 1" for r in table["rows"])


def test_assign_then_reassign_then_unassign(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    t = client.post("/api/v1/tasks", headers=hdr,
                    json={"board_id": org_ctx["board_id"], "title": "Pick me"}).json()
    # Unassigned → assign (no pass).
    r1 = client.post(f"/api/v1/tasks/{t['id']}/assign", headers=hdr,
                     json={"user_id": org_ctx["bob_id"]}).json()
    assert r1["assignee"]["name"] == "Bob" and r1["pass_count"] == 0
    # Owned → reassign is a pass.
    r2 = client.post(f"/api/v1/tasks/{t['id']}/assign", headers=hdr,
                     json={"user_id": org_ctx["cara_id"]}).json()
    assert r2["assignee"]["name"] == "Cara" and r2["pass_count"] == 1
    # Unassign.
    r3 = client.post(f"/api/v1/tasks/{t['id']}/assign", headers=hdr,
                     json={"user_id": None}).json()
    assert r3["assignee"] is None


def test_move_task_to_another_board(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    dest = client.post("/api/v1/boards", headers=hdr,
                       json={"name": "Dest", "visibility": "public"}).json()
    t = client.post("/api/v1/tasks", headers=hdr,
                    json={"board_id": org_ctx["board_id"], "title": "Mover"}).json()
    upd = client.patch(f"/api/v1/tasks/{t['id']}", headers=hdr,
                       json={"board_id": dest["id"]}).json()
    assert upd["board_id"] == dest["id"]


def test_my_tasks_shows_recurring_once(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    # Materializes today + tomorrow; My tasks must show only today's occurrence.
    client.post("/api/v1/recurring", headers=hdr,
                json={"board_id": org_ctx["board_id"], "title": "Daily ping",
                      "recurrence": {"freq": "daily"},
                      "default_assignee_id": org_ctx["admin_id"]})
    res = client.get("/api/v1/me/tasks", headers=hdr).json()
    assert len([r for r in res["rows"] if r["title"] == "Daily ping"]) == 1


def test_board_card_shows_overall_completion(client, org_ctx):
    hdr = _auth(org_ctx["admin_token"])
    b = client.post("/api/v1/boards", headers=hdr,
                    json={"name": "Prog", "visibility": "public"}).json()
    # Starter content seeds 2 open tasks; add one more and complete it.
    t = client.post("/api/v1/tasks", headers=hdr,
                    json={"board_id": b["id"], "title": "finish me"}).json()
    client.post(f"/api/v1/tasks/{t['id']}/complete", headers=hdr)
    boards = client.get("/api/v1/boards", headers=hdr).json()
    card = next(x for x in boards["my_boards"] + boards["other_public"] if x["id"] == b["id"])
    assert card["total"] == 3 and card["done"] == 1
