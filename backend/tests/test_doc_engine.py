"""
Tests for the V3 Document Engine: the server side (doc_engine.py — PDF reading
with OCR, external-link guard, checking/finishing/previewing returned files,
the sandbox run with a fake OpenAI client), the agent's make_document tool and
the chat glue (flag, quota, files, routing). Offline; LibreOffice/Tesseract
parts skip when the tool is not installed (they run in the backend image).
"""

import io
import json
import shutil
import zipfile
from types import SimpleNamespace as NS

import pytest
from PIL import Image, ImageDraw, ImageFont
from starlette.testclient import TestClient

import agent
import attachments as att
import chat_pg
import code_interpreter as ci
import doc_engine as de
import main
import sirigpt_docs as sd
from tests.test_agent import FakeClient, call, outputs, resp, stream, tool_names
from tests.test_code_interpreter import ADMIN, FakeCI, _gate, glue, run_loader  # noqa: F401 — fixture

needs_soffice = pytest.mark.skipif(not de._soffice(), reason="LibreOffice not installed")
needs_tesseract = pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract not installed")
FONT = str(de._FONTS / "Sarabun-Regular.ttf")


def _docx_bytes(with_table=True, text="รายงานทดสอบภาษาไทย") -> bytes:
    doc = sd.new_docx()
    sd.heading(doc, text, 1)
    sd.para(doc, "เนื้อหา **สำคัญ**")
    if with_table:
        sd.table(doc, [["เดือน", "ยอด"], ["ม.ค.", 41650]])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── flag ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,user,expected", [
    ("off", {"role": "admin"}, False), ("admin", {"role": "admin"}, True),
    ("admin", {"role": "user", "username": "u"}, False), ("admin", {"role": "user", "username": "doc1"}, True),
    ("all", {"role": "user", "username": "u"}, True),
])
def test_feature_flag(monkeypatch, mode, user, expected):
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_MODE", mode)
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_USERS", {"doc1"})
    assert de.doc_enabled_for(user) is expected


def test_doc_files_vs_pictures():
    assert de.is_doc_file("a.DOCX") and de.is_doc_file("b.pdf") and de.is_doc_file("c.csv")
    assert not de.is_doc_file("logo.png") and de.is_doc_input("logo.png")    # travels along only
    assert not de.is_doc_input("x.exe")


# ── reading PDFs ────────────────────────────────────────────────────────────

def _pdf_with_table(path):
    import fitz

    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 80), "Quarterly report page one", fontsize=14)
    p2 = doc.new_page()
    x0, y0, cw, rh = 72, 100, 150, 24
    rows = [["Month", "Sales"], ["Jan", "41650"], ["Feb", "33400"]]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            rect = fitz.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            p2.draw_rect(rect, color=(0, 0, 0), width=1)
            p2.insert_text((rect.x0 + 6, rect.y1 - 7), val, fontsize=11)
    doc.save(path)


def test_pdf_text_and_tables_become_markdown(tmp_path):
    _pdf_with_table(str(tmp_path / "r.pdf"))
    md = de.pdf_to_markdown(str(tmp_path / "r.pdf"))
    assert "## หน้า 1" in md and "Quarterly report page one" in md
    assert "**ตาราง 1 (หน้า 2)**" in md and "| Month | Sales |" in md and "| Jan | 41650 |" in md


def _scanned_thai_pdf(path, lines=("ยอดขายประจำเดือน", "รายงานฝ่ายผลิต")):
    import fitz

    img = Image.new("RGB", (1600, 500), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, 72)
    for i, line in enumerate(lines):
        d.text((60, 60 + i * 140), line, fill="black", font=f)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(path)


@needs_tesseract
def test_scanned_pdf_is_read_with_thai_ocr(tmp_path):
    _scanned_thai_pdf(str(tmp_path / "scan.pdf"))
    md = de.pdf_to_markdown(str(tmp_path / "scan.pdf"))
    assert "(OCR" in md and "ยอดขาย" in md.replace(" ", "")


def test_ocr_page_cap_is_reported(tmp_path):
    _scanned_thai_pdf(str(tmp_path / "scan.pdf"))
    md = de.pdf_to_markdown(str(tmp_path / "scan.pdf"), ocr_pages=0)
    assert "เกินจำนวนหน้าที่อ่าน OCR ได้" in md


