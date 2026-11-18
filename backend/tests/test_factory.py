"""
Live MI job system (on .19 via OPENQUERY) — job status / production / delivery
/ QC by job number, due dates, machine repairs, QC overview. Offline: the DB
session and the AI planner/answer are faked.
"""

import datetime as dt
from decimal import Decimal

import pytest

import factory as fx
import linsip
import main
import priced_jobs as pj
from tests.test_v4_glue import pipe, post, stream  # noqa: F401  (pipe is a fixture)

REAL_IS = main._is_factory_query
REAL_TRY = main._try_factory_answer
TODAY = dt.date(2026, 11, 18)  # a Wednesday

MASTER = {"jobid": "J52600854", "job_name": "สมุดบันทึก ธกส ปี 2570", "custid": "C2010218",
          "custName": "ธนาคารเพื่อการเกษตรและสหกรณ์การเกษตร", "qty1": 229430,
          "createDate": dt.datetime(2026, 10, 1, 19, 34), "artworkDate": "2026-10-01", "ok1": "2026-10-06",
          "printDate": "2026-10-07", "due1": "2026-10-26", "cancelJob": 0, "deleteJob": 0, "parent_jobid": None,
          "rfq_mi2": None}


def overview(**over):
    o = {"found": True, "job": "J52600854", "master": dict(MASTER), "closed": [],
         "plan": [{"machine_id": "3423", "machine_name": "L444APC", "plan_date": "2026-11-05"},
                  {"machine_id": "3423", "machine_name": "L444APC", "plan_date": "2026-11-12"}],
         "production": [{"machine_id": "28140001", "machine_name": "CTP 1", "sheets": 23,
                         "first_start": dt.datetime(2026, 10, 12, 20, 5), "last_end": dt.datetime(2026, 11, 14, 13, 39),
                         "qty": 12, "waste": 133}],
         "requests": [{"n": 20, "qty": Decimal("2350"), "first_date": dt.datetime(2026, 10, 26, 13, 30),
                       "last_date": dt.datetime(2026, 11, 30)}],
         "upcoming": [{"delivery_date": dt.datetime(2026, 11, 30), "qty": Decimal("500")}],
         "delivered": [{"dockets": 20, "qty": Decimal("2350"), "first_date": dt.datetime(2026, 11, 4, 13, 10),
                        "last_date": dt.datetime(2026, 11, 6, 14, 53)}],
         "pallets": [{"pallets": 52, "pieces": Decimal("42820"), "last_product_date": dt.date(2026, 11, 18),
                      "received": 32, "issued": 24}],
         "qc": [{"inspections": 10, "checked": 4300, "failed": 14, "first_date": dt.datetime(2026, 11, 11),
                 "last_date": dt.datetime(2026, 11, 15)}],
         "qc_problems": [{"problem_name": "ปกยับ / ปกเป็นรอย", "major": 1, "minor": 6}],
         "checklists": [{"n": 10, "approved": 9, "last_date": dt.date(2026, 10, 22)}]}
    o.update(over)
    return o


# ── job numbers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,job", [
    ("J52600854 ถึงไหนแล้ว", "J52600854"), ("j82602753 ส่งยัง", "J82602753"),
    ("งาน 26110209 ส่งของหรือยัง", "26110209"), ("เลขงาน 26110096-1 ปิดงานยัง", "26110096-1"),
    ("job 26090450-1 qc ผ่านไหม", "26090450-1"),
])
def test_job_numbers_are_found(q, job):
    assert fx.extract_job(q) == job


@pytest.mark.parametrize("q", ["โทร 0812345678 ได้ไหม", "ยอดขาย 26110209 บาท", "E26110001 ราคาเท่าไหร่", "สวัสดี"])
def test_other_numbers_are_not_jobs(q):
    assert fx.extract_job(q) is None


def test_values_put_into_sql_are_made_safe():
    assert fx.safe_job("J52600854") == "J52600854"
    assert fx.safe_job("J1'; DROP TABLE mi--") is None
    assert fx.safe_keyword("AROMA'; DROP TABLE x--%_[") == "AROMA DROP TABLE x--"
    assert fx.safe_keyword("เครื่อง L444APC (ตัวที่ 2)") == "เครื่อง L444APC (ตัวที่ 2)"
    assert fx.safe_keyword("  ") is None


