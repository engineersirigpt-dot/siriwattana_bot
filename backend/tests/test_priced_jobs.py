"""
Priced jobs ("งานใหม่ที่เคาะราคาแล้ว") from the MI estimate system — offline.

The question → small AI planner (faked here) → fixed read-only SQL on
MI2_ESTIMATE.dbo.tb_rfq (faked) → answer from that data. The lookup skips the
AI sensitivity classifier (owner opened it to everyone, 2026-11-17); test rows
are filtered out; a DB failure gives a Thai message, not a guess.
"""

import datetime as dt
import json
from decimal import Decimal

import pytest

import chat_pg
import main
import priced_jobs as pj
from tests.test_v4_glue import ADMIN, pipe, stream  # noqa: F401  (pipe is a fixture)

REAL_IS = main._is_priced_jobs_query
REAL_TRY = main._try_priced_jobs_answer

ROW = {
    "doc_id": "E26110001", "estimate_name": "กล่อง AROMA OIL แบบปลอกสวม", "customer_id": "C20330637",
    "customer_name": "บริษัท อโรมา จำกัด", "ae_team_name": "ทีม A", "estimate_status": "approve",
    "estimate_date": dt.date(2026, 11, 3), "request_for_approve_at": dt.datetime(2026, 11, 3, 9, 49),
    "approved_at": dt.datetime(2026, 11, 6, 14, 52, 16), "qty_list": "2000", "mid_qty": 2000,
    "mid_total_ex_vat": Decimal("31947.09"), "print_type": "Offset", "box_type": "9. ปลอกกล่อง : Sleeve",
    "component_type": "ไม่ประกบลูกฟูก", "credit_term": "30 วัน",
}
PLAN_USAGE = {"model": "gpt-4o-mini", "prompt_tokens": 400, "completion_tokens": 40, "cost_usd": 0.0001}


# ── pre-filter ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "งานใหม่ที่เคาะราคาแล้ว", "สัปดาห์นี้มีงานใหม่ที่เคาะราคาแล้วกี่งาน อะไรบ้าง", "E26110001 ราคาเท่าไหร่",
    "ใบเสนอราคา QE2600190 อนุมัติหรือยัง", "มีงานรออนุมัติราคากี่งาน", "งานที่ประเมินราคาเดือนนี้",
])
def test_estimate_questions_are_picked_up(q):
    assert pj.mentions(q)


@pytest.mark.parametrize("q", [
    "สวัสดีครับ", "ราคาทองวันนี้เท่าไหร่", "กระดาษ 500 แผ่น แผ่นละ 2.75 บาท รวมเท่าไหร่", "งาน J82601758 ใช้กาวอะไร",
])
def test_other_questions_are_not(q):
    assert not pj.mentions(q)


# ── planner output ──────────────────────────────────────────────────────────

def test_plan_is_cleaned():
    assert pj._clean_plan({"lookup": False}) is None
    p = pj._clean_plan({"lookup": True, "status": "weird", "date_from": "2026-11-30", "date_to": "2026-11-01",
                        "doc_id": "e26110001", "keyword": "  AROMA "})
    assert p["status"] == "any"
    assert (p["date_from"], p["date_to"]) == (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    assert p["doc_id"] == "E26110001" and p["keyword"] == "AROMA"
    assert pj._clean_plan({"lookup": True, "doc_id": "E123"})["doc_id"] is None


def test_an_e_number_is_looked_up_even_if_the_ai_is_unsure(monkeypatch):
    monkeypatch.setattr(pj, "_plan_cached", lambda q, prev, today: ('{"lookup": false}', json.dumps(PLAN_USAGE)))
    p, usage = pj.plan("ช่วยดู E26110001 ให้หน่อย")
    assert p["doc_id"] == "E26110001" and usage == PLAN_USAGE


def test_date_anchors_for_the_planner():
    a = pj._date_anchors(dt.date(2026, 11, 17))   # a Tuesday
    assert "สัปดาห์นี้ = 2026-11-16 ถึง 2026-11-17" in a
    assert "สัปดาห์ที่แล้ว = 2026-11-09 ถึง 2026-11-15" in a
    assert "เดือนที่แล้ว = 2026-10-01 ถึง 2026-10-31" in a


# ── SQL ─────────────────────────────────────────────────────────────────────

def test_search_sql_reads_the_live_server_and_leaves_out_test_rows():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 17),
         "keyword": "AROMA'; DROP TABLE x--"}
    rows_sql, totals_sql = pj.build_search(p)
    for sql in (rows_sql, totals_sql):
        # fully qualified: runs on .19 through OPENQUERY (the .10 copy is the test system)
        assert "FROM MI2_ESTIMATE.dbo.tb_rfq WITH (NOLOCK)" in sql
        assert "estimate_status = 'approve'" in sql
        assert "approved_at >= '2026-11-16'" in sql and "approved_at < '2026-11-18'" in sql   # date_to inclusive
        # C9999998 = "ลูกค้าใหม่", not a test customer: every approved estimate on the
        # live system used it, and excluding it answered "0 งาน" (2026-11-18)
        assert "C9999998" not in sql and "customer_id NOT IN" not in sql
        assert "N'%test%'" in sql and "N'%เทส%'" in sql and "N'%ทดสอบ%'" in sql
        # OPENQUERY takes no parameters: the keyword is reduced to safe characters
        assert "N'%AROMA DROP TABLE x--%'" in sql and "';" not in sql
    assert rows_sql.startswith(f"SELECT TOP {pj.MAX_ROWS} ")
    assert "contact" not in rows_sql and "ae_emp_name" not in rows_sql   # no personal columns


