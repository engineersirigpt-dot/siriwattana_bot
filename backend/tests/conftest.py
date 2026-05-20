"""
Shared fixtures for all test modules.

Isolation strategy:
- Each test gets a fresh SQLite file in tmp_path (avoids singleton bleed-through).
- All OpenAI / embedding calls are patched at the main.py import boundary so
  tests run fully offline.
- File uploads are redirected to tmp_path/uploads so no files land in data/.
"""

import os
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# Make sure backend/ root is importable when pytest runs from backend/.
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set dummy env vars before any module-level code runs.
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-real")
os.environ.setdefault("JWT_SECRET", "test-secret-fixed")

import attachments as att_module
import db as db_module
import main  # noqa: E402 — must come after env setup

MOCK_EMBEDDING = [0.0] * 1024
MOCK_ANSWER = "คำตอบทดสอบจากระบบ"


# ---------------------------------------------------------------------------
# DB + upload-dir isolation (runs for every test)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_conn", None)
    monkeypatch.setattr(att_module, "UPLOAD_DIR", upload_dir)

    yield

    if db_module._conn:
        db_module._conn.close()
    monkeypatch.setattr(db_module, "_conn", None)


# ---------------------------------------------------------------------------
# LLM / embedding stubs (runs for every test)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_external(monkeypatch):
    """Patch all names as they exist in main.py's namespace (imported-from forms)."""
    monkeypatch.setattr("main.embed", lambda text: MOCK_EMBEDDING)
    monkeypatch.setattr("main.search_knowledge", lambda vec, k=1: None)
    monkeypatch.setattr("main.log_pending_question", lambda q, v: 1)
    monkeypatch.setattr("main.add_knowledge", lambda q, a, uid: 1)
    monkeypatch.setattr("main.resolve_pending", lambda pid, ans, uid: 99)
    monkeypatch.setattr("main.classify_query", lambda q: "general")
    monkeypatch.setattr(
        "main.answer_freely",
        lambda q, model=None, history=None: MOCK_ANSWER,
    )
    monkeypatch.setattr(
        "main.answer_from_context",
        lambda q, cq, ca, model=None, history=None: MOCK_ANSWER,
    )
    monkeypatch.setattr(
        "main.answer_with_files",
        lambda q, imgs, texts, history=None: MOCK_ANSWER,
    )


# ---------------------------------------------------------------------------
# HTTP client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Pre-authenticated user fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_token(client) -> str:
    """Register the first user (auto-admin) and return their JWT."""
    client.post("/auth/register", data={"username": "admin_user", "password": "pass1234"})
    r = client.post("/auth/login", data={"username": "admin_user", "password": "pass1234"})
    return r.json()["access_token"]


@pytest.fixture
def user_token(client, admin_token) -> str:
    """Register a second (non-admin) user and return their JWT."""
    client.post("/auth/register", data={"username": "regular_user", "password": "pass1234"})
    r = client.post("/auth/login", data={"username": "regular_user", "password": "pass1234"})
    return r.json()["access_token"]


# ---------------------------------------------------------------------------
# Small helpers (not fixtures — import these in test modules that need them)
# ---------------------------------------------------------------------------

def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def post_chat(client, token: str, message: str = "ทดสอบ", session_id=None) -> dict:
    data = {"message": message}
    if session_id is not None:
        data["session_id"] = str(session_id)
    r = client.post("/chat", data=data, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()