def test_openquery_wrapping_doubles_quotes(monkeypatch):
    s = fx._Session()
    s.direct = False
    got = []

    class Cur:
        def execute(self, sql):
            got.append(sql)

        def fetchall(self):
            return []

    s.cur = Cur()
    s.run("SELECT 1 FROM mi.dbo.mi WHERE jobid = 'J52600854' AND job_name LIKE N'%กล่อง%'")
    assert got[0] == ("SELECT * FROM OPENQUERY([192.168.5.19], 'SELECT 1 FROM mi.dbo.mi WHERE jobid = ''J52600854'' "
                      "AND job_name LIKE N''%กล่อง%''')")


def test_job_queries_use_indexes_bounds_and_no_personal_columns():
    qs = fx.job_queries("J52600854")
    assert "WHERE m.jobid = 'J52600854'" in qs["master"]
    assert "header_jobid = 'J52600854'" in qs["production"] and "plan_id IN" in qs["production"]
    assert "MAX(qc_head_id) - 20000" in qs["qc"]                  # tb_qc_head has no job index
    assert "ISNULL(k.canceled, 0) = 0" in qs["delivered"]
    every = " ".join(qs.values()).lower()
    for col in ("contact", "emp_id", "worker", "address", "destination", "price", "wage", "remark"):
        assert col not in every, col


def test_due_overdue_repair_and_qc_queries():
    rows, by_day = fx.due_queries({"kind": "due", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 22),
                                   "keyword": "ธกส'; --"}, TODAY)
    assert "m.due1 >= '2026-11-16'" in rows and "m.due1 <= '2026-11-22'" in rows
    assert "N'%ธกส --%'" in rows and "';" not in rows.split("N'%")[1]
    assert "NOT EXISTS (SELECT 1 FROM mi.dbo.mi_close_job" in rows and "GROUP BY m.due1" in by_day
    assert "m.job_name NOT LIKE N'%ยกเลิก%'" in rows      # renamed "ยกเลิก …" jobs are cancelled in practice
    rows, _ = fx.due_queries({"kind": "overdue"}, TODAY)
    # only due dates in the last 30 days: years-old due dates on recent jobs are data-entry noise
    assert "m.due1 >= '2026-10-19'" in rows and "m.due1 <= '2026-11-17'" in rows
    assert "ISNULL(x.delivered, 0) < m.qty1" in rows
    rows, counts = fx.repair_queries({"kind": "repairs", "status": "open"}, TODAY)
    assert "r.ma_status = 0" in rows and "r.notify_datetime >= '2026-08-20'" in rows   # open: last 90 days
    assert "COLLATE DATABASE_DEFAULT" in rows and "emp" not in rows and "GROUP BY r.ma_status" in counts
    assert "r.ma_status = 0" not in counts       # counts cover every status in the period
    summary, problems, jobs = fx.qc_queries({"kind": "qc", "date_from": dt.date(2026, 11, 1)}, TODAY)
    assert "h.qc_datetime >= '2026-11-01'" in summary and "MAX(qc_head_id) - 20000" in problems


# ── planner ─────────────────────────────────────────────────────────────────

def test_plan_is_cleaned_and_dates_anchor_on_bangkok_week():
    assert fx._clean_plan({"kind": "none"}) is None
    p = fx._clean_plan({"kind": "repairs", "date_from": "2026-11-30", "date_to": "2026-11-01",
                        "keyword": "L444'%", "status": "weird"})
    assert (p["date_from"], p["date_to"], p["keyword"], p["status"]) == (
        dt.date(2026, 11, 1), dt.date(2026, 11, 30), "L444", "any")
    a = fx._date_anchors(TODAY)
    assert "สัปดาห์นี้ = 2026-11-16 ถึง 2026-11-22" in a and "สัปดาห์หน้า = 2026-11-23 ถึง 2026-11-29" in a
    assert "เดือนนี้ = 2026-11-01 ถึง 2026-11-30" in a


@pytest.mark.parametrize("q", ["สัปดาห์นี้มีงานต้องส่งกี่งาน", "งานไหนเลยกำหนดส่งบ้าง", "เครื่องไหนค้างซ่อมอยู่",
                               "เดือนนี้ QC เจอปัญหาอะไรบ่อย"])
def test_ops_questions_reach_the_planner(q):
    assert fx.mentions_ops(q)


