"""
Postgres integration test: the documents a follow-up may edit
(chat_pg.get_session_documents_pg). Runs only with DATABASE_URL +
DB_ENGINE=postgres (e.g. the throwaway E2E stack). Cleans up after itself.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL") or os.getenv("DB_ENGINE", "").lower() != "postgres",
    reason="needs a postgres DATABASE_URL",
)


@pytest.fixture
def two_users():
    import db_pg
    from db_pg import get_pg_conn

    db_pg.init_pg_schema()
    ids = []
    with get_pg_conn() as conn, conn.cursor() as cur:
        for _ in range(2):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'user') RETURNING id",
                        (f"doc_test_{uuid.uuid4().hex[:8]}",))
            ids.append(cur.fetchone()[0])
    yield ids
    with get_pg_conn() as conn, conn.cursor() as cur:
        for uid in ids:
            cur.execute("DELETE FROM attachments WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM ai_usage_log WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM chat_history WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM chat_sessions WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def _msg_with(uid, sid, files):
    from chat_pg import save_attachment_pg, save_chat_message_pg

    mid = save_chat_message_pg(user_id=uid, session_id=sid, question="q", answer="a", source="document",
                               knowledge_id=None)
    for name, generated in files:
        save_attachment_pg(message_id=mid, user_id=uid, filename=name, content_type="x", size_bytes=1,
                           file_path=f"/tmp/{uuid.uuid4().hex}_{name}", generated=generated)
    return mid


def test_session_documents_newest_first_owner_only(two_users):
    from chat_pg import ensure_session_pg, get_session_documents_pg

    me, other = two_users
    sid, _ = ensure_session_pg(user_id=me, session_id=None, first_question="q")
    _msg_with(me, sid, [("รายงาน.pdf", False), ("logo.png", False)])                 # my upload + a picture
    _msg_with(me, sid, [("สไลด์.pptx", True), ("สไลด์_ตัวอย่าง.png", True)])         # bot output + preview
    _msg_with(me, sid, [("สไลด์.pptx", True)])                                      # a newer version

    docs = get_session_documents_pg(sid, me)
    assert [d["filename"] for d in docs] == ["สไลด์.pptx", "รายงาน.pdf"]              # newest first, no pictures
    newest = docs[0]["file_path"]
    assert len({d["file_path"] for d in docs}) == 2 and newest.endswith("สไลด์.pptx")

    assert get_session_documents_pg(sid, other) == []                                # not another user's chat