def test_pdf_inputs_get_a_markdown_companion(tmp_path):
    _pdf_with_table(str(tmp_path / "r.pdf"))
    out = de._prepare_inputs([("r.pdf", str(tmp_path / "r.pdf"))], tmp_path)
    assert [n for n, _ in out] == ["r.pdf", "r.เนื้อหา.md"]
    assert (tmp_path / "r.เนื้อหา.md").read_text(encoding="utf-8").startswith("# เนื้อหาจาก r.pdf")


# ── external links are never fetched by LibreOffice on our server ───────────

def _with_rel(data: bytes, rel: str) -> bytes:
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            b = src.read(n)
            if n == "word/_rels/document.xml.rels":
                b = b.replace(b"</Relationships>", rel.encode() + b"</Relationships>")
            z.writestr(n, b)
    return out.getvalue()


def test_external_content_detection(tmp_path):
    plain = tmp_path / "plain.docx"
    plain.write_bytes(_docx_bytes())
    assert de.has_external_content(str(plain)) is False
    link = tmp_path / "link.docx"
    link.write_bytes(_with_rel(_docx_bytes(), '<Relationship Id="rX" Type="http://schemas.openxmlformats.org/'
                                             'officeDocument/2006/relationships/hyperlink" '
                                             'Target="https://example.com" TargetMode="External"/>'))
    assert de.has_external_content(str(link)) is False                 # a hyperlink is fine
    remote = tmp_path / "remote.docx"
    remote.write_bytes(_with_rel(_docx_bytes(), '<Relationship Id="rY" Type="http://schemas.openxmlformats.org/'
                                               'officeDocument/2006/relationships/image" '
                                               'Target="http://10.0.0.5/x.png" TargetMode="External"/>'))
    assert de.has_external_content(str(remote)) is True
    assert de.to_pdf(str(remote), str(tmp_path / "r")) is None          # never rendered
    (tmp_path / "bad.docx").write_bytes(b"nope")
    assert de.has_external_content(str(tmp_path / "bad.docx")) is True


# ── checking, finishing and previewing what the sandbox returned ────────────

DOCX_CT = de.DOC_TYPES[".docx"]


def test_good_docx_is_delivered_with_embedded_thai_font(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "to_pdf", lambda *a: None)                  # no LibreOffice here
    files, reports = de.postprocess(
        [ci.CIFile("รายงาน.docx", DOCX_CT, _docx_bytes()), ci.CIFile("chart.png", "image/png", b"png")],
        [], "docx", tmp_path)
    assert [f.name for f in files] == ["รายงาน.docx", "chart.png"]
    with zipfile.ZipFile(io.BytesIO(files[0].data)) as z:
        assert any(n.startswith("word/fonts/") for n in z.namelist())   # Sarabun embedded
    r = reports[0]
    assert r.ok and r.stats["tables"] == 1
    assert "ยังไม่ได้ตรวจหน้าตาเอกสาร (แปลงไฟล์เพื่อแสดงผลไม่ได้)" in r.warnings


def test_broken_or_wrong_files_are_not_delivered(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append([1, 0, "=A1/B1"])
    buf = io.BytesIO()
    wb.save(buf)
    files, reports = de.postprocess(
        [ci.CIFile("เสีย.docx", DOCX_CT, b"broken"), ci.CIFile("หาร.xlsx", ci.CI_OUTPUT_TYPES[".xlsx"], buf.getvalue())],
        [], "auto", tmp_path)
    assert files == []
    assert [r.ok for r in reports] == [False, False]
    note = de.report_note(reports)
    assert "❌ เสีย.docx — ไม่ได้ส่งไฟล์นี้" in note and "#DIV/0!" in note


def test_edit_losses_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "to_pdf", lambda *a: None)
    orig = tmp_path / "รายงาน.docx"
    orig.write_bytes(_docx_bytes(with_table=True))
    files, reports = de.postprocess([ci.CIFile("รายงาน_แก้ไข.docx", DOCX_CT, _docx_bytes(with_table=False))],
                                    [("รายงาน.docx", str(orig))], "auto", tmp_path / "out")
    assert reports[0].ok and "ตาราง ลดลงจาก 1 เหลือ 0" in reports[0].warnings
    assert de._original_for("x.docx", [("a.docx", "/a"), ("b.docx", "/b")]) is None   # ambiguous → none