def test_ordinary_questions_do_not():
    assert not fx.mentions_ops("สวัสดีครับ วันนี้อากาศดีไหม")


# ── context text ────────────────────────────────────────────────────────────

def test_job_context_tells_the_whole_story():
    text = fx.job_context(overview())
    for part in ("J52600854", "ธนาคารเพื่อการเกษตร", "229,430", "วันกำหนดส่งลูกค้า: 2026-10-26", "ยังไม่ปิดงาน",
                 "L444APC: 2026-11-05 ถึง 2026-11-12 (2 รอบ)", "CTP 1", "ส่งของแล้ว (ตามใบส่งของ): 20 ใบ รวม 2,350",
                 "คงเหลือตามจำนวนสั่ง 227,080", "นัดส่งที่ยังไม่ถึง: 2026-11-30 จำนวน 500", "52 พาเลท",
                 "QC: ตรวจ 10 ครั้ง", "ปกยับ", "checklist QC หน้าเครื่อง: 10 ใบ"):
        assert part in text, part
    closed = fx.job_context(overview(closed=[{"created": dt.datetime(2026, 11, 16, 20, 7), "job_qty": 6000}],
                                     master=dict(MASTER, cancelJob=1)))
    assert "ปิดงานแล้ว: ใช่ เมื่อ 2026-11-16 20:07" in closed and "ยกเลิกงานแล้ว" in closed
    bare = fx.job_context(overview(plan=[], production=[], qc=[{}], pallets=[{}], checklists=[{}]))
    assert "แผนเครื่องจักร: ยังไม่มี" in bare and "บันทึกการผลิตจริง: ยังไม่มี" in bare and "QC: ยังไม่มีผลตรวจ" in bare
    assert "ไม่พบเลขงาน J99999999" in fx.job_context({"found": False, "job": "J99999999"})


def test_ops_contexts():
    due = fx.ops_context({"kind": "due", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 22),
                          "keyword": None},
                         {"rows": [{"jobid": "J82602753", "job_name": "กล่อง", "custName": "ลูกค้า ก", "qty1": 5000,
                                    "due1": "2026-11-20", "printDate": "2026-11-19", "delivered": None}], "count": 251,
                          "by_day": [("2026-11-20", 120), ("2026-11-21", 131)]})
    assert "พบทั้งหมด: 251 งาน (รายการด้านล่างคือ 1 งานแรก" in due and "กำหนดส่ง 2026-11-20" in due
    assert "ส่งแล้ว 0" in due and "2026-11-20 = 120 งาน, 2026-11-21 = 131 งาน" in due
    open_ = fx.ops_context({"kind": "repairs", "status": "open"},
                           {"counts": {0: 140, 1: 168, 2: 31}, "rows": [{"ma_id": "MR1", "ma_status": 0}]}, today=TODAY)
    assert "ทั้งหมด 339 ใบ · ยังไม่เสร็จ 140 · ซ่อมเสร็จ 168" in open_
    assert "ผู้ใช้ถามเฉพาะใบที่ยังไม่เสร็จ: 140 ใบ (รายการด้านล่างคือ 1 ใบล่าสุด)" in open_
    rep = fx.ops_context({"kind": "repairs", "date_from": None, "date_to": None, "keyword": "L444"},
                         {"counts": {0: 2, 1: 5}, "rows": [{"ma_id": "MR26110178", "notify_datetime": dt.datetime(2026, 11, 17, 15, 36),
                                                            "machine_name": "L444SP", "ma_status": 0, "approve_status": 1,
                                                            "problem": "ตรวจเช็คไฟฟ้าสถิต", "solution": None}]})
    assert "ทั้งหมด 7 ใบ · ยังไม่เสร็จ 2 · ซ่อมเสร็จ 5" in rep and "แจ้งซ่อม (ยังไม่เสร็จ) (อนุมัติแล้ว)" in rep
    qc = fx.ops_context({"kind": "qc", "date_from": dt.date(2026, 11, 1), "date_to": TODAY, "keyword": None},
                        {"summary": {"inspections": 663, "jobs": 101, "checked": 90000, "failed": 536},
                         "problems": [{"problem_name": "ปกยับ", "times": 78, "major": 16, "minor": 302}],
                         "jobs": [{"job_id": "J52600854", "job_name": "สมุด", "inspections": 10, "failed": 14}]})
    assert "ตรวจ 663 ครั้ง · 101 งาน" in qc and "ปกยับ: 78 ครั้ง" in qc and "J52600854" in qc


