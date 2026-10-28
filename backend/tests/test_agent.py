"""
Tests for Agent V1 (agent.py) and its chat glue (main._agent_turn).

The OpenAI client is faked: each scripted step is either a Responses object
(non-stream router call) or a list of stream events (later steps).
"""

import json
from types import SimpleNamespace as NS

import pytest

import agent
import chat_pg
import llm
import main
import url_reader


# ---------------------------------------------------------------------------
# Fake Responses API
# ---------------------------------------------------------------------------

def _usage(i=100, o=20):
    return NS(input_tokens=i, output_tokens=o)


def call(name, n=0, **args):
    return NS(type="function_call", name=name, arguments=json.dumps(args), call_id=f"c-{name}-{n}")


def resp(*calls, text="", rid="r0"):
    return NS(id=rid, output=list(calls), usage=_usage(), output_text=text)


def stream(text="", *calls, rid="r1"):
    events = [NS(type="response.output_text.delta", delta=ch) for ch in ([text] if text else [])]
    events.append(NS(type="response.completed", response=resp(*calls, rid=rid)))
    return events


class FakeClient:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = []
        self.responses = self

    def create(self, **kw):
        self.calls.append(kw)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def run(client, question="q", quota=20, on_web_search=None):
    return list(agent.run_agent(question, [], web_quota_left=quota, on_web_search=on_web_search, client=client))


def tool_names(kw):
    return [t["name"] for t in kw["tools"]]


def outputs(kw):
    return [json.loads(o["output"]) for o in kw["input"]]


@pytest.fixture
def pages(monkeypatch):
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return (f"Title {url}", f"text of {url}")

    monkeypatch.setattr(url_reader, "fetch_url_text", fake_fetch)
    return fetched


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "mode,user,expected",
    [
        ("off", {"role": "admin", "username": "a"}, False),
        ("admin", {"role": "admin", "username": "a"}, True),
        ("admin", {"role": "user", "username": "u"}, False),
        ("admin", {"role": "user", "username": "trial1"}, True),
        ("all", {"role": "user", "username": "u"}, True),
    ],
)
def test_feature_flag(monkeypatch, mode, user, expected):
    monkeypatch.setattr(agent, "AGENT_V1_MODE", mode)
    monkeypatch.setattr(agent, "AGENT_V1_USERS", {"trial1"})
    assert agent.agent_enabled_for(user) is expected


# ---------------------------------------------------------------------------
# Routing and tools
# ---------------------------------------------------------------------------

def test_no_tool_needed_routes_back_to_standard_pipeline():
    client = FakeClient(resp(call("use_standard_answer")))

    events = run(client)

    assert [k for k, _ in events] == ["standard"]
    assert events[0][1]["prompt_tokens"] == 100                  # router cost handed back
    assert client.calls[0]["tool_choice"] == "required"
    assert "use_standard_answer" in tool_names(client.calls[0])


def test_read_url_then_streamed_answer(pages):
    client = FakeClient(resp(call("read_url", url="https://example.com/a")), stream("คำตอบ"))

    events = run(client)

    assert ("status", "agent_reading") in events
    assert ("delta", "คำตอบ") in events
    result = events[-1][1]
    assert events[-1][0] == "done" and result.answer == "คำตอบ"
    assert result.citations == [{"title": "Title https://example.com/a", "url": "https://example.com/a"}]
    assert len(result.usage) == 2 and result.steps == 2
    second = client.calls[1]
    assert second["previous_response_id"] == "r0" and second["stream"] is True
    assert second["tool_choice"] == "auto"
    assert "use_standard_answer" not in tool_names(second)
    assert outputs(second)[0]["text"] == "text of https://example.com/a"


