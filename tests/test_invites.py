"""P0-BE-04 done-when: invite URL -> session; reuse -> 401; 6th rapid -> 429."""

import uuid

from app.core.rate_limit import limiter


def _register_admin(client, email, cleanup):
    resp = client.post(
        "/api/v1/auth/register-org",
        json={"org_name": "Inv Org", "name": "Admin", "email": email,
              "password": "supersecret1", "timezone": "Asia/Kolkata"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cleanup["orgs"].append(uuid.UUID(body["org"]["id"]))
    cleanup["users"].append(uuid.UUID(body["user"]["id"]))
    return body


def test_invite_link_creates_session(client, unique_email, cleanup):
    admin = _register_admin(client, unique_email, cleanup)
    headers = {"Authorization": f"Bearer {admin['access_token']}"}

    inv = client.post(
        "/api/v1/org/members/invite",
        headers=headers,
        json={"name": "Ramesh", "phone": "+919800001234", "role": "member"},
    )
    assert inv.status_code == 200, inv.text
    data = inv.json()
    cleanup["users"].append(uuid.UUID(data["user_id"]))
    token = data["invite_url"].rsplit("/join/", 1)[1]
    assert token

    # Redeeming the invite token mints a session for the invited member.
    sess = client.post("/api/v1/auth/verify", json={"token": token})
    assert sess.status_code == 200, sess.text
    assert sess.json()["org_role"] == "member"
    assert sess.json()["org"]["id"] == admin["org"]["id"]

    # That session is authenticated.
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {sess.json()['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["user"]["name"] == "Ramesh"

    # Single-use: the same token cannot be redeemed twice.
    again = client.post("/api/v1/auth/verify", json={"token": token})
    assert again.status_code == 401


def test_verify_rejects_garbage_token(client):
    assert client.post("/api/v1/auth/verify", json={"token": "not-a-real-token"}).status_code == 401


def test_magic_link_request_never_leaks(client):
    # Unregistered email still returns 200 (no account enumeration).
    resp = client.post("/api/v1/auth/forgot-password",
                       json={"email": "nobody-xyz@example.com"})
    assert resp.status_code == 200


def test_rate_limit_returns_429(client):
    # Re-enable limiting for this test (autouse fixture disables it elsewhere).
    limiter.enabled = True
    limiter.reset()
    try:
        statuses = [
            client.post("/api/v1/auth/forgot-password",
                        json={"email": "nobody-xyz@example.com"}).status_code
            for _ in range(6)
        ]
    finally:
        limiter.enabled = False
    assert statuses[:5] == [200, 200, 200, 200, 200], statuses
    assert statuses[5] == 429, statuses