def test_names_in_repair_notes_are_dropped():
    assert fx.scrub_names("-สั่งซื้อ Proximity Sensor PE-B7D จำนวน 2 ตัว(ณรงค์ศักดิ์)") == \
        "สั่งซื้อ Proximity Sensor PE-B7D จำนวน 2 ตัว"
    assert "สมชาย" not in fx.scrub_names("แจ้งคุณสมชายให้เปลี่ยนลูกปืนแล้ว")
    assert fx.scrub_names("ชุดลูกกลิ้งส่งงานเข้าฉากไม่ทำงาน") == "ชุดลูกกลิ้งส่งงานเข้าฉากไม่ทำงาน"
    assert fx.scrub_names(None) == "-"
    rep = fx.ops_context({"kind": "repairs", "status": "any"}, {"counts": {1: 1}, "rows": [
        {"ma_id": "MR1", "machine_name": "L444SP", "ma_status": 1, "approve_status": 1,
         "problem": "กดลิฟไม่ได้", "solution": "เปลี่ยน sensor (ณรงค์ศักดิ์)"}]}, today=TODAY)
    assert "ณรงค์ศักดิ์" not in rep and "ช่วงวันที่แจ้ง: 2026-11-11 ถึง 2026-11-18" in rep


def test_cancelled_by_name_is_pointed_out():
    text = fx.job_context(overview(master=dict(MASTER, job_name="ยกเลิก - MIMS Thailand 4/2026")))
    assert "น่าจะเป็นงานที่ยกเลิกแล้ว" in text


def test_offers_and_promises_are_trimmed_from_data_answers():
    tidy = main._tidy_data_answer
    # 26110209 on prod (2026-11-18): the fact stays, the offer/promise goes
    got = tidy("งาน 26110209 ตอนนี้ยังไม่เริ่มผลิต และยังไม่ปิดงานครับ/ค่ะ\n\nหมายเหตุเพิ่มเติม\n"
               "- ในชื่องานมีคำว่า “ยกเลิก” ระบบระบุว่าอาจเป็นงานที่ยกเลิกแล้ว แม้ยังไม่ได้ติ๊กยกเลิกในระบบ "
               "หากต้องการให้ยืนยันสถานะการยกเลิกอย่างเป็นทางการ แจ้งได้ครับ/ค่ะ จะตรวจเช็คให้ต่อเนื่อง")
    assert got.startswith("งาน 26110209 ตอนนี้ยังไม่เริ่มผลิต และยังไม่ปิดงานค่ะ")
    assert "อาจเป็นงานที่ยกเลิกแล้ว แม้ยังไม่ได้ติ๊กยกเลิกในระบบ" in got
    assert "หากต้องการ" not in got and "ตรวจเช็คให้" not in got and "ครับ/ค่ะ" not in got
    # a whole offer line goes; a heading left empty goes with it
    got = tidy("มีใบแจ้งซ่อมค้าง 140 ใบ\n- MR1 ...\n\nหมายเหตุ\n"
               "- หากต้องการรายชื่อครบทุกเครื่อง แจ้งได้เลยครับ จะสรุปให้เป็นชุดถัดไป")
    assert got == "มีใบแจ้งซ่อมค้าง 140 ใบ\n- MR1 ..."
    plain = "สัปดาห์นี้มีงานต้องส่ง 229 งาน\n| เลขงาน | ชื่องาน |"
    assert tidy(plain) == plain
    # prod 2026-11-18: an offer phrased with verbs the first list didn't have
    got = tidy("เดือนนี้ยังไม่มีงานเคาะราคาเลยค่ะ\n- ระบบระบุว่าพบทั้งหมด 0 งาน\n"
               "หากต้องการดูย้อนหลังมากกว่านี้หรือเปลี่ยนช่วงวันที่ สามารถระบุช่วงใหม่เพื่อให้ดึงข้อมูลเพิ่มเติมได้ค่ะ")
    assert got == "เดือนนี้ยังไม่มีงานเคาะราคาเลยค่ะ\n- ระบบระบุว่าพบทั้งหมด 0 งาน"