def test_upload_prefix_is_removed_from_delivered_names(tmp_path, monkeypatch):
    # An edit saved next to its source in the sandbox carries the upload's
    # file-<id>- prefix; the user gets the plain name, still compared with its original.
    monkeypatch.setattr(de, "to_pdf", lambda *a: None)
    orig = tmp_path / "รายงาน.docx"
    orig.write_bytes(_docx_bytes(with_table=True))
    files, reports = de.postprocess(
        [ci.CIFile("file-3fWZXgXr91Vu6UHy2Je415-รายงาน_แก้ไข.docx", DOCX_CT, _docx_bytes(with_table=False))],
        [("รายงาน.docx", str(orig))], "auto", tmp_path / "out")
    assert [f.name for f in files] == ["รายงาน_แก้ไข.docx"]
    assert "ตาราง ลดลงจาก 1 เหลือ 0" in reports[0].warnings


def test_sandbox_loader_and_file_helpers_work_with_prefixed_uploads(tmp_path):
    then = ("print(sd.__file__); print(sorted(sd.user_files('/mnt/data'))); "
            "print(sd.font_path(folder='/mnt/data')); print(sd.output_path('file-3fWZXgXr91Vu-รายงาน_แก้ไข.docx'))")
    out = run_loader(de.LOADER, then.replace("/mnt/data", str(tmp_path / "data")), tmp_path, {
        "sirigpt_docs.py": str(de._SUPPORT[0]), "Sarabun-Regular.ttf": FONT,
        "รายงาน.docx": b"doc", "ต้นฉบับ.เนื้อหา.md": b"md"})
    assert out[0] == "<scratch>/sirigpt_docs.py"
    assert out[1] == "['ต้นฉบับ.เนื้อหา.md', 'รายงาน.docx']"             # our module and fonts left out
    assert out[2].startswith("<data>/file-") and out[2].endswith("-Sarabun-Regular.ttf")
    assert out[3] == "/mnt/data/รายงาน_แก้ไข.docx"
    assert de.LOADER in de._INSTRUCTIONS and "sd.user_files()" in de._INSTRUCTIONS
    assert "sys.path.append('/mnt/data')" not in de._INSTRUCTIONS


def test_pdf_output_gets_pages_and_a_preview(tmp_path):
    _pdf_with_table(str(tmp_path / "src.pdf"))
    files, reports = de.postprocess(
        [ci.CIFile("รวมไฟล์.pdf", de.DOC_TYPES[".pdf"], (tmp_path / "src.pdf").read_bytes())], [], "auto",
        tmp_path / "out")
    assert reports[0].pages == 2 and reports[0].rendered
    assert [f.name for f in files] == ["รวมไฟล์.pdf", "รวมไฟล์_ตัวอย่าง.png"]
    Image.open(io.BytesIO(files[1].data)).verify()


@needs_soffice
def test_docx_is_rendered_counted_and_converted_to_pdf(tmp_path):
    files, reports = de.postprocess([ci.CIFile("รายงาน.docx", DOCX_CT, _docx_bytes())], [], "pdf", tmp_path)
    assert [f.name for f in files] == ["รายงาน.docx", "รายงาน.pdf", "รายงาน_ตัวอย่าง.png"]
    assert reports[0].pages == 1 and reports[0].rendered and not reports[0].warnings
    import fitz

    with fitz.open(stream=files[1].data, filetype="pdf") as pdf:
        text = pdf[0].get_text()
        fonts = {f[3] for f in pdf[0].get_fonts()}
    assert "รายงานทดสอบภาษาไทย" in text.replace("\n", "")             # Thai shaped + extractable
    assert any("Sarabun" in f for f in fonts)


@needs_soffice
def test_pptx_is_rendered_slide_by_slide(tmp_path):
    prs = sd.new_pptx()
    sd.title_slide(prs, "ทดสอบ", "สไลด์")
    sd.bullet_slide(prs, "หัวข้อ", ["หนึ่ง", "สอง"])
    buf = io.BytesIO()
    prs.save(buf)
    files, reports = de.postprocess([ci.CIFile("deck.pptx", de.DOC_TYPES[".pptx"], buf.getvalue())],
                                    [], "pptx", tmp_path)
    assert reports[0].pages == 2 and [f.name for f in files] == ["deck.pptx", "deck_ตัวอย่าง.png"]


