"""
Tests for Code Interpreter V2: code_interpreter.run_code (OpenAI-hosted sandbox),
the agent's run_python tool, the chat glue (main._agent_turn) and the
owner-checked download of generated files.

OpenAI is faked throughout — these run offline. Real-API checks live in the
E2E script (tests/e2e_code_interpreter.py).
"""

import json
from types import SimpleNamespace as NS

import pytest
from starlette.testclient import TestClient

import agent
import attachments as att
import chat_pg
import code_interpreter as ci
import main
from tests.test_agent import FakeClient, call, outputs, resp, stream, tool_names


# ---------------------------------------------------------------------------
# Fake OpenAI client for run_code (files + responses + containers)
# ---------------------------------------------------------------------------

class FakeCI:
    def __init__(self, answer="ผลรวม 98,730 บาท", container_files=(), error=None, calls=1):
        self.uploaded, self.deleted_files, self.deleted_containers, self.requests = [], [], [], []
        self._answer, self._error, self._n = answer, error, calls
        self._cfiles = list(container_files)   # (path, source, data)
        self.files = NS(create=self._upload, delete=self.deleted_files.append)
        self.responses = NS(create=self._create)
        self.containers = NS(
            delete=self.deleted_containers.append,
            files=NS(list=self._list, content=NS(retrieve=self._retrieve)),
        )

    def _upload(self, file, purpose):
        name, fh = file
        self.uploaded.append((name, purpose, len(fh.read())))
        return NS(id=f"file-{len(self.uploaded)}")

    def _create(self, **kw):
        self.requests.append(kw)
        if self._error:
            raise self._error
        out = [NS(type="code_interpreter_call", container_id="cntr_1") for _ in range(self._n)]
        out.append(NS(type="message"))
        return NS(output=out, output_text=self._answer, usage=NS(input_tokens=2000, output_tokens=1000))

    def _list(self, container_id):
        assert container_id == "cntr_1"
        return [NS(id=f"cf{i}", path=p, source=s, bytes=len(d)) for i, (p, s, d) in enumerate(self._cfiles)]

    def _retrieve(self, fid, container_id):
        data = self._cfiles[int(fid[2:])][2]
        return NS(read=lambda: data)


@pytest.fixture
def xlsx(tmp_path):
    p = tmp_path / "sales.xlsx"
    p.write_bytes(b"PK\x03\x04 fake workbook")
    return ("sales.xlsx", str(p))


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
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", mode)
    monkeypatch.setattr(ci, "CODE_INTERPRETER_USERS", {"trial1"})
    assert ci.ci_enabled_for(user) is expected


# ---------------------------------------------------------------------------
# run_code — sandbox request, file handling, cleanup
# ---------------------------------------------------------------------------

def test_run_code_uses_isolated_sandbox_and_cleans_up(xlsx):
    client = FakeCI(
        answer="ยอดรวม 98,730 บาท ดู [summary.xlsx](sandbox:/mnt/data/summary.xlsx) และ "
               "![กราฟ](sandbox:/mnt/data/revenue.png)",
        container_files=[
            ("/mnt/data/sales.xlsx", "user", b"input"),          # the user's own upload
            ("/mnt/data/Sarabun-Regular.ttf", "user", b"font"),
            ("/mnt/data/summary.xlsx", "assistant", b"xlsx-out"),
            ("/mnt/data/revenue.png", "assistant", b"png-out"),
            ("/mnt/data/script.py", "assistant", b"print()"),    # not an allowed output type
        ],
    )

    r = ci.run_code("รวมยอดขายทุก sheet", "sum all sheets", [], [xlsx], client=client)

    kw = client.requests[0]
    tool = kw["tools"][0]
    assert tool["type"] == "code_interpreter"
    assert tool["container"]["network_policy"] == {"type": "disabled"}
    assert tool["container"]["file_ids"] == ["file-1", "file-2"]       # workbook + Thai font
    assert kw["tool_choice"] == "required" and kw["max_tool_calls"] == ci.CI_MAX_TOOL_CALLS
    assert kw["timeout"] == ci.CI_TIMEOUT_S
    assert [u[0] for u in client.uploaded] == ["sales.xlsx", "Sarabun-Regular.ttf"]
    assert all(u[1] == "user_data" for u in client.uploaded)

    assert "sandbox:" not in r.answer and "summary.xlsx" in r.answer
    assert [(f.name, f.content_type) for f in r.files] == [
        ("summary.xlsx", ci.CI_OUTPUT_TYPES[".xlsx"]),
        ("revenue.png", "image/png"),
    ]
    assert r.files[1].data == b"png-out"
    assert r.code_runs == 1
    assert r.usage["prompt_tokens"] == 2000
    assert r.usage["cost_usd"] == pytest.approx(
        ci.estimate_cost_usd(ci.CI_MODEL, 2000, 1000) + ci.CI_CONTAINER_USD
    )
    # nothing left behind on OpenAI's side
    assert client.deleted_containers == ["cntr_1"]
    assert client.deleted_files == ["file-1", "file-2"]