def test_a_record_copied_as_one_piped_bullet_is_split():
    # the estimate answer on prod, 2026-11-18
    got = main._tidy_data_answer(
        "มีงานเคาะราคาแล้วเดือนนี้ 1 งาน รวมมูลค่า 31,947.09 บาท (ราคาก่อน VAT)\n\n"
        "รายละเอียดจากระบบประเมินราคา:\n"
        "- E26110001 | กล่อง AROMA OIL แบบปลอกสวม | ลูกค้า: ศิริ แลบอราทอรีส์ | จำนวนที่ขอราคา: 2,000 ชิ้น | "
        "ราคา: 31,947.09 บาท (ก่อน VAT ที่จำนวน 2,000 ชิ้น) | อนุมัติ: 2026-11-06 14:52\n\n"
        "ข้อมูลอ้างอิงจากระบบประเมินราคา ช่วงวันที่ 2026-11-01 ถึง 2026-11-18 โดยแสดงเฉพาะรายการล่าสุดตามเงื่อนไขดังกล่าว")
    assert got.endswith(
        "- **E26110001 กล่อง AROMA OIL แบบปลอกสวม**\n"
        "  - ลูกค้า: ศิริ แลบอราทอรีส์\n"
        "  - จำนวนที่ขอราคา: 2,000 ชิ้น\n"
        "  - ราคา: 31,947.09 บาท (ก่อน VAT ที่จำนวน 2,000 ชิ้น)\n"
        "  - อนุมัติ: 2026-11-06 14:52")
    assert "ข้อมูลอ้างอิงจาก" not in got and " | " not in got
    table = "สัปดาห์นี้ 2 งาน\n\n| เลขที่ | ชื่องาน | จำนวน |\n|---|---|---|\n| E1 | กล่อง | 100 |\n| E2 | ซอง | 50 |"
    assert main._tidy_data_answer(table) == table              # real tables stay as they are
    assert main._tidy_data_answer("- ส่งแล้ว 2,350 | เหลือ 0") == "- ส่งแล้ว 2,350 | เหลือ 0"   # one bar: left alone


def test_off_switch(monkeypatch):
    import mi_auth

    monkeypatch.setattr(mi_auth, "mi_enabled", lambda: True)
    monkeypatch.setattr(fx, "FACTORY_MODE", "off")
    assert not fx.enabled()
    monkeypatch.setattr(fx, "FACTORY_MODE", "on")
    assert fx.enabled()


# ── priced jobs: nothing in range → the newest ones instead ─────────────────

def test_priced_jobs_empty_range_gives_the_latest(monkeypatch):
    calls = []

    def fake_query(sql, params=()):
        calls.append(sql)
        if sql.startswith("SELECT COUNT(*)"):        # the totals query (rows count price tiers inside)
            return [{"n": 0, "total": None, "priced": 0}]
        return [] if "l.estimate_date >=" in sql else [{"job_id": "E26110464", "job_name": "Rep.SRP (Tray)",
                                                         "status_id": 3, "source_system": "V1", "price_qty": 3000,
                                                         "price_ex": 26305.78, "total_price": 27094.95,
                                                         "currency_no": "THB", "tiers": 1}]

    monkeypatch.setattr(pj, "_run", lambda sqls: [fake_query(s) for s in sqls])   # one session, three queries
    p = {"status": "approve", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 18), "keyword": None}
    data = pj.search(p)
    assert data["count"] == 0 and data["latest"][0]["job_id"] == "E26110464"
    assert calls[-1].startswith("SELECT TOP 3 ") and "l.estimate_date >=" not in calls[-1]
    text = pj.to_context(p, data)
    assert "พบทั้งหมด: 0 งาน" in text and "งานล่าสุดที่ตรงสถานะ" in text and "E26110464" in text


# ── in the chat pipeline ────────────────────────────────────────────────────