# ── one sandbox run, end to end with a fake OpenAI client ───────────────────

def test_run_document_uses_the_sandbox_with_doc_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "to_pdf", lambda *a: None)
    _pdf_with_table(str(tmp_path / "ต้นฉบับ.pdf"))
    client = FakeCI(answer="สร้างรายงานแล้ว", container_files=[
        ("/mnt/data/รายงาน.docx", "assistant", _docx_bytes()),
        ("/mnt/data/script.py", "assistant", b"print()"),               # not an allowed output
        ("/mnt/data/ต้นฉบับ.เนื้อหา.md", "user", b"md"),
    ])

    r = de.run_document("ทำรายงาน Word จาก PDF นี้", "สรุปเป็นรายงาน", "docx", [],
                        [("ต้นฉบับ.pdf", str(tmp_path / "ต้นฉบับ.pdf")), ("x.exe", __file__)], client=client)

    uploaded = [u[0] for u in client.uploaded]
    assert uploaded[:2] == ["ต้นฉบับ.pdf", "ต้นฉบับ.เนื้อหา.md"] and "x.exe" not in uploaded
    assert {"sirigpt_docs.py", "Sarabun-Regular.ttf", "Sarabun-Bold.ttf"} <= set(uploaded)
    kw = client.requests[0]
    assert "sirigpt_docs" in kw["instructions"] and kw["max_tool_calls"] == de.DOC_MAX_TOOL_CALLS
    assert kw["timeout"] <= de.DOC_TIMEOUT_S
    assert "ผลลัพธ์ที่ต้องการ: ไฟล์ .docx" in kw["input"][-1]["content"]
    assert [f.name for f in r.files] == ["รายงาน.docx"]
    assert "สร้างรายงานแล้ว" in r.answer and "✅ รายงาน.docx" in r.answer
    assert r.usage["cost_usd"] > 0 and client.deleted_containers == ["cntr_1"]


def test_v2_runs_are_unchanged_by_the_doc_overrides():
    client = FakeCI(container_files=[("/mnt/data/r.docx", "assistant", b"doc")])
    res = ci.run_code("q", "t", [], [], client=client)                  # V2 defaults
    assert res.files == []                                              # .docx not a V2 output
    assert [u[0] for u in client.uploaded] == ["Sarabun-Regular.ttf", "sirigpt_tools.py"]
    assert client.requests[0]["max_tool_calls"] == ci.CI_MAX_TOOL_CALLS


# ── the agent's make_document tool ──────────────────────────────────────────

def run(client, **kw):
    return list(agent.run_agent("ทำสไลด์สรุปยอดขาย 5 หน้า", [], web_quota_left=20, client=client, **kw))


@pytest.fixture
def fake_doc(monkeypatch):
    seen = []

    def fake_run_document(question, task, fmt, history, input_files=None, *, client=None, timeout=None):
        seen.append(NS(task=task, fmt=fmt, files=input_files, timeout=timeout))
        return de.DocResult(answer="ทำสไลด์ 5 หน้าแล้ว\n\n---\n**ตรวจไฟล์ก่อนส่ง**\n- ✅ สไลด์.pptx — 5 สไลด์",
                            files=[ci.CIFile("สไลด์.pptx", de.DOC_TYPES[".pptx"], b"pptx")],
                            usage={"model": "gpt-5", "prompt_tokens": 9000, "completion_tokens": 5000,
                                   "cost_usd": 0.09})

    monkeypatch.setattr(de, "run_document", fake_run_document)
    return seen


def test_make_document_offered_only_when_enabled():
    client = FakeClient(resp(call("use_standard_answer")))
    run(client)
    assert "make_document" not in tool_names(client.calls[0])
    client = FakeClient(resp(call("use_standard_answer")))
    run(client, doc_enabled=True, web_enabled=False)
    assert tool_names(client.calls[0]) == ["make_document", "use_standard_answer"]
    assert "make_document:" in client.calls[0]["instructions"]


