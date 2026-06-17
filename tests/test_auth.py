"""P0-BE-03 done-when: register -> login -> /me round-trip; refresh rotation."""

import uuid


def _register(client, email, cleanup):
    resp = client.post(
        "/api/v1/auth/register-org",
        json={
            "org_name": "Test Org",
            "name": "Owner",
            "email": email,
            "password": "supersecret1",
            "timezone": "Asia/Kolkata",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cleanup["orgs"].append(uuid.UUID(body["org"]["id"]))
    cleanup["users"].append(uuid.UUID(body["user"]["id"]))
    return body


def test_register_login_me_roundtrip(client, unique_email, cleanup):
    reg = _register(client, unique_email, cleanup)
    assert reg["org_role"] == "admin"
    assert reg["org"]["name"] == "Test Org"
    assert reg["access_token"] and reg["refresh_token"]

    # /me with the access token returns the same identity.
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {reg['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == unique_email
    assert me.json()["org_role"] == "admin"

    # login returns a fresh session for the same user.
    login = client.post(
        "/api/v1/auth/login", json={"identifier": unique_email, "password": "supersecret1"}
    )
    assert login.status_code == 200, login.text
    assert login.json()["user"]["id"] == reg["user"]["id"]


def test_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rejects_bad_password(client, unique_email, cleanup):
    _register(client, unique_email, cleanup)
    bad = client.post("/api/v1/auth/login", json={"identifier": unique_email, "password": "wrongwrong1"})
    assert bad.status_code == 401


def test_register_rejects_duplicate_email(client, unique_email, cleanup):
    _register(client, unique_email, cleanup)
    dup = client.post(
        "/api/v1/auth/register-org",
        json={
            "org_name": "Second Org",
            "name": "Other",
            "email": unique_email,
            "password": "supersecret1",
            "timezone": "Asia/Kolkata",
        },
    )
    assert dup.status_code == 409


def test_refresh_rotation_invalidates_old_token(client, unique_email, cleanup):
    reg = _register(client, unique_email, cleanup)
    old_refresh = reg["refresh_token"]

    # First refresh succeeds and returns a new pair.
    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200, r1.text
    new_refresh = r1.json()["refresh_token"]
    assert new_refresh != old_refresh

    # Reusing the consumed (rotated) refresh token is rejected.
    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401

    # The new refresh token still works.
    r3 = client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert r3.status_code == 200, r3.text
