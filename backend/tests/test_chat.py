"""
Tests for /chat, /chat/sessions/*, and /chat/search endpoints.

Coverage:
- POST /chat: LLM path, RAG path, calc-query routing, file upload, empty message
- Session lifecycle: auto-create, continue, list, get, rename, delete
- Session isolation: user cannot access another user's session
- GET /chat/search
- GET /chat/sessions/{id}/export  (CSV)
"""

from tests.conftest import MOCK_ANSWER, bearer, post_chat


# ---------------------------------------------------------------------------
# POST /chat — text-only paths
# ---------------------------------------------------------------------------

def test_chat_llm_path_returns_answer(client, user_token):
    # Default mock: search_knowledge returns None → LLM path
    r = client.post("/chat", data={"message": "ทดสอบ"}, headers=bearer(user_token))
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == MOCK_ANSWER
    assert data["source"] == "llm"
    assert data["session_id"] is not None


def test_chat_rag_path_returns_answer(client, user_token, monkeypatch):
    monkeypatch.setattr(
        "main.search_knowledge",
        lambda vec, k=1: {"id": 1, "question": "q", "answer": "a", "similarity": 0.9},
    )
    r = client.post("/chat", data={"message": "ทดสอบ"}, headers=bearer(user_token))
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "rag"
    assert data["similarity"] == 0.9


def test_chat_calc_query_uses_calc_source(client, user_token, monkeypatch):
    monkeypatch.setattr("main.classify_query", lambda q: "calc")
    r = client.post("/chat", data={"message": "คำนวณ OT"}, headers=bearer(user_token))
    assert r.status_code == 200
    assert r.json()["source"] == "llm-calc"


def test_chat_empty_message_returns_400(client, user_token):
    r = client.post("/chat", data={"message": ""}, headers=bearer(user_token))
    assert r.status_code == 400


def test_chat_requires_auth(client):
    r = client.post("/chat", data={"message": "hello"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /chat — file upload
# ---------------------------------------------------------------------------

def test_chat_with_text_file_uses_files_source(client, user_token):
    r = client.post(
        "/chat",
        data={"message": "สรุปไฟล์นี้"},
        files={"files": ("report.txt", b"Line1\nLine2\nLine3", "text/plain")},
        headers=bearer(user_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "files"
    assert data["answer"] == MOCK_ANSWER
    assert len(data["attachments"]) == 1
    assert data["attachments"][0]["filename"] == "report.txt"


def test_chat_with_disallowed_file_returns_400(client, user_token):
    r = client.post(
        "/chat",
        data={"message": "ส่งไฟล์"},
        files={"files": ("virus.exe", b"\x4d\x5a", "application/octet-stream")},
        headers=bearer(user_token),
    )
    assert r.status_code == 400


def test_chat_file_only_no_message_succeeds(client, user_token):
    r = client.post(
        "/chat",
        data={"message": ""},
        files={"files": ("notes.txt", b"some content", "text/plain")},
        headers=bearer(user_token),
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def test_chat_creates_new_session_automatically(client, user_token):
    data = post_chat(client, user_token, "สวัสดี")
    assert data["session_id"] > 0
    assert data["session_title"] != ""


def test_chat_continues_existing_session(client, user_token):
    first = post_chat(client, user_token, "คำถามแรก")
    sid = first["session_id"]

    second = post_chat(client, user_token, "คำถามสอง", session_id=sid)
    assert second["session_id"] == sid


def test_list_sessions_empty_initially(client, user_token):
    r = client.get("/chat/sessions", headers=bearer(user_token))
    assert r.status_code == 200
    assert r.json() == []


def test_list_sessions_after_chat(client, user_token):
    post_chat(client, user_token)
    post_chat(client, user_token)
    r = client.get("/chat/sessions", headers=bearer(user_token))
    assert r.status_code == 200
    assert len(r.json()) == 2  # each chat without session_id creates a new session


def test_get_session_returns_messages(client, user_token):
    sid = post_chat(client, user_token, "สวัสดีครับ")["session_id"]
    r = client.get(f"/chat/sessions/{sid}", headers=bearer(user_token))
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == sid
    assert len(data["messages"]) == 1
    assert data["messages"][0]["question"] == "สวัสดีครับ"


def test_get_session_of_other_user_returns_404(client, admin_token, user_token):
    sid = post_chat(client, admin_token)["session_id"]
    r = client.get(f"/chat/sessions/{sid}", headers=bearer(user_token))
    assert r.status_code == 404


def test_rename_session(client, user_token):
    sid = post_chat(client, user_token)["session_id"]
    r = client.patch(
        f"/chat/sessions/{sid}",
        json={"title": "ชื่อใหม่"},
        headers=bearer(user_token),
    )
    assert r.status_code == 200
    assert r.json()["title"] == "ชื่อใหม่"


def test_rename_session_empty_title_returns_400(client, user_token):
    sid = post_chat(client, user_token)["session_id"]
    r = client.patch(
        f"/chat/sessions/{sid}",
        json={"title": "   "},
        headers=bearer(user_token),
    )
    assert r.status_code == 400


def test_rename_other_users_session_returns_404(client, admin_token, user_token):
    sid = post_chat(client, admin_token)["session_id"]
    r = client.patch(
        f"/chat/sessions/{sid}",
        json={"title": "hack"},
        headers=bearer(user_token),
    )
    assert r.status_code == 404


def test_delete_session_removes_history(client, user_token):
    sid = post_chat(client, user_token)["session_id"]
    r = client.delete(f"/chat/sessions/{sid}", headers=bearer(user_token))
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Session should no longer be accessible.
    r2 = client.get(f"/chat/sessions/{sid}", headers=bearer(user_token))
    assert r2.status_code == 404


def test_delete_session_removes_uploaded_files(client, user_token, tmp_path):
    r = client.post(
        "/chat",
        data={"message": "อ่านให้หน่อย"},
        files={"files": ("data.txt", b"content", "text/plain")},
        headers=bearer(user_token),
    )
    assert r.status_code == 200
    sid = r.json()["session_id"]

    del_r = client.delete(f"/chat/sessions/{sid}", headers=bearer(user_token))
    assert del_r.status_code == 200
    assert del_r.json()["deleted_files"] == 1


def test_delete_other_users_session_returns_404(client, admin_token, user_token):
    sid = post_chat(client, admin_token)["session_id"]
    r = client.delete(f"/chat/sessions/{sid}", headers=bearer(user_token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def test_search_finds_matching_message(client, user_token):
    post_chat(client, user_token, "ราคากล่องกระดาษ")
    r = client.get("/chat/search?q=กล่อง", headers=bearer(user_token))
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 1
    assert any("กล่อง" in res["question"] for res in results)


def test_search_returns_empty_for_no_match(client, user_token):
    post_chat(client, user_token, "สวัสดีครับ")
    r = client.get("/chat/search?q=xyznomatch", headers=bearer(user_token))
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def test_export_session_returns_csv(client, user_token):
    sid = post_chat(client, user_token, "ทดสอบ export")["session_id"]
    r = client.get(f"/chat/sessions/{sid}/export", headers=bearer(user_token))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.strip().split("\n")
    assert lines[0].startswith("timestamp")  # header row
    assert len(lines) == 2  # header + 1 message


def test_export_other_users_session_returns_404(client, admin_token, user_token):
    sid = post_chat(client, admin_token)["session_id"]
    r = client.get(f"/chat/sessions/{sid}/export", headers=bearer(user_token))
    assert r.status_code == 404
