"""
Tests for main._is_image_request — regex first, classifier only as a fallback.
"""

import pytest

import main


@pytest.fixture
def classifier(monkeypatch):
    """Record classifier calls and answer `yes` (wants an image)."""
    calls = []

    def fake(q):
        calls.append(q)
        return True

    monkeypatch.setattr("llm.wants_image_generation", fake)
    return calls


@pytest.mark.parametrize(
    "text",
    [
        "สร้างโปสเตอร์รับสมัครงาน",
        "ทำโปสเตอร์ลดราคา",
        "ทำโปรเตอร์ป้ายรับสมัครงาน 3 ตำแหน่ง",  # real user message, misspelled
        "draw a picture of a cat",
    ],
)
def test_regex_hit_skips_classifier(classifier, text):
    assert main._is_image_request(text) is True
    assert classifier == []


@pytest.mark.parametrize(
    "text",
    ["ทำรูปโปสเตอร์รับสมัครงาน", "ขอโปสเตอร์โปรโมชัน", "ทำประกาศรับสมัครงานเป็นรูป"],
)
def test_regex_miss_with_image_noun_asks_classifier(classifier, text):
    assert main._is_image_request(text) is True
    assert classifier == [text]


@pytest.mark.parametrize(
    "text",
    ["สรุปยอดขายเดือนนี้", "รูปแบบการทำงานเป็นยังไง", "ขอภาพรวมโครงการ", ""],
)
def test_no_image_noun_never_asks_classifier(classifier, text):
    assert main._is_image_request(text) is False
    assert classifier == []


def test_classifier_no_means_no_image(monkeypatch):
    monkeypatch.setattr("llm.wants_image_generation", lambda q: False)
    assert main._is_image_request("ขอรูปเครื่องจักรรุ่นนี้") is False


def test_auto_decide_off_skips_classifier(classifier, monkeypatch):
    monkeypatch.setattr(main, "IMAGE_AUTO_DECIDE", False)
    assert main._is_image_request("ขอโปสเตอร์โปรโมชัน") is False
    assert classifier == []