def test_multi_step_search_then_read(monkeypatch, pages):
    searches = []
    monkeypatch.setattr(
        llm, "answer_with_web_search",
        lambda q, timeout=None: ("สรุปผลค้นหา", [{"title": "s", "url": "https://src.example/x"}],
                                 {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.05}),
    )
    client = FakeClient(
        resp(call("web_search", query="ข่าวล่าสุด")),
        stream("", call("read_url", url="https://src.example/x"), rid="r1"),
        stream("สรุปจากต้นฉบับ", rid="r2"),
    )

    events = run(client, on_web_search=lambda q, cost: searches.append((q, cost)))

    result = events[-1][1]
    assert searches == [("ข่าวล่าสุด", 0.05)]                      # counted against the daily quota
    assert pages == ["https://src.example/x"]
    assert [t["tool"] for t in result.tools] == ["web_search", "read_url"]
    assert [c["url"] for c in result.citations] == ["https://src.example/x"]   # de-duplicated
    assert sum(u["cost_usd"] for u in result.usage) >= 0.05        # search surcharge kept
    assert client.calls[2]["previous_response_id"] == "r1"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_loop_guard_forces_final_answer_within_max_steps(pages):
    steps = [resp(call("read_url", url="https://e.com/0"))]
    steps += [stream("", call("read_url", n, url=f"https://e.com/{n}"), rid=f"r{n}") for n in range(1, agent.AGENT_MAX_STEPS - 1)]
    steps.append(stream("จบ"))
    client = FakeClient(*steps)

    events = run(client)

    assert events[-1][0] == "done"
    assert len(client.calls) == agent.AGENT_MAX_STEPS
    assert client.calls[-1]["tool_choice"] == "none"
    assert len(pages) <= agent.AGENT_MAX_URL_READS


def test_duplicate_tool_call_is_not_reexecuted(pages):
    client = FakeClient(
        resp(call("read_url", 0, url="https://e.com/a"), call("read_url", 1, url="https://e.com/a")),
        stream("ok"),
    )

    run(client)

    assert pages == ["https://e.com/a"]
    assert "เรียกซ้ำ" in outputs(client.calls[1])[1]["note"]


def test_web_search_hidden_when_daily_quota_used_up():
    client = FakeClient(resp(call("use_standard_answer")))

    run(client, quota=0)

    assert "web_search" not in tool_names(client.calls[0])


@pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://e.com/x", "javascript:alert(1)"])
def test_non_http_urls_are_refused_without_fetching(pages, bad):
    client = FakeClient(resp(call("read_url", url=bad)), stream("ตอบ"))

    run(client)

    assert pages == []
    assert "error" in outputs(client.calls[1])[0]


def test_tool_exception_is_reported_to_model_not_raised(monkeypatch):
    def boom(q, timeout=None):
        raise RuntimeError("search down")

    monkeypatch.setattr(llm, "answer_with_web_search", boom)
    client = FakeClient(resp(call("web_search", query="x")), stream("ตอบจากที่มี"))

    events = run(client)

    assert events[-1][0] == "done"
    assert "ค้นเว็บไม่สำเร็จ" in outputs(client.calls[1])[0]["error"]


def test_timeout_skips_tools_and_forces_final_answer(monkeypatch, pages):
    monkeypatch.setattr(agent, "AGENT_TIMEOUT_S", 5)   # already below the per-call floor
    client = FakeClient(resp(call("read_url", url="https://e.com/a")), stream("ตอบ"))

    run(client)

    assert pages == []
    assert "หมดเวลา" in outputs(client.calls[1])[0]["error"]
    assert client.calls[1]["tool_choice"] == "none"


def test_api_error_raises_agent_error():
    client = FakeClient(RuntimeError("503"))

    with pytest.raises(agent.AgentError):
        run(client)


def test_failed_stream_raises_agent_error(pages):
    client = FakeClient(resp(call("read_url", url="https://e.com/a")), [NS(type="response.failed")])

    with pytest.raises(agent.AgentError):
        run(client)


# ---------------------------------------------------------------------------
# main._agent_turn glue (persistence, cost, fallback)
# ---------------------------------------------------------------------------

