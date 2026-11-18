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
    "updated": dt.datetime(2026, 11, 18, 11, 25, 28), "price_qty": 3000, "price_ex": 26305.78,
    "total_price": 27094.95, "currency_no": "THB", "tiers": 1, "mid_qty": None, "mid_total_ex_vat": None,
}
ROW_V2 = {  # new system (V2): no price lines in the old table → its ex-VAT middle-quantity price
    "job_id": "E26110334", "job_name": "กล่อง Jaelek", "customer_id": "C9999998", "custName": "ลูกค้าใหม่",
    "est_type": "packaging", "status_id": 3, "source_system": "V2",
    "estimate_date": dt.datetime(2026, 11, 13, 11, 18, 45), "created": dt.datetime(2026, 11, 12, 14, 28, 23),
    "updated": dt.datetime(2026, 11, 13, 11, 18, 45), "price_qty": None, "price_ex": None, "total_price": None,
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


def test_a_status_count_question_is_a_lookup_even_if_the_planner_says_no(monkeypatch):
    # prod 2026-11-18: gpt-4o-mini said lookup=false and the bot answered "ขอข้อมูลเพิ่มนิดนะครับ…"
    monkeypatch.setattr(pj, "_plan_cached", lambda q, prev, today: ('{"lookup": false}', json.dumps(PLAN_USAGE)))
    p, usage = pj.plan("ตอนนี้มีใบประเมินราคารออนุมัติกี่ใบ")
    assert p["status"] == "pending" and p["date_from"] is None and usage == PLAN_USAGE
    assert pj.plan("ใบเสนอราคาคืออะไร")[0] is None                        # a definition stays a chat answer

    def down(q, prev, today):
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(pj, "_plan_cached", down)
    assert pj.plan("มีงานที่ไม่อนุมัติราคากี่งาน")[0]["status"] == "reject"   # the planner down too


@pytest.mark.parametrize("q, want", [
    ("ตอนนี้มีใบประเมินราคารออนุมัติกี่ใบ", ("pending", None, None)),
    ("ใบประเมินที่ยังไม่อนุมัติมีไหม", ("pending", None, None)),
    ("มีงานที่ไม่อนุมัติราคากี่งาน", ("reject", None, None)),
    ("เดือนนี้เคาะราคาไปกี่งาน", ("approve", "2026-11-01", "2026-11-18")),
    ("งานเคาะราคาเดือนตุลาคมมีอะไรบ้าง", ("approve", "2026-10-01", "2026-10-31")),
    ("งานเคาะราคา ม.ค. มีกี่งาน", ("approve", "2026-01-01", "2026-01-31")),
    ("สัปดาห์ที่แล้วมีงานเคาะราคากี่งาน", ("approve", "2026-11-09", "2026-11-15")),
    ("ใบเสนอราคาคืออะไร", None),
    ("ใบเสนอราคามีกี่แบบ", None),
    ("ราคาอนุมัติแล้วต้องทำอะไรต่อ", None),
    ("ช่วยร่างใบเสนอราคาให้หน่อย", None),
    ("วิธีขออนุมัติราคา ทำยังไง", None),
])
def test_the_fallback_rule(q, want):
    p = pj.fallback_plan(q, dt.date(2026, 11, 18))
    got = (p["status"], p["date_from"] and str(p["date_from"]), p["date_to"] and str(p["date_to"])) if p else None
    assert got == want


def test_date_anchors_for_the_planner():
    a = pj._date_anchors(dt.date(2026, 11, 17))   # a Tuesday
    assert "สัปดาห์นี้ = 2026-11-16 ถึง 2026-11-17" in a
    assert "สัปดาห์ที่แล้ว = 2026-11-09 ถึง 2026-11-15" in a
    assert "เดือนที่แล้ว = 2026-10-01 ถึง 2026-10-31" in a


# ── SQL ─────────────────────────────────────────────────────────────────────

def test_search_reads_the_old_system_minus_what_mi2_owns():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 17),
         "keyword": "AROMA'; DROP TABLE x--"}
    rows_sql, totals_sql = pj.build_search(p)
    for sql in (rows_sql, totals_sql):
        # the live master list on .19, without MI2's rows: those are read from MI2 itself
        assert "FROM ESTIMATE.dbo.tb_rfq_list l WITH (NOLOCK)" in sql
        assert "ISNULL(l.source_system, 'V1') <> 'V2'" in sql
        assert ("NOT EXISTS (SELECT 1 FROM MI2_ESTIMATE.dbo.tb_rfq m2 WITH (NOLOCK) WHERE ISNULL(m2.is_deleted, 0) = 0 "
                "AND m2.doc_id COLLATE DATABASE_DEFAULT = l.job_id COLLATE DATABASE_DEFAULT)") in sql
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
    assert "x.total_price - ISNULL(x.tax, 0) AS price_ex" in rows_sql                     # before tax, like MI2
    # export jobs (USD / EUR) keep baht in total_price, so every priced job is summed
    assert "SUM(CASE WHEN p.price_ex > 0 THEN p.price_ex END) AS total" in totals_sql
    assert "x.currency_no, x.exchange_rate" in rows_sql and "p.exchange_rate" in rows_sql