def test_run_code_cleans_up_when_api_fails(xlsx):
    client = FakeCI(error=TimeoutError("read timeout"))

    with pytest.raises(ci.CIError):
        ci.run_code("q", "t", [], [xlsx], client=client)

    assert client.deleted_files == ["file-1", "file-2"]


@pytest.mark.parametrize("name", ["report.pdf", "evil.exe", "photo.png", "noext"])
def test_run_code_rejects_unsupported_inputs_before_upload(tmp_path, name):
    p = tmp_path / name
    p.write_bytes(b"x")
    client = FakeCI()

    with pytest.raises(ci.CIError):
        ci.run_code("q", "t", [], [(name, str(p))], client=client)
    assert client.uploaded == [] and client.requests == []


def test_run_code_rejects_oversize_and_too_many_inputs(monkeypatch, xlsx):
    monkeypatch.setattr(ci, "CI_MAX_INPUT_BYTES", 5)
    with pytest.raises(ci.CIError, match="ใหญ่เกิน"):
        ci.run_code("q", "t", [], [xlsx], client=FakeCI())

    monkeypatch.setattr(ci, "CI_MAX_INPUT_BYTES", 10**9)
    with pytest.raises(ci.CIError, match="สูงสุด"):
        ci.run_code("q", "t", [], [xlsx] * (ci.CI_MAX_INPUT_FILES + 1), client=FakeCI())


def test_run_code_caps_output_files_and_size(monkeypatch):
    monkeypatch.setattr(ci, "CI_MAX_OUTPUT_BYTES", 10)
    files = [(f"/mnt/data/c{i}.png", "assistant", b"png") for i in range(ci.CI_MAX_OUTPUT_FILES + 3)]
    files.append(("/mnt/data/huge.xlsx", "assistant", b"x" * 50))

    r = ci.run_code("q", "t", [], [], client=FakeCI(container_files=files))

    assert len(r.files) == ci.CI_MAX_OUTPUT_FILES
    assert "huge.xlsx" not in [f.name for f in r.files]


def test_run_code_empty_answer_is_an_error():
    with pytest.raises(ci.CIError):
        ci.run_code("q", "t", [], [], client=FakeCI(answer=""))


def test_timeout_never_exceeds_the_ci_limit():
    client = FakeCI()
    ci.run_code("q", "t", [], [], client=client, timeout=10_000)
    ci.run_code("q", "t", [], [], client=client, timeout=30)
    assert [r["timeout"] for r in client.requests] == [ci.CI_TIMEOUT_S, 30]


def test_safe_name_strips_paths():
    assert ci._safe_name("/mnt/data/../../etc/passwd") == "passwd"
    assert ci._safe_name("/mnt/data/สรุป ยอด<>.xlsx") == "สรุป ยอด__.xlsx"


# ---------------------------------------------------------------------------
# Agent run_python tool
# ---------------------------------------------------------------------------

def run(client, question="q", **kw):
    return list(agent.run_agent(question, [], web_quota_left=20, client=client, **kw))


@pytest.fixture
def fake_code(monkeypatch):
    seen = []

    def fake_run_code(question, task, history, input_files=None, *, client=None, timeout=None):
        seen.append(NS(question=question, task=task, input_files=input_files, timeout=timeout))
        return ci.CIResult(
            answer="ดอกเบี้ยทบต้น 10 ปี = 162,889.46 บาท",
            files=[ci.CIFile("chart.png", "image/png", b"png")],
            usage={"model": "gpt-5", "prompt_tokens": 50, "completion_tokens": 10, "cost_usd": 0.04},
            code_runs=1,
        )

    monkeypatch.setattr(ci, "run_code", fake_run_code)
    return seen