@pytest.fixture
def live(pipe, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "_is_factory_query", REAL_IS)
    monkeypatch.setattr(main, "_try_factory_answer", REAL_TRY)
    monkeypatch.setattr(fx, "enabled", lambda: True)
    monkeypatch.setattr(linsip, "LINSIP_ENABLED", False)
    pipe.jobs, pipe.plans, pipe.docs = [], [], []
    monkeypatch.setattr(fx, "job_overview", lambda job: pipe.jobs.append(job) or overview(job=job))
    monkeypatch.setattr(fx, "plan", lambda q, previous="": pipe.plans.append((q, previous)) or (
        {"kind": "due", "date_from": dt.date(2026, 11, 16), "date_to": dt.date(2026, 11, 22), "keyword": None,
         "status": "any"}, {"model": "gpt-4o-mini", "prompt_tokens": 300, "completion_tokens": 30, "cost_usd": 0.0001}))
    monkeypatch.setattr(fx, "run_plan", lambda p: {"rows": [], "count": 0})

    def answer(question, images, docs, history=None, system_prompt=None):
        pipe.docs.append((docs, system_prompt))
        return "งานนี้ส่งไปแล้ว 2,350 ชิ้น", {"model": "gpt-4.1", "prompt_tokens": 2000, "completion_tokens": 150,
                                             "cost_usd": 0.005}

    monkeypatch.setattr(main, "answer_with_files", answer)
    return pipe


def test_job_question_is_answered_from_mi(live):
    events = stream("J52600854 ถึงไหนแล้ว ส่งไปกี่ชิ้น")
    assert events[-1]["source"] == "factory_job"
    assert live.jobs == ["J52600854"] and live.safety == [] and live.plans == []   # no classifier, no planner
    docs, prompt = live.docs[0]
    assert docs[0][0] == "ระบบงาน MI" and "J52600854" in docs[0][1] and "ระบบงาน MI" in prompt
    assert live.saved[-1]["source"] == "factory_job"


def test_wi_page_is_added_for_j_jobs_when_linsip_is_on(live, monkeypatch):
    monkeypatch.setattr(linsip, "LINSIP_ENABLED", True)
    monkeypatch.setattr(linsip, "fetch_job_wi", lambda job: ("ใบสั่งงาน WI " + job, "กาว: PVA"))
    stream("J52600854 ใช้กาวอะไร ส่งยัง")
    docs, _ = live.docs[-1]
    assert [d[0] for d in docs] == ["ระบบงาน MI", "ใบสั่งงาน WI J52600854"]


def test_job_follow_up_reuses_the_number(live):
    live.history = [{"role": "user", "content": "งาน 26110209 ถึงไหนแล้ว"}, {"role": "assistant", "content": "…"}]
    events = stream("แล้วส่งของไปหรือยัง")
    assert events[-1]["source"] == "factory_job" and live.jobs == ["26110209"]


def test_due_question_goes_through_the_planner(live):
    events = stream("สัปดาห์นี้มีงานต้องส่งกี่งาน")
    # asked by the classifier gate and by the answer step — one AI call in real life (_plan_cached)
    assert events[-1]["source"] == "factory" and set(live.plans) == {("สัปดาห์นี้มีงานต้องส่งกี่งาน", "")}
    assert "พบทั้งหมด: 0 งาน" in live.docs[0][0][0][1]
    assert live.saved[-1]["usage"]["cost_usd"] == pytest.approx(0.0051)


def test_not_a_lookup_goes_the_normal_way(live, monkeypatch):
    monkeypatch.setattr(fx, "plan", lambda q, previous="": (None, {}))
    events = stream("วิธีซ่อมเครื่องพิมพ์เบื้องต้นทำยังไง")
    assert events[-1]["source"] not in {"factory", "factory_job"}
    assert live.safety == ["วิธีซ่อมเครื่องพิมพ์เบื้องต้นทำยังไง"]


def test_mi_down_gives_a_thai_message(live, monkeypatch):
    def boom(job):
        raise ConnectionError("linked server timeout")

    monkeypatch.setattr(fx, "job_overview", boom)
    events = stream("J52600854 ถึงไหนแล้ว")
    assert events[-1]["source"] == "factory_error"
    assert "ดึงข้อมูลจากระบบงาน MI ไม่สำเร็จ" in "".join(e.get("v", "") for e in events if e["type"] == "delta")
    assert "factory_failed" in live.audits and live.docs == []


def test_switched_off_leaves_linsip_in_charge(live, monkeypatch):
    monkeypatch.setattr(fx, "enabled", lambda: False)
    assert main._try_factory_answer({"id": 1}, "J52600854 ถึงไหนแล้ว", 5, "normal", None) is None


def test_non_stream_chat_too(live):
    r = post("J52600854 ถึงไหนแล้ว").json()
    assert r["source"] == "factory_job" and "2,350" in r["answer"]