def test_mi2_is_read_directly_in_the_same_shape():
    # prod 2026-11-18 worry: once the old app is retired, a stopped sync into the
    # master list must not turn into "เดือนนี้ไม่มีงาน"
    p = {"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18),
         "keyword": "AROMA'; DROP TABLE x--"}
    sqls = pj.build_mi2(p)
    assert len(sqls) == 6 and all("FROM MI2_ESTIMATE.dbo.tb_rfq r WITH (NOLOCK)" in q for q in sqls)
    for q in sqls:
        assert "ISNULL(r.is_deleted, 0) = 0" in q and "r.estimate_status = 'approve'" in q
        assert "r.approved_at >= '2026-11-01'" in q and "r.approved_at < '2026-11-19'" in q
        assert "r.estimate_name NOT LIKE N'%ทดสอบ%'" in q
        assert "N'%AROMA DROP TABLE x--%'" in q and "';" not in q
    rows = sqls[0]
    assert rows.startswith(f"SELECT TOP {pj.MAX_ROWS} r.doc_id AS job_id, r.estimate_name AS job_name")
    assert "CAST(r.approved_at AS datetime) AS estimate_date" in rows and "'V2' AS source_system" in rows
    assert "WHEN 'approve' THEN 3" in rows and "WHEN r.doc_id LIKE 'EB%' THEN 'book'" in rows
    assert "r.estimate_status IN ('pending', 'reject', 'approve')" in pj.build_mi2({"status": "any"})[0]
    assert pj.build_mi2(dict(p, full_list=True))[0].startswith("SELECT TOP 1000 ")


def test_long_lists_get_breakdowns_in_the_same_session(monkeypatch):
    sqls = pj.build_breakdowns({"status": "approve", "date_from": dt.date(2026, 11, 1),
                                "date_to": dt.date(2026, 11, 18), "keyword": None})
    assert sqls[0].startswith("SELECT DISTINCT l.customer_id ")
    assert "GROUP BY ISNULL(l.est_type, '-')" in sqls[1] and "GROUP BY ISNULL(p.currency_no, 'THB')" in sqls[2]
    assert sqls[3].startswith("SELECT TOP 10 l.customer_id") and "GROUP BY l.customer_id" in sqls[3]
    assert "SUM(CASE WHEN p.price_ex > 0 AND p.exchange_rate > 0 THEN p.price_ex / p.exchange_rate END) AS total_fx"         in sqls[2]
    assert sqls[4].startswith("SELECT TOP 5 l.job_id") and "ORDER BY CASE WHEN p.price_ex > 0" in sqls[4]
    assert all("l.estimate_date >= '2026-11-01'" in q for q in sqls)
    calls = []
    monkeypatch.setattr(pj, "_run_both", lambda a, b: calls.append((a, b)) or ([[] for _ in a], [[] for _ in b]))
    pj.search({"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18),
               "keyword": None})
    # old: rows, totals, 5 breakdowns, latest · MI2: rows, totals, ids, by type, per customer, priciest, latest
    assert len(calls) == 1 and len(calls[0][0]) == 8 and len(calls[0][1]) == 7      # one connection


