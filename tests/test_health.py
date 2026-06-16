"""Scaffold smoke test (P0-BE-01 done-when)."""


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"


def test_auth_router_mounted(client):
    # Real auth router is mounted; /me without a token is a clean 401.
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
