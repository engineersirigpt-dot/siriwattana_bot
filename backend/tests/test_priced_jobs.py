"""
Priced jobs ("งานใหม่ที่เคาะราคาแล้ว") from the estimate systems — offline.

The question → small AI planner (faked here) → fixed read-only SQL on the live
master list ESTIMATE.dbo.tb_rfq_list (both the old V1 and the new V2 system;
faked) → answer from that data. The lookup skips the AI sensitivity classifier
(owner opened it to everyone, 2026-11-17); test rows are filtered out by name;
a DB failure gives a Thai message, not a guess.
"""

import datetime as dt
import json
import re
from decimal import Decimal

import pytest

import chat_pg
import main
import priced_jobs as pj
from tests.test_v4_glue import ADMIN, pipe, stream  # noqa: F401  (pipe is a fixture)

REAL_IS = main._is_priced_jobs_query
REAL_TRY = main._try_priced_jobs_answer

# rows as the live list returns them (est_legacy_peek / est19_peek, 2026-11-18)
ROW = {  # old system (V1), price from tb_rfq_price1
    "job_id": "E26110464", "job_name": "Rep.SRP (Tray) - Can Pack 24 x 7", "customer_id": "C2014056",
    "custName": "โชติวัฒน์อุตสาหกรรมการผลิต", "est_type": "packaging", "status_id": 3, "source_system": "V1",
    "estimate_date": dt.datetime(2026, 11, 18, 11, 25, 28), "created": dt.datetime(2026, 11, 17, 15, 41, 25),
    "updated": dt.datetime(2026, 11, 18, 11, 25, 28), "price_qty": 3000, "unit_price": 9.03,
    "total_price": 27094.95, "currency_no": "THB", "tiers": 1, "mid_qty": None, "mid_total_ex_vat": None,
}
ROW_V2 = {  # new system (V2): no price lines in the old table → its ex-VAT middle-quantity price
    "job_id": "E26110334", "job_name": "กล่อง Jaelek", "customer_id": "C9999998", "custName": "ลูกค้าใหม่",
    "est_type": "packaging", "status_id": 3, "source_system": "V2",
    "estimate_date": dt.datetime(2026, 11, 13, 11, 18, 45), "created": dt.datetime(2026, 11, 12, 14, 28, 23),
    "updated": dt.datetime(2026, 11, 13, 11, 18, 45), "price_qty": None, "unit_price": None, "total_price": None,
    "currency_no": None, "tiers": 0, "mid_qty": 500, "mid_total_ex_vat": Decimal("25832.50"),
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

def test_search_reads_the_master_list_of_both_systems():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 17),
         "keyword": "AROMA'; DROP TABLE x--"}
    rows_sql, totals_sql = pj.build_search(p)
    for sql in (rows_sql, totals_sql):
        # the live master list on .19 — old (V1) and new (V2) estimates, no double counting
        assert "FROM ESTIMATE.dbo.tb_rfq_list l WITH (NOLOCK)" in sql
        assert "l.status_id = 3" in sql
        # estimate_date = when it was approved; date_to is inclusive
        assert "l.estimate_date >= '2026-11-16'" in sql and "l.estimate_date < '2026-11-18'" in sql
        # C9999998 = "ลูกค้าใหม่", not a test customer (excluding it answered "0 งาน", 2026-11-18)
        assert "C9999998" not in sql and "customer_id NOT IN" not in sql
        assert "N'%test%'" in sql and "N'%เทส%'" in sql and "N'%ทดสอบ%'" in sql
        # OPENQUERY takes no parameters: the keyword is reduced to safe characters
        assert "N'%AROMA DROP TABLE x--%'" in sql and "';" not in sql
        # selling price only: the price table's cost columns are never read
        for col in ("total_material", "total_print", "total_process", "marking", "profit_sharing", "sale_id"):
            assert col not in sql, col
    assert rows_sql.startswith(f"SELECT TOP {pj.MAX_ROWS} ")
    assert "ORDER BY CASE WHEN x.qty = x.main_qty THEN 0 ELSE 1 END, x.qty" in rows_sql   # price at the main qty
    assert "ISNULL(p.currency_no, 'THB') = 'THB'" in totals_sql                              # only baht is summed


def test_live_approvals_of_new_customers_count_and_the_it_test_does_not():
    rows_sql, _ = pj.build_search({"status": "approve", "date_from": dt.date(2026, 11, 1),
                                   "date_to": dt.date(2026, 11, 18), "keyword": None})
    patterns = re.findall(r"job_name NOT LIKE N'%(.+?)%'", rows_sql)

    def kept(name):
        return not any(p.lower() in name.lower() for p in patterns)

    assert kept("WAGEN-Box_FORPRESS") and kept("กล่อง Jaelek") and kept("Ashton Weapons Catalog - offset")
    assert not kept("Test - IT ทดสอบระบบ สร้าง Estimate Packaging")


def test_statuses_map_to_the_old_ids():
    rows_sql, _ = pj.build_search({"status": "any"})
    assert "l.status_id IN (1, 2, 3)" in rows_sql                       # any = everything but drafts
    assert pj.build_search({"status": "weird'; --"})[0] == rows_sql     # unknown status → any
    assert "l.status_id = 1" in pj.build_search({"status": "pending"})[0]


