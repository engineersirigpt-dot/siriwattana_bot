"""Live factory data from the MI job system — read-only.

The live MI databases are on 192.168.5.19 (mi, QC_MASTER, MAINTENANCE_MACHINE).
The SiriGPT MI account reaches them through the linked server that the MI
login server (MI_DB_SERVER, 192.168.5.10) already has, so every query here is
wrapped in OPENQUERY and runs ON .19. Once IT gives SiriGPT its own read-only
account on .19, set FACTORY_DB_SERVER (+ _USER/_PASSWORD) to query it directly.

Six lookups (owner request 2026-11-17, everyone may ask):
  1. job status, 2. delivery, 3. production — job_overview(job no.)
  4. due soon / overdue — due_jobs()
  5. machine repairs — repairs()
  6. QC — qc_overview() (+ per job inside job_overview)

Rules: fixed SELECTs only; every value put into SQL is validated or reduced to
a safe character set first (OPENQUERY takes no parameters); lookups use an
indexed column or a bounded id range; NOLOCK reads never block MI users. Never
selected: people's names/ids, contacts, addresses, wages, prices.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from functools import lru_cache

FACTORY_MODE = os.getenv("FACTORY_MODE", "on").strip().lower()
FACTORY_LINK = os.getenv("FACTORY_LINK", "192.168.5.19").strip()
FACTORY_DB_SERVER = os.getenv("FACTORY_DB_SERVER", "").strip()
_DB_TIMEOUT = 20
MAX_ROWS = 40

if not re.fullmatch(r"[A-Za-z0-9._\-]{1,64}", FACTORY_LINK):
    FACTORY_LINK = "192.168.5.19"

# J82602753 · 26110209 · 26110096-1
JOB_J_RE = re.compile(r"\bJ\d{8}(?:-\d{1,2})?\b", re.IGNORECASE)
_JOB_NUM_RE = re.compile(r"(?:งาน|จ๊อบ|จ็อบ|job\s*(?:id|no\.?)?|เลขที่งาน|เลขงาน)\s*[:#]?\s*(2\d{7}(?:-\d{1,2})?)\b",
                         re.IGNORECASE)
JOB_FOLLOWUP_RE = re.compile(
    r"งานนี้|งานนั้น|จ๊อบนี้|job\s*นี้|ถึงไหน|สถานะ|status|ผลิต|พิมพ์|ส่งของ|ส่งแล้ว|ส่งไป|ส่งครบ|ค้างส่ง|"
    r"เหลือ|ปิดงาน|เสร็จ|กำหนด|due|qc|คิวซี|ตรวจ|พาเลท|pallet|ยอด|เครื่อง|แผน|ลูกค้า|เมื่อไหร่|วันไหน",
    re.IGNORECASE,
)
# non-job questions that may be for due dates / repairs / QC (the AI planner decides)
OPS_TRIGGER_RE = re.compile(
    r"กำหนดส่ง|ครบกำหนด|ถึงกำหนด|เลยกำหนด|เกินกำหนด|ส่งไม่ทัน|ต้องส่ง|งานค้างส่ง|ค้างส่ง|"
    r"แจ้งซ่อม|ใบซ่อม|ใบแจ้งซ่อม|ซ่อม|เครื่องเสีย|เครื่องพัง|เครื่องจักร.{0,10}(เสีย|ปัญหา)|"
    r"\bqc\b|คิวซี|ตรวจคุณภาพ|ปัญหาคุณภาพ|งานเสีย|ของเสีย|ตีกลับ|\breject",
    re.IGNORECASE,
)

MA_STATUS = {0: "แจ้งซ่อม (ยังไม่เสร็จ)", 1: "ซ่อมเสร็จแล้ว", 2: "ยกเลิก"}
MA_APPROVE = {0: "รออนุมัติ", 1: "อนุมัติแล้ว", 2: "ไม่อนุมัติ", 3: "ส่งให้ช่างแล้ว"}


def enabled() -> bool:
    if FACTORY_MODE in {"off", "0", "false", "no"}:
        return False
    if FACTORY_DB_SERVER:
        return True
    try:
        from mi_auth import mi_enabled
    except Exception:
        return False
    return mi_enabled()


def extract_job(text: str) -> str | None:
    """A job number in the text: J + 8 digits, or 8 digits (+ -n) next to the word งาน/job."""
    if not text:
        return None
    m = JOB_J_RE.search(text)
    if m:
        return m.group(0).upper()
    m = _JOB_NUM_RE.search(text)
    return m.group(1) if m else None


def looks_like_job_followup(text: str) -> bool:
    return bool(text) and JOB_FOLLOWUP_RE.search(text) is not None


def mentions_ops(text: str) -> bool:
    return bool(text and OPS_TRIGGER_RE.search(text))


# ── safe values for OPENQUERY (it takes no parameters) ──────────────────────

def safe_job(job: str | None) -> str | None:
    return job.upper() if job and re.fullmatch(r"[A-Za-z0-9\-]{4,20}", job) else None


def safe_keyword(text: str | None) -> str | None:
    """Letters (Thai/English), digits, spaces and - . / ( ) & + only; no SQL or LIKE specials."""
    if not text:
        return None
    kept = re.sub(r"[^0-9A-Za-z฀-๿ \-./()&+]", "", str(text)).strip()
    kept = re.sub(r"\s+", " ", kept)[:40]
    return kept or None


def _day(d: dt.date | None) -> str | None:
    return d.isoformat() if isinstance(d, dt.date) else None


# ── database access ─────────────────────────────────────────────────────────

class _Session:
    """One connection per question; run(inner) executes `inner` on .19."""

    def __enter__(self):
        import pymssql

        if FACTORY_DB_SERVER:
            self.direct = True
            self.conn = pymssql.connect(
                server=FACTORY_DB_SERVER, port=int(os.getenv("FACTORY_DB_PORT", "1433")),
                user=os.getenv("FACTORY_DB_USER", ""), password=os.getenv("FACTORY_DB_PASSWORD", ""),
                database="mi", login_timeout=10, timeout=_DB_TIMEOUT, charset="UTF-8")
        else:
            from mi_auth import MI_DB_PASSWORD, MI_DB_PORT, MI_DB_SERVER, MI_DB_USER

            self.direct = False
            self.conn = pymssql.connect(server=MI_DB_SERVER, port=MI_DB_PORT, user=MI_DB_USER,
                                        password=MI_DB_PASSWORD, database="master", login_timeout=10,
                                        timeout=_DB_TIMEOUT, charset="UTF-8")
        self.cur = self.conn.cursor(as_dict=True)
        return self

    def run(self, inner: str) -> list[dict]:
        sql = inner if self.direct else f"SELECT * FROM OPENQUERY([{FACTORY_LINK}], '{inner.replace(chr(39), chr(39) * 2)}')"
        self.cur.execute(sql)
        return list(self.cur.fetchall())

    def __exit__(self, *exc):
        try:
            self.conn.close()
        except Exception:
            pass


def session() -> _Session:
    return _Session()


# ── 1–3, 6: one job ─────────────────────────────────────────────────────────

def job_queries(job: str) -> dict[str, str]:
    """The fixed queries for one job (job already validated by safe_job)."""
    j = f"'{job}'"
    return {
        "master": f"""SELECT m.jobid, m.job_name, m.custid, c.custName, m.qty1, m.createDate, m.artworkDate, m.ok1,
                m.printDate, m.due1, m.cancelJob, m.deleteJob, m.parent_jobid, m.rfq_mi2
            FROM mi.dbo.mi m WITH (NOLOCK) LEFT JOIN mi.dbo.customer c WITH (NOLOCK) ON c.custID = m.custid
            WHERE m.jobid = {j}""",
        "closed": f"SELECT TOP 1 created, job_qty FROM mi.dbo.mi_close_job WITH (NOLOCK) WHERE job_id = {j} ORDER BY created DESC",
        "plan": f"""SELECT TOP 80 p.machine_id, mc.machine_name, p.plan_date
            FROM mi.dbo.machine_planning p WITH (NOLOCK)
            LEFT JOIN mi.dbo.machine mc WITH (NOLOCK) ON mc.machine_id = p.machine_id
            WHERE p.jobid = {j} AND p.machine_id IS NOT NULL AND p.machine_id <> ''""",
        "production": f"""SELECT h.machine_id, mc.machine_name, COUNT(DISTINCT h.header_id) AS sheets,
                MIN(i.startTime) AS first_start, MAX(i.endTime) AS last_end, SUM(i.qty) AS qty, SUM(i.waste) AS waste
            FROM mi.dbo.timesheet_header h WITH (NOLOCK)
            JOIN mi.dbo.timesheet_item i WITH (NOLOCK) ON i.header_id = h.header_id
            LEFT JOIN mi.dbo.machine mc WITH (NOLOCK) ON mc.machine_id = h.machine_id
            WHERE h.header_id IN (
                SELECT header_id FROM mi.dbo.timesheet_header WITH (NOLOCK) WHERE header_jobid = {j}
                UNION SELECT header_id FROM mi.dbo.timesheet_header WITH (NOLOCK)
                      WHERE plan_id IN (SELECT id FROM mi.dbo.machine_planning WITH (NOLOCK) WHERE jobid = {j}))
            GROUP BY h.machine_id, mc.machine_name ORDER BY MIN(i.startTime)""",
        "requests": f"""SELECT COUNT(*) AS n, SUM(qty) AS qty, MIN(delivery_date) AS first_date,
                MAX(delivery_date) AS last_date
            FROM mi.dbo.mi_delivery WITH (NOLOCK) WHERE JobId = {j}""",
        "upcoming": f"""SELECT TOP 5 delivery_date, qty FROM mi.dbo.mi_delivery WITH (NOLOCK)
            WHERE JobId = {j} AND delivery_date >= CONVERT(date, GETDATE()) ORDER BY delivery_date""",
        "delivered": f"""SELECT COUNT(DISTINCT d.ddid) AS dockets, SUM(d.qty) AS qty, MIN(k.createtime) AS first_date,
                MAX(k.createtime) AS last_date
            FROM mi.dbo.mi_delivery_docket_detail d WITH (NOLOCK)
            LEFT JOIN mi.dbo.mi_delivery_docket k WITH (NOLOCK) ON k.ddid = d.ddid
            WHERE d.jobid = {j} AND ISNULL(k.canceled, 0) = 0""",
        "pallets": f"""SELECT COUNT(*) AS pallets, SUM(qty_pieces) AS pieces, MAX(product_date) AS last_product_date,
                SUM(CASE WHEN receive_status = 1 THEN 1 ELSE 0 END) AS received,
                SUM(CASE WHEN issue_status = 1 THEN 1 ELSE 0 END) AS issued
            FROM mi.dbo.FG_Product_Pallet WITH (NOLOCK) WHERE job_id = {j}""",
        # tb_qc_head has no job index: look only at the newest 20,000 inspections (~2 years)
        "qc": f"""SELECT COUNT(*) AS inspections, SUM(problem_all) AS checked, SUM(problem_fail) AS failed,
                MIN(qc_datetime) AS first_date, MAX(qc_datetime) AS last_date
            FROM QC_MASTER.dbo.tb_qc_head WITH (NOLOCK)
            WHERE job_id = {j} AND qc_head_id > (SELECT MAX(qc_head_id) - 20000 FROM QC_MASTER.dbo.tb_qc_head)""",
        "qc_problems": f"""SELECT TOP 5 p.problem_name, SUM(d.major_count) AS major, SUM(d.minor_count) AS minor
            FROM QC_MASTER.dbo.tb_qc_detail d WITH (NOLOCK)
            JOIN QC_MASTER.dbo.tb_qc_head h WITH (NOLOCK) ON h.qc_head_id = d.qc_head_id
            LEFT JOIN QC_MASTER.dbo.tb_qc_problem p WITH (NOLOCK) ON p.problem_id = d.problem_id
            WHERE h.job_id = {j} AND h.qc_head_id > (SELECT MAX(qc_head_id) - 20000 FROM QC_MASTER.dbo.tb_qc_head)
              AND d.qc_detail_id > (SELECT MAX(qc_detail_id) - 60000 FROM QC_MASTER.dbo.tb_qc_detail)
              AND (d.major_count > 0 OR d.minor_count > 0)
            GROUP BY p.problem_name ORDER BY SUM(d.major_count) + SUM(d.minor_count) DESC""",
        "checklists": f"""SELECT COUNT(*) AS n, SUM(CASE WHEN status_approved = '1' THEN 1 ELSE 0 END) AS approved,
                MAX(doc_date) AS last_date
            FROM mi.dbo.tb_timesheet_checklist_qc_head WITH (NOLOCK) WHERE jobid = {j}""",
    }


def job_overview(job: str) -> dict:
    """Everything about one job. Raises on a DB error; {'found': False} when unknown."""
    job = safe_job(job)
    if not job:
        return {"found": False, "job": None}
    out: dict = {"job": job}
    with session() as s:
        qs = job_queries(job)
        master = s.run(qs["master"])
        if not master:
            return {"found": False, "job": job}
        out["found"] = True
        out["master"] = master[0]
        for key in ("closed", "plan", "production", "requests", "upcoming", "delivered", "pallets", "qc",
                    "qc_problems", "checklists"):
            out[key] = s.run(qs[key])
    return out


# ── 4: due soon / overdue ───────────────────────────────────────────────────

def effective_range(p: dict, today: dt.date) -> tuple[dt.date, dt.date]:
    """The date range a question really covers once the defaults are filled in."""
    kind = p.get("kind")
    if kind == "overdue":   # due dates years ago on recent jobs are data-entry noise: the last 30 days only
        yesterday = today - dt.timedelta(days=1)
        return p.get("date_from") or today - dt.timedelta(days=30), min(p.get("date_to") or yesterday, yesterday)
    if kind == "repairs":
        back = 90 if p.get("status") == "open" else 7
        return p.get("date_from") or today - dt.timedelta(days=back), p.get("date_to") or today
    if kind == "qc":
        return p.get("date_from") or today - dt.timedelta(days=7), p.get("date_to") or today
    return p.get("date_from") or today, p.get("date_to") or today + dt.timedelta(days=7)


def due_queries(p: dict, today: dt.date) -> tuple[str, str]:
    """(rows, count) for jobs due in a range ('due') or past due and not fully delivered ('overdue')."""
    kw = safe_keyword(p.get("keyword"))
    d_from, d_to = effective_range(p, today)
    # Jobs renamed "ยกเลิก …" are cancelled in practice even when the cancel flag isn't set.
    where = ["m.createDate >= DATEADD(day, -240, GETDATE())", "ISNULL(m.cancelJob, 0) = 0",
             "ISNULL(m.deleteJob, 0) = 0", "LEN(m.due1) = 10", "m.job_name NOT LIKE N'%ยกเลิก%'",
             "NOT EXISTS (SELECT 1 FROM mi.dbo.mi_close_job c WITH (NOLOCK) WHERE c.job_id = m.jobid)",
             f"m.due1 >= '{_day(d_from)}'", f"m.due1 <= '{_day(d_to)}'"]
    if p.get("kind") == "overdue":
        where.append("ISNULL(x.delivered, 0) < m.qty1")
    order = "m.due1, m.jobid"
    if kw:
        where.append(f"(m.job_name LIKE N'%{kw}%' OR c.custName LIKE N'%{kw}%' OR m.jobid = '{kw}')")
    w = " AND ".join(where)
    base = f"""FROM mi.dbo.mi m WITH (NOLOCK)
        LEFT JOIN mi.dbo.customer c WITH (NOLOCK) ON c.custID = m.custid
        OUTER APPLY (SELECT SUM(d.qty) AS delivered FROM mi.dbo.mi_delivery_docket_detail d WITH (NOLOCK)
                     WHERE d.jobid = m.jobid) x
        WHERE {w}"""
    rows = f"SELECT TOP {MAX_ROWS} m.jobid, m.job_name, c.custName, m.qty1, m.due1, m.printDate, x.delivered {base} ORDER BY {order}"
    by_day = f"SELECT m.due1, COUNT(*) AS n {base} GROUP BY m.due1 ORDER BY m.due1"
    return rows, by_day


def due_jobs(p: dict, today: dt.date | None = None) -> dict:
    today = today or _bangkok_today()
    rows_sql, by_day_sql = due_queries(p, today)
    with session() as s:
        rows = s.run(rows_sql)
        by_day = [(str(r.get("due1")), int(r.get("n") or 0)) for r in s.run(by_day_sql)]
    return {"rows": rows, "count": sum(n for _, n in by_day), "by_day": by_day}


# ── 5: machine repairs ──────────────────────────────────────────────────────

def repair_queries(p: dict, today: dt.date) -> tuple[str, str]:
    kw = safe_keyword(p.get("keyword"))
    status = p.get("status") or "any"
    d_from, d_to = effective_range(dict(p, kind="repairs"), today)
    where = [f"r.notify_datetime >= '{_day(d_from)}'", f"r.notify_datetime < '{_day(d_to + dt.timedelta(days=1))}'"]
    if kw:
        where.append(f"(m.machine_name LIKE N'%{kw}%' OR r.machine_id = '{kw}' "
                     f"OR CAST(r.ma_remark AS nvarchar(max)) LIKE N'%{kw}%')")
    joins = """FROM MAINTENANCE_MACHINE.dbo.ma_request r WITH (NOLOCK)
        LEFT JOIN MAINTENANCE_MACHINE.dbo.v_machine m WITH (NOLOCK)
          ON m.machine_id COLLATE DATABASE_DEFAULT = r.machine_id COLLATE DATABASE_DEFAULT"""
    # the counts cover the whole period (so "ค้างซ่อม 140" sits next to how many were done)
    counts = f"SELECT r.ma_status, COUNT(*) AS n {joins} WHERE {' AND '.join(where)} GROUP BY r.ma_status"
    if status in {"open", "done", "cancelled"}:
        where.append(f"r.ma_status = {({'open': 0, 'done': 1, 'cancelled': 2})[status]}")
    rows = (f"SELECT TOP {MAX_ROWS} r.ma_id, r.notify_datetime, r.need_datetime, r.machine_id, m.machine_name, "
            f"r.ma_status, r.approve_status, r.ma_complete_date, "
            f"LEFT(CAST(r.ma_remark AS nvarchar(max)), 200) AS problem, "
            f"LEFT(CAST(r.ma_solution_ma AS nvarchar(max)), 200) AS solution {joins} WHERE {' AND '.join(where)} "
            f"ORDER BY r.notify_datetime DESC")
    return rows, counts


def repairs(p: dict, today: dt.date | None = None) -> dict:
    today = today or _bangkok_today()
    rows_sql, counts_sql = repair_queries(p, today)
    with session() as s:
        rows = s.run(rows_sql)
        counts = {int(r["ma_status"] or 0): int(r["n"]) for r in s.run(counts_sql)}
    return {"rows": rows, "counts": counts}


# ── 6: QC overview ──────────────────────────────────────────────────────────

def qc_queries(p: dict, today: dt.date) -> tuple[str, str, str]:
    kw = safe_keyword(p.get("keyword"))
    d_from, d_to = effective_range(dict(p, kind="qc"), today)
    where = [f"h.qc_datetime >= '{_day(d_from)}'", f"h.qc_datetime < '{_day(d_to + dt.timedelta(days=1))}'",
             "h.qc_head_id > (SELECT MAX(qc_head_id) - 20000 FROM QC_MASTER.dbo.tb_qc_head)"]
    if kw:
        where.append(f"(h.job_id = '{kw}' OR h.machine_id = '{kw}' OR h.job_description LIKE N'%{kw}%')")
    w = " AND ".join(where)
    summary = (f"SELECT COUNT(*) AS inspections, COUNT(DISTINCT h.job_id) AS jobs, SUM(h.problem_all) AS checked, "
               f"SUM(h.problem_fail) AS failed FROM QC_MASTER.dbo.tb_qc_head h WITH (NOLOCK) WHERE {w}")
    problems = f"""SELECT TOP 10 p.problem_name, COUNT(*) AS times, SUM(d.major_count) AS major, SUM(d.minor_count) AS minor
        FROM QC_MASTER.dbo.tb_qc_detail d WITH (NOLOCK)
        JOIN QC_MASTER.dbo.tb_qc_head h WITH (NOLOCK) ON h.qc_head_id = d.qc_head_id
        LEFT JOIN QC_MASTER.dbo.tb_qc_problem p WITH (NOLOCK) ON p.problem_id = d.problem_id
        WHERE {w} AND d.qc_detail_id > (SELECT MAX(qc_detail_id) - 60000 FROM QC_MASTER.dbo.tb_qc_detail)
          AND (d.major_count > 0 OR d.minor_count > 0)
        GROUP BY p.problem_name ORDER BY COUNT(*) DESC"""
    jobs = f"""SELECT TOP 10 h.job_id, MAX(h.job_description) AS job_name, COUNT(*) AS inspections,
            SUM(h.problem_fail) AS failed
        FROM QC_MASTER.dbo.tb_qc_head h WITH (NOLOCK) WHERE {w} AND h.problem_fail > 0
        GROUP BY h.job_id ORDER BY SUM(h.problem_fail) DESC"""
    return summary, problems, jobs


def qc_overview(p: dict, today: dt.date | None = None) -> dict:
    today = today or _bangkok_today()
    summary_sql, problems_sql, jobs_sql = qc_queries(p, today)
    with session() as s:
        summary = (s.run(summary_sql) or [{}])[0]
        problems = s.run(problems_sql)
        jobs = s.run(jobs_sql)
    return {"summary": summary, "problems": problems, "jobs": jobs}


# ── planner for the non-job questions (small AI call, cached) ───────────────

def _bangkok_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).replace(tzinfo=None)


def _bangkok_today() -> dt.date:
    return _bangkok_now().date()


_PLAN_PROMPT = (
    "คุณเป็นตัวแยกคำสั่งของ chatbot ภายในโรงพิมพ์ ระบบดูข้อมูลโรงงานได้ 4 แบบ ตอบ JSON เท่านั้น:\n"
    '{"kind": "due"|"overdue"|"repairs"|"qc"|"none", "date_from": "YYYY-MM-DD"|null, '
    '"date_to": "YYYY-MM-DD"|null, "keyword": string|null, "status": "open"|"done"|"cancelled"|"any", '
    '"show_list": true|false}\n'
    "- due: งานที่ถึงกำหนดส่ง/ต้องส่งในช่วงเวลาหนึ่ง (เช่น 'สัปดาห์นี้มีงานต้องส่งกี่งาน')\n"
    "- overdue: งานที่เลยกำหนดส่งแล้วแต่ยังส่งไม่ครบ\n"
    "- repairs: ใบแจ้งซ่อมเครื่องจักร (status: open=ยังไม่เสร็จ/ค้างซ่อม, done=ซ่อมเสร็จ, cancelled=ยกเลิก, any)\n"
    "- qc: ผลตรวจ QC / ปัญหาคุณภาพในช่วงเวลาหนึ่ง\n"
    "- none: อย่างอื่น เช่น ถามวิธีซ่อม ความรู้ทั่วไป ขอให้เขียน/คำนวณ หรือไม่ได้ถามข้อมูลที่บันทึกในระบบ\n"
    "วันที่ (รวมวันสุดท้าย) ใช้ตามนี้: {anchors}. พ.ศ. ให้ลบ 543. ไม่ระบุช่วง → null ทั้งคู่\n"
    "keyword: ชื่อเครื่อง ชื่อลูกค้า ชื่องาน หรือเลขงาน ที่ผู้ใช้ต้องการกรอง ไม่มี → null\n"
    "ถ้ามี 'คำถามก่อนหน้า' และคำถามนี้ถามต่อ (เช่น 'แล้วเดือนที่แล้วล่ะ') ให้ใช้ kind/เงื่อนไขเดิมเว้นแต่เปลี่ยน\n"
    "show_list: true เมื่อต้องการดูรายการ (ถามใหม่หรือเปลี่ยนเงื่อนไข); false เมื่อถามยืนยัน/สงสัยคำตอบก่อนหน้า (เช่น 'มีเยอะขนาดนั้นเลยหรอ') หรือถามเจาะจงข้อเดียว (เช่น 'เครื่องไหนค้างนานสุด')"
)


def _date_anchors(today: dt.date) -> str:
    monday = today - dt.timedelta(days=today.weekday())
    first = today.replace(day=1)
    last_month_end = first - dt.timedelta(days=1)
    next_month_first = (first + dt.timedelta(days=32)).replace(day=1)
    days = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
    return (
        f"วันนี้ = {today} (วัน{days[today.weekday()]}); พรุ่งนี้ = {today + dt.timedelta(days=1)}; "
        f"เมื่อวาน = {today - dt.timedelta(days=1)}; สัปดาห์นี้ = {monday} ถึง {monday + dt.timedelta(days=6)}; "
        f"สัปดาห์หน้า = {monday + dt.timedelta(days=7)} ถึง {monday + dt.timedelta(days=13)}; "
        f"สัปดาห์ที่แล้ว = {monday - dt.timedelta(days=7)} ถึง {monday - dt.timedelta(days=1)}; "
        f"เดือนนี้ = {first} ถึง {next_month_first - dt.timedelta(days=1)}; "
        f"เดือนที่แล้ว = {last_month_end.replace(day=1)} ถึง {last_month_end}"
    )


def _clean_plan(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "none").lower()
    if kind not in {"due", "overdue", "repairs", "qc"}:
        return None

    def day(v):
        try:
            return dt.date.fromisoformat(str(v)[:10]) if v else None
        except ValueError:
            return None

    d_from, d_to = day(data.get("date_from")), day(data.get("date_to"))
    if d_from and d_to and d_from > d_to:
        d_from, d_to = d_to, d_from
    status = str(data.get("status") or "any").lower()
    return {"kind": kind, "date_from": d_from, "date_to": d_to, "keyword": safe_keyword(data.get("keyword")),
            "status": status if status in {"open", "done", "cancelled", "any"} else "any",
            "show_list": data.get("show_list") is not False}


@lru_cache(maxsize=256)
def _plan_cached(question: str, previous: str, today_iso: str) -> tuple[str, str]:
    from llm import LLM_MODEL_CLASSIFIER, _classifier_kwargs, _extract_usage, _get_client

    user = (f"คำถามก่อนหน้า: {previous}\n" if previous else "") + f"คำถาม: {question}"
    res = _get_client().chat.completions.create(
        model=LLM_MODEL_CLASSIFIER,
        messages=[{"role": "system", "content": _PLAN_PROMPT.replace(
            "{anchors}", _date_anchors(dt.date.fromisoformat(today_iso)))},
                  {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        **_classifier_kwargs(LLM_MODEL_CLASSIFIER, 150),
    )
    return res.choices[0].message.content or "{}", json.dumps(_extract_usage(res, LLM_MODEL_CLASSIFIER))


def plan(question: str, previous: str = "") -> tuple[dict | None, dict]:
    try:
        raw, usage = _plan_cached(question.strip(), (previous or "").strip(), _bangkok_today().isoformat())
        return _clean_plan(json.loads(raw)), json.loads(usage)
    except Exception:
        return None, {}


def run_plan(p: dict) -> dict:
    if p["kind"] in {"due", "overdue"}:
        return due_jobs(p)
    if p["kind"] == "repairs":
        return repairs(p)
    return qc_overview(p)


# ── data → text for the answer model ───────────────────────────────────────

def _n(v, digits=0) -> str:
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _when(v, with_time=True) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M" if with_time and (v.hour or v.minute) else "%Y-%m-%d")
    return str(v)[:16]


def _head() -> str:
    return f"ข้อมูลจากระบบงาน MI ของโรงงาน (ดึงเมื่อ {_bangkok_now():%Y-%m-%d %H:%M})"


def job_context(o: dict) -> str:
    if not o.get("found"):
        return f"{_head()}\nไม่พบเลขงาน {o.get('job') or ''} ในระบบงาน MI"
    m = o["master"]
    qty = m.get("qty1") or 0
    out = [_head(), f"เลขงาน: {m.get('jobid')}", f"ชื่องาน: {m.get('job_name') or '-'}",
           f"ลูกค้า: {m.get('custName') or '-'} ({m.get('custid') or '-'})", f"จำนวนสั่งผลิต: {_n(qty)}",
           f"วันเปิดงาน: {_when(m.get('createDate'))}", f"วันได้อาร์ตเวิร์ก: {m.get('artworkDate') or '-'}",
           f"วัน OK งาน: {m.get('ok1') or '-'}", f"วันกำหนดพิมพ์: {m.get('printDate') or '-'}",
           f"วันกำหนดส่งลูกค้า: {m.get('due1') or '-'}"]
    if m.get("rfq_mi2"):
        out.append(f"เปิดจากใบประเมินราคา: {m['rfq_mi2']}")
    if "ยกเลิก" in str(m.get("job_name") or "") and not m.get("cancelJob"):
        out.append("หมายเหตุจากชื่องาน: ชื่องานมีคำว่า 'ยกเลิก' — น่าจะเป็นงานที่ยกเลิกแล้ว แม้ระบบยังไม่ได้ติ๊กยกเลิก")
    if m.get("cancelJob"):
        out.append("สถานะ: ยกเลิกงานแล้ว")
    closed = o.get("closed") or []
    out.append(f"ปิดงานแล้ว: {'ใช่ เมื่อ ' + _when(closed[0].get('created')) if closed else 'ยังไม่ปิดงาน'}")

    plan = o.get("plan") or []
    if plan:
        by: dict[str, list] = {}
        for r in plan:
            by.setdefault(r.get("machine_name") or r.get("machine_id") or "-", []).append(str(r.get("plan_date") or ""))
        out.append("แผนเครื่องจักร (วันที่วางแผน):")
        for name, days in by.items():
            days = sorted(d for d in days if d)
            span = f"{days[0]} ถึง {days[-1]}" if len(set(days)) > 1 else (days[0] if days else "-")
            out.append(f"- {name}: {span} ({len(days)} รอบ)")
    else:
        out.append("แผนเครื่องจักร: ยังไม่มี")

    prod = o.get("production") or []
    if prod:
        out.append("บันทึกการผลิตจริง (timesheet) แยกตามเครื่อง:")
        for r in prod:
            out.append(f"- {r.get('machine_name') or r.get('machine_id')}: {_when(r.get('first_start'))} ถึง "
                       f"{_when(r.get('last_end'))} · {r.get('sheets')} ใบบันทึก · ได้ {_n(r.get('qty'))} · "
                       f"เสีย {_n(r.get('waste'))}")
    else:
        out.append("บันทึกการผลิตจริง: ยังไม่มี")

    req = (o.get("requests") or [{}])[0]
    dlv = (o.get("delivered") or [{}])[0]
    out.append(f"ใบขอส่งของ: {req.get('n') or 0} ใบ รวม {_n(req.get('qty'))} "
               f"(นัดส่ง {_when(req.get('first_date'), False)} ถึง {_when(req.get('last_date'), False)})")
    upcoming = o.get("upcoming") or []
    if upcoming:
        out.append("นัดส่งที่ยังไม่ถึง: " + ", ".join(f"{_when(u.get('delivery_date'), False)} จำนวน {_n(u.get('qty'))}"
                                                   for u in upcoming))
    delivered = float(dlv.get("qty") or 0)
    left = f" · คงเหลือตามจำนวนสั่ง {_n(max(qty - delivered, 0))}" if qty else ""
    out.append(f"ส่งของแล้ว (ตามใบส่งของ): {dlv.get('dockets') or 0} ใบ รวม {_n(delivered)}{left} "
               f"(ครั้งแรก {_when(dlv.get('first_date'))}, ล่าสุด {_when(dlv.get('last_date'))})")
    pal = (o.get("pallets") or [{}])[0]
    if pal.get("pallets"):
        out.append(f"สินค้าสำเร็จรูป (พาเลท): {pal['pallets']} พาเลท รวม {_n(pal.get('pieces'))} ชิ้น · "
                   f"เข้าคลังแล้ว {pal.get('received') or 0} · เบิกออกแล้ว {pal.get('issued') or 0} · "
                   f"ผลิตล่าสุด {_when(pal.get('last_product_date'), False)}")
    qc = (o.get("qc") or [{}])[0]
    if qc.get("inspections"):
        out.append(f"QC: ตรวจ {qc['inspections']} ครั้ง ({_when(qc.get('first_date'), False)} ถึง "
                   f"{_when(qc.get('last_date'), False)}) · สุ่มตรวจ {_n(qc.get('checked'))} · ไม่ผ่าน {_n(qc.get('failed'))}")
        probs = o.get("qc_problems") or []
        if probs:
            out.append("ปัญหา QC ที่พบ: " + ", ".join(
                f"{p.get('problem_name') or 'อื่นๆ'} (major {p.get('major') or 0}, minor {p.get('minor') or 0})"
                for p in probs))
    else:
        out.append("QC: ยังไม่มีผลตรวจ")
    chk = (o.get("checklists") or [{}])[0]
    if chk.get("n"):
        out.append(f"checklist QC หน้าเครื่อง: {chk['n']} ใบ (อนุมัติแล้ว {chk.get('approved') or 0}) ล่าสุด "
                   f"{_when(chk.get('last_date'), False)}")
    return "\n".join(out)


_NAME_IN_PARENS = re.compile(r"\(\s*(?:คุณ|ช่าง|นาย|นาง|น\.ส\.)?\s*[฀-๿.\s]{2,30}\)")
_KHUN_NAME = re.compile(r"(คุณ|นาย|นางสาว|นาง|น\.ส\.)\s?[฀-๿]{2,20}")


def scrub_names(text: str | None) -> str:
    """Repair notes are typed freely and sometimes end with who did it, e.g.
    '…สั่งซื้อ Sensor 2 ตัว(ณรงค์ศักดิ์)'. Drop such Thai names in brackets and
    'คุณ/นาย/นาง…' names before the text reaches the answer."""
    t = _NAME_IN_PARENS.sub("", text or "")
    t = _KHUN_NAME.sub("", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -,") or "-"


def _cell(v, width: int = 60) -> str:
    return re.sub(r"\s+", " ", str(v if v not in (None, "") else "-")).replace("|", "/").strip()[:width]


def ops_table(p: dict, data: dict) -> str:
    """The list part of a due-date / repair answer as a Markdown table, built in
    code — a model-written table lost its |---| line on prod (2026-11-18).
    Up to 15 rows, in the order the query gave them."""
    kind = p.get("kind")
    rows = (data or {}).get("rows") or []
    if kind not in {"due", "overdue", "repairs"} or not rows or p.get("show_list") is False:
        return ""
    shown = rows[:15]
    if kind == "repairs":
        total = sum(((data or {}).get("counts") or {}).values())
        head = "| เลขที่ใบ | เครื่อง | อาการ | สถานะ | แจ้งเมื่อ |\n|---|---|---|---|---|"
        body = [f"| {_cell(r.get('ma_id'), 14)} | {_cell(r.get('machine_name') or r.get('machine_id'), 40)} | "
                f"{_cell(scrub_names(r.get('problem')), 80)} | {MA_STATUS.get(r.get('ma_status'), '-')} | "
                f"{_when(r.get('notify_datetime'))} |" for r in shown]
    else:
        total = (data or {}).get("count") or len(rows)
        head = "| เลขงาน | ชื่องาน | ลูกค้า | สั่ง | ส่งแล้ว | กำหนดพิมพ์ | กำหนดส่ง |\n|---|---|---|---:|---:|---|---|"
        body = [f"| {_cell(r.get('jobid'), 14)} | {_cell(r.get('job_name'))} | {_cell(r.get('custName'), 40)} | "
                f"{_n(r.get('qty1'))} | {_n(r.get('delivered') or 0)} | {_cell(r.get('printDate'), 10)} | "
                f"{_cell(r.get('due1'), 10)} |" for r in shown]
    title = f"**รายการ {len(shown)} จากทั้งหมด {total:,}**\n\n" if total > len(shown) else ""
    return title + head + "\n" + "\n".join(body)


def ops_context(p: dict, data: dict, today: dt.date | None = None) -> str:
    d_from, d_to = effective_range(p, today or _bangkok_today())
    rng = f"{_day(d_from)} ถึง {_day(d_to)}"
    kw = f" · คำค้น: {p['keyword']}" if p.get("keyword") else ""
    lines = [_head()]
    if p.get("kind") in {"due", "overdue", "repairs"} and data.get("rows"):
        if p.get("show_list", True):
            lines.append("(ระบบจะแนบตารางรายการต่อท้ายคำตอบให้เอง — ห้ามเขียนตารางหรือไล่รายการทีละรายการเอง "
                         "ให้เขียนแค่สรุป)")
        else:
            lines.append("(ผู้ใช้ไม่ได้ขอดูรายการ — ตอบเฉพาะสิ่งที่ถามจากคำตอบก่อนหน้าตรง ๆ ห้ามทวนคำตอบเดิมทั้งหมด "
                         "และไม่ต้องแสดงรายการหรือตาราง)")
    if p["kind"] in {"due", "overdue"}:
        what = ("งานที่เลยกำหนดส่งแล้วแต่ยอดในใบส่งของยังไม่ครบตามจำนวนสั่ง (ยังไม่ปิดงาน, ไม่รวมงานยกเลิก, "
                "ดูเฉพาะกำหนดส่งในช่วงนี้ — งานบางงานอาจส่งด้วยวิธีอื่นที่ไม่ได้ออกใบส่งของ)"
                if p["kind"] == "overdue" else "งานที่ถึงกำหนดส่งลูกค้าในช่วงที่ถาม (ยังไม่ปิดงาน, ไม่รวมงานยกเลิก)")
        lines += [f"เรื่อง: {what}", f"ช่วงวันกำหนดส่ง: {rng}{kw}",
                  f"พบทั้งหมด: {data['count']} งาน" + (f" (รายการด้านล่างคือ {len(data['rows'])} งานแรกเรียงตามวันกำหนดส่ง)"
                                                    if data["count"] > len(data["rows"]) else "")]
        if data.get("by_day"):
            lines.append("จำนวนงานแยกตามวันกำหนดส่ง: " + ", ".join(f"{d} = {n} งาน" for d, n in data["by_day"]))
        for r in data["rows"]:
            lines.append(f"- {r.get('jobid')} | {r.get('job_name') or '-'} | ลูกค้า: {r.get('custName') or '-'} | "
                         f"สั่ง {_n(r.get('qty1'))} | ส่งแล้ว {_n(r.get('delivered') or 0)} | "
                         f"กำหนดพิมพ์ {r.get('printDate') or '-'} | กำหนดส่ง {r.get('due1') or '-'}")
    elif p["kind"] == "repairs":
        c = data["counts"]
        lines += [f"เรื่อง: ใบแจ้งซ่อมเครื่องจักร · ช่วงวันที่แจ้ง: {rng}{kw}",
                  f"สรุปใบที่แจ้งในช่วงนี้: ทั้งหมด {sum(c.values())} ใบ · ยังไม่เสร็จ {c.get(0, 0)} · "
                  f"ซ่อมเสร็จ {c.get(1, 0)} · ยกเลิก {c.get(2, 0)}"]
        if p.get("status") in {"open", "done", "cancelled"}:
            label = {"open": "ยังไม่เสร็จ", "done": "ซ่อมเสร็จแล้ว", "cancelled": "ยกเลิก"}[p["status"]]
            shown = {"open": c.get(0, 0), "done": c.get(1, 0), "cancelled": c.get(2, 0)}[p["status"]]
            lines.append(f"ผู้ใช้ถามเฉพาะใบที่{label}: {shown} ใบ"
                         + (f" (รายการด้านล่างคือ {len(data['rows'])} ใบล่าสุด)" if shown > len(data["rows"]) else ""))
        for r in data["rows"]:
            lines.append(
                f"- {r.get('ma_id')} | แจ้ง {_when(r.get('notify_datetime'))} | เครื่อง: {r.get('machine_name') or r.get('machine_id') or '-'}"
                f" | สถานะ: {MA_STATUS.get(r.get('ma_status'), '-')} ({MA_APPROVE.get(r.get('approve_status'), '-')})"
                f" | เสร็จ: {_when(r.get('ma_complete_date'))} | ต้องการให้เสร็จ: {_when(r.get('need_datetime'))}"
                f" | อาการ: {scrub_names(r.get('problem'))} | การแก้ไข: {scrub_names(r.get('solution'))}")
    else:
        s = data["summary"]
        lines += [f"เรื่อง: ผลตรวจ QC · ช่วงวันที่: {rng}{kw}",
                  f"สรุป: ตรวจ {s.get('inspections') or 0} ครั้ง · {s.get('jobs') or 0} งาน · "
                  f"สุ่มตรวจ {_n(s.get('checked'))} · ไม่ผ่าน {_n(s.get('failed'))}"]
        if data["problems"]:
            lines.append("ปัญหาที่พบบ่อย:")
            lines += [f"- {r.get('problem_name') or 'อื่นๆ'}: {r.get('times')} ครั้ง (major {r.get('major') or 0}, "
                      f"minor {r.get('minor') or 0})" for r in data["problems"]]
        if data["jobs"]:
            lines.append("งานที่ไม่ผ่าน QC มากที่สุด:")
            lines += [f"- {r.get('job_id')} | ประเภทงาน: {r.get('job_name') or '-'} | ตรวจ {r.get('inspections')} ครั้ง | "
                      f"ไม่ผ่าน {_n(r.get('failed'))}" for r in data["jobs"]]
    return "\n".join(lines)
