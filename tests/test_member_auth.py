"""Members & auth redesign — username/password onboarding & login."""

import uuid

import pytest
from sqlalchemy.orm import Session as OrmSession

from app.core.exceptions import AuthError, ValidationError
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

    with pytest.raises(AuthError):  # already used
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


def test_update_profile_name(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)
    h = {"Authorization": f"Bearer {reg['access_token']}"}
    r = client.patch("/api/v1/auth/me", headers=h, json={"name": "Renamed Admin"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["name"] == "Renamed Admin"
    assert client.get("/api/v1/auth/me", headers=h).json()["user"]["name"] == "Renamed Admin"


def test_change_password_flow(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)
    h = {"Authorization": f"Bearer {reg['access_token']}"}

    bad = client.post("/api/v1/auth/change-password", headers=h,
                      json={"current_password": "wrongpass1", "new_password": "brandnew123"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "bad_password"

    ok = client.post("/api/v1/auth/change-password", headers=h,
                     json={"current_password": "ownerpass1", "new_password": "brandnew123"})
    assert ok.status_code == 200, ok.text

    # Old password is dead, new one works.
    assert client.post("/api/v1/auth/login",
                       json={"identifier": unique_email, "password": "ownerpass1"}).status_code == 401
    assert client.post("/api/v1/auth/login",
                       json={"identifier": unique_email, "password": "brandnew123"}).status_code == 200


def test_set_password_can_set_name(client, cleanup):
    """First login lets username staff replace the placeholder name."""
    from app.core.database import SessionLocal

    uname = f"setname{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    try:
        org, user = _make_user_in_org(db, username=uname, password="temppass1")
        user.must_set_password = True
        user.name = uname  # placeholder, as nameless bulk-create leaves it
        db.commit()
        cleanup["orgs"].append(org.id)
        cleanup["users"].append(user.id)
    finally:
        db.close()

    token = client.post("/api/v1/auth/login",
                        json={"identifier": uname, "password": "temppass1"}).json()["access_token"]
    sp = client.post("/api/v1/auth/set-password", headers={"Authorization": f"Bearer {token}"},
                     json={"password": "myownpass1", "name": "Ravi Kumar"})
    assert sp.status_code == 200, sp.text
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["user"]["name"] == "Ravi Kumar"
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


def test_bulk_create_without_name_defaults_to_username(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    uname = f"noname{uuid.uuid4().hex[:6]}"
    resp = client.post("/api/v1/org/members/bulk", headers=h,
                       json={"members": [{"username": uname, "password": "temppass1"}]})  # no name
    assert resp.status_code == 200, resp.text
    row = resp.json()["results"][0]
    assert row["ok"]
    cleanup["users"].append(uuid.UUID(row["user_id"]))
    # Until they set their own on first login, the display name is the username.
    members = client.get("/api/v1/org/members", headers=h).json()["members"]
    created = next(m for m in members if m["username"] == uname)
    assert created["name"] == uname


def test_username_availability_check(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    taken = f"taken{uuid.uuid4().hex[:6]}"
    bulk = client.post("/api/v1/org/members/bulk", headers=h,
                       json={"members": [{"username": taken, "password": "temppass1"}]})
    cleanup["users"].append(uuid.UUID(bulk.json()["results"][0]["user_id"]))

    taken_resp = client.get(f"/api/v1/org/members/username-available?username={taken}", headers=h)
    assert taken_resp.status_code == 200, taken_resp.text
    assert taken_resp.json()["available"] is False
    assert taken_resp.json()["error"] == "username_taken"

    free_resp = client.get(
        f"/api/v1/org/members/username-available?username=free{uuid.uuid4().hex[:6]}", headers=h)
    assert free_resp.status_code == 200 and free_resp.json()["available"] is True

    bad_resp = client.get("/api/v1/org/members/username-available?username=ab", headers=h)  # too short
    assert bad_resp.status_code == 200 and bad_resp.json()["available"] is False
    assert bad_resp.json()["error"] == "invalid_username"


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


def test_invite_brand_new_returns_link_and_pending(client, unique_email, cleanup):
    h = _admin_headers(client, unique_email, cleanup)
    invitee = f"fresh-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post("/api/v1/org/members/invite", headers=h,
                       json={"name": "Fresh", "email": invitee, "role": "member"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cleanup["users"].append(uuid.UUID(body["user_id"]))
    assert "/join/" in body["invite_url"]   # a shareable link, not just "sent"
    assert body["pending"] is True

    # Re-inviting the same active member is a clear conflict, not a silent re-add.
    dup = client.post("/api/v1/org/members/invite", headers=h,
                      json={"name": "Fresh", "email": invitee, "role": "member"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_member"


def test_invite_email_registered_to_another_org_joins(client, unique_email, cleanup):
    # TrackBit is multi-org: inviting an email that already has an account adds that
    # account to THIS org as a new membership (it must not error). The person can
    # then switch into the org.
    other_email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    a = _register_admin(client, other_email, cleanup)  # org A + user(other_email)

    h = _admin_headers(client, unique_email, cleanup)  # org B admin
    resp = client.post("/api/v1/org/members/invite", headers=h,
                       json={"name": "Taken", "email": other_email, "role": "member"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == a["user"]["id"]  # same global account, no new user

    # They now appear as a member of org B,
    members = client.get("/api/v1/org/members", headers=h).json()["members"]
    assert any(m["email"] == other_email for m in members)

    # and logging in as them lists BOTH orgs in the switcher payload.
    sess = client.post("/api/v1/auth/login",
                       json={"identifier": other_email, "password": "ownerpass1"})
    assert sess.status_code == 200, sess.text
    org_ids = {o["id"] for o in sess.json()["orgs"]}
    assert a["org"]["id"] in org_ids and len(org_ids) >= 2


# --------------------------------------------------------------------------
# Multi-tenancy: create org, switch org, default-org-at-login
# --------------------------------------------------------------------------
def test_me_includes_orgs_list(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)
    h = {"Authorization": f"Bearer {reg['access_token']}"}
    # register response and /me both carry the switcher list.
    assert reg["orgs"][0]["id"] == reg["org"]["id"]
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert len(me["orgs"]) == 1
    assert me["orgs"][0]["id"] == reg["org"]["id"]
    assert me["orgs"][0]["org_role"] == "admin"


def test_create_org_and_switch(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)  # org A
    h = {"Authorization": f"Bearer {reg['access_token']}"}
    org_a_id = reg["org"]["id"]

    # Create a second org while signed in -> the session switches into it.
    created = client.post("/api/v1/auth/orgs", headers=h, json={"org_name": "Second Org"})
    assert created.status_code == 200, created.text
    body = created.json()
    cleanup["orgs"].append(uuid.UUID(body["org"]["id"]))
    org_b_id = body["org"]["id"]
    assert org_b_id != org_a_id
    assert body["org"]["name"] == "Second Org" and body["org_role"] == "admin"
    assert {o["id"] for o in body["orgs"]} == {org_a_id, org_b_id}

    # The returned token is scoped to org B.
    hb = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=hb).json()["org"]["id"] == org_b_id

    # Switch back to org A.
    switched = client.post("/api/v1/auth/switch-org", headers=hb, json={"org_id": org_a_id})
    assert switched.status_code == 200, switched.text
    assert switched.json()["org"]["id"] == org_a_id
    ha = {"Authorization": f"Bearer {switched.json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=ha).json()["org"]["id"] == org_a_id


def test_switch_to_non_member_org_rejected(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)  # our user (org A)
    stranger = _register_admin(client, f"x-{uuid.uuid4().hex[:8]}@example.com", cleanup)  # org C
    h = {"Authorization": f"Bearer {reg['access_token']}"}
    resp = client.post("/api/v1/auth/switch-org", headers=h,
                       json={"org_id": stranger["org"]["id"]})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "not_member"


def test_login_lands_in_most_recently_active_org(client, unique_email, cleanup):
    reg = _register_admin(client, unique_email, cleanup)  # org A
    h = {"Authorization": f"Bearer {reg['access_token']}"}
    org_a_id = reg["org"]["id"]
    created = client.post("/api/v1/auth/orgs", headers=h, json={"org_name": "MRA Second"})
    org_b_id = created.json()["org"]["id"]
    cleanup["orgs"].append(uuid.UUID(org_b_id))
    # Touch org A last (switch into it) so it becomes most-recently-active.
    hb = {"Authorization": f"Bearer {created.json()['access_token']}"}
    client.post("/api/v1/auth/switch-org", headers=hb, json={"org_id": org_a_id})
    # A fresh login lands in org A.
    sess = client.post("/api/v1/auth/login",
                       json={"identifier": unique_email, "password": "ownerpass1"})
    assert sess.status_code == 200, sess.text
    assert sess.json()["org"]["id"] == org_a_id


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