def test_a_full_list_fetches_every_row_but_the_fallback_stays_small(monkeypatch):
    # prod 2026-11-18: "ถ้าให้แสดงมาทั้ง 661 งานเลยได้มั้ย" got the same newest-10 table again
    p = {"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18), "keyword": None,
         "full_list": True}
    calls = []
    monkeypatch.setattr(pj, "_run_both", lambda a, b: calls.append((a, b)) or ([[] for _ in a], [[] for _ in b]))
    pj.search(p)
    (legacy, mi2), = calls
    assert legacy[0].startswith("SELECT TOP 1000 ") and legacy[-1].startswith("SELECT TOP 3 ")
    assert mi2[0].startswith("SELECT TOP 1000 ") and mi2[-1].startswith("SELECT TOP 3 ")
    assert pj.build_search(dict(p, full_list=False))[0].startswith(f"SELECT TOP {pj.MAX_ROWS} ")


MI2_ROW = {"job_id": "E26110600", "job_name": "กล่อง MI2", "customer_id": "C2014056", "custName": "โชติวัฒน์ (MI2)",
           "est_type": "packaging", "status_id": 3, "source_system": "V2",
           "estimate_date": dt.datetime(2026, 11, 18, 14, 0), "created": dt.datetime(2026, 11, 18, 9, 0),
           "updated": dt.datetime(2026, 11, 18, 14, 0), "mid_qty": 1000, "mid_total_ex_vat": Decimal("90000.00")}


def _results(legacy_rows, mi2_rows):
    legacy = [legacy_rows,
              [{"n": 655, "total": 73_000_000.0, "priced": 592, "other_currency": 63}],
              [{"customer_id": "C2014056"}, {"customer_id": "C9999998"}],
              [{"est_type": "packaging", "n": 453, "total": 41_000_000.0}, {"est_type": "book", "n": 195, "total": 30.0}],
              [{"currency": "THB", "n": 592, "total": 73_000_000.0}, {"currency": "USD", "n": 52, "total": 5.0}],
              [{"customer_id": "C2014056", "customer": "โชติวัฒน์อุตสาหกรรมการผลิต", "n": 9, "total": 500_000.0},
               {"customer_id": "C9999998", "customer": "ลูกค้าใหม่", "n": 4, "total": 100_000.0}],
              [ROW]]
    mi2 = [mi2_rows,
           [{"n": 6, "total": Decimal("1027473.89"), "priced": 6}],
           [{"customer_id": "c2014056 "}, {"customer_id": "C2000001"}],
           [{"est_type": "packaging", "n": 6, "total": Decimal("1027473.89")}],
           [{"customer_id": "C2014056", "customer": "โชติวัฒน์ (MI2)", "n": 2, "total": Decimal("90000.00")},
            {"customer_id": "C9999998", "customer": "บริษัท ใหม่ จำกัด", "n": 4, "total": Decimal("937473.89")}],
           [MI2_ROW]]
    return legacy, mi2


