"""
Tests for GET /health (Docker HEALTHCHECK liveness probe).
"""


def test_health_ok_without_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
