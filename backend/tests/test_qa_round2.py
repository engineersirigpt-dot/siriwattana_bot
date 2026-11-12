"""
QA round 2 (2026-11-11) — the board's failing items. Offline.

C-02/C-03  a reload, closed tab or dropped connection is not หยุด: the answer
           is finished in the background and saved whole; only /chat/stop
           (หยุด) cuts it short; the reopened chat shows it as pending.
C-01       deleting a chat while its answer is still being written saves
           nothing (the chat doesn't come back).
C-07       an over-long paste is refused with a Thai hint.
C-10       "คำแปลของคำว่า …" is an ordinary question, not a translation follow-up.
F-01/F-04  .doc/.ppt are read (LibreOffice); HEIC and other refusals are Thai.
F-03       a password-protected file is refused with a Thai explanation.
T-02       a PDF with Thai headings over English text still counts as English.
A-01/A-02  a disabled account stops at once; /ui/config returns the current role.
A-04       /auth/config gives the SSO error page the MI portal link.
O-02       the 429 message is Thai.
"""

import inspect
import io
import json
import threading
import time

import anyio
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.testclient import TestClient

import attachments as att
import auth
import main
from tests.test_v4_glue import ADMIN, pipe  # noqa: F401  (pipe is a fixture)

CID = "client-0001-abcdef"


@pytest.fixture(autouse=True)
def _no_db_marker(monkeypatch):
    monkeypatch.setattr(main, "_session_last_mid", lambda sid: 0)
    main._TURNS.clear()
    yield
    main._TURNS.clear()


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ── C-02: the browser goes away (no หยุด) → the answer is finished and saved ──

def _drop_after(question, n, client_id=CID, session_id=5):
    req = Request({"type": "http", "method": "POST", "path": "/chat/stream", "headers": [], "client": ("t", 1)})
    handler = inspect.unwrap(main.chat_stream)

    async def run():
        resp = await handler(request=req, message=question, session_id=session_id, mode="normal",
                             client_id=client_id, user=ADMIN)
        it = resp.body_iterator
        got = [json.loads(await it.__anext__()) for _ in range(n)]
        await it.aclose()   # reload / closed tab
        return got

    return anyio.run(run)


def test_reload_mid_answer_finishes_and_saves_the_whole_answer(pipe):  # noqa: F811
    got = _drop_after("วิธีเบิกค่าเดินทาง", 2)          # session + the first delta, then the tab goes
    assert [e["type"] for e in got] == ["session", "delta"]
    assert _wait(lambda: CID not in main._TURNS)          # finished in the background
    assert len(pipe.saved) == 1
    assert main.STOPPED_NOTE not in pipe.saved[0]["answer"]   # the whole answer, not "หยุดแล้ว"
    assert "chat_stream_stopped" not in pipe.audits


def test_without_client_id_going_away_is_still_stop(pipe):  # noqa: F811
    _drop_after("วิธีเบิกค่าเดินทาง", 2, client_id=None)   # a page cached from before this change
    assert len(pipe.saved) == 1 and pipe.saved[0]["answer"].endswith(main.STOPPED_NOTE)


def test_stop_button_while_connected_cuts_it_short(pipe):  # noqa: F811
    req = Request({"type": "http", "method": "POST", "path": "/chat/stream", "headers": [], "client": ("t", 1)})

    async def run():
        resp = await inspect.unwrap(main.chat_stream)(request=req, message="วิธีเบิกค่าเดินทาง", session_id=5,
                                                      mode="normal", client_id=CID, user=ADMIN)
        it = resp.body_iterator
        got = [json.loads(await it.__anext__()) for _ in range(2)]   # session, first delta
        assert main.chat_stop(main.ChatStopIn(client_id=CID), user=ADMIN) == {"ok": True}
        async for chunk in it:                                      # at most one more chunk, then it ends
            got.append(json.loads(chunk))
        return got

    got = anyio.run(run)
    assert all(e["type"] != "done" for e in got)
    assert len(pipe.saved) == 1 and pipe.saved[0]["answer"].endswith(main.STOPPED_NOTE)
    assert CID not in main._TURNS


def test_stop_needs_the_owner():
    stopped = threading.Event()
    main._turn_start(CID, ADMIN, "q", stopped, {"sid": 5})
    assert main.chat_stop(main.ChatStopIn(client_id=CID), user={"id": 99, "role": "user"}) == {"ok": False}
    assert not stopped.is_set()
    assert main.chat_stop(main.ChatStopIn(client_id="nope-nope-nope"), user=ADMIN) == {"ok": False}