def test_run_python_not_offered_by_default():
    client = FakeClient(resp(call("use_standard_answer")))
    run(client)
    assert "run_python" not in tool_names(client.calls[0])
    assert "run_python" not in client.calls[0]["instructions"]


def test_run_python_offered_with_guide_when_enabled():
    client = FakeClient(resp(call("use_standard_answer")))
    run(client, ci_enabled=True, web_enabled=False)
    assert tool_names(client.calls[0]) == ["run_python", "use_standard_answer"]
    assert "run_python:" in client.calls[0]["instructions"]
    assert "web_search:" not in client.calls[0]["instructions"]


def test_general_question_routes_to_standard_without_running_code(fake_code):
    client = FakeClient(resp(call("use_standard_answer")))
    events = run(client, ci_enabled=True)
    assert [k for k, _ in events] == ["standard"]
    assert fake_code == []


def test_run_python_answer_is_returned_directly(fake_code):
    client = FakeClient(resp(call("run_python", task="100000 * 1.05**10")))

    events = run(client, question="ฝาก 100,000 ดอก 5% ทบต้น 10 ปี", ci_enabled=True,
                 input_files=[("a.xlsx", "/x")], file_note="\n\n[ผู้ใช้แนบไฟล์: a.xlsx]")

    assert ("status", "agent_coding") in events
    kind, result = events[-1]
    assert kind == "done" and "162,889.46" in result.answer
    assert result.used_code and [f.name for f in result.files] == ["chart.png"]
    assert sum(u["cost_usd"] for u in result.usage) >= 0.04       # CI cost tracked
    assert len(client.calls) == 1                                   # no rewrite call
    assert fake_code[0].task == "100000 * 1.05**10"
    assert fake_code[0].input_files == [("a.xlsx", "/x")]
    assert "a.xlsx" in fake_code[0].question                        # file note reaches the sandbox
    assert "a.xlsx" in client.calls[0]["input"][-1]["content"]      # …and the router


def test_run_python_failure_is_reported_to_model(monkeypatch):
    def boom(*a, **kw):
        raise ci.CIError("ไฟล์ x.xlsx ใหญ่เกิน 20 MB")

    monkeypatch.setattr(ci, "run_code", boom)
    client = FakeClient(resp(call("run_python", task="t")), stream("ขออภัย ไฟล์ใหญ่เกินไป"))

    events = run(client, ci_enabled=True)

    assert events[-1][0] == "done" and not events[-1][1].used_code
    assert "ใหญ่เกิน" in outputs(client.calls[1])[0]["error"]


def test_run_python_mixed_with_web_search_gets_a_final_rewrite(monkeypatch, fake_code):
    monkeypatch.setattr(
        agent.llm, "answer_with_web_search",
        lambda q, timeout=None: ("USD = 36.5 บาท", [{"title": "s", "url": "https://fx.example"}],
                                 {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.01}),
    )
    client = FakeClient(
        resp(call("web_search", query="ค่าเงินบาทวันนี้")),
        stream("", call("run_python", task="1000 USD เป็นบาท"), rid="r1"),
        stream("1,000 USD = 36,500 บาท", rid="r2"),
    )

    events = run(client, ci_enabled=True)

    result = events[-1][1]
    assert result.answer == "1,000 USD = 36,500 บาท" and result.used_code
    assert json.loads(client.calls[2]["input"][0]["output"])["files_attached_for_user"] == ["chart.png"]


def test_run_python_capped_per_question(monkeypatch, fake_code):
    monkeypatch.setattr(agent, "AGENT_MAX_CODE_RUNS", 1)
    client = FakeClient(
        resp(call("run_python", 0, task="a"), call("run_python", 1, task="b")),
        stream("สรุป"),
    )

    run(client, ci_enabled=True)

    assert len(fake_code) == 1
    assert "ครบจำนวน" in outputs(client.calls[1])[1]["error"]
    assert "run_python" not in tool_names(client.calls[1])


def test_run_python_ignored_when_not_enabled(fake_code):
    client = FakeClient(resp(call("run_python", task="x")), stream("ตอบ"))
    run(client)
    assert fake_code == []
    assert "ไม่รู้จักเครื่องมือ" in outputs(client.calls[1])[0]["error"]