@pytest.mark.parametrize("q,doc", [("E26110464 ราคาเท่าไหร่", "E26110464"), ("EB26110174 อนุมัติยัง", "EB26110174"),
                                   ("ep26110001", "EP26110001")])
def test_book_and_popup_numbers_are_estimates_too(q, doc):
    assert pj.mentions(q) and pj.DOC_RE.search(q).group(0).upper() == doc


def test_doc_lookup_validates_numbers(monkeypatch):
    seen = []
    monkeypatch.setattr(pj, "_run", lambda sqls: seen.extend(sqls) or [[] for _ in sqls])
    assert pj.lookup_doc("E2611'; DROP", None)["rows"] == [] and seen == []
    pj.lookup_doc("eb26110174", None)
    assert "FROM ESTIMATE.dbo.tb_rfq_list l" in seen[0] and "l.job_id = 'EB26110174'" in seen[0]
    assert "FROM ESTIMATE.dbo.tb_rfq_price1" in seen[1] and "FROM ESTIMATE.dbo.tb_rfq_status_log" in seen[2]
    assert "FROM MI2_ESTIMATE.dbo.tb_quotation" in seen[4]


# ── context for the answer model ────────────────────────────────────────────

def test_list_context_has_count_total_and_prices():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 17), "keyword": None}
    free = dict(ROW, job_id="E26110465", total_price=None, tiers=0)
    text = pj.to_context(p, {"rows": [ROW, ROW_V2, free], "count": 5, "total": 52927.45, "priced": 2,
                             "other_currency": 1})
    assert "พบทั้งหมด: 5 งาน" in text and "52,927.45 บาท" in text and "1 งานที่ราคาเป็นสกุลเงินอื่น" in text
    assert "27,094.95 THB ที่จำนวน 3,000 ชิ้น (≈ 9.03 THB/ชิ้น)" in text            # old system: no VAT claim
    assert "25,832.50 บาท ก่อน VAT ที่จำนวน 500 ชิ้น" in text                       # new system: ex VAT
    assert "ยังไม่มีราคา" in text and "แสดงรายละเอียด 3 งานล่าสุด" in text
    assert "ระบบประเมินราคาเดิม" in text and "ระบบประเมินราคาใหม่ (MI2)" in text
    assert "อนุมัติแล้ว (เคาะราคาแล้ว) · อนุมัติเมื่อ 2026-11-18 11:25" in text
    # one labelled field per line — a one-line "a | b | c" record got copied into an answer
    assert ("[ใบประเมิน E26110464] Rep.SRP (Tray) - Can Pack 24 x 7\n"
            "  ลูกค้า: โชติวัฒน์อุตสาหกรรมการผลิต (C2014056)") in text
    assert " | " not in text


def test_empty_and_single_document_contexts():
    p = {"status": "approve", "date_from": None, "date_to": None, "keyword": "XYZ"}
    assert "พบทั้งหมด: 0 งาน" in pj.to_context(p, {"rows": [], "count": 0, "total": None, "priced": 0})
    one = pj.to_context({"doc_id": "E26110464"}, {
        "rows": [dict(ROW, credit_term_name="เครดิต 30 วัน")],
        "prices": [{"qty": 3000, "unit_price": 9.03, "total_price": 27094.95, "currency_no": "THB"},
                   {"qty": 5000, "unit_price": 8.1, "total_price": 40500.0, "currency_no": "THB"}],
        "history": [{"from_status_id": 0, "to_status_id": 1, "created": dt.datetime(2026, 11, 17, 16, 19)},
                    {"from_status_id": 1, "to_status_id": 3, "created": dt.datetime(2026, 11, 18, 11, 25)}],
        "detail": [], "quotes": [], "doc_id": "E26110464"})
    assert "3,000 ชิ้น: 27,094.95 THB" in one and "5,000 ชิ้น: 40,500.00 THB" in one
    assert "มีราคาทั้งหมด 2 ระดับจำนวน" in one and "เครดิต 30 วัน" in one
    assert "2026-11-18 11:25: อนุมัติแล้ว (เคาะราคาแล้ว)" in one
    quote = {"quotation_id": "QE2600190", "revision": 0, "status_label": "Approved", "currency": "THB",
             "qty_list": "500", "grand_total": Decimal("27640.78"), "issue_date": dt.date(2026, 11, 13),
             "expiry_date": dt.date(2026, 12, 13), "approved_at": None}
    v2 = pj.to_context({"doc_id": "E26110334"}, {
        "rows": [ROW_V2], "prices": [], "history": [], "quotes": [quote], "doc_id": "E26110334",
        "detail": [{"qty_list": "500", "mid_qty": 500, "mid_total_ex_vat": Decimal("25832.50"), "print_type": "Offset",
                    "box_type": "กล่องหูช้าง", "component_type": "ไม่ประกบลูกฟูก"}]})
    assert "25,832.50 บาท ก่อน VAT ที่จำนวน 500 ชิ้น" in v2 and "QE2600190" in v2 and "Offset" in v2
    assert "ไม่พบใบประเมินราคา E26119999" in pj.to_context({"doc_id": "E26119999"}, {"rows": [], "prices": []})


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
                        {"rows": [ROW], "count": 1, "total": ROW["total_price"], "priced": 1})

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
    assert docs[0][0] == "ระบบประเมินราคา" and "E26110464" in docs[0][1]
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