def _slow_gen(seen, n=30, delay=0.03):
    try:
        for i in range(n):
            time.sleep(delay)
            yield f"{i}\n"
        seen["finished"] = True
    except GeneratorExit:
        seen["closed"] = True
        raise


def _consume_then_drop(it, turn, keep=2):
    async def run():
        got = []
        with anyio.CancelScope() as scope:
            async for chunk in main._close_on_disconnect(it, threading.Event(), turn):
                got.append(chunk)
                if len(got) == keep:
                    scope.cancel()        # the browser went away
        return got

    return anyio.run(run)


def test_background_finish_runs_the_generator_to_its_end():
    seen = {}
    turn = main._turn_start(CID, ADMIN, "q", threading.Event(), {"sid": 5})
    assert _consume_then_drop(_slow_gen(seen), turn) == ["0\n", "1\n"]
    assert _wait(lambda: seen.get("finished"))
    assert "closed" not in seen and CID not in main._TURNS


def test_stop_after_the_browser_left_still_stops_it():
    seen = {}
    turn = main._turn_start(CID, ADMIN, "q", threading.Event(), {"sid": 5})
    _consume_then_drop(_slow_gen(seen), turn)
    main._turn_stop(turn)                                   # หยุด from the reopened page
    assert _wait(lambda: seen.get("closed"))
    assert "finished" not in seen and CID not in main._TURNS


# ── the reopened chat shows the answer still being written ──────────────────

def test_pending_turn_until_the_answer_is_saved():
    live = {"sid": 5}
    main._turn_start(CID, ADMIN, "คำถามยาว", threading.Event(), live)
    main._TURNS[CID]["after_mid"] = 40
    older = [{"id": 39}, {"id": 40}]
    assert main._pending_turn(ADMIN["id"], 5, older) == {"id": CID, "question": "คำถามยาว"}
    assert main._pending_turn(ADMIN["id"], 6, older) is None          # another chat
    assert main._pending_turn(2, 5, older) is None                    # another user
    assert main._pending_turn(ADMIN["id"], 5, older + [{"id": 41}]) is None   # saved already
    main._TURNS[CID]["t0"] -= main._TURN_MAX_AGE_S + 1                # never started → forgotten
    assert main._pending_turn(ADMIN["id"], 5, older) is None and CID not in main._TURNS


# ── C-01: delete the chat while its answer is written → nothing saved ───────

def test_deleting_the_chat_stops_and_discards_its_answer(pipe):  # noqa: F811
    stopped = threading.Event()
    live = {"sid": 5, "source": "llm", "kid": None, "parts": ["ครึ่ง", "คำตอบ"]}
    turn = main._turn_start(CID, ADMIN, "q", stopped, live)
    main._stop_session_turns(ADMIN["id"], 5)
    assert stopped.is_set() and turn["user_stop"] and live["discard"]
    req = Request({"type": "http", "method": "POST", "path": "/chat/stream", "headers": [], "client": ("t", 1)})
    main._save_stopped(ADMIN, "q", "normal", req, live)
    assert pipe.saved == []


# ── C-07: an over-long paste ────────────────────────────────────────────────

def test_overlong_message_gets_a_thai_hint(monkeypatch):
    monkeypatch.setattr(main, "MAX_MESSAGE_CHARS", 50)
    with pytest.raises(HTTPException) as e:
        main._check_message_length("ก" * 51)
    assert e.value.status_code == 400 and "แนบไฟล์" in e.value.detail
    main._check_message_length("ก" * 50)


# ── C-10: "คำแปล" alone is not a follow-up about the translated file ────────

@pytest.mark.parametrize("q", [
    "คำแปลของคำว่า interlock คืออะไร",
    "interlock แปลว่าอะไร",
    "คำนี้แปลแล้วหมายถึงอะไร",
    "มีความหมายที่แปลว่าอะไร",
    "what is the translation of interlock",
])
def test_word_meaning_questions_are_not_translation_followups(q):
    assert not main._TRANSLATION_FOLLOWUP_RE.search(q)


@pytest.mark.parametrize("q", [
    "สรุปฉบับแปลให้หน่อย",
    "ในไฟล์ที่แปลไว้ หน้า 2 พูดถึงอะไร",
    "คำแปลหน้า 3 ว่าอย่างไร",
    "เอกสารแปลมีกี่หัวข้อ",
    "summarize the translated file",
])
def test_translated_file_questions_still_are(q):
    assert main._TRANSLATION_FOLLOWUP_RE.search(q)


# ── F-01 / F-04 / F-03: files ───────────────────────────────────────────────

