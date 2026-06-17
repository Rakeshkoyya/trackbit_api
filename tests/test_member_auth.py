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