def test_ci_extends_the_agent_time_budget(monkeypatch, fake_code):
    monkeypatch.setattr(agent, "AGENT_TIMEOUT_S", 5)          # too little on its own
    client = FakeClient(resp(call("run_python", task="t")))
    run(client, ci_enabled=True)
    assert fake_code and fake_code[0].timeout > 100


# ---------------------------------------------------------------------------
# main._agent_turn glue — persistence of generated files, flags, quota
# ---------------------------------------------------------------------------

@pytest.fixture
def glue(monkeypatch):
    saved, audits, attached = [], [], []
    state = NS(code_runs_today=0, session_sheets=[])
    monkeypatch.setattr(agent, "AGENT_V1_MODE", "off")
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", "admin")
    monkeypatch.setattr(main, "use_postgres_auth", lambda: True)
    monkeypatch.setattr(chat_pg, "get_session_history_pg", lambda **kw: [])
    monkeypatch.setattr(chat_pg, "count_web_searches_today_pg", lambda uid: 0)
    monkeypatch.setattr(chat_pg, "count_code_runs_today_pg", lambda uid: state.code_runs_today)
    monkeypatch.setattr(chat_pg, "get_session_spreadsheets_pg", lambda sid, uid: state.session_sheets)
    monkeypatch.setattr(chat_pg, "record_web_search_pg", lambda *a: None)
    monkeypatch.setattr(chat_pg, "ensure_session_pg", lambda **kw: (7, "title"))
    monkeypatch.setattr(chat_pg, "save_chat_message_pg", lambda **kw: saved.append(kw) or 99)
    monkeypatch.setattr(
        chat_pg, "save_attachment_pg",
        lambda **kw: attached.append(kw) or {"id": len(attached), "filename": kw["filename"],
                                             "content_type": kw["content_type"],
                                             "generated": kw.get("generated", False)},
    )
    monkeypatch.setattr(main, "audit_log", lambda action, **kw: audits.append(action))
    return NS(saved=saved, audits=audits, attached=attached, state=state)


ADMIN = {"id": 1, "role": "admin", "username": "a"}


def _turn(monkeypatch, events, seen, user=ADMIN, **kw):
    def fake_run(question, history, **opts):
        seen.append(opts)
        yield from events

    monkeypatch.setattr(agent, "run_agent", fake_run)
    return list(main._agent_turn(user, kw.pop("question", "q"), kw.pop("session_id", None), "normal", None, **kw))