def test_live_approvals_of_new_customers_count_and_the_it_test_does_not():
    # the three approved rows on .19 (est19_peek, 2026-11-18), against the name filter
    import re as _re

    rows_sql, _ = pj.build_search({"status": "approve", "date_from": dt.date(2026, 11, 1),
                                   "date_to": dt.date(2026, 11, 18), "keyword": None})
    patterns = _re.findall(r"estimate_name NOT LIKE N'%(.+?)%'", rows_sql)

    def kept(name):
        return not any(p.lower() in name.lower() for p in patterns)

    assert kept("WAGEN-Box_FORPRESS") and kept("กล่อง Jaelek")
    assert not kept("Test - IT ทดสอบระบบ สร้าง Estimate Packaging")


def test_any_status_list_excludes_drafts():
    rows_sql, _ = pj.build_search({"status": "any"})
    assert "estimate_status IN ('approve', 'pending', 'reject', 'over_limit_approve')" in rows_sql
    assert pj.build_search({"status": "weird'; --"})[0] == rows_sql     # unknown status → any


def test_doc_lookup_validates_numbers(monkeypatch):
    seen = []
    monkeypatch.setattr(pj, "_run", lambda sqls: seen.extend(sqls) or [[] for _ in sqls])
    assert pj.lookup_doc("E2611'; DROP", None) == {"rows": [], "quotes": []} and seen == []
    pj.lookup_doc("e26110334", None)
    assert "WHERE doc_id = 'E26110334'" in seen[0] and "FROM MI2_ESTIMATE.dbo.tb_quotation" in seen[1]


# ── context for the answer model ────────────────────────────────────────────

def test_list_context_has_count_total_and_prices():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 17), "keyword": None}
    free = dict(ROW, doc_id="E26110002", mid_total_ex_vat=Decimal("0"))
    text = pj.to_context(p, {"rows": [ROW, free], "count": 5, "total": Decimal("31947.09"), "priced": 1})
    assert "พบทั้งหมด: 5 งาน" in text and "31,947.09 บาท" in text
    assert "≈ 15.97 บาท/ชิ้น" in text and "ยังไม่มีราคา" in text
    assert "แสดงรายละเอียด 2 งานล่าสุด" in text
    assert "ไม่รวมรายการทดสอบ" in text and "อนุมัติแล้ว (เคาะราคาแล้ว)" in text
    # one labelled field per line — a one-line "a | b | c" record got copied into an answer
    assert "[ใบประเมิน E26110001] กล่อง AROMA OIL แบบปลอกสวม\n  ลูกค้า: บริษัท อโรมา จำกัด (C20330637)" in text
    assert " | " not in text


def test_empty_and_single_document_contexts():
    p = {"status": "approve", "date_from": None, "date_to": None, "keyword": "XYZ"}
    assert "พบทั้งหมด: 0 งาน" in pj.to_context(p, {"rows": [], "count": 0, "total": None, "priced": 0})
    quote = {"quotation_id": "QE2600190", "revision": 0, "status_label": "Approved", "currency": "THB",
             "qty_list": "2000", "grand_total": Decimal("34183.39"), "issue_date": dt.date(2026, 11, 6),
             "expiry_date": dt.date(2026, 12, 6), "approved_at": None}
    one = pj.to_context({"doc_id": "E26110001"}, {"rows": [ROW], "quotes": [quote], "doc_id": "E26110001"})
    assert "QE2600190" in one and "34,183.39 THB" in one
    assert "ไม่พบใบประเมินราคา E26119999" in pj.to_context({"doc_id": "E26119999"}, {"rows": [], "quotes": []})