@pytest.fixture
def glue(monkeypatch):
    saved, audits, searches, attached = [], [], [], []
    monkeypatch.setattr(agent, "AGENT_V1_MODE", "all")
    monkeypatch.setattr(main, "use_postgres_auth", lambda: True)
    monkeypatch.setattr(chat_pg, "get_session_history_pg", lambda **kw: [])
    monkeypatch.setattr(chat_pg, "count_web_searches_today_pg", lambda uid: 18)
    monkeypatch.setattr(chat_pg, "count_code_runs_today_pg", lambda uid: 0)
    monkeypatch.setattr(chat_pg, "get_session_spreadsheets_pg", lambda sid, uid: [])
    monkeypatch.setattr(chat_pg, "record_web_search_pg", lambda uid, q, cost: searches.append((uid, q, cost)))
    monkeypatch.setattr(chat_pg, "ensure_session_pg", lambda **kw: (7, "title"))
    monkeypatch.setattr(chat_pg, "save_chat_message_pg", lambda **kw: saved.append(kw) or 99)
    monkeypatch.setattr(
        chat_pg, "save_attachment_pg",
        lambda **kw: attached.append(kw) or {"id": len(attached), "filename": kw["filename"],
                                             "generated": kw.get("generated", False)},
    )
    monkeypatch.setattr(main, "audit_log", lambda action, **kw: audits.append(action))
    return NS(saved=saved, audits=audits, searches=searches, attached=attached)


def _turn(monkeypatch, events, seen=None):
    def fake_run(question, history, *, web_quota_left, on_web_search, **kw):
        if seen is not None:
            seen.update(quota=web_quota_left, on_web_search=on_web_search, **kw)
        for e in events:
            if isinstance(e, Exception):
                raise e
            yield e

    monkeypatch.setattr(agent, "run_agent", fake_run)
    user = {"id": 1, "role": "admin", "username": "a"}
    return list(main._agent_turn(user, "q", None, "normal", None))


def test_agent_turn_saves_answer_with_cost_and_sources(monkeypatch, glue):
    result = agent.AgentResult(
        answer="ตอบ", citations=[{"title": "T", "url": "https://e.com"}],
        usage=[{"model": "m", "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.01},
               {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.04}],
        steps=2,
    )
    seen = {}
    out = _turn(monkeypatch, [("delta", "ตอบ"), ("done", result)], seen)

    assert seen["quota"] == main.WEB_SEARCH_DAILY_LIMIT - 18
    seen["on_web_search"]("gold price", 0.03)
    assert glue.searches == [(1, "gold price", 0.03)]
    kind, payload = out[-1]
    assert kind == "done" and payload["source"] == "agent" and payload["message_id"] == 99
    assert "https://e.com" in payload["tail"] and payload["answer"].startswith("ตอบ")
    row = glue.saved[0]
    assert row["source"] == "agent" and row["usage"]["cost_usd"] == pytest.approx(0.05)
    assert row["usage"]["prompt_tokens"] == 11
    assert "agent_answer" in glue.audits


def test_agent_turn_standard_route_saves_nothing(monkeypatch, glue):
    out = _turn(monkeypatch, [("standard", {"cost_usd": 0.001})])

    # Agent V1 on → the legacy URL/web steps are skipped (the router covered them)
    assert out == [("standard", {"usage": {"cost_usd": 0.001}, "legacy_tools": False})]
    assert glue.saved == []


def test_agent_turn_falls_back_when_failing_before_any_text(monkeypatch, glue):
    out = _turn(monkeypatch, [("status", "agent_searching"), agent.AgentError("boom")])

    assert out[-1] == ("fallback", None)
    assert glue.saved == [] and "agent_failed" in glue.audits


def test_agent_turn_reports_error_when_failing_mid_answer(monkeypatch, glue):
    out = _turn(monkeypatch, [("delta", "ครึ่ง"), agent.AgentError("boom")])

    assert out[-1] == ("error", None)


@pytest.mark.parametrize("mode", ["company"])
def test_agent_never_runs_in_company_mode(monkeypatch, mode):
    monkeypatch.setattr(agent, "AGENT_V1_MODE", "all")
    monkeypatch.setattr(main, "use_postgres_auth", lambda: True)
    assert main._agent_enabled({"role": "admin"}, mode) is False
    assert main._agent_enabled({"role": "admin"}, "normal") is True