def _code_result():
    return agent.AgentResult(
        answer="ยอดรวม 98,730 บาท",
        usage=[{"model": "gpt-5", "prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 0.001},
               {"model": "gpt-5", "prompt_tokens": 2000, "completion_tokens": 900, "cost_usd": 0.042}],
        files=[ci.CIFile("summary.xlsx", ci.CI_OUTPUT_TYPES[".xlsx"], b"xlsx"),
               ci.CIFile("pareto.png", "image/png", b"png")],
        used_code=True, steps=1,
    )


def test_code_answer_saved_with_files_cost_and_source(monkeypatch, glue):
    seen = []
    out = _turn(monkeypatch, [("status", "agent_coding"), ("delta", "ยอดรวม 98,730 บาท"),
                              ("done", _code_result())], seen)

    kind, payload = out[-1]
    assert kind == "done" and payload["source"] == "code"
    row = glue.saved[0]
    assert row["source"] == "code" and row["usage"]["cost_usd"] == pytest.approx(0.043)
    assert row["usage"]["model_used"] == ci.CI_MODEL
    # generated files: stored inside UPLOAD_DIR, flagged, returned to the client
    assert [a["filename"] for a in glue.attached] == ["summary.xlsx", "pareto.png"]
    assert all(a["generated"] for a in glue.attached)
    for a in glue.attached:
        assert att.validate_upload_path(a["file_path"])           # inside UPLOAD_DIR
        assert a["message_id"] == 99 and a["user_id"] == 1
    assert [a["generated"] for a in payload["attachments"]] == [True, True]
    # CI-only user: web tools off, run_python on
    assert seen[0]["web_enabled"] is False and seen[0]["ci_enabled"] is True


def test_ci_only_standard_route_keeps_legacy_web_steps(monkeypatch, glue):
    out = _turn(monkeypatch, [("standard", {"cost_usd": 0.0005})], [])
    assert out == [("standard", {"usage": {"cost_usd": 0.0005}, "legacy_tools": True})]
    assert glue.saved == []


def test_daily_cap_skips_the_router(monkeypatch, glue):
    glue.state.code_runs_today = ci.CI_DAILY_LIMIT
    seen = []
    out = _turn(monkeypatch, [], seen)
    assert out == [("fallback", None)] and seen == []
    assert "code_limit_hit" in glue.audits


def test_both_flags_off_never_calls_router(monkeypatch, glue):
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", "off")
    assert main._agent_enabled(ADMIN, "normal") is False
    assert main._agent_enabled(ADMIN, "company") is False


def test_regular_user_not_in_trial_gets_nothing(monkeypatch, glue):
    assert main._agent_enabled({"id": 2, "role": "user", "username": "u"}, "normal") is False


def test_uploaded_spreadsheet_goes_to_sandbox_and_is_saved(monkeypatch, glue, tmp_path):
    _, _, size, path = att.save_generated_file(b"wb", "sales.xlsx", "x")
    uploads = [{"filename": "sales.xlsx", "content_type": "application/vnd.ms-excel",
                "size": size, "file_path": path}]
    seen = []
    out = _turn(monkeypatch, [("done", _code_result())], seen, question="", uploads=uploads)

    opts = seen[0]
    assert opts["input_files"] == [("sales.xlsx", str(att.validate_upload_path(path)))]
    assert "sales.xlsx" in opts["file_note"] and opts["web_enabled"] is False
    assert glue.saved[0]["question"] == "[ไฟล์แนบ]"
    # the user's upload + 2 generated files, upload first and not flagged
    assert [(a["filename"], a.get("generated", False)) for a in glue.attached] == [
        ("sales.xlsx", False), ("summary.xlsx", True), ("pareto.png", True)]
    assert len(out[-1][1]["attachments"]) == 3


def test_upload_falls_back_to_file_answer_when_sandbox_fails(monkeypatch, glue):
    _, _, size, path = att.save_generated_file(b"wb", "sales.xlsx", "x")
    uploads = [{"filename": "sales.xlsx", "content_type": "x", "size": size, "file_path": path}]
    failed = agent.AgentResult(answer="ขออภัย รันโค้ดไม่สำเร็จ",
                               tools=[{"tool": "run_python", "arg": "t", "ok": False}])

    out = _turn(monkeypatch, [("delta", "ขออภัย"), ("done", failed)], [], uploads=uploads)

    assert out[-1] == ("fallback", None)
    assert glue.saved == [] and glue.attached == []           # the files path saves them instead
    assert "code_failed_fallback" in glue.audits


def test_follow_up_reuses_session_spreadsheet(monkeypatch, glue):
    _, _, _, path = att.save_generated_file(b"wb", "sales.xlsx", "x")
    glue.state.session_sheets = [{"filename": "sales.xlsx", "file_path": path},
                                 {"filename": "gone.xlsx", "file_path": "missing-file.xlsx"}]
    seen = []
    _turn(monkeypatch, [("standard", {})], seen, question="แล้วเดือนไหนขายดีสุด", session_id=5)

    assert [n for n, _ in seen[0]["input_files"]] == ["sales.xlsx"]   # purged file skipped
    assert "sales.xlsx" in seen[0]["file_note"]


# ---------------------------------------------------------------------------
# Download: generated files only through the owner-checked route
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("viewer,status", [
    ({"id": 1, "role": "user", "username": "owner"}, 200),
    ({"id": 2, "role": "user", "username": "other"}, 403),
])
def test_generated_file_download_is_owner_only(monkeypatch, viewer, status):
    display, ctype, _, path = att.save_generated_file(b"\x89PNG data", "pareto.png", "image/png")
    monkeypatch.setattr(main, "use_postgres_auth", lambda: True)
    monkeypatch.setattr(
        chat_pg, "get_attachment_pg",
        lambda aid: {"user_id": 1, "filename": display, "content_type": ctype, "file_path": path}
        if aid == 5 else None,
    )
    audits = []
    monkeypatch.setattr(main, "audit_log", lambda action, **kw: audits.append(action))
    main.app.dependency_overrides[main.current_user] = lambda: viewer
    try:
        r = TestClient(main.app).get("/attachments/5")
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)

    assert r.status_code == status
    if status == 200:
        assert r.content == b"\x89PNG data"
        assert "pareto.png" in r.headers["content-disposition"]
    else:
        assert "attachment_forbidden" in audits