def test_legacy_office_files_are_accepted():
    assert att._is_allowed("application/msword", "a.doc")
    assert att._is_allowed("", "slides.ppt")
    assert att.legacy_office_target("application/octet-stream", "a.DOC") == ".docx"
    assert att.legacy_office_target("application/vnd.ms-powerpoint", "x") == ".pptx"
    assert att.legacy_office_target("application/pdf", "a.pdf") is None


def test_unsupported_messages_are_plain_thai():
    heic = att.unsupported_message("IMG_0001.HEIC", "image/heic")
    assert "HEIC" in heic and "JPG" in heic and "image/" not in heic
    other = att.unsupported_message("a.exe", "application/x-msdownload")
    assert "ยังไม่รองรับ" in other and "application/" not in other


def test_legacy_office_without_libreoffice_says_what_to_do(monkeypatch, tmp_path):
    import translate_manual

    monkeypatch.setattr(att, "UPLOAD_DIR", tmp_path)
    (tmp_path / "a.doc").write_bytes(b"\xd0\xcf\x11\xe0 not really")
    monkeypatch.setattr(translate_manual, "_find_soffice", lambda: None)
    text = att.extract_any_text(str(tmp_path / "a.doc"), "application/msword", "a.doc")
    assert ".docx" in text and "ไม่สำเร็จ" in text


def test_legacy_doc_is_read_through_libreoffice(monkeypatch, tmp_path):
    import shutil
    import subprocess

    import translate_manual

    soffice = translate_manual._find_soffice()
    if not soffice:
        pytest.skip("LibreOffice not installed here (it is in the backend image)")
    from docx import Document

    monkeypatch.setattr(att, "UPLOAD_DIR", tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    d = Document()
    d.add_paragraph("ใบสั่งงานพิมพ์ เลขที่ 12345")
    d.save(src / "old.docx")
    subprocess.run([soffice, "--headless", "--convert-to", "doc", "--outdir", str(tmp_path), str(src / "old.docx")],
                   check=True, capture_output=True, timeout=120)
    shutil.rmtree(src)
    text = att.extract_any_text(str(tmp_path / "old.doc"), "application/msword", "old.doc")
    assert "12345" in text
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["old.doc"]   # the converted copy is cleaned up


def test_password_protected_pdf_is_refused_in_thai(tmp_path):
    import fitz

    locked, plain = tmp_path / "locked.pdf", tmp_path / "plain.pdf"
    for path, kw in ((locked, {"encryption": fitz.PDF_ENCRYPT_AES_256, "user_pw": "1234", "owner_pw": "x"}),
                     (plain, {})):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "hello")
        doc.save(str(path), **kw)
        doc.close()
    msg = att.locked_file_message(str(locked), "สัญญา.pdf", "application/pdf")
    assert msg and "รหัสผ่าน" in msg and "สัญญา.pdf" in msg
    assert att.locked_file_message(str(plain), "plain.pdf", "application/pdf") is None


def test_password_protected_office_file_is_refused(tmp_path):
    f = tmp_path / "budget.xlsx"
    f.write_bytes(att._OLE_MAGIC + b"\0" * 100)       # what Office writes for an encrypted .xlsx
    assert "รหัสผ่าน" in att.locked_file_message(str(f), "budget.xlsx", "")
    ok = tmp_path / "ok.xlsx"
    ok.write_bytes(b"PK\x03\x04rest")
    assert att.locked_file_message(str(ok), "ok.xlsx", "") is None


def test_upload_of_a_locked_pdf_is_refused_and_removed(monkeypatch, tmp_path):
    import fitz
    from fastapi import UploadFile

    monkeypatch.setattr(att, "UPLOAD_DIR", tmp_path)
    buf = io.BytesIO()
    doc = fitz.open()
    doc.new_page()
    doc.save(buf, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="1", owner_pw="2")
    up = UploadFile(file=io.BytesIO(buf.getvalue()), filename="x.pdf", headers={"content-type": "application/pdf"})
    with pytest.raises(HTTPException) as e:
        anyio.run(att.save_upload, up)
    assert e.value.status_code == 400 and "รหัสผ่าน" in e.value.detail
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []   # nothing left behind


# ── T-02: Thai headings over English text is still an English document ─────

def test_mixed_thai_english_pdf_counts_as_english():
    mixed = "คู่มือ " * 20 + "Press operator manual " * 10     # about half Thai letters
    assert not att._mostly_thai(mixed)
    assert att._mostly_thai("คู่มือการใช้งานเครื่องพิมพ์ ฉบับภาษาไทย " * 10 + "Heidelberg")


# ── A-01 / A-02 / A-04 / O-02 ───────────────────────────────────────────────

def _user_req():
    return Request({"type": "http", "method": "GET", "path": "/auth/me", "headers": [], "client": ("t", 1)})


