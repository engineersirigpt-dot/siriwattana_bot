"""The "ข้อมูลด้านบนเป็นความรู้ทั่วไป…" note is off unless COMPANY_FACT_NOTE is set."""
import main


def test_note_is_off_by_default(monkeypatch):
    monkeypatch.setattr(main, "COMPANY_FACT_NOTE_ENABLED", False)
    assert main._company_fact_note("งานใหม่ที่เคาะราคาแล้ว", "llm") == ""
    assert main._company_fact_note("บริษัทให้ลาพักร้อนกี่วันต่อปี", "llm") == ""


def test_note_comes_back_with_the_flag(monkeypatch):
    monkeypatch.setattr(main, "COMPANY_FACT_NOTE_ENABLED", True)
    assert "หมายเหตุ" in main._company_fact_note("บริษัทให้ลาพักร้อนกี่วันต่อปี", "llm")
    # still only for ungrounded answers to company-fact questions
    assert main._company_fact_note("บริษัทให้ลาพักร้อนกี่วันต่อปี", "rag") == ""
    assert main._company_fact_note("Lewis Hamilton อยู่ทีมไหน", "llm") == ""


def test_default_env_is_off():
    import os

    assert os.getenv("COMPANY_FACT_NOTE") in (None, "", "0")
    assert main.COMPANY_FACT_NOTE_ENABLED is False
