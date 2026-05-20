"""
Tests for /admin/* endpoints.

Coverage:
- All admin endpoints return 403 for regular users (role guard)
- Pending questions: list, answer, ignore
- Knowledge base: list, create, delete
- Chat-history admin view + CSV export
"""

from tests.conftest import bearer, post_chat


# ---------------------------------------------------------------------------
# Role guard — every admin endpoint must reject regular users
# ---------------------------------------------------------------------------

ADMIN_ENDPOINTS = [
    ("GET",  "/admin/pending"),
    ("GET",  "/admin/knowledge"),
    ("GET",  "/admin/chat-history"),
    ("GET",  "/admin/chat-history/export/all"),
]


def test_admin_endpoints_reject_regular_user(client, user_token):
    for method, path in ADMIN_ENDPOINTS:
        r = client.request(method, path, headers=bearer(user_token))
        assert r.status_code == 403, f"{method} {path} should return 403 for user, got {r.status_code}"


def test_admin_endpoints_reject_unauthenticated(client):
    for method, path in ADMIN_ENDPOINTS:
        r = client.request(method, path)
        assert r.status_code == 401, f"{method} {path} should return 401 without token"


# ---------------------------------------------------------------------------
# Pending questions
# ---------------------------------------------------------------------------

def test_list_pending_returns_list(client, admin_token):
    r = client.get("/admin/pending", headers=bearer(admin_token))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_answer_pending_returns_knowledge_id(client, admin_token):
    # main.resolve_pending is mocked to return 99
    r = client.post(
        "/admin/pending/999/answer",
        json={"answer": "คำตอบสำหรับคำถามนี้ครับ"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["knowledge_id"] == 99


def test_answer_pending_as_regular_user_returns_403(client, user_token):
    r = client.post(
        "/admin/pending/1/answer",
        json={"answer": "พยายามตอบ"},
        headers=bearer(user_token),
    )
    assert r.status_code == 403


def test_ignore_pending_returns_ok(client, admin_token):
    r = client.post("/admin/pending/999/ignore", headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_ignore_pending_as_regular_user_returns_403(client, user_token):
    r = client.post("/admin/pending/1/ignore", headers=bearer(user_token))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------

def test_list_knowledge_returns_list(client, admin_token):
    r = client.get("/admin/knowledge", headers=bearer(admin_token))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_knowledge_returns_id(client, admin_token):
    # main.add_knowledge is mocked to return 1
    r = client.post(
        "/admin/knowledge",
        json={"question": "บริษัทอยู่ที่ไหน", "answer": "สาทร กรุงเทพ"},
        headers=bearer(admin_token),
    )
    assert r.status_code == 200
    assert "id" in r.json()


def test_create_knowledge_as_regular_user_returns_403(client, user_token):
    r = client.post(
        "/admin/knowledge",
        json={"question": "q", "answer": "a"},
        headers=bearer(user_token),
    )
    assert r.status_code == 403


def test_delete_knowledge_returns_ok(client, admin_token):
    r = client.delete("/admin/knowledge/1", headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_delete_knowledge_as_regular_user_returns_403(client, user_token):
    r = client.delete("/admin/knowledge/1", headers=bearer(user_token))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Admin chat-history view
# ---------------------------------------------------------------------------

def test_admin_chat_history_lists_all_sessions(client, admin_token, user_token):
    post_chat(client, admin_token, "คำถามของ admin")
    post_chat(client, user_token, "คำถามของ user")

    r = client.get("/admin/chat-history", headers=bearer(admin_token))
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) >= 2
    usernames = {s["username"] for s in sessions}
    assert "admin_user" in usernames
    assert "regular_user" in usernames


def test_admin_chat_history_session_detail(client, admin_token, user_token):
    sid = post_chat(client, admin_token, "คำถามทดสอบ detail")["session_id"]
    r = client.get(f"/admin/chat-history/{sid}", headers=bearer(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == sid
    assert len(data["messages"]) == 1


def test_admin_chat_history_unknown_session_returns_404(client, admin_token):
    r = client.get("/admin/chat-history/99999", headers=bearer(admin_token))
    assert r.status_code == 404


def test_admin_export_all_returns_csv(client, admin_token, user_token):
    post_chat(client, admin_token, "แอดมินถาม")
    post_chat(client, user_token, "ยูสเซอร์ถาม")

    r = client.get("/admin/chat-history/export/all", headers=bearer(admin_token))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().split("\n")
    assert lines[0].startswith("username")  # header row
    assert len(lines) >= 3  # header + 2 messages


def test_admin_export_all_as_regular_user_returns_403(client, user_token):
    r = client.get("/admin/chat-history/export/all", headers=bearer(user_token))
    assert r.status_code == 403
