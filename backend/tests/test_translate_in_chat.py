"""
"แปลไฟล์นี้" in the chat: a long English PDF starts the page-by-page translator
(doc_translation, the แปลเอกสาร engine) and the answer carries a
`<!-- translate-job:<id> -->` marker for the V5 card; anything else — part of
a file, a word, another language, a short or Thai PDF — stays a chat answer.
Offline (the job is faked).
"""

from pathlib import Path
from types import SimpleNamespace as NS

import fitz
import pytest
from starlette.testclient import TestClient

import attachments as att
import code_interpreter as ci
import doc_engine as de
import doc_translation as doctr
import main
from tests.test_code_interpreter import _gate

ADMIN = {"id": 1, "role": "admin", "username": "a"}
FONT = Path(__file__).resolve().parent.parent / "fonts" / "Sarabun-Regular.ttf"
EN = "Before operating the guillotine, check the blade guard and the emergency stop button. "
TH = "ก่อนใช้งานเครื่องตัด ให้ตรวจการ์ดใบมีดและปุ่มหยุดฉุกเฉินทุกครั้ง "


def _pdf(pages: int, text: str = EN, thai: bool = False) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        kw = {"fontfile": str(FONT), "fontname": "sarabun"} if thai else {}
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), f"Page {i + 1}. " + text * 6, fontsize=10, **kw)
    data = doc.tobytes()
    doc.close()
    return data


def _saved(name: str, data: bytes) -> str:
    att.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    p = att.UPLOAD_DIR / name
    p.write_bytes(data)
    return str(p)


@pytest.mark.parametrize("q,want", [
    ("แปลไฟล์นี้", True),
    ("ช่วยแปลคู่มือนี้เป็นภาษาไทยให้หน่อย", True),
    ("translate this manual", True),
    ("แปลหน้า 3 ให้หน่อย", False),             # a part
    ("สรุปแล้วแปลเป็นไทย", False),              # a summary
    ("คำว่า interlock แปลว่าอะไร", False),       # a word
    ("แปลเป็นภาษาอังกฤษ", False),               # the translator is English → Thai
    ("translate it into Japanese", False),
    ("สรุปไฟล์นี้", False),
])
def test_whole_translation_intent(q, want):
    assert att.wants_whole_translation(q) is want


def test_long_english_pdf():
    assert att.is_long_english_pdf(_saved("t_long_en.pdf", _pdf(4))) is True
    assert att.is_long_english_pdf(_saved("t_short_en.pdf", _pdf(1))) is False   # a chat answer fits
    assert att.is_long_english_pdf(_saved("t_long_th.pdf", _pdf(4, TH, thai=True))) is False


def _post(monkeypatch, files, question):
    calls = NS(turns=[], scope=[], charged=[], audits=[], saved=[], attached=[], answered=[], jobs=[])
    _gate(monkeypatch, calls, keyword=None, flagged=None, decision="denied")
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_MODE", "admin")   # the PDF must not go to the Document Engine
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", "off")
    monkeypatch.setattr(main, "_agent_turn", lambda *a, **kw: calls.turns.append(kw) or iter(()))
    monkeypatch.setattr(main, "_answer_files", lambda *a: calls.answered.append(a) or ("ตอบจากไฟล์", {}))

    def fake_start(path, filename, user_id):
        calls.jobs.append((filename, user_id))
        return {"id": f"{len(calls.jobs):012x}", "filename": filename, "total": 4, "translated_pages": 4,
                "exceeds_cap": False, "max_pages": 150}

    monkeypatch.setattr(doctr, "start_job", fake_start)
    import chat_pg
    monkeypatch.setattr(chat_pg, "get_session_history_pg", lambda **kw: [])
    main.app.dependency_overrides[main.current_user] = lambda: ADMIN
    try:
        return TestClient(main.app).post("/chat", data={"message": question}, files=files), calls
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)


PDF_CT = "application/pdf"


def test_translate_a_long_pdf_starts_the_job(monkeypatch):
    r, calls = _post(monkeypatch, [("files", ("manual.pdf", _pdf(4), PDF_CT))], "แปลไฟล์นี้")
    body = r.json()
    assert body["source"] == "translate_job"
    assert calls.jobs == [("manual.pdf", 1)]
    assert "<!-- translate-job:000000000001 -->" in body["answer"] and "manual.pdf" in body["answer"]
    assert calls.saved[0]["source"] == "translate_job" and len(calls.attached) == 1   # the upload stays with it
    assert calls.answered == [] and calls.turns == []           # no chat answer, no Document Engine
    assert ("translate_started", {"job_id": "000000000001", "filename": "manual.pdf", "pages": 4, "via": "chat"}) \
        in calls.audits


@pytest.mark.parametrize("files,question", [
    ([("files", ("manual.pdf", _pdf(1), PDF_CT))], "แปลไฟล์นี้"),          # short → chat translation
    ([("files", ("manual.pdf", _pdf(4), PDF_CT))], "สรุปไฟล์นี้"),          # not a translation
    ([("files", ("manual.pdf", _pdf(4), PDF_CT))], "แปลหน้า 2 ให้หน่อย"),   # part of it
])
def test_other_file_questions_stay_in_chat(monkeypatch, files, question):
    r, calls = _post(monkeypatch, files, question)
    assert r.json()["source"] != "translate_job" and calls.jobs == []