def test_off_switch_and_missing_mi(monkeypatch):
    import mi_auth

    monkeypatch.setattr(mi_auth, "mi_enabled", lambda: True)
    monkeypatch.setattr(pj, "PRICED_JOBS_MODE", "off")
    assert not pj.enabled()
    monkeypatch.setattr(pj, "PRICED_JOBS_MODE", "on")
    assert pj.enabled()
    monkeypatch.setattr(mi_auth, "mi_enabled", lambda: False)
    assert not pj.enabled()


# ── in the chat pipeline ────────────────────────────────────────────────────

@pytest.fixture
def live(pipe, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "_is_priced_jobs_query", REAL_IS)
    monkeypatch.setattr(main, "_try_priced_jobs_answer", REAL_TRY)
    monkeypatch.setattr(pj, "enabled", lambda: True)
    pipe.plans, pipe.queries, pipe.contexts = [], [], []

    def plan(q, previous=""):
        pipe.plans.append((q, previous))
        return {"doc_id": None, "quotation_id": None, "status": "approve", "date_from": dt.date(2026, 11, 16),
                "date_to": dt.date(2026, 11, 17), "keyword": None}, dict(PLAN_USAGE)

    monkeypatch.setattr(pj, "plan", plan)
    monkeypatch.setattr(pj, "search", lambda p: pipe.queries.append(p) or
                        {"rows": [ROW], "count": 1, "total": ROW["mid_total_ex_vat"], "priced": 1})

    def answer(question, images, docs, history=None, system_prompt=None):
        pipe.contexts.append((docs, system_prompt))
        return "สัปดาห์นี้มีงานเคาะราคาแล้ว 1 งาน", {"model": "gpt-4.1", "prompt_tokens": 1500,
                                                     "completion_tokens": 120, "cost_usd": 0.004}

    monkeypatch.setattr(main, "answer_with_files", answer)
    return pipe


def test_priced_jobs_question_is_answered_from_the_estimate_system(live):
    events = stream("สัปดาห์นี้มีงานใหม่ที่เคาะราคาแล้วกี่งาน อะไรบ้าง")
    done = events[-1]
    assert done["type"] == "done" and done["source"] == "priced_jobs"
    assert "1 งาน" in "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert live.safety == []                                   # the AI classifier was skipped
    assert live.queries and live.queries[0]["status"] == "approve"
    docs, prompt = live.contexts[0]
    assert docs[0][0] == "ระบบประเมินราคา" and "E26110001" in docs[0][1]
    assert "ระบบประเมินราคา" in prompt
    saved = live.saved[-1]
    assert saved["source"] == "priced_jobs"
    assert saved["usage"]["model_used"] == "gpt-4.1"
    assert saved["usage"]["cost_usd"] == pytest.approx(0.0041)   # planner + answer


def test_a_follow_up_keeps_the_previous_question(live):
    live.history = [{"role": "user", "content": "งานที่เคาะราคาแล้วเดือนนี้มีอะไรบ้าง"},
                    {"role": "assistant", "content": "มี 3 งาน …"}]
    events = stream("แล้วเดือนที่แล้วล่ะ")
    assert events[-1]["source"] == "priced_jobs"
    assert live.plans[-1] == ("แล้วเดือนที่แล้วล่ะ", "งานที่เคาะราคาแล้วเดือนนี้มีอะไรบ้าง")


def test_not_a_lookup_goes_the_normal_way(live, monkeypatch):
    monkeypatch.setattr(pj, "plan", lambda q, previous="": (None, dict(PLAN_USAGE)))
    events = stream("ใบเสนอราคาคืออะไร")
    assert events[-1]["source"] != "priced_jobs"
    assert live.safety == ["ใบเสนอราคาคืออะไร"]            # the classifier ran as usual
    assert live.queries == []


def test_unrelated_question_never_reaches_the_planner(live):
    stream("สวัสดีครับ")
    assert live.plans == []


def test_database_down_gives_a_thai_message(live, monkeypatch):
    def boom(p):
        raise ConnectionError("MI DB down")

    monkeypatch.setattr(pj, "search", boom)
    events = stream("งานใหม่ที่เคาะราคาแล้ว")
    assert events[-1]["source"] == "priced_jobs_error"
    text = "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert "ดึงข้อมูลจากระบบประเมินราคาไม่สำเร็จ" in text
    assert "priced_jobs_failed" in live.audits
    assert live.contexts == []                               # no AI answer was made up


def test_non_stream_chat_too(live):
    from tests.test_v4_glue import post

    r = post("งานใหม่ที่เคาะราคาแล้ว").json()
    assert r["source"] == "priced_jobs" and "1 งาน" in r["answer"]
    assert chat_pg.save_chat_message_pg.__name__ == "<lambda>"   # pipe's fake store was used