def test_make_document_runs_and_answers_directly(fake_doc):
    client = FakeClient(resp(call("make_document", task="สไลด์ 5 หน้า ยอดขาย Q1", format="pptx")))
    events = run(client, doc_enabled=True, doc_files=[("ยอดขาย.xlsx", "/x")])
    assert ("status", "agent_document") in events
    kind, result = events[-1]
    assert kind == "done" and result.used_document and not result.used_code
    assert [f.name for f in result.files] == ["สไลด์.pptx"] and "✅ สไลด์.pptx" in result.answer
    assert fake_doc[0].fmt == "pptx" and fake_doc[0].files == [("ยอดขาย.xlsx", "/x")]
    assert len(client.calls) == 1                                       # no rewrite call
    assert fake_doc[0].timeout > de.DOC_TIMEOUT_S - 5                   # budget covers a document


def test_make_document_failure_goes_back_to_the_model(monkeypatch):
    def boom(*a, **kw):
        raise ci.CIError("TimeoutError: read timeout")

    monkeypatch.setattr(de, "run_document", boom)
    client = FakeClient(resp(call("make_document", task="t", format="docx")), stream("ขออภัย สร้างไม่สำเร็จ"))
    events = run(client, doc_enabled=True)
    assert events[-1][1].used_document is False
    assert "สร้างเอกสารไม่สำเร็จ" in outputs(client.calls[1])[0]["error"]


def test_make_document_ignored_when_disabled(fake_doc):
    client = FakeClient(resp(call("make_document", task="t", format="docx")), stream("ตอบ"))
    run(client)
    assert fake_doc == [] and "ไม่รู้จักเครื่องมือ" in outputs(client.calls[1])[0]["error"]


# ── chat glue: flag, quota, files, source ───────────────────────────────────

def _turn(monkeypatch, events, seen, **kw):
    def fake_run(question, history, **opts):
        seen.append(opts)
        yield from events

    monkeypatch.setattr(agent, "run_agent", fake_run)
    return list(main._agent_turn(ADMIN, kw.pop("question", "q"), kw.pop("session_id", None), "normal",
                                 None, **kw))


@pytest.fixture
def docs_on(monkeypatch, glue):
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_MODE", "admin")
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", "off")
    monkeypatch.setattr(chat_pg, "get_session_documents_pg", lambda sid, uid: glue.state.session_docs)
    glue.state.session_docs = []
    return glue


def _doc_result():
    return agent.AgentResult(answer="ทำรายงานแล้ว", used_document=True, steps=1,
                             usage=[{"model": "gpt-5", "prompt_tokens": 9000, "completion_tokens": 4000,
                                     "cost_usd": 0.08}],
                             files=[ci.CIFile("รายงาน.docx", DOCX_CT, b"d"),
                                    ci.CIFile("รายงาน_ตัวอย่าง.png", "image/png", b"p")])


def test_document_turn_is_saved_with_its_files(monkeypatch, docs_on):
    seen = []
    out = _turn(monkeypatch, [("status", "agent_document"), ("delta", "ทำรายงานแล้ว"), ("done", _doc_result())],
                seen)
    opts = seen[0]
    assert opts["doc_enabled"] is True and opts["ci_enabled"] is False and opts["web_enabled"] is False
    kind, payload = out[-1]
    assert payload["source"] == "document" and docs_on.saved[0]["source"] == "document"
    assert [a["filename"] for a in docs_on.attached] == ["รายงาน.docx", "รายงาน_ตัวอย่าง.png"]
    assert all(a["generated"] for a in docs_on.attached)


def test_follow_up_edits_the_document_already_in_the_chat(monkeypatch, docs_on):
    _, _, _, path = att.save_generated_file(b"pptx", "สไลด์.pptx", "x")
    docs_on.state.session_docs = [{"filename": "สไลด์.pptx", "file_path": path},
                                  {"filename": "gone.docx", "file_path": "missing.docx"}]
    seen = []
    _turn(monkeypatch, [("standard", {})], seen, question="แก้สไลด์ 3 ให้สั้นลง", session_id=5)
    assert [n for n, _ in seen[0]["doc_files"]] == ["สไลด์.pptx"]
    assert "สไลด์.pptx" in seen[0]["file_note"]