def test_disabled_account_stops_at_once(monkeypatch):
    import auth_pg

    monkeypatch.setattr(auth, "use_postgres_auth", lambda: True)
    monkeypatch.setattr(auth, "audit_log", lambda *a, **kw: None)
    token = auth.create_token(7, "2133557", "user")
    row = {"id": 7, "username": "2133557", "role": "user", "is_disabled": False}
    monkeypatch.setattr(auth_pg, "get_user_by_id_pg", lambda uid: dict(row))
    assert auth.current_user(_user_req(), token) == {"id": 7, "username": "2133557", "role": "user"}
    row["is_disabled"] = True                                  # an admin disables the account
    with pytest.raises(HTTPException) as e:
        auth.current_user(_user_req(), token)
    assert e.value.status_code == 401 and "ระงับ" in e.value.detail


def test_ui_config_returns_the_current_role():
    main.app.dependency_overrides[main.current_user] = lambda: {"id": 3, "username": "x", "role": "user"}
    try:
        r = TestClient(main.app).get("/ui/config")
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)
    assert r.json()["role"] == "user"


def test_auth_config_is_public(monkeypatch):
    monkeypatch.setattr(main, "MI_PORTAL_URL", "http://mi.example/portal")
    assert TestClient(main.app).get("/auth/config").json() == {"mi_portal_url": "http://mi.example/portal"}
    monkeypatch.setattr(main, "MI_PORTAL_URL", "")
    assert TestClient(main.app).get("/auth/config").json() == {"mi_portal_url": None}


def test_rate_limit_message_is_thai():
    from slowapi.errors import RateLimitExceeded

    class Lim:
        error_message = None
        limit = "30/minute"

    req = Request({"type": "http", "method": "POST", "path": "/chat/stream", "headers": [], "client": ("t", 1)})
    resp = anyio.run(main.rate_limit_handler, req, RateLimitExceeded(Lim()))
    assert resp.status_code == 429
    assert "รอสักครู่" in json.loads(resp.body)["detail"]


# ── /chat (files): a browser that goes away no longer stops it — only หยุด ──

