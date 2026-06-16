"""Phase 4 attachments (P4-BE-02). Local-disk storage fallback; Pro-gated."""

import base64
import uuid

import pytest

from app.models import Organization
from tests.conftest import AdminSession

# 1x1 transparent PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _set_plan(org_id: str, plan: str) -> None:
    db = AdminSession()
    try:
        org = db.get(Organization, uuid.UUID(org_id))
        org.plan = plan
        db.commit()
    finally:
        db.close()


@pytest.fixture
def task_ctx(client, unique_email, cleanup):
    reg = client.post(
        "/api/v1/auth/register-org",
        json={"org_name": "Att Co", "name": "Adam Admin", "email": unique_email,
              "password": "supersecret1", "timezone": "Asia/Kolkata"},
    ).json()
    cleanup["orgs"].append(uuid.UUID(reg["org"]["id"]))
    cleanup["users"].append(uuid.UUID(reg["user"]["id"]))
    token = reg["access_token"]
    board = client.post("/api/v1/boards", headers=_auth(token), json={"name": "Ops"}).json()
    task = client.post(
        "/api/v1/tasks", headers=_auth(token),
        json={"board_id": board["id"], "title": "Do the thing"},
    ).json()
    return {"token": token, "org_id": reg["org"]["id"], "task_id": task["id"]}


def test_attachments_blocked_on_free(client, task_ctx):
    r = client.post(
        f"/api/v1/tasks/{task_ctx['task_id']}/notes", headers=_auth(task_ctx["token"]),
        json={"content": "A note"},
    )
    assert r.status_code == 402
    assert r.json()["error"]["details"]["feature"] == "attachments"


def test_note_and_photo_appear_in_history(client, task_ctx):
    _set_plan(task_ctx["org_id"], "pro")
    tid, hdr = task_ctx["task_id"], _auth(task_ctx["token"])

    note = client.post(f"/api/v1/tasks/{tid}/notes", headers=hdr, json={"content": "Looks good"})
    assert note.status_code == 200 and note.json()["content"] == "Looks good"

    photo = client.post(
        f"/api/v1/tasks/{tid}/photos", headers=hdr,
        files={"file": ("proof.png", _PNG, "image/png")},
    )
    assert photo.status_code == 200
    pj = photo.json()
    assert pj["kind"] == "photo" and pj["file_url"] and "/media/" in pj["file_url"]

    # Both surface in the attachments list...
    atts = client.get(f"/api/v1/tasks/{tid}/attachments", headers=hdr).json()
    kinds = sorted(a["kind"] for a in atts)
    assert kinds == ["note", "photo"]

    # ...and in the task event history.
    detail = client.get(f"/api/v1/tasks/{tid}", headers=hdr).json()
    types = [e["type"] for e in detail["events"]]
    assert "commented" in types and "attached" in types


def test_photo_rejects_non_image(client, task_ctx):
    _set_plan(task_ctx["org_id"], "pro")
    r = client.post(
        f"/api/v1/tasks/{task_ctx['task_id']}/photos", headers=_auth(task_ctx["token"]),
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "bad_image_type"
