"""
Postgres integration tests for the AI usage log: rows written with each saved
answer, kept when the chat is deleted, the report over real rows, and the
backfill's sources. Runs only with DATABASE_URL + DB_ENGINE=postgres (e.g. the
throwaway E2E stack); skipped in the offline suite. Cleans up after itself.
"""

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL") or os.getenv("DB_ENGINE", "").lower() != "postgres",
    reason="needs a postgres DATABASE_URL",
)


@pytest.fixture
def pg_user():
    import db_pg
    from db_pg import get_pg_conn

    db_pg.init_pg_schema()
    name = f"usage_test_{uuid.uuid4().hex[:8]}"
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'user') RETURNING id",
                    (name,))
        uid = cur.fetchone()[0]
    yield uid, name
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ai_usage_log WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM chat_history WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def _log_rows(uid):
    from db_pg import get_pg_conn

    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT username, feature, source, model, prompt_tokens, completion_tokens, "
                    "cost_usd::float, message_id FROM ai_usage_log WHERE user_id = %s ORDER BY id", (uid,))
        return cur.fetchall()


ANSWER = {"model_used": "gpt-5", "prompt_tokens": 5000, "completion_tokens": 2000, "cost_usd": 0.0563}
CLASSIFIER = {"model_used": "gpt-4o-mini", "prompt_tokens": 1500, "completion_tokens": 50, "cost_usd": 0.000255}


def test_answer_and_classifier_are_logged_and_survive_chat_deletion(pg_user):
    from chat_pg import add_usage_to_message_pg, delete_session_pg, ensure_session_pg, save_chat_message_pg

    uid, name = pg_user
    sid, _ = ensure_session_pg(user_id=uid, session_id=None, first_question="q")
    mid = save_chat_message_pg(user_id=uid, session_id=sid, question="ฝากเงิน 100,000", answer="162,889.46",
                               source="code", knowledge_id=None, usage=ANSWER)
    add_usage_to_message_pg(mid, CLASSIFIER)
    save_chat_message_pg(user_id=uid, session_id=sid, question="ขอ PDF", answer="offer",
                         source="export_offer", knowledge_id=None)          # no usage → no log row

    rows = _log_rows(uid)
    assert rows == [
        (name, "code_interpreter", "code", "gpt-5", 5000, 2000, pytest.approx(0.0563), mid),
        (name, "safety", "classifier", "gpt-4o-mini", 1500, 50, pytest.approx(0.000255), mid),
    ]

    delete_session_pg(sid, uid)                                        # the user deletes the chat
    assert len(_log_rows(uid)) == 2                                    # cost is kept


def test_report_over_real_rows(pg_user):
    import usage_log
    from chat_pg import ensure_session_pg, save_chat_message_pg

    uid, name = pg_user
    sid, _ = ensure_session_pg(user_id=uid, session_id=None, first_question="q")
    for _ in range(3):
        save_chat_message_pg(user_id=uid, session_id=sid, question="q", answer="a", source="llm",
                             knowledge_id=None, usage={"model_used": "gpt-4.1", "prompt_tokens": 2000,
                                                       "completion_tokens": 100, "cost_usd": 0.0048})
    usage_log.log_usage_pg(uid, "safety", "blocked", CLASSIFIER)

    today = datetime.now(timezone(timedelta(hours=7))).date()
    report = usage_log.usage_report_pg(today.replace(day=1), today, 30)
    me = next(u for u in report["users"] if u["username"] == name)
    assert me["requests"] == 4                                         # 3 answers + 1 blocked
    assert me["prompt_tokens"] == 6000 + 1500
    assert me["cost_usd"] == pytest.approx(3 * 0.0048 + 0.000255)


def test_backfill_takes_image_and_web_from_their_own_tables():
    import backfill_usage_log as bf
    from db_pg import get_pg_conn

    with get_pg_conn() as conn, conn.cursor() as cur:
        rows = bf.collect(cur, None)
        cur.execute("SELECT count(*) FROM image_generations")
        n_images = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM web_searches")
        n_web = cur.fetchone()[0]
        conn.rollback()
    assert sum(1 for r in rows if r["feature"] == "image") == n_images
    # web searches made by an agent turn that still exists are inside its cost
    assert sum(1 for r in rows if r["feature"] == "web_search") <= n_web
    # image/web answers in chat_history are not taken twice
    assert not any(r.get("message_id") and r["source"] in bf._FROM_OWN_TABLE for r in rows)