def test_both_systems_are_merged_into_one_answer(monkeypatch):
    monkeypatch.setattr(pj, "_run_both", lambda a, b: _results([ROW], [MI2_ROW]))
    out = pj.search({"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18)})
    assert out["count"] == 661 and out["priced"] == 598 and out["other_currency"] == 63
    assert out["total"] == pytest.approx(74_027_473.89)                # float + Decimal, no TypeError
    assert out["customers"] == 3                                        # C2014056 in both counts once
    assert [r["job_id"] for r in out["rows"]] == ["E26110600", "E26110464"]   # newest first across systems
    assert {b["est_type"]: b["n"] for b in out["by_type"]} == {"packaging": 459, "book": 195}
    assert {b["currency"]: b["n"] for b in out["by_currency"]} == {"THB": 598, "USD": 52}
    top = {b["customer_id"]: b for b in out["top_customers"]}
    assert top["C9999998"]["customer"] == "ลูกค้าใหม่" and top["C9999998"]["n"] == 8          # not one prospect's name
    assert top["C2014056"]["total"] == pytest.approx(590_000.0)
    assert out["top_customers"][0]["customer_id"] == "C9999998"         # 1,037,473.89 > 590,000
    assert [r["job_id"] for r in out["priciest"]] == ["E26110600", "E26110464"]
    assert out["legacy_missing"] is False


def test_the_old_system_gone_still_answers_from_mi2(monkeypatch):
    class Session:
        def __init__(self, error):
            self.error = error

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, sql):
            if "ESTIMATE.dbo.tb_rfq_list" in sql:
                raise self.error
            return [{"n": 6, "total": 1.0, "priced": 6}] if "COUNT(*) AS n" in sql else []

    gone = Exception("[42S02] [SQL Server]Invalid object name 'ESTIMATE.dbo.tb_rfq_list'. (208)")
    monkeypatch.setattr(pj.factory, "session", lambda: Session(gone))
    out = pj.search({"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18)})
    assert out["legacy_missing"] is True and out["count"] == 6
    assert "ข้อมูลจากระบบประเมินราคาใหม่ MI2 เท่านั้น" in pj.to_context({"status": "approve"}, out)
    closed = Exception("The SELECT permission was denied on the object 'tb_rfq_price1', database 'ESTIMATE'")
    monkeypatch.setattr(pj.factory, "session", lambda: Session(closed))
    assert pj.search({"status": "approve"})["legacy_missing"] is True
    # anything else is an error, never a quietly smaller number
    for other in (Exception("Query timeout expired"),
                  Exception("Database 'MI2_ESTIMATE' does not exist"),
                  Exception("Invalid column name 'source_system'")):
        monkeypatch.setattr(pj.factory, "session", lambda e=other: Session(e))
        with pytest.raises(Exception):
            pj.search({"status": "approve"})


def test_doc_lookup_validates_numbers(monkeypatch):
    seen = []

    def run_both(legacy, mi2):
        seen.extend(legacy + mi2)
        return [[] for _ in legacy], [[] for _ in mi2]

    monkeypatch.setattr(pj, "_run_both", run_both)
    monkeypatch.setattr(pj, "_run", lambda sqls: seen.extend(sqls) or [[] for _ in sqls])
    assert pj.lookup_doc("E2611'; DROP", None)["rows"] == [] and seen == []
    pj.lookup_doc("eb26110174", None)
    assert "FROM ESTIMATE.dbo.tb_rfq_list l" in seen[0] and "l.job_id = 'EB26110174'" in seen[0]
    assert "FROM ESTIMATE.dbo.tb_rfq_price1" in seen[1] and "FROM ESTIMATE.dbo.tb_rfq_status_log" in seen[2]
    assert "FROM MI2_ESTIMATE.dbo.tb_rfq r" in seen[3] and "r.doc_id = 'EB26110174'" in seen[3]
    assert "FROM MI2_ESTIMATE.dbo.tb_quotation" in seen[4]


def test_doc_lookup_takes_the_live_status_from_mi2(monkeypatch):
    # the master list still says "รออนุมัติ" (sync late); MI2 has it approved
    stale = dict(ROW_V2, status_id=1, estimate_date=None, credit_term_name="เครดิต 30 วัน")
    live = dict(ROW_V2, qty_list="500", print_type="Offset", box_type="กล่องหูช้าง", component_type="-")
    monkeypatch.setattr(pj, "_run_both", lambda a, b: ([[stale], [], []], [[live], []]))
    got = pj.lookup_doc("E26110334", None)
    assert got["rows"][0]["status_id"] == 3 and got["rows"][0]["credit_term_name"] == "เครดิต 30 วัน"
    assert got["legacy_missing"] is False
    monkeypatch.setattr(pj, "_run_both", lambda a, b: (None, [[live], []]))       # old system gone
    got = pj.lookup_doc("E26110334", None)
    assert got["rows"][0]["job_id"] == "E26110334" and got["legacy_missing"] is True


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


# ── context for the answer model ────────────────────────────────────────────

def test_list_context_has_count_total_and_prices():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 17), "keyword": None}
    free = dict(ROW, job_id="E26110465", price_ex=None, total_price=None, tiers=0)
    text = pj.to_context(p, {"rows": [ROW, ROW_V2, free], "count": 5, "total": 52927.45, "priced": 4,
                             "other_currency": 1,
                             "by_type": [{"est_type": "packaging", "n": 4, "total": 52927.45},
                                         {"est_type": "book", "n": 1, "total": None}],
                             "by_currency": [{"currency": "THB", "n": 4, "total": 52927.45},
                                             {"currency": "USD", "n": 1, "total": 580637.94,
                                              "total_fx": 18628.10}],
                             "top_customers": [{"customer": "ลูกค้าใหม่", "customer_id": "C9999998", "n": 2,
                                                "total": 25832.50}]})
    # a long list is summarised: by type, by currency, the biggest customers — and it is not revenue
    assert "แยกตามประเภทงาน: packaging 4 งาน (มูลค่า 52,927.45 บาท) · book 1 งาน" in text
    # export jobs: baht, with the customer's currency beside it (checked on 30 jobs, 2026-11-18)
    assert ("แยกตามสกุลเงินที่เสนอลูกค้า: THB 4 งาน (52,927.45 บาท) · "
            "USD 1 งาน (580,637.94 บาท ≈ 18,628.10 USD)") in text
    assert "1 งานที่เสนอราคาลูกค้าเป็นเงินต่างประเทศ" in text and "รวมอยู่ในมูลค่าบาทข้างบนด้วย" in text
    assert "จาก 4 งานที่มีราคา — อีก 1 งานยังไม่มีราคา" in text
    assert "1) ลูกค้าใหม่ (รวมลูกค้าที่ยังไม่ลงทะเบียนหลายราย) 2 งาน 25,832.50 บาท" in text
    assert "ไม่ใช่ยอดขายจริง" in text
    # the team's definition (confirmed 2026-11-18) travels with every approved-list answer
    assert "'เคาะราคาแล้ว' = ใบประเมินราคาที่หัวหน้าอนุมัติราคาแล้ว (Approved ในระบบประเมินราคา)" in text
    assert "นิยาม" not in pj.to_context(dict(p, status="pending"), {"rows": [], "count": 0, "total": None,
                                                                    "priced": 0})
    assert "พบทั้งหมด: 5 งาน" in text and "52,927.45 บาท" in text
    # old system: its tax line taken off — 27,094.95 incl. 789.17 tax = the new system's 26,305.78 ex VAT
    assert "26,305.78 บาท ก่อน VAT ที่จำนวน 3,000 ชิ้น (≈ 8.77 บาท/ชิ้น)" in text and "27,094.95" not in text
    assert "25,832.50 บาท ก่อน VAT ที่จำนวน 500 ชิ้น" in text                       # new system: ex VAT
    assert "ยังไม่มีราคา" in text and "ข้อมูลประกอบ: 3 งานล่าสุด" in text
    # the bot told users "ระบบแสดงเฉพาะรายการล่าสุด 40 งาน" — the row cap is internal now
    assert "ห้ามบอกผู้ใช้ว่าระบบดึงหรือแสดงได้แค่จำนวนนี้" in text and "ไม่เกิน 40" not in text
    assert "ระบบจะแนบตารางรายการงานต่อท้ายคำตอบให้เอง" in text      # the model must not write its own table
    assert "ระบบประเมินราคาเดิม" in text and "ระบบประเมินราคาใหม่ (MI2)" in text
    assert "อนุมัติแล้ว (เคาะราคาแล้ว) · อนุมัติเมื่อ 2026-11-18 11:25" in text
    # one labelled field per line — a one-line "a | b | c" record got copied into an answer
    assert ("[E26110464] Rep.SRP (Tray) - Can Pack 24 x 7\n"
            "  ลูกค้า: โชติวัฒน์อุตสาหกรรมการผลิต (C2014056)") in text
    assert " | " not in text


EXPORT = dict(ROW, job_id="EB26110201", job_name="Mensa Crossword PAD 2028", custName="IMAGO PUBLISHING",
              est_type="book", price_qty=15810, price_ex=580637.94, total_price=580637.94, currency_no="USD",
              exchange_rate=31.17)


def test_an_export_job_is_baht_with_its_foreign_amount_beside_it():
    # prod 2026-11-18: the table said "580,637.94 USD" — the stored price is baht (580,637.94 ÷ 15,810 ÷ 31.17
    # = 1.1782 USD a copy = unit_price_exchange)
    table = pj.list_table({"status": "approve"}, [EXPORT], 1)
    assert "| 15,810 | 580,637.94 THB (≈ 18,628.10 USD) |" in table and "580,637.94 USD" not in table
    text = pj._price_text(EXPORT)
    assert text.startswith("580,637.94 บาท ก่อน VAT ที่จำนวน 15,810 ชิ้น (≈ 36.73 บาท/ชิ้น)")
    assert "เสนอลูกค้าเป็น USD: ≈ 18,628.10 USD (≈ 1.1782 USD/ชิ้น) ที่อัตรา 31.17 บาท/USD" in text
    assert pj._fx(ROW) is None and pj._fx(dict(EXPORT, exchange_rate=None)) is None
    assert pj._thb(EXPORT) == pytest.approx(580637.94)                   # counts for "แพงสุด" too


def test_list_table_is_built_in_code():
    # prod 2026-11-18: the model's table lost its |---| line and showed as a paragraph of bars
    named = dict(ROW, job_name="Box A | B")
    table = pj.list_table({"status": "approve"}, [named, ROW_V2], 661)
    lines = table.split("\n")
    assert lines[0] == "**รายการล่าสุด 2 จากทั้งหมด 661 งาน**" and lines[1] == ""
    assert lines[2] == "| เลขที่ | ชื่องาน | ลูกค้า | จำนวน | ราคา (ก่อน VAT) | อนุมัติเมื่อ |"
    assert lines[3] == "|---|---|---|---:|---:|---|"
    assert lines[4] == ("| E26110464 | Box A / B | โชติวัฒน์อุตสาหกรรมการผลิต | 3,000 | 26,305.78 THB | "
                        "2026-11-18 11:25 |")
    assert lines[5] == "| E26110334 | กล่อง Jaelek | ลูกค้าใหม่ | 500 | 25,832.50 THB | 2026-11-13 11:18 |"
    many = pj.list_table({"status": "approve"}, [ROW] * 40, 661)
    assert many.count("| E26110464 |") == 10                    # more than 15 → the newest 10
    assert pj.list_table({"status": "approve"}, [ROW] * 12, 12).count("| E26110464 |") == 12
    assert "**" not in pj.list_table({"status": "approve"}, [ROW], 1)   # all shown → no "x จากทั้งหมด"
    assert pj.list_table({"status": "approve"}, [], 0) == ""
    # "ขอดูทั้งหมด": every fetched row, and the title says so
    full = pj.list_table({"status": "approve", "full_list": True}, [ROW] * 661, 661)
    assert full.count("| E26110464 |") == 661 and full.startswith("**รายการทั้งหมด 661 งาน**")
    capped = pj.list_table({"status": "approve", "full_list": True}, [ROW] * 1000, 52000)
    assert capped.count("| E26110464 |") == 1000 and capped.startswith("**รายการล่าสุด 1,000 จากทั้งหมด 52,000 งาน**")


def test_full_list_and_priciest_in_the_context():
    p = {"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18), "keyword": None,
         "full_list": True}
    dear = dict(ROW, job_id="E26119999", price_ex=Decimal("1500000"), price_qty=10000)
    text = pj.to_context(p, {"rows": [ROW] * 661, "count": 661, "total": 1.0, "priced": 598,
                             "priciest": [dear]})
    assert "ผู้ใช้ขอดูรายการทั้งหมด" in text and "ห้ามบอกว่าแสดงได้ไม่ครบ" in text
    assert text.count("[E26110464]") == pj.MAX_ROWS            # the model still sees only MAX_ROWS rows
    # "อันไหนแพงสุด" is answered over the whole range, not over the newest rows
    assert "5 งานที่ราคาสูงสุดในเงื่อนไขนี้ (เทียบเป็นเงินบาทจากทั้งหมด 661 งาน" in text and "[E26119999]" in text


def test_empty_and_single_document_contexts():
    p = {"status": "approve", "date_from": None, "date_to": None, "keyword": "XYZ"}
    assert "พบทั้งหมด: 0 งาน" in pj.to_context(p, {"rows": [], "count": 0, "total": None, "priced": 0})
    one = pj.to_context({"doc_id": "E26110464"}, {
        "rows": [dict(ROW, credit_term_name="เครดิต 30 วัน")],
        "prices": [{"qty": 3000, "price_ex": 26305.78, "total_price": 27094.95, "currency_no": "THB"},
                   {"qty": 5000, "price_ex": 40500.0, "total_price": 41715.0, "currency_no": "THB"}],
        "history": [{"from_status_id": 0, "to_status_id": 1, "created": dt.datetime(2026, 11, 17, 16, 19)},
                    {"from_status_id": 1, "to_status_id": 3, "created": dt.datetime(2026, 11, 18, 11, 25)}],
        "detail": [], "quotes": [], "doc_id": "E26110464"})
    assert "3,000 ชิ้น: 26,305.78 บาท ก่อน VAT (≈ 8.77 บาท/ชิ้น)" in one and "5,000 ชิ้น: 40,500.00 บาท ก่อน VAT" in one
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
    shown = "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert "1 งาน" in shown
    # the list table is appended in code after the model's summary
    assert "\n\n| เลขที่ | ชื่องาน |" in shown and "| E26110464 |" in shown and "|---|" in shown
    assert live.safety == []                                   # the AI classifier was skipped
    assert live.queries and live.queries[0]["status"] == "approve"
    docs, prompt = live.contexts[0]
    assert docs[0][0] == "ระบบประเมินราคา" and "E26110464" in docs[0][1]
    assert "ระบบประเมินราคา" in prompt
    saved = live.saved[-1]
    assert saved["source"] == "priced_jobs"
    assert saved["usage"]["model_used"] == "gpt-4.1"
    assert saved["usage"]["cost_usd"] == pytest.approx(0.0041)   # planner + answer


def test_a_check_on_the_last_answer_is_answered_not_repeated(live, monkeypatch):
    # prod 2026-11-18: "ของเดือนนี้มี 661 ใบเลยหรอ" got the whole answer and its table again
    monkeypatch.setattr(pj, "plan", lambda q, previous="": (
        {"doc_id": None, "quotation_id": None, "status": "approve", "date_from": dt.date(2026, 11, 1),
         "date_to": dt.date(2026, 11, 18), "keyword": None, "show_list": False}, dict(PLAN_USAGE)))
    live.history = [{"role": "user", "content": "เดือนนี้มีงานเคาะราคาแล้วกี่งาน"},
                    {"role": "assistant", "content": "661 ใบค่ะ …"}]
    events = stream("ของเดือนนี้มี 661 ใบเลยหรอ")
    shown = "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert events[-1]["source"] == "priced_jobs" and "| เลขที่ |" not in shown      # no table again
    docs, _ = live.contexts[-1]
    assert "ผู้ใช้ไม่ได้ขอดูรายการ" in docs[0][1] and "ระบบจะแนบตาราง" not in docs[0][1]


def test_show_list_is_decided_by_rule_not_by_the_planner(monkeypatch):
    # prod 2026-11-18: the planner said show_list=false for "ของเดือนตุลาคมหล่ะ" (a new month)
    raw = json.dumps({"lookup": True, "status": "approve", "date_from": "2026-10-01", "date_to": "2026-10-31",
                      "show_list": False})
    monkeypatch.setattr(pj, "_plan_cached", lambda q, prev, today: (raw, json.dumps(PLAN_USAGE)))
    assert pj.plan("ของเดือนตุลาคมหล่ะ")[0]["show_list"] is True
    assert pj.plan("ของเดือนนี้มี 661 ใบเลยหรอ")[0]["show_list"] is False
    assert pj.plan("ทำไมตาราง excel ไม่ออกมาด้วยครับ")[0]["show_list"] is True
    assert pj.plan("E26110334 ราคาเท่าไหร่")[0]["show_list"] is True
    assert pj.plan("ถ้าให้แสดงมาทั้ง 661 งานเลยได้มั้ยครับ")[0]["full_list"] is True
    assert pj.plan("ของเดือนตุลาคมหล่ะ")[0]["full_list"] is False


def test_asking_for_everything_gets_every_row(live, monkeypatch):
    monkeypatch.setattr(pj, "plan", lambda q, previous="": (
        {"doc_id": None, "quotation_id": None, "status": "approve", "date_from": dt.date(2026, 11, 1),
         "date_to": dt.date(2026, 11, 18), "keyword": None, "show_list": True, "full_list": True}, dict(PLAN_USAGE)))
    monkeypatch.setattr(pj, "search", lambda p: {"rows": [ROW] * 30, "count": 30, "total": 1.0, "priced": 30})
    live.history = [{"role": "user", "content": "เดือนนี้มีงานเคาะราคาแล้วกี่งาน"},
                    {"role": "assistant", "content": "30 งานค่ะ …"}]
    events = stream("ถ้าให้แสดงมาทั้ง 30 งานเลยได้มั้ยครับ")
    shown = "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert "**รายการทั้งหมด 30 งาน**" in shown and shown.count("| E26110464 |") == 30
    text = pj.to_context({"status": "approve", "date_from": dt.date(2026, 11, 1), "date_to": dt.date(2026, 11, 18),
                          "keyword": None},
                         {"rows": [ROW], "count": 661, "total": 1.0, "priced": 598, "customers": 214})
    assert "เฉลี่ยวันละ 36.7 งาน (ช่วง 18 วัน) · ลูกค้า 214 ราย · นับทุกใบที่อนุมัติ รวมใบแก้ไข/หลายเวอร์ชัน" in text


def test_a_follow_up_keeps_the_previous_question(live):
    live.history = [{"role": "user", "content": "งานที่เคาะราคาแล้วเดือนนี้มีอะไรบ้าง"},
                    {"role": "assistant", "content": "มี 3 งาน …"}]
    events = stream("แล้วเดือนที่แล้วล่ะ")
    assert events[-1]["source"] == "priced_jobs"
    assert live.plans[-1] == ("แล้วเดือนที่แล้วล่ะ", "งานที่เคาะราคาแล้วเดือนนี้มีอะไรบ้าง")


def test_a_follow_up_gets_the_whole_question_chain(live):
    # prod 2026-11-18: after "ของเดือนตุลาคมหล่ะ" the next follow-up was planned from the
    # first question only and answered November again
    live.history = [{"role": "user", "content": "เดือนนี้มีงานเคาะราคาแล้วกี่งาน"},
                    {"role": "assistant", "content": "661 ใบค่ะ"},
                    {"role": "user", "content": "ของเดือนนี้มี 661 ใบเลยหรอ"},
                    {"role": "assistant", "content": "ใช่ค่ะ"},
                    {"role": "user", "content": "ของเดือนตุลาคมหล่ะ"},
                    {"role": "assistant", "content": "เดือนตุลาคม…"}]
    stream("ทำไมตาราง excel ไม่ออกมาด้วยครับ")
    assert live.plans[-1] == ("ทำไมตาราง excel ไม่ออกมาด้วยครับ",
                              "เดือนนี้มีงานเคาะราคาแล้วกี่งาน → ของเดือนนี้มี 661 ใบเลยหรอ → ของเดือนตุลาคมหล่ะ")


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