def test_uploaded_document_goes_to_the_engine(monkeypatch, docs_on):
    _, _, size, path = att.save_generated_file(b"pdf", "คู่มือ.pdf", "x")
    up = [{"filename": "คู่มือ.pdf", "content_type": "application/pdf", "size": size, "file_path": path}]
    seen = []
    out = _turn(monkeypatch, [("done", _doc_result())], seen, question="แปลงเป็น Word", uploads=up)
    assert [n for n, _ in seen[0]["doc_files"]] == ["คู่มือ.pdf"]
    assert [a["filename"] for a in docs_on.attached][0] == "คู่มือ.pdf"   # the upload is kept too
    assert out[-1][1]["source"] == "document"


def test_daily_cap_is_shared_with_code_interpreter(monkeypatch, docs_on):
    docs_on.state.code_runs_today = ci.CI_DAILY_LIMIT
    seen = []
    assert _turn(monkeypatch, [], seen) == [("fallback", None)] and seen == []


def test_flag_off_means_no_router_for_documents(monkeypatch, docs_on):
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_MODE", "off")
    assert main._agent_enabled(ADMIN, "normal") is False
    assert main._doc_enabled(ADMIN, "company") is False


# ── routing of uploads in /chat ─────────────────────────────────────────────

EDITED = {"answer": "แก้รูปแล้ว", "source": "image_edit", "session_id": 5, "session_title": "t",
          "message_id": 3, "attachment": None}


def _upload(monkeypatch, tmp_path, files, *, doc_mode="admin", question="ทำรายงานจากไฟล์นี้"):
    calls = NS(turns=[], scope=[], charged=[], audits=[], saved=[], attached=[], answered=[], edited=[])
    _gate(monkeypatch, calls, keyword=None, flagged=None, decision="denied")
    monkeypatch.setattr(de, "DOCUMENT_ENGINE_MODE", doc_mode)
    monkeypatch.setattr(ci, "CODE_INTERPRETER_MODE", "off")
    monkeypatch.setattr(chat_pg, "get_session_history_pg", lambda **kw: [])

    def fake_turn(*a, **kw):
        calls.turns.append(kw)
        yield "done", {"answer": "ทำแล้ว", "tail": "", "source": "document", "session_id": 5,
                       "session_title": "t", "message_id": 9, "attachments": []}

    monkeypatch.setattr(main, "_agent_turn", fake_turn)
    monkeypatch.setattr(main, "_answer_files", lambda *a: calls.answered.append(a) or ("ตอบจากไฟล์", {}))
    monkeypatch.setattr(main, "_handle_image_edit", lambda *a, **kw: calls.edited.append(a) or EDITED)
    main.app.dependency_overrides[main.current_user] = lambda: ADMIN
    try:
        return TestClient(main.app).post("/chat", data={"message": question}, files=files), calls
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    return buf.getvalue()


def test_docx_upload_with_engine_on_goes_to_the_router(monkeypatch, tmp_path):
    r, calls = _upload(monkeypatch, tmp_path, [("files", ("a.docx", _docx_bytes(), DOCX_CT))])
    assert r.json()["source"] == "document" and len(calls.turns) == 1 and calls.answered == []


def test_docx_upload_with_engine_off_keeps_the_old_file_answer(monkeypatch, tmp_path):
    r, calls = _upload(monkeypatch, tmp_path, [("files", ("a.docx", _docx_bytes(), DOCX_CT))], doc_mode="off")
    assert r.json()["source"] == "files" and calls.turns == [] and len(calls.answered) == 1


def test_picture_with_a_document_is_material_not_an_image_edit(monkeypatch, tmp_path):
    files = [("files", ("a.docx", _docx_bytes(), DOCX_CT)), ("files", ("logo.png", _png(), "image/png"))]
    r, calls = _upload(monkeypatch, tmp_path, files, question="ใส่รูปนี้ในรายงาน")
    assert calls.edited == [] and len(calls.turns) == 1


def test_picture_alone_keeps_the_image_paths(monkeypatch, tmp_path):
    r, calls = _upload(monkeypatch, tmp_path, [("files", ("logo.png", _png(), "image/png"))],
                       question="ใส่ข้อความในรูปนี้")
    assert r.json()["source"] == "image_edit" and len(calls.edited) == 1 and calls.turns == []
