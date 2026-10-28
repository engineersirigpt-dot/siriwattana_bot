"""
Stop button on /chat/stream: when the client goes away the stream generator is
closed at once (not left to GC) and the answer text already sent is saved with
STOPPED_NOTE; a `session` event names the chat before the answer starts, so a
new chat that was stopped keeps its id. Offline.
"""

import inspect
import json
import threading

import anyio
from starlette.requests import Request
from starlette.testclient import TestClient

import main
from tests.test_v4_glue import ADMIN, pipe, stream  # noqa: F401  (pipe is a fixture)


def test_close_on_disconnect_closes_the_generator_at_once():
    seen = {}

    def gen():
        try:
            for i in range(100):
                yield f"{i}\n"
        except GeneratorExit:
            seen["closed_in"] = threading.current_thread() is not threading.main_thread()
            raise

    async def run():
        got = []
        with anyio.CancelScope() as scope:   # the response task being cancelled
            async for chunk in main._close_on_disconnect(gen()):
                got.append(chunk)
                if len(got) == 2:
                    scope.cancel()
        return got

    assert anyio.run(run) == ["0\n", "1\n"]
    assert seen == {"closed_in": True}   # closed right away, off the event loop


def _stop_after(question, n, session_id=5):
    """Call /chat/stream's handler directly, read n events, then drop the
    connection the way Starlette does (the body iterator is closed)."""
    req = Request({"type": "http", "method": "POST", "path": "/chat/stream", "headers": [], "client": ("t", 1)})
    handler = inspect.unwrap(main.chat_stream)

    async def run():
        resp = await handler(request=req, message=question, session_id=session_id, mode="normal", user=ADMIN)
        it = resp.body_iterator
        got = [json.loads(await it.__anext__()) for _ in range(n)]
        await it.aclose()
        return got

    return anyio.run(run)


def stream_new_chat(question):
    main.app.dependency_overrides[main.current_user] = lambda: ADMIN
    try:
        r = TestClient(main.app).post("/chat/stream", data={"message": question})
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)
    return [json.loads(line) for line in r.text.splitlines() if line]


def test_session_event_comes_before_the_answer(pipe):  # noqa: F811
    events = stream("วิธีเบิกค่าเดินทาง")
    assert [e["type"] for e in events] == ["session", "delta", "delta", "done"]
    assert events[0] == {"type": "session", "session_id": 5, "session_title": "t"}


def test_stop_mid_answer_saves_the_text_sent(pipe):  # noqa: F811
    got = _stop_after("วิธีเบิกค่าเดินทาง", 2)          # session + the first delta
    assert [e["type"] for e in got] == ["session", "delta"]
    assert len(pipe.saved) == 1
    row = pipe.saved[0]
    assert row["answer"] == "คำ" + main.STOPPED_NOTE
    assert row["source"] == "llm" and row["session_id"] == 5
    assert row.get("usage") is None                      # the usage chunk never came
    assert "chat_stream_stopped" in pipe.audits


def test_stop_before_any_text_saves_nothing(pipe):  # noqa: F811
    _stop_after("วิธีเบิกค่าเดินทาง", 1)                 # only the session event
    assert pipe.saved == []
    assert "chat_stream_stopped" not in pipe.audits


def test_a_finished_answer_is_not_saved_twice(pipe):  # noqa: F811
    _stop_after("วิธีเบิกค่าเดินทาง", 4)                 # everything, then the client goes
    assert len(pipe.saved) == 1 and main.STOPPED_NOTE not in pipe.saved[0]["answer"]
    assert "chat_stream_stopped" not in pipe.audits


def _agent(monkeypatch, pipe):  # noqa: F811
    turns = []

    def fake_turn(u, q, sid, mode, req, uploads=None, scoped=False, session_ref=None):
        turns.append(session_ref)
        yield "status", "agent_searching"
        yield "delta", "ราคา"
        yield "delta", "ทอง"
        yield "done", {"answer": "ราคาทอง", "tail": "", "source": "agent",
                       "session_id": session_ref["id"], "session_title": "t", "message_id": 9, "attachments": []}

    monkeypatch.setattr(main, "_agent_enabled", lambda *a: True)
    monkeypatch.setattr(main, "_agent_turn", fake_turn)
    return turns


def test_agent_new_chat_gets_its_id_before_the_answer(monkeypatch, pipe):  # noqa: F811
    turns = _agent(monkeypatch, pipe)
    events = stream_new_chat("ค้นราคาทองวันนี้")
    assert [e["type"] for e in events] == ["status", "session", "delta", "delta", "done"]
    assert turns == [{"id": 5}]                          # the agent saves into that chat
    assert pipe.saved == []                              # (the fake agent "saved" it)


def test_agent_stop_mid_answer_saves_the_text_sent(monkeypatch, pipe):  # noqa: F811
    _agent(monkeypatch, pipe)
    _stop_after("ค้นราคาทองวันนี้", 3, session_id=None)   # status, session, first delta
    assert [r["answer"] for r in pipe.saved] == ["ราคา" + main.STOPPED_NOTE]
    assert pipe.saved[0]["source"] == "agent" and pipe.saved[0]["session_id"] == 5
