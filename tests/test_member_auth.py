"""Members & auth redesign — username/password onboarding & login."""

import uuid

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.core.exceptions import ValidationError
from app.core.security import hash_password
from app.core.validators import normalize_username
from app.models import Membership, Organization, User
from app.services.tokens import TokenService


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _make_user_in_org(db: OrmSession, *, email=None, username=None, password=None, role="member"):
    org = Organization(name="MA Org")
    db.add(org)
    db.flush()
    user = User(
        name="Tok User", email=email, username=username,
        password_hash=hash_password(password) if password else None,
    )
    db.add(user)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, org_role=role, status="active"))
    db.flush()
    return org, user


def _register_admin(client, email, cleanup):
    reg = client.post("/api/v1/auth/register-org", json={
        "org_name": "MA Admin Org", "name": "Admin", "email": email,
        "password": "ownerpass1", "timezone": "Asia/Kolkata"})
    assert reg.status_code == 200, reg.text
    cleanup["orgs"].append(uuid.UUID(reg.json()["org"]["id"]))
    cleanup["users"].append(uuid.UUID(reg.json()["user"]["id"]))
    return reg.json()


def _admin_headers(client, unique_email, cleanup):
    return {"Authorization": f"Bearer {_register_admin(client, unique_email, cleanup)['access_token']}"}


# --------------------------------------------------------------------------
# Username validator
# --------------------------------------------------------------------------
def test_normalize_username_lowercases_and_trims():
    assert normalize_username("  Ravi_Kumar ") == "ravi_kumar"


@pytest.mark.parametrize("bad", ["ab", "has space", "a" * 33, "no@at", "dot.", "UPPER!"])
def test_normalize_username_rejects_invalid(bad):
    with pytest.raises(ValidationError):
        normalize_username(bad)


def test_normalize_username_allows_dot_dash_underscore():
    assert normalize_username("a.b-c_d") == "a.b-c_d"


# --------------------------------------------------------------------------
# Password-reset tokens
# --------------------------------------------------------------------------
def test_password_reset_token_single_use(db_session, cleanup):
    email = f"reset-{uuid.uuid4().hex[:8]}@example.com"
    org, user = _make_user_in_org(db_session, email=email, password="oldpassword1")
    cleanup["orgs"].append(org.id)
    cleanup["users"].append(user.id)

    svc = TokenService(db_session)
    raw = svc.issue_password_reset(user.id)
    db_session.flush()

    u, o, m = svc.consume_reset_token(raw)
    assert u.id == user.id and o.id == org.id and m.org_role == "member"

    with pytest.raises(Exception):  # AuthError — already used
        svc.consume_reset_token(raw)


