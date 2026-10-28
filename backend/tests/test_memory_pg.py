"""
Postgres checks for V4: memories and session summaries are only ever read or
changed by their owner, the summary update is race-safe and logged, and the
route columns of the usage log land in the report. Runs only with
DATABASE_URL + DB_ENGINE=postgres (the throwaway E2E stack). Cleans up.
"""

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL") or os.getenv("DB_ENGINE", "").lower() != "postgres",
    reason="needs a postgres DATABASE_URL",
)


@pytest.fixture
def users():
    import db_pg
    from db_pg import get_pg_conn

    db_pg.init_pg_schema()
    ids = []
    with get_pg_conn() as conn, conn.cursor() as cur:
        for _ in range(2):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'user') RETURNING id",
                        (f"mem_test_{uuid.uuid4().hex[:8]}",))
            ids.append(cur.fetchone()[0])
    yield ids
    with get_pg_conn() as conn, conn.cursor() as cur:
        for uid in ids:
            cur.execute("DELETE FROM ai_usage_log WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM chat_history WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM chat_sessions WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))          # cascades memories + settings


def test_memories_belong_to_their_owner(users):
    import memory

    a, b = users
    store = memory.PgStore()
    assert memory.save(a, "ฉันอยู่ฝ่ายผลิต")[0] and memory.save(b, "ฉันอยู่ฝ่ายบัญชี")[0]
    mine = store.items(a)
    assert [i["content"] for i in mine] == ["ฉันอยู่ฝ่ายผลิต"] and store.enabled(a)
    assert store.delete(b, mine[0]["id"]) is False                      # b can't delete a's
    assert store.clear(b) == 1 and [i["content"] for i in store.items(a)] == ["ฉันอยู่ฝ่ายผลิต"]
    assert "ฝ่ายบัญชี" not in memory.prompt_message(a)["content"] and memory.prompt_message(b) is None
    store.set_enabled(a, False)
    assert memory.prompt_message(a) is None and store.items(a)
    store.set_enabled(a, True)
    assert store.delete(a, mine[0]["id"]) and store.items(a) == []


def _session_with_turns(uid, n):
    from chat_pg import ensure_session_pg, save_chat_message_pg

    sid, _ = ensure_session_pg(user_id=uid, session_id=None, first_question="q1", mode="normal")
    for i in range(1, n + 1):
        save_chat_message_pg(user_id=uid, session_id=sid, question=f"คำถาม {i}", answer=f"ตอบ {i}",
                             source="llm", knowledge_id=None)
    return sid


def test_long_chat_summary_is_owned_race_safe_and_logged(users):
    import context_manager as cm
    from db_pg import get_pg_conn

    a, b = users
    sid = _session_with_turns(a, 8)
    calls = []
    client = NS(chat=NS(completions=NS(create=lambda **kw: calls.append(kw) or NS(
        choices=[NS(message=NS(content="- ผู้ใช้ถามคำถาม 1–2"))], usage=NS(prompt_tokens=500, completion_tokens=30)))))

    assert cm.load(sid, b) == (None, 0, [])                             # another user's session: nothing
    assert cm.update_summary(sid, b, client=client) is False and calls == []
    assert cm.update_summary(sid, a, client=client) is True
    summary, upto, turns = cm.load(sid, a)
    assert summary == "- ผู้ใช้ถามคำถาม 1–2" and [t.question for t in turns][0] == "คำถาม 3"
    assert "ผู้ใช้: คำถาม 1" in calls[0]["messages"][1]["content"]
    msgs = cm.history(sid, a)
    assert msgs[0]["content"].startswith(cm.SUMMARY_HEADER) and msgs[1]["content"] == "คำถาม 3"
    assert cm.history(sid, b) == []
    assert cm.update_summary(sid, a, client=client) is False            # nothing new to fold
    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT feature, source, model, prompt_tokens FROM ai_usage_log WHERE user_id = %s "
                    "AND feature = 'context'", (a,))
        assert cur.fetchall() == [("context", "summary", cm.SUMMARY_MODEL, 500)]


def test_a_stale_summary_write_is_dropped(users, monkeypatch):
    import context_manager as cm
    from db_pg import get_pg_conn

    a, _ = users
    sid = _session_with_turns(a, 8)

    def summarize_while_another_run_finishes(previous, turns, client=None):
        with get_pg_conn() as conn, conn.cursor() as cur:            # the other run lands first
            cur.execute("UPDATE chat_sessions SET context_summary = 'newer', summary_upto = %s WHERE id = %s",
                        (turns[-1].id, sid))
        return "stale", {"model": "gpt-5-mini", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.0}

    monkeypatch.setattr(cm, "summarize", summarize_while_another_run_finishes)
    assert cm.update_summary(sid, a) is False and cm.load(sid, a)[0] == "newer"


def test_route_columns_reach_the_report(users):
    import usage_log as ul
    from chat_pg import save_chat_message_pg

    a, _ = users
    sid = _session_with_turns(a, 0)
    save_chat_message_pg(user_id=a, session_id=sid, question="สวัสดี", answer="สวัสดีค่ะ", source="llm",
                         knowledge_id=None, usage={
                             "model_used": "gpt-5-mini", "prompt_tokens": 300, "completion_tokens": 20,
                             "cost_usd": 0.000115, "route": "light", "route_reason": "greeting/ack",
                             "reasoning_effort": "minimal", "latency_ms": 1200, "first_token_ms": 700})
    today = datetime.now(timezone(timedelta(hours=7))).date()
    report = ul.usage_report_pg(today - timedelta(days=1), today + timedelta(days=1), 30)
    light = [r for r in report["routing"] if r["route"] == "light" and r["model"] == "gpt-5-mini"]
    assert light and light[0]["requests"] >= 1 and light[0]["avg_latency_ms"] is not None
    assert isinstance(report["routing"], list) and date.today()
