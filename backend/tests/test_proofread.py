"""
Tests for proofreading routing: _is_proofread_request and _answer_files.
(The pipeline itself is covered in test_proofread_pipeline.py.)
"""

import pytest

import main
import proofread

IMG = "data:image/png;base64,iVBORw0KGgo="


@pytest.mark.parametrize(
    "text",
    ["ช่วยตรวจคำผิดในรูปนี้ให้หน่อย", "มีคำไหนพิมพ์ผิดบ้าง", "ตรวจทานเอกสารนี้", "proofread this", "check typos"],
)
def test_proofread_intent_detected(text):
    assert main._is_proofread_request(text) is True


@pytest.mark.parametrize("text", ["สรุปไฟล์นี้ให้หน่อย", "แปลเป็นภาษาอังกฤษ", "อธิบายรูปนี้", ""])
def test_other_file_questions_are_not_proofreading(text):
    assert main._is_proofread_request(text) is False


def test_proofread_request_goes_to_proofread_pipeline(monkeypatch):
    seen = {}

    def fake_proofread(q, imgs, docs):
        seen.update(q=q, imgs=imgs, docs=docs)
        return "ok", {}

    monkeypatch.setattr(proofread, "proofread", fake_proofread)
    monkeypatch.setattr(main, "answer_with_files", lambda *a, **k: pytest.fail("normal path used"))

    answer, _ = main._answer_files("ตรวจคำผิดให้หน่อย", [IMG, IMG], [("a.docx", "ข้อความ")], None)

    assert answer == "ok"
    assert seen["imgs"] == [IMG, IMG]
    assert seen["docs"] == [("a.docx", "ข้อความ")]


def test_non_proofread_question_keeps_normal_files_path(monkeypatch):
    monkeypatch.setattr(main, "answer_with_files", lambda q, i, t, history=None: ("normal", {}))
    monkeypatch.setattr(proofread, "proofread", lambda *a, **k: pytest.fail("proofread path used"))

    assert main._answer_files("สรุปรูปนี้", [IMG], [], None)[0] == "normal"