def _files_chat(monkeypatch, data):
    from types import SimpleNamespace as NS

    import chat_pg
    import code_interpreter as ci
    import doc_engine as de
    import usage_log
    from tests.test_code_interpreter import _gate

    calls = NS(turns=[], scope=[], charged=[], audits=[], saved=[], attached=[], answered=[], logged=[])
    _gate(monkeypatch, calls, keyword=None, flagged=None, decision="denied")
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_MODE", "off")
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", "off")
    monkeypatch.setattr(chat_pg, "get_session_history_pg", lambda **kw: [])
    monkeypatch.setattr(usage_log, "log_usage_pg", lambda *a, **kw: calls.logged.append(a))
    monkeypatch.setattr(main, "_answer_files", lambda *a: calls.answered.append(a) or (
        "ตอบ", {"model": "gpt-4.1", "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.001}))

    async def gone_at_once(request, stopped):   # the browser drops the request straight away
        stopped.set()

    monkeypatch.setattr(main, "_watch_disconnect", gone_at_once)
    main.app.dependency_overrides[main.current_user] = lambda: ADMIN
    try:
        r = TestClient(main.app).post("/chat", data=data, files=[("files", ("a.txt", b"hello", "text/plain"))])
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)
    return r, calls


def test_files_chat_with_client_id_is_saved_even_if_the_browser_leaves(monkeypatch):
    r, calls = _files_chat(monkeypatch, {"message": "สรุปไฟล์นี้", "client_id": CID})
    assert r.json()["source"] != "stopped" and calls.saved      # finished and saved
    assert CID not in main._TURNS


def test_files_chat_without_client_id_keeps_the_old_rule(monkeypatch):
    r, calls = _files_chat(monkeypatch, {"message": "สรุปไฟล์นี้"})
    assert r.json()["source"] == "stopped" and calls.saved == []


# ── C-09: "จำไว้ว่า…" with memory off → the model is told there is no memory ──

def test_remember_request_gets_the_no_memory_note(monkeypatch):
    import memory

    monkeypatch.setattr(memory, "MEMORY_MODE", "off")
    hist = [{"role": "user", "content": "ก่อน"}]
    out = main._with_memory_note(ADMIN, "จำไว้ว่า ฉันอยู่ฝ่ายผลิต", hist)
    assert out[:-1] == hist and out[-1] == {"role": "system", "content": main.NO_MEMORY_NOTE}
    assert main._with_memory_note(ADMIN, "Please remember I work nights", hist)[-1]["role"] == "system"
    assert main._with_memory_note(ADMIN, "วิธีเบิกค่าเดินทาง", hist) == hist        # other questions untouched
    monkeypatch.setattr(memory, "MEMORY_MODE", "all")                              # memory on: it really remembers
    assert main._with_memory_note(ADMIN, "จำไว้ว่า ฉันอยู่ฝ่ายผลิต", hist) == hist


# ── C-07: a long paste no longer breaks the knowledge search (8,192-token cap) ──

def test_embedding_input_is_capped(monkeypatch):
    import rag
    import rag_pg

    sent = []

    class Emb:
        def create(self, model, input, dimensions):
            sent.append(input)
            from types import SimpleNamespace as NS
            return NS(data=[NS(embedding=[0.0])])

    monkeypatch.setattr(rag, "_use_postgres", lambda: True)
    monkeypatch.setattr(rag_pg, "_get_cached_embedding_pg", lambda t: None)
    monkeypatch.setattr(rag_pg, "_save_cached_embedding_pg", lambda t, v: None)
    monkeypatch.setattr(rag_pg, "audit_log", lambda *a, **kw: None)
    monkeypatch.setattr(rag_pg, "_client", lambda: type("C", (), {"embeddings": Emb()})())
    rag.embed("ก" * 70_000)
    assert len(sent[0]) == rag.EMBED_MAX_CHARS


# ── I-03: another brand's logo → told, not silently left out ────────────────

def _enhancer_says(monkeypatch, text):
    from types import SimpleNamespace as NS

    import llm

    reply = NS(choices=[NS(message=NS(content=text))])
    client = NS(chat=NS(completions=NS(create=lambda **kw: reply)))
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "IMAGE_PROMPT_ENHANCE", True)
    return llm


def test_trademark_request_is_refused_before_any_image(monkeypatch):
    llm = _enhancer_says(monkeypatch, "REFUSE_TRADEMARK")
    with pytest.raises(llm.ImageRequestRefused):
        llm._enhance_image_prompt("วาดโลโก้ Coca-Cola บนกล่องลูกฟูก")
    llm = _enhancer_says(monkeypatch, "A brown corrugated box, studio light")
    assert llm._enhance_image_prompt("กล่องลูกฟูก") == "A brown corrugated box, studio light"


def test_trademark_refusal_message_is_thai_and_costs_no_quota(monkeypatch, pipe):  # noqa: F811
    import chat_pg
    import llm

    quota = []
    monkeypatch.setattr(main, "_is_image_request", lambda q: True)
    monkeypatch.setattr(chat_pg, "count_images_today_pg", lambda uid: 0)
    monkeypatch.setattr(chat_pg, "record_image_generation_pg", lambda *a: quota.append(a))

    def refuse(question, logo_path=None):
        raise llm.ImageRequestRefused()

    monkeypatch.setattr(main, "generate_image", refuse)
    from tests.test_v4_glue import stream as stream_v4

    events = stream_v4("วาดโลโก้ Coca-Cola บนกล่องลูกฟูก")
    text = "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert "แบรนด์อื่น" in text and "โลโก้ตามไฟล์" in text
    assert events[-1]["source"] == "image_blocked" and quota == []


# ── O-05: OpenAI down → the user is told what happened, in Thai ─────────────

def test_answer_error_text_names_the_cause():
    import httpx as hx
    import openai

    req = hx.Request("POST", "https://api.openai.com/v1/chat/completions")
    assert "เชื่อมต่อระบบ AI" in main._answer_error_text(openai.APIConnectionError(request=req))
    assert "เชื่อมต่อระบบ AI" in main._answer_error_text(openai.APITimeoutError(request=req))
    resp = hx.Response(429, request=req)
    assert "โควตา" in main._answer_error_text(openai.RateLimitError("x", response=resp, body=None))
    assert "ลองใหม่" in main._answer_error_text(ValueError("x"))


def test_legacy_office_without_a_work_dir_says_what_to_do(monkeypatch, tmp_path):
    import tempfile

    import translate_manual

    monkeypatch.setattr(att, "UPLOAD_DIR", tmp_path)
    (tmp_path / "a.ppt").write_bytes(b"\xd0\xcf\x11\xe0")
    monkeypatch.setattr(translate_manual, "_find_soffice", lambda: "/usr/bin/soffice")

    def no_dir(*a, **kw):
        raise OSError("read-only")

    monkeypatch.setattr(tempfile, "mkdtemp", no_dir)
    text = att.extract_any_text(str(tmp_path / "a.ppt"), "application/vnd.ms-powerpoint", "a.ppt")
    assert ".pptx" in text and "ไม่สำเร็จ" in text