# --------------------------------------------------------------------------
# Login by identifier (email or username)
# --------------------------------------------------------------------------
def test_login_by_username(client, cleanup):
    from app.core.database import SessionLocal

    uname = f"ravi{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        org, user = _make_user_in_org(db, username=uname, password="staffpass1")
        db.commit()
        cleanup["orgs"].append(org.id)
        cleanup["users"].append(user.id)
    finally:
        db.close()

    resp = client.post("/api/v1/auth/login", json={"identifier": uname, "password": "staffpass1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["username"] == uname


def test_login_by_email_still_works(client, unique_email, cleanup):
    _register_admin(client, unique_email, cleanup)
    resp = client.post("/api/v1/auth/login", json={"identifier": unique_email, "password": "ownerpass1"})
    assert resp.status_code == 200, resp.text


def test_login_bad_credentials_generic(client):
    resp = client.post("/api/v1/auth/login", json={"identifier": "nope.nobody", "password": "whatever1"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bad_credentials"


# --------------------------------------------------------------------------
# set / forgot / reset password
# --------------------------------------------------------------------------
def test_forgot_password_never_leaks(client):
    resp = client.post("/api/v1/auth/forgot-password", json={"email": "ghost-xyz@example.com"})
    assert resp.status_code == 200


def test_reset_password_round_trip(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)
    uid = uuid.UUID(reg["user"]["id"])

    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        raw = TokenService(db).issue_password_reset(uid)
        db.commit()
    finally:
        db.close()

    resp = client.post("/api/v1/auth/reset-password", json={"token": raw, "password": "brandnew99"})
    assert resp.status_code == 200, resp.text
    assert client.post("/api/v1/auth/login",
                       json={"identifier": unique_email, "password": "brandnew99"}).status_code == 200
    assert client.post("/api/v1/auth/login",
                       json={"identifier": unique_email, "password": "ownerpass1"}).status_code == 401


def test_set_password_clears_flag(client, cleanup):
    from app.core.database import SessionLocal

    uname = f"needspw{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        org, user = _make_user_in_org(db, username=uname, password="temppass1")
        user.must_set_password = True
        db.commit()
        cleanup["orgs"].append(org.id)
        cleanup["users"].append(user.id)
    finally:
        db.close()

    sess = client.post("/api/v1/auth/login", json={"identifier": uname, "password": "temppass1"})
    assert sess.json()["must_set_password"] is True
    token = sess.json()["access_token"]
    sp = client.post("/api/v1/auth/set-password", headers={"Authorization": f"Bearer {token}"},
                     json={"password": "myownpass1"})
    assert sp.status_code == 200, sp.text
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["must_set_password"] is False


# --------------------------------------------------------------------------
# Member service: bulk create, pending, admin reset
# --------------------------------------------------------------------------
def test_bulk_create_members(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    suffix = uuid.uuid4().hex[:6]
    resp = client.post("/api/v1/org/members/bulk", headers=h, json={"members": [
        {"name": "Ravi", "username": f"ravi{suffix}", "password": "temppass1", "role": "member"},
        {"name": "Sita", "username": f"sita{suffix}", "password": "temppass2", "role": "admin"},
    ]})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["created"] == 2
    for r in data["results"]:
        assert r["ok"] and r["password"] is not None
        cleanup["users"].append(uuid.UUID(r["user_id"]))

    sess = client.post("/api/v1/auth/login", json={"identifier": f"ravi{suffix}", "password": "temppass1"})
    assert sess.status_code == 200 and sess.json()["must_set_password"] is True


def test_bulk_create_best_effort_on_dup(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    suffix = uuid.uuid4().hex[:6]
    first = client.post("/api/v1/org/members/bulk", headers=h, json={"members": [
        {"name": "Ravi", "username": f"dup{suffix}", "password": "temppass1"}]})
    cleanup["users"].append(uuid.UUID(first.json()["results"][0]["user_id"]))

    resp = client.post("/api/v1/org/members/bulk", headers=h, json={"members": [
        {"name": "Ravi2", "username": f"dup{suffix}", "password": "temppass1"},      # taken
        {"name": "New", "username": f"fresh{suffix}", "password": "temppass1"},      # ok
    ]})
    data = resp.json()
    assert data["created"] == 1
    assert data["results"][0]["ok"] is False and data["results"][0]["error"] == "username_taken"
    assert data["results"][1]["ok"] is True
    cleanup["users"].append(uuid.UUID(data["results"][1]["user_id"]))


def test_invite_email_user_is_pending(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    invitee = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    inv = client.post("/api/v1/org/members/invite", headers=h,
                      json={"name": "Newbie", "email": invitee, "role": "member", "mode": "email_invite"})
    assert inv.status_code == 200, inv.text
    cleanup["users"].append(uuid.UUID(inv.json()["user_id"]))
    members = client.get("/api/v1/org/members", headers=h).json()["members"]
    row = next(m for m in members if m["email"] == invitee)
    assert row["pending"] is True


def test_admin_reset_username_user(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    suffix = uuid.uuid4().hex[:6]
    bulk = client.post("/api/v1/org/members/bulk", headers=h, json={"members": [
        {"name": "Ravi", "username": f"reset{suffix}", "password": "temppass1"}]})
    uid = bulk.json()["results"][0]["user_id"]
    cleanup["users"].append(uuid.UUID(uid))
    r = client.post(f"/api/v1/org/members/{uid}/reset-password", headers=h, json={"password": "newtemp99"})
    assert r.status_code == 200 and r.json()["mode"] == "password_set"
    assert client.post("/api/v1/auth/login",
                       json={"identifier": f"reset{suffix}", "password": "newtemp99"}).status_code == 200
