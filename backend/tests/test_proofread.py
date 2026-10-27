"""
Tests for proofreading routing: _is_proofread_request, _answer_files, and
attachments.ocr_data_url's graceful fallback.
"""

import pytest

import attachments as att
import main

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


def test_proofread_ocrs_each_image_and_uses_proofread_model(monkeypatch):
    seen = {}
    monkeypatch.setattr(att, "ocr_data_url", lambda u: "สามรถ")

    def fake_proofread(q, imgs, ocr, docs, history=None):
        seen.update(q=q, imgs=imgs, ocr=ocr, docs=docs)
        return "ok", {}

    monkeypatch.setattr(main, "answer_proofread", fake_proofread)
    monkeypatch.setattr(main, "answer_with_files", lambda *a, **k: pytest.fail("normal path used"))

    answer, _ = main._answer_files("ตรวจคำผิดให้หน่อย", [IMG, IMG], [("a.docx", "ข้อความ")], None)

    assert answer == "ok"
    assert seen["ocr"] == ["สามรถ", "สามรถ"]
    assert seen["docs"] == [("a.docx", "ข้อความ")]


def test_non_proofread_question_keeps_normal_files_path(monkeypatch):
    monkeypatch.setattr(main, "answer_with_files", lambda q, i, t, history=None: ("normal", {}))
    monkeypatch.setattr(main, "answer_proofread", lambda *a, **k: pytest.fail("proofread path used"))
    monkeypatch.setattr(att, "ocr_data_url", lambda u: pytest.fail("OCR should not run"))

    assert main._answer_files("สรุปรูปนี้", [IMG], [], None)[0] == "normal"


def test_ocr_returns_empty_without_tesseract(monkeypatch):
    monkeypatch.setattr(att.shutil, "which", lambda name: None)
    assert att.ocr_data_url(IMG) == ""
