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
from tests.test_v4_glue import pipe  # noqa: F401  (a fixture)

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
    ("แปลทั้งหมดเป็นภาษาไทย", True),
    ("แปลหน้า 3 ให้หน่อย", False),             # a part
    ("แปล 2 หน้าแรกให้หน่อย", False),           # QA 2026-11-02: these started a whole-book job
    ("แปลเฉพาะหัวข้อ Safety", False),
    ("แปลบทที่ 4", False),
    ("translate the first 3 pages", False),
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


def _scanned(pages: int, line: str, thai: bool) -> bytes:
    """An image-only PDF (no text layer), like a scanner makes."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(FONT), 34) if thai else ImageFont.load_default(size=34)
    doc = fitz.open()
    for i in range(pages):
        img = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(img)
        for row in range(8):
            draw.text((80, 120 + row * 60), line, font=font, fill="black")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        page = doc.new_page()
        page.insert_image(page.rect, stream=buf.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.skipif(not __import__("shutil").which("tesseract"), reason="needs Tesseract (in the backend image)")
def test_scanned_pdf_language_comes_from_ocr():
    # QA 2026-11-02: a scanned Thai PDF started an English → Thai job
    thai = _saved("t_scan_th.pdf", _scanned(3, "ประกาศบริษัท เรื่องวันหยุดประจำปีของพนักงาน", thai=True))
    eng = _saved("t_scan_en.pdf", _scanned(3, "Check the blade guard before operating the machine.", thai=False))
    assert att.is_long_english_pdf(thai) is False
    assert att.is_long_english_pdf(eng) is True


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
    ([("files", ("manual.pdf", _pdf(4), PDF_CT))], "แปล 2 หน้าแรกให้หน่อย"),
])
def test_other_file_questions_stay_in_chat(monkeypatch, files, question):
    r, calls = _post(monkeypatch, files, question)
    assert r.json()["source"] != "translate_job" and calls.jobs == []


def _docx(text: str) -> bytes:
    import io

    from docx import Document

    d = Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_other_files_sent_along_get_a_chat_answer(monkeypatch):
    # QA 2026-11-02: a short PDF and a Word file sent with a long PDF were dropped
    files = [("files", ("A.pdf", _pdf(4), PDF_CT)), ("files", ("B.pdf", _pdf(1), PDF_CT)),
             ("files", ("D.docx", _docx("Machine safety instructions."), DOCX_CT))]
    r, calls = _post(monkeypatch, files, "แปลทั้งหมดเป็นภาษาไทย")
    body = r.json()
    assert body["source"] == "translate_job" and calls.jobs == [("A.pdf", 1)]
    assert [name for name, _ in calls.answered[0][2]] == ["B.pdf", "D.docx"]   # the others → a chat answer
    assert "<!-- translate-job:000000000001 -->" in body["answer"] and "ตอบจากไฟล์" in body["answer"]
    assert len(calls.attached) == 3                                         # every upload stays with it


# ── a finished job: its Word/PDF goes on the chat message ───────────────────

def test_a_finished_job_attaches_its_files_to_the_message(monkeypatch, tmp_path):
    import chat_pg

    out_docx = tmp_path / "out.docx"
    out_docx.write_bytes(_docx("คำแปลภาษาไทยของคู่มือ"))
    out_pdf = tmp_path / "out.pdf"
    out_pdf.write_bytes(_pdf(1))
    attached = []
    monkeypatch.setattr(chat_pg, "save_attachment_pg", lambda **kw: attached.append(kw) or {"id": 1})

    def fake_translate(pdf_path, out_dir, base, **kw):
        return {"docx": str(out_docx), "pdf": str(out_pdf), "review": None, "n_flagged": 0,
                "cost_usd": 0.1, "n_pages": 4, "tokens_in": 1, "tokens_out": 1}

    monkeypatch.setattr(doctr.tm, "translate_pdf", fake_translate)
    monkeypatch.setattr(doctr, "_db_on", lambda: False)
    doctr._jobs["abc"] = {"id": "abc", "user_id": 1, "filename": "GL44 manual.pdf", "status": "queued", "total": 4}
    doctr.on_done("abc", lambda job: main._attach_translation(1, 77, job))
    doctr._run("abc", "/x.pdf", str(tmp_path), "GL44 manual")
    assert [a["filename"] for a in attached] == ["GL44 manual_แปลไทย.docx", "GL44 manual_แปลไทย.pdf"]
    assert all(a["message_id"] == 77 and a["generated"] for a in attached)
    assert "คำแปลภาษาไทย" in attached[0]["extracted_text"]    # for "สรุปฉบับแปล" later
    # registered after it's done → runs right away
    later = []
    doctr.on_done("abc", later.append)
    assert later and later[0]["status"] == "done"


# ── "สรุปฉบับแปล" in the same chat: from the translated text ───────────────

def _followup(monkeypatch, pipe, translations, question="สรุปฉบับแปลให้หน่อย 5 ข้อ"):
    import chat_pg

    from tests.test_v4_glue import stream

    answered = []
    monkeypatch.setattr(chat_pg, "get_session_translations_pg", lambda sid, uid: translations)
    monkeypatch.setattr(main, "_answer_files", lambda q, imgs, texts, hist: answered.append(texts) or ("สรุป 5 ข้อ", {}))
    return stream(question), answered


def test_followup_answers_from_the_translation(monkeypatch, pipe):  # noqa: F811
    events, answered = _followup(monkeypatch, pipe, {"asked": 1, "files": [
        {"filename": "A_แปลไทย.docx", "file_path": "x", "extracted_text": "เนื้อหาฉบับแปล"}]})
    assert [e["type"] for e in events] == ["delta", "done"] and events[0]["v"] == "สรุป 5 ข้อ"
    assert answered == [[("A_แปลไทย.docx", "เนื้อหาฉบับแปล")]]
    assert pipe.saved[0]["source"] == "files"


def test_followup_while_still_translating_says_so(monkeypatch, pipe):  # noqa: F811
    events, answered = _followup(monkeypatch, pipe, {"asked": 1, "files": []})
    assert "ยังไม่เสร็จ" in events[0]["v"] and answered == []


def test_no_translation_in_this_chat_runs_the_normal_answer(monkeypatch, pipe):  # noqa: F811
    events, answered = _followup(monkeypatch, pipe, {"asked": 0, "files": []})
    assert events[-1]["source"] == "llm" and answered == []
