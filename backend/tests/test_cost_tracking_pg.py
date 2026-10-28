"""
Postgres integration test for chat_pg.add_usage_to_message_pg — the hook that
adds the safety classifiers' cost to a saved answer row.

Runs only against a real database (DATABASE_URL set, DB_ENGINE=postgres), e.g.
the throwaway E2E stack; skipped in the normal offline suite. Cleans up after
itself.
"""

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL") or os.getenv("DB_ENGINE", "").lower() != "postgres",
    reason="needs a postgres DATABASE_URL",
)


@pytest.fixture
def pg_user():
    from db_pg import get_pg_conn

    name = f"cost_test_{uuid.uuid4().hex[:8]}"
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'user') RETURNING id",
            (name,),
        )
        uid = cur.fetchone()[0]
    yield uid
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chat_history WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM chat_sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))


def _row(mid):
    from db_pg import get_pg_conn

    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT prompt_tokens, completion_tokens, model_used, cost_usd::float FROM chat_history WHERE id = %s",
            (mid,),
        )
        return cur.fetchone()


CLASSIFIER = {"model_used": "gpt-4o-mini", "prompt_tokens": 1500, "completion_tokens": 50, "cost_usd": 0.000255}


def test_adds_to_answer_usage_without_replacing_it(pg_user):
    from chat_pg import add_usage_to_message_pg, ensure_session_pg, save_chat_message_pg

    sid, _ = ensure_session_pg(user_id=pg_user, session_id=None, first_question="q")
    mid = save_chat_message_pg(
        user_id=pg_user, session_id=sid, question="q", answer="a", source="llm", knowledge_id=None,
        usage={"model_used": "gpt-4.1", "prompt_tokens": 2000, "completion_tokens": 100, "cost_usd": 0.0048},
    )

    add_usage_to_message_pg(mid, CLASSIFIER)

    p, c, model, cost = _row(mid)
    assert (p, c, model) == (3500, 150, "gpt-4.1")          # answer model stays primary
    assert cost == pytest.approx(0.005055)


def test_no_llm_row_gets_classifier_cost_and_model(pg_user):
    from chat_pg import add_usage_to_message_pg, ensure_session_pg, save_chat_message_pg

    sid, _ = ensure_session_pg(user_id=pg_user, session_id=None, first_question="q")
    mid = save_chat_message_pg(
        user_id=pg_user, session_id=sid, question="ขอ PDF", answer="offer", source="export_offer",
        knowledge_id=None,
    )
    assert _row(mid) == (None, None, None, None)

    add_usage_to_message_pg(mid, CLASSIFIER)

    p, c, model, cost = _row(mid)
    assert (p, c, model) == (1500, 50, "gpt-4o-mini")
    assert cost == pytest.approx(0.000255)
