"""Priced jobs ("งานเคาะราคา") — read-only lookups in the estimate systems.

Source (LIVE, on 192.168.5.19): ESTIMATE.dbo.tb_rfq_list — the master list of
estimates from BOTH estimate systems (source_system V1 = the old one, V2 = the
new MI2 one, which syncs its estimates in), so nothing is counted twice.
status_id 0 ร่าง · 1 ยืนยัน/รออนุมัติ · 2 ไม่อนุมัติ · 3 อนุมัติ (= "เคาะราคาแล้ว");
estimate_date is the approval time. Selling prices per quantity are in
ESTIMATE.dbo.tb_rfq_price1 (cost columns there are never read). V2 estimates
also have details and quotations (QE…) in MI2_ESTIMATE. Numbers: E/EB/EP + 8 digits.

Only fixed SELECTs, through factory.session() (OPENQUERY via the MI login
server's linked server, or a direct .19 account); nothing is ever written.
Owner decision 2026-11-17: every signed-in user may ask, prices included. Test
rows ("test"/"เทส"/"ทดสอบ" in the name) are left out. PRICED_JOBS_MODE=off
disables it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from functools import lru_cache

import factory

PRICED_JOBS_MODE = os.getenv("PRICED_JOBS_MODE", "on").strip().lower()
TEST_CUSTOMERS = tuple(
    c.strip() for c in os.getenv("PRICED_JOBS_TEST_CUSTOMERS", "").split(",") if c.strip()
)
MAX_ROWS = 40

DOC_RE = re.compile(r"\bE[BP]?\d{8}\b", re.IGNORECASE)
QUOTE_RE = re.compile(r"\bQE\d{7}\b", re.IGNORECASE)
# Cheap pre-filter: only questions that mention estimates / approved prices go on
# to the (small) AI planner, which decides whether it really is a lookup.
TRIGGER_RE = re.compile(
    r"เคาะราคา|เคาะแล้ว|ประเมินราคา|อนุมัติราคา|ราคา.{0,12}(อนุมัติ|ผ่านแล้ว)|"
    r"ใบประเมิน|ใบเสนอราคา|ใบขอราคา|\brfq\b|estimate|quotation|"
    r"\bE[BP]?\d{8}\b|\bQE\d{7}\b",
    re.IGNORECASE,
)


def enabled() -> bool:
    if PRICED_JOBS_MODE in {"off", "0", "false", "no"}:
        return False
    if os.getenv("FACTORY_DB_SERVER", "").strip():
        return True
    try:
        from mi_auth import mi_enabled
    except Exception:
        return False
    return mi_enabled()


def mentions(text: str) -> bool:
    return bool(text and TRIGGER_RE.search(text))


# ── planner: question → filters (small AI call, cached) ─────────────────────

def _bangkok_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).replace(tzinfo=None)


def _bangkok_today() -> dt.date:
    return _bangkok_now().date()


def _date_anchors(today: dt.date) -> str:
    monday = today - dt.timedelta(days=today.weekday())
    last_mon = monday - dt.timedelta(days=7)
    first = today.replace(day=1)
    last_month_end = first - dt.timedelta(days=1)
    days = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
    return (
        f"วันนี้ = {today} (วัน{days[today.weekday()]}); "
        f"เมื่อวาน = {today - dt.timedelta(days=1)}; "
        f"สัปดาห์นี้ = {monday} ถึง {today}; "
        f"สัปดาห์ที่แล้ว = {last_mon} ถึง {monday - dt.timedelta(days=1)}; "
        f"เดือนนี้ = {first} ถึง {today}; "
        f"เดือนที่แล้ว = {last_month_end.replace(day=1)} ถึง {last_month_end}; "
        f"ปีนี้ = {today.replace(month=1, day=1)} ถึง {today}"
    )


_PLAN_PROMPT = (
    "คุณเป็นตัวแยกคำสั่งของ chatbot ภายในบริษัทโรงพิมพ์ บริษัทมี 'ระบบประเมินราคา' ที่เก็บใบประเมินราคา "
    "(เลข E, EB หรือ EP ตามด้วยตัวเลข 8 หลัก เช่น E26110334, EB26110174) แต่ละใบมีสถานะ: approve = อนุมัติแล้ว/เคาะราคาแล้ว, "
    "pending = รออนุมัติ/รอเคาะ, reject = ไม่อนุมัติ/ตีกลับ, draft = ร่าง และอาจมีใบเสนอราคา (เลข QE + 7 หลัก)\n"
    "ตัดสินว่าผู้ใช้ต้องการ 'ดูข้อมูลที่บันทึกในระบบประเมินราคา' หรือไม่ (รายการ จำนวน มูลค่ารวม "
    "หรือรายละเอียดใบใดใบหนึ่ง) แล้วตอบ JSON เท่านั้น:\n"
    '{"lookup": true|false, "doc_id": "E…/EB…/EP…"|null, "quotation_id": "QE…"|null, '
    '"status": "approve"|"pending"|"reject"|"draft"|"any", "date_from": "YYYY-MM-DD"|null, '
    '"date_to": "YYYY-MM-DD"|null, "keyword": string|null}\n'
    "กติกา:\n"
    "- lookup=false ถ้าผู้ใช้ขอให้คำนวณ/ประเมินราคาเอง, ร่างหรือเขียนใบเสนอราคา, ถามความหมาย/วิธีทำ, "
    "หรือถามความรู้ทั่วไป\n"
    "- คำถามที่ถามจำนวนหรือรายการของใบตามสถานะ (เช่น 'ตอนนี้มีใบประเมินราคารออนุมัติกี่ใบ', "
    "'มีงานที่ไม่อนุมัติราคากี่งาน', 'เดือนนี้เคาะราคาไปกี่งาน') → lookup=true เสมอ\n"
    "- 'เคาะราคาแล้ว', 'อนุมัติราคาแล้ว', 'ราคาผ่านแล้ว', 'งานใหม่ที่ได้ราคาแล้ว' → approve; "
    "'รอเคาะ', 'รออนุมัติ' → pending; 'ไม่ผ่าน', 'ตีกลับ', 'reject' → reject; "
    "ถามถึงใบเดียว (เลข E/QE) โดยไม่ระบุสถานะ → any\n"
    "- วันที่ (รวมวันสุดท้าย) ใช้ตามนี้: {anchors}. เดือน/วันที่ระบุเป็น พ.ศ. ให้ลบ 543. "
    "ถ้าไม่ได้ระบุช่วงเวลา ให้ date_from และ date_to เป็น null (= ล่าสุด)\n"
    "- keyword: ชื่อลูกค้า แบรนด์ สินค้า ชื่องาน หรือทีม/AE ที่ผู้ใช้ต้องการกรอง (เช่น 'AROMA', 'ศรีวรรณ') "
    "ไม่ใส่คำบอกสถานะหรือเวลาใน keyword ถ้าไม่มีให้ null\n"
    "- ถ้ามี 'คำถามก่อนหน้า' (อาจหลายข้อเรียงตามเวลา คั่นด้วย →) และคำถามนี้เป็นคำถามต่อเนื่อง "
    "(เช่น 'แล้วเดือนที่แล้วล่ะ', 'ของเดือนตุลาคมล่ะ', 'อันไหนแพงสุด', 'มีของลูกค้า X ไหม', 'มีเยอะขนาดนั้นเลยหรอ', "
    "'ขอเป็นตาราง') ให้ lookup=true และใช้เงื่อนไขล่าสุดในสายคำถามนั้น เว้นแต่คำถามนี้เปลี่ยน"
)


def _clean_plan(data: dict) -> dict | None:
    if not isinstance(data, dict) or not data.get("lookup"):
        return None
    status = str(data.get("status") or "any").strip().lower()
    if status not in {"approve", "pending", "reject", "draft", "any"}:
        status = "any"

    def day(v):
        try:
            return dt.date.fromisoformat(str(v)[:10]) if v else None
        except ValueError:
            return None

    d_from, d_to = day(data.get("date_from")), day(data.get("date_to"))
    if d_from and d_to and d_from > d_to:
        d_from, d_to = d_to, d_from
    doc = data.get("doc_id")
    quote = data.get("quotation_id")
    kw = (data.get("keyword") or "").strip()[:60] or None
    return {
        "doc_id": doc.upper() if isinstance(doc, str) and DOC_RE.fullmatch(doc.strip()) else None,
        "quotation_id": quote.upper() if isinstance(quote, str) and QUOTE_RE.fullmatch(quote.strip()) else None,
        "status": status, "date_from": d_from, "date_to": d_to, "keyword": kw,
        "show_list": True,  # plan() sets it from the question (factory.wants_list)
    }


@lru_cache(maxsize=256)
def _plan_cached(question: str, previous: str, today_iso: str) -> tuple[str, str]:
    from llm import LLM_MODEL_CLASSIFIER, _classifier_kwargs, _extract_usage, _get_client

    today = dt.date.fromisoformat(today_iso)
    user = (f"คำถามก่อนหน้า: {previous}\n" if previous else "") + f"คำถาม: {question}"
    res = _get_client().chat.completions.create(
        model=LLM_MODEL_CLASSIFIER,
        messages=[{"role": "system", "content": _PLAN_PROMPT.replace("{anchors}", _date_anchors(today))},
                  {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        **_classifier_kwargs(LLM_MODEL_CLASSIFIER, 200),
    )
    return res.choices[0].message.content or "{}", json.dumps(_extract_usage(res, LLM_MODEL_CLASSIFIER))


# ── when the small planner says "not a lookup" to a plain data question ──
# gpt-4o-mini answered lookup=false to "ตอนนี้มีใบประเมินราคารออนุมัติกี่ใบ" and it
# fell through to a generic chat answer (prod, 2026-11-18). A question that names
# a status AND asks for a count or a list is a lookup, whatever the planner says.
_STATUS_WORDS = [   # in this order: "ยังไม่อนุมัติ" is pending, "ไม่อนุมัติ" reject
    ("pending", re.compile(r"รอ(?:การ)?อนุมัติ|รอเคาะ|ยังไม่(?:ได้)?(?:อนุมัติ|เคาะ)|pending", re.IGNORECASE)),
    ("reject", re.compile(r"ไม่อนุมัติ|ตีกลับ|ไม่ผ่าน|reject", re.IGNORECASE)),
    ("draft", re.compile(r"ร่าง|draft", re.IGNORECASE)),
    ("approve", re.compile(r"เคาะ|อนุมัติ|ผ่านแล้ว|approve", re.IGNORECASE)),
]
_ASKS_DATA_RE = re.compile(r"กี่|เท่าไหร่|เท่าไร|อะไรบ้าง|รายการ|ขอดู|แสดง|ล่าสุด|ทั้งหมด|ตาราง|excel|ยอด|มูลค่า|"
                           r"มี(?:ไหม|มั้ย|บ้าง)|\blist\b", re.IGNORECASE)
_NOT_DATA_RE = re.compile(r"วิธี|ยังไง|อย่างไร|ทำไง|คืออะไร|หมายถึง|หมายความ|ขั้นตอน|ตัวอย่าง|สูตร|แบบฟอร์ม|template|"
                          r"ช่วย(?:เขียน|ร่าง|คิด|คำนวณ|ประเมิน|ทำ)|ร่างให้|กี่แบบ|กี่ประเภท|กี่ขั้นตอน|กี่วัน",
                          re.IGNORECASE)
_TH_MONTHS = [("มกรา", "ม.ค."), ("กุมภา", "ก.พ."), ("มีนา", "มี.ค."), ("เมษา", "เม.ย."), ("พฤษภา", "พ.ค."),
              ("มิถุนา", "มิ.ย."), ("กรกฎา", "ก.ค."), ("สิงหา", "ส.ค."), ("กันยา", "ก.ย."), ("ตุลา", "ต.ค."),
              ("พฤศจิกา", "พ.ย."), ("ธันวา", "ธ.ค.")]


def _simple_range(q: str, today: dt.date) -> tuple[dt.date | None, dt.date | None]:
    """The plain date words the planner's anchors cover, read without it (no words → no range)."""
    monday = today - dt.timedelta(days=today.weekday())
    first = today.replace(day=1)
    if "วันนี้" in q:
        return today, today
    if "เมื่อวาน" in q:
        return today - dt.timedelta(days=1), today - dt.timedelta(days=1)
    if re.search(r"(?:สัปดาห์|อาทิตย์)(?:ที่แล้ว|ก่อน)", q):
        return monday - dt.timedelta(days=7), monday - dt.timedelta(days=1)
    if re.search(r"(?:สัปดาห์|อาทิตย์)นี้", q):
        return monday, today
    if re.search(r"เดือน(?:ที่แล้ว|ก่อน)", q):
        end = first - dt.timedelta(days=1)
        return end.replace(day=1), end
    if "เดือนนี้" in q:
        return first, today
    if "ปีนี้" in q:
        return today.replace(month=1, day=1), today
    for month, (stem, short) in enumerate(_TH_MONTHS, 1):
        if stem in q or short in q:
            start = dt.date(today.year if month <= today.month else today.year - 1, month, 1)
            end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
            return start, min(end, today)
    return None, None


def fallback_plan(question: str, today: dt.date | None = None) -> dict | None:
    q = (question or "").strip()
    if not mentions(q) or not _ASKS_DATA_RE.search(q) or _NOT_DATA_RE.search(q):
        return None
    status = next((s for s, rx in _STATUS_WORDS if rx.search(q)), None)
    if status is None:
        return None
    d_from, d_to = _simple_range(q, today or _bangkok_today())
    return {"doc_id": None, "quotation_id": None, "status": status, "date_from": d_from, "date_to": d_to,
            "keyword": None, "show_list": True}


def plan(question: str, previous: str = "") -> tuple[dict | None, dict]:
    """(filters or None when it isn't a lookup, usage of the planner call).
    An E-/QE-number in the question is taken as-is even if the AI is unsure, and
    so is a status + count/list question (fallback_plan)."""
    try:
        raw, usage = _plan_cached(question.strip(), (previous or "").strip(), _bangkok_today().isoformat())
        p = _clean_plan(json.loads(raw))
        usage = json.loads(usage)
    except Exception:
        p, usage = None, {}
    if p is None:
        p = fallback_plan(question)
    doc, quote = DOC_RE.search(question or ""), QUOTE_RE.search(question or "")
    if (doc or quote) and p is None:
        p = {"doc_id": None, "quotation_id": None, "status": "any", "date_from": None, "date_to": None,
             "keyword": None, "show_list": True}
    if p is not None:
        if doc:
            p["doc_id"] = doc.group(0).upper()
        if quote:
            p["quotation_id"] = quote.group(0).upper()
        # a check on the last answer ("…เลยหรอ") gets no table; anything else does
        p["full_list"] = factory.wants_all(question)
        p["show_list"] = p["full_list"] or factory.wants_list(question)
    return p, usage


# ── database (fixed SELECTs only) ──────────────────────────────────────────
# Everything is read on the LIVE server 192.168.5.19. (The MI2_ESTIMATE on the
# MI login server .10 is a test copy — the first version read it by mistake.)
# Two sources, so the bot keeps working when the old estimate app is retired:
#   - the new MI2 app: MI2_ESTIMATE.dbo.tb_rfq, read directly — it is the owner
#     of every MI2 estimate, so the bot does not depend on its sync into the
#     master list (a stopped or late sync would read as "0 งาน", 2026-11-18);
#   - the old app: the master list ESTIMATE.dbo.tb_rfq_list, minus every row the
#     MI2 table holds (its synced V2 rows, and old estimates if they are ever
#     moved into MI2), so nothing is counted twice.
# If the old system's database is gone or closed to us, the MI2 part is still
# answered and the answer says so. Any other database error stays an error.
# OPENQUERY takes no parameters, so every value below is validated or reduced
# to safe characters.

_LIST = "ESTIMATE.dbo.tb_rfq_list"
_PRICE = "ESTIMATE.dbo.tb_rfq_price1"
_LOG = "ESTIMATE.dbo.tb_rfq_status_log"
_MI2_RFQ = "MI2_ESTIMATE.dbo.tb_rfq"
_MI2_QUOTE = "MI2_ESTIMATE.dbo.tb_quotation"

STATUS_ID = {"draft": 0, "pending": 1, "reject": 2, "approve": 3}
STATUS_NAME = {0: "ร่าง (ยังไม่ส่งอนุมัติ)", 1: "ส่งขออนุมัติแล้ว รออนุมัติ", 2: "ไม่อนุมัติ",
               3: "อนุมัติแล้ว (เคาะราคาแล้ว)", 4: "เกินวงเงิน รออนุมัติพิเศษ"}
# the date that matters for each status (estimate_date / approved_at = when it was approved)
_DATE_COL = {"approve": "l.estimate_date", "pending": "l.updated", "reject": "l.updated", "draft": "l.created",
             "any": "COALESCE(l.estimate_date, l.updated, l.created)"}
_MI2_DATE_COL = {"approve": "r.approved_at", "pending": "r.updated_at", "reject": "r.updated_at",
                 "draft": "r.created_at", "any": "COALESCE(r.approved_at, r.updated_at, r.created_at)"}


def _run(sqls: list[str]) -> list[list[dict]]:
    with factory.session() as s:
        return [s.run(q) for q in sqls]


# the old system's tables are missing or closed to this account — not a timeout or a typo
_GONE_RE = re.compile(r"invalid object name|does not exist|cannot open database|not able to access|denied",
                      re.IGNORECASE)
_LEGACY_RE = re.compile(r"\bESTIMATE\b|tb_rfq_list|tb_rfq_price1|tb_rfq_status_log", re.IGNORECASE)


def _legacy_gone(e: Exception) -> bool:
    text = str(e)
    return bool(_GONE_RE.search(text) and _LEGACY_RE.search(text))


def _run_both(legacy_sqls: list[str], mi2_sqls: list[str]) -> tuple[list[list[dict]] | None, list[list[dict]]]:
    """(old-system results — None when its database is gone —, MI2 results), in
    one connection. MI2 runs first: its error is always raised."""
    with factory.session() as s:
        mi2 = [s.run(q) for q in mi2_sqls]
        try:
            legacy = [s.run(q) for q in legacy_sqls]
        except Exception as e:
            if not _legacy_gone(e):
                raise
            legacy = None
    return legacy, mi2


def _not_test_sql(name_col: str = "l.job_name", customer_col: str = "l.customer_id") -> str:
    ids = [c for c in TEST_CUSTOMERS if re.fullmatch(r"[A-Za-z0-9_\-]{1,20}", c)]
    parts = [f"{customer_col} NOT IN ({', '.join(repr(c) for c in ids)})"] if ids else []
    # C9999998 is NOT a test customer: it is "ลูกค้าใหม่" (a customer not registered
    # yet). Every approved estimate on the new system used it, so excluding it
    # gave "0 งาน" (2026-11-18). Test rows are recognised by their name only.
    parts += [f"{name_col} NOT LIKE N'%{w}%'" for w in ("test", "เทส", "ทดสอบ")]
    return " AND ".join(parts)


# ── the old system: master list rows that MI2 does not own ──

# the price shown for a job: the line at its main quantity, else its smallest quantity.
# price_ex: the old system stores the price WITH its tax line (E26110464: 27,094.95 = 26,305.78 + 789.17
# tax), MI2 before it (mid_total_ex_vat 26,305.78) — both are shown before tax.
_PRICE_APPLY = f"""OUTER APPLY (SELECT TOP 1 x.qty AS price_qty, x.total_price - ISNULL(x.tax, 0) AS price_ex,
            x.total_price, x.currency_no, x.exchange_rate
        FROM {_PRICE} x WITH (NOLOCK) WHERE x.job_id = l.job_id
        ORDER BY CASE WHEN x.qty = x.main_qty THEN 0 ELSE 1 END, x.qty) p
    OUTER APPLY (SELECT COUNT(*) AS tiers FROM {_PRICE} y WITH (NOLOCK) WHERE y.job_id = l.job_id) t"""

# synced MI2 rows (V2), and any row MI2 holds a live copy of, are read from MI2 instead
_NOT_MI2 = (f"ISNULL(l.source_system, 'V1') <> 'V2' AND NOT EXISTS (SELECT 1 FROM {_MI2_RFQ} m2 WITH (NOLOCK) "
            "WHERE ISNULL(m2.is_deleted, 0) = 0 "
            "AND m2.doc_id COLLATE DATABASE_DEFAULT = l.job_id COLLATE DATABASE_DEFAULT)")

_LIST_COLS = ("l.job_id, l.job_name, l.customer_id, c.custName, l.est_type, l.status_id, l.source_system, "
              "l.estimate_date, l.created, l.updated")
_ROW_COLS = f"{_LIST_COLS}, p.price_qty, p.price_ex, p.total_price, p.currency_no, p.exchange_rate, t.tiers"
_AMOUNT = "p.price_ex"
# total_price is in BAHT for every currency: a USD / EUR estimate keeps baht there and
# its rate in exchange_rate (EB26110201: 580,637.94 ÷ 15,810 ÷ 31.17 = 1.1782 =
# unit_price_exchange, the USD price a copy; checked on 30 export jobs, 2026-11-18).
# So export jobs are summed in baht too; their foreign amount is price ÷ rate.
_AMOUNT_THB = f"CASE WHEN {_AMOUNT} > 0 THEN {_AMOUNT} END"
_AMOUNT_FX = f"CASE WHEN {_AMOUNT} > 0 AND p.exchange_rate > 0 THEN {_AMOUNT} / p.exchange_rate END"


def _base(p: dict) -> tuple[str, str]:
    """(FROM … WHERE … on the old system for a list question, its date column) — values inlined, all validated."""
    status = p.get("status") if p.get("status") in _DATE_COL else "any"
    date_col = _DATE_COL[status]
    where = ["l.status_id IN (1, 2, 3)" if status == "any" else f"l.status_id = {STATUS_ID[status]}",
             _not_test_sql(), _NOT_MI2]
    if isinstance(p.get("date_from"), dt.date):
        where.append(f"{date_col} >= '{p['date_from'].isoformat()}'")
    if isinstance(p.get("date_to"), dt.date):
        where.append(f"{date_col} < '{(p['date_to'] + dt.timedelta(days=1)).isoformat()}'")
    kw = factory.safe_keyword(p.get("keyword"))
    if kw:
        where.append(f"(l.job_name LIKE N'%{kw}%' OR c.custName LIKE N'%{kw}%' OR l.customer_id = '{kw}' "
                     f"OR l.job_id = '{kw}')")
    base = (f"FROM {_LIST} l WITH (NOLOCK) LEFT JOIN mi.dbo.customer c WITH (NOLOCK) ON c.custID = l.customer_id "
            f"{_PRICE_APPLY} WHERE {' AND '.join(where)}")
    return base, date_col


def build_search(p: dict) -> tuple[str, str]:
    """(rows SQL, totals SQL) on the old system for a list question."""
    base, date_col = _base(p)
    top = factory.FULL_MAX if p.get("full_list") else MAX_ROWS   # "ขอดูทั้งหมด" → every row for the table
    rows_sql = f"SELECT TOP {top} {_ROW_COLS} {base} ORDER BY {date_col} DESC"
    totals_sql = (f"SELECT COUNT(*) AS n, SUM({_AMOUNT_THB}) AS total, "
                  f"SUM(CASE WHEN {_AMOUNT} > 0 THEN 1 ELSE 0 END) AS priced, "
                  f"SUM(CASE WHEN ISNULL(p.currency_no, 'THB') <> 'THB' THEN 1 ELSE 0 END) AS other_currency "
                  f"{base}")
    return rows_sql, totals_sql


def build_breakdowns(p: dict) -> list[str]:
    """For a long list (660 approvals in half a month): the customer ids (for a
    distinct count across both systems), counts and values by job type and by
    currency, the customers with the highest value, and the five priciest."""
    base, _ = _base(p)
    return [
        f"SELECT DISTINCT l.customer_id {base}",
        f"SELECT ISNULL(l.est_type, '-') AS est_type, COUNT(*) AS n, SUM({_AMOUNT_THB}) AS total {base} "
        f"GROUP BY ISNULL(l.est_type, '-') ORDER BY COUNT(*) DESC",
        f"SELECT ISNULL(p.currency_no, 'THB') AS currency, COUNT(*) AS n, "
        f"SUM({_AMOUNT_THB}) AS total, SUM({_AMOUNT_FX}) AS total_fx {base} "
        f"GROUP BY ISNULL(p.currency_no, 'THB') ORDER BY COUNT(*) DESC",
        # top 10 here: MI2's own totals per customer are added before the top 5 is picked
        f"SELECT TOP 10 l.customer_id, MAX(c.custName) AS customer, COUNT(*) AS n, SUM({_AMOUNT_THB}) AS total {base} "
        f"GROUP BY l.customer_id ORDER BY SUM({_AMOUNT_THB}) DESC",
        # "อันไหนแพงสุด" over the whole range — the model only sees the newest MAX_ROWS rows
        f"SELECT TOP 5 {_ROW_COLS} {base} ORDER BY {_AMOUNT_THB} DESC",
    ]


# ── the new system: MI2_ESTIMATE.dbo.tb_rfq, shaped like the old rows ──

_MI2_STATUS_ID = ("CASE r.estimate_status WHEN 'draft' THEN 0 WHEN 'pending' THEN 1 WHEN 'reject' THEN 2 "
                  "WHEN 'approve' THEN 3 ELSE 4 END")
# E… packaging, EB… book, EP… popup — the numbering both systems share
_MI2_TYPE = "CASE WHEN r.doc_id LIKE 'EB%' THEN 'book' WHEN r.doc_id LIKE 'EP%' THEN 'popup' ELSE 'packaging' END"
_MI2_ROW_COLS = (f"r.doc_id AS job_id, r.estimate_name AS job_name, r.customer_id, r.customer_name AS custName, "
                 f"{_MI2_TYPE} AS est_type, {_MI2_STATUS_ID} AS status_id, 'V2' AS source_system, "
                 "CAST(r.approved_at AS datetime) AS estimate_date, CAST(r.created_at AS datetime) AS created, "
                 "CAST(r.updated_at AS datetime) AS updated, r.mid_qty, r.mid_total_ex_vat")
_MI2_AMOUNT = "CASE WHEN r.mid_total_ex_vat > 0 THEN r.mid_total_ex_vat END"


def _mi2_base(p: dict) -> tuple[str, str]:
    status = p.get("status") if p.get("status") in _MI2_DATE_COL else "any"
    date_col = _MI2_DATE_COL[status]
    where = ["ISNULL(r.is_deleted, 0) = 0",
             "r.estimate_status IN ('pending', 'reject', 'approve')" if status == "any"
             else f"r.estimate_status = '{status}'",
             _not_test_sql("r.estimate_name", "r.customer_id")]
    if isinstance(p.get("date_from"), dt.date):
        where.append(f"{date_col} >= '{p['date_from'].isoformat()}'")
    if isinstance(p.get("date_to"), dt.date):
        where.append(f"{date_col} < '{(p['date_to'] + dt.timedelta(days=1)).isoformat()}'")
    kw = factory.safe_keyword(p.get("keyword"))
    if kw:
        where.append(f"(r.estimate_name LIKE N'%{kw}%' OR r.customer_name LIKE N'%{kw}%' "
                     f"OR r.customer_id = '{kw}' OR r.doc_id = '{kw}')")
    return f"FROM {_MI2_RFQ} r WITH (NOLOCK) WHERE {' AND '.join(where)}", date_col


def build_mi2(p: dict) -> list[str]:
    """MI2's part of a list question: rows, totals, customer ids, by type, per customer, the five priciest."""
    base, date_col = _mi2_base(p)
    top = factory.FULL_MAX if p.get("full_list") else MAX_ROWS
    return [
        f"SELECT TOP {top} {_MI2_ROW_COLS} {base} ORDER BY {date_col} DESC",
        f"SELECT COUNT(*) AS n, SUM({_MI2_AMOUNT}) AS total, "
        f"SUM(CASE WHEN r.mid_total_ex_vat > 0 THEN 1 ELSE 0 END) AS priced {base}",
        f"SELECT DISTINCT r.customer_id {base}",
        f"SELECT {_MI2_TYPE} AS est_type, COUNT(*) AS n, SUM({_MI2_AMOUNT}) AS total {base} GROUP BY {_MI2_TYPE}",
        f"SELECT r.customer_id, MAX(r.customer_name) AS customer, COUNT(*) AS n, SUM({_MI2_AMOUNT}) AS total "
        f"{base} GROUP BY r.customer_id",
        f"SELECT TOP 5 {_MI2_ROW_COLS} {base} ORDER BY {_MI2_AMOUNT} DESC",
    ]


# ── both together ──

def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _add(*values) -> float | None:
    got = [x for x in (_f(v) for v in values) if x is not None]
    return sum(got) if got else None


def _thb(r: dict) -> float:
    """A row's before-tax amount in baht (export jobs too: stored in baht), 0 when it has none."""
    ex = _f(r.get("price_ex"))
    if ex and ex > 0:
        return ex
    return _f(r.get("mid_total_ex_vat")) or 0.0


def _newest(p: dict, rows: list[dict], n: int) -> list[dict]:
    status = p.get("status")
    if status == "approve":
        key = lambda r: r.get("estimate_date")  # noqa: E731
    elif status in ("pending", "reject"):
        key = lambda r: r.get("updated")  # noqa: E731
    elif status == "draft":
        key = lambda r: r.get("created")  # noqa: E731
    else:
        key = lambda r: r.get("estimate_date") or r.get("updated") or r.get("created")  # noqa: E731
    return sorted(rows, key=lambda r: str(key(r) or ""), reverse=True)[:n]


def _ids(rows: list[dict]) -> set[str]:
    return {str(r.get("customer_id") or "").strip().upper() for r in rows if r.get("customer_id")}


def _merge(p: dict, legacy: list[list[dict]] | None, mi2: list[list[dict]]) -> dict:
    """One answer from both systems' results (legacy None = the old system is gone)."""
    l_rows, l_tot, l_ids, l_type, l_cur, l_cust, l_dear = (legacy or [[]] * 7)[:7]
    m_rows, m_tot, m_ids, m_type, m_cust, m_dear = mi2[:6]
    lt, mt = (l_tot or [{}])[0], (m_tot or [{}])[0]
    m_n = int(mt.get("n") or 0)

    by_type: dict[str, dict] = {}
    for b in list(l_type) + list(m_type):
        k = b.get("est_type") or "-"
        cur = by_type.setdefault(k, {"est_type": k, "n": 0, "total": None})
        cur["n"] += int(b.get("n") or 0)
        cur["total"] = _add(cur["total"], b.get("total"))
    by_currency: dict[str, dict] = {}
    mi2_thb = [{"currency": "THB", "n": m_n, "total": mt.get("total")}] if m_n else []
    for b in list(l_cur) + mi2_thb:
        k = b.get("currency") or "THB"
        cur = by_currency.setdefault(k, {"currency": k, "n": 0, "total": None, "total_fx": None})
        cur["n"] += int(b.get("n") or 0)
        cur["total"] = _add(cur["total"], b.get("total"))
        cur["total_fx"] = _add(cur["total_fx"], b.get("total_fx"))
    customers: dict[str, dict] = {}
    for b in list(l_cust) + list(m_cust):
        k = str(b.get("customer_id") or "-").strip().upper()
        cur = customers.setdefault(k, {"customer_id": b.get("customer_id"), "customer": None, "n": 0, "total": None})
        cur["customer"] = cur["customer"] or ("ลูกค้าใหม่" if k == "C9999998"
                                              else (b.get("customer") or b.get("customer_id")))
        cur["n"] += int(b.get("n") or 0)
        cur["total"] = _add(cur["total"], b.get("total"))

    top = factory.FULL_MAX if p.get("full_list") else MAX_ROWS
    return {
        "rows": _newest(p, list(l_rows) + list(m_rows), top),
        "count": int(lt.get("n") or 0) + m_n,
        "total": _add(lt.get("total"), mt.get("total")),
        "priced": int(lt.get("priced") or 0) + int(mt.get("priced") or 0),
        "other_currency": int(lt.get("other_currency") or 0),
        "customers": len(_ids(l_ids) | _ids(m_ids)),
        "by_type": sorted(by_type.values(), key=lambda b: -b["n"]),
        "by_currency": sorted(by_currency.values(), key=lambda b: -b["n"]),
        "top_customers": sorted(customers.values(), key=lambda b: -(b["total"] or 0))[:5],
        "priciest": sorted(list(l_dear) + list(m_dear), key=_thb, reverse=True)[:5],
        "legacy_missing": legacy is None,
    }


def search(p: dict) -> dict:
    rows_sql, totals_sql = build_search(p)
    legacy_sqls = [rows_sql, totals_sql] + build_breakdowns(p)
    mi2_sqls = build_mi2(p)
    dated = bool(p.get("date_from") or p.get("date_to"))
    if dated:
        # in case nothing is in that range: the newest matching ones instead of a bare "0"
        wide = dict(p, date_from=None, date_to=None, full_list=False)
        legacy_sqls.append(build_search(wide)[0].replace(f"TOP {MAX_ROWS} ", "TOP 3 ", 1))
        mi2_sqls.append(build_mi2(wide)[0].replace(f"TOP {MAX_ROWS} ", "TOP 3 ", 1))
    legacy, mi2 = _run_both(legacy_sqls, mi2_sqls)
    out = _merge(p, legacy, mi2)
    if not out["count"] and dated:
        latest = (legacy[7] if legacy and len(legacy) > 7 else []) + (mi2[6] if len(mi2) > 6 else [])
        if latest:
            out["latest"] = _newest(p, latest, 3)
    return out


def lookup_doc(doc_id: str | None, quotation_id: str | None) -> dict:
    """One estimate (any status, test rows included — the user named it): from
    MI2 when MI2 holds it (its status there is the live one), else the old
    system's list entry; the old system's price per quantity and status history;
    MI2's print details and quotations."""
    doc_id = doc_id.upper() if doc_id and DOC_RE.fullmatch(doc_id) else None
    quotation_id = quotation_id.upper() if quotation_id and QUOTE_RE.fullmatch(quotation_id) else None
    if not doc_id and quotation_id:
        got = _run([f"SELECT TOP 1 doc_id FROM {_MI2_QUOTE} WITH (NOLOCK) WHERE quotation_id = '{quotation_id}'"])[0]
        found = str((got[0].get("doc_id") or "") if got else "")
        doc_id = found.upper() if DOC_RE.fullmatch(found) else None
    if not doc_id:
        return {"rows": [], "prices": [], "history": [], "detail": [], "quotes": []}
    legacy, (detail, quotes) = _run_both([
        f"SELECT TOP 1 {_LIST_COLS}, l.credit_term_name FROM {_LIST} l WITH (NOLOCK) "
        f"LEFT JOIN mi.dbo.customer c WITH (NOLOCK) ON c.custID = l.customer_id WHERE l.job_id = '{doc_id}'",
        f"SELECT TOP 12 qty, total_price - ISNULL(tax, 0) AS price_ex, total_price, currency_no, exchange_rate "
        f"FROM {_PRICE} WITH (NOLOCK) "
        f"WHERE job_id = '{doc_id}' ORDER BY qty",
        f"SELECT TOP 20 from_status_id, to_status_id, created FROM {_LOG} WITH (NOLOCK) "
        f"WHERE job_id = '{doc_id}' ORDER BY created",
    ], [
        f"SELECT TOP 1 {_MI2_ROW_COLS}, r.qty_list, r.print_type, r.box_type, r.component_type "
        f"FROM {_MI2_RFQ} r WITH (NOLOCK) WHERE r.doc_id = '{doc_id}' AND ISNULL(r.is_deleted, 0) = 0",
        f"SELECT quotation_id, revision, status_label, currency, qty_list, grand_total, issue_date, expiry_date, "
        f"approved_at FROM {_MI2_QUOTE} WITH (NOLOCK) WHERE doc_id = '{doc_id}' AND ISNULL(is_deleted, 0) = 0 "
        f"ORDER BY created_at DESC",
    ])
    rows, prices, history = legacy or ([], [], [])
    if detail:
        old = rows[0] if rows else {}
        rows = [dict(detail[0], credit_term_name=old.get("credit_term_name"))]
    return {"rows": rows, "prices": prices, "history": history, "detail": detail, "quotes": quotes,
            "doc_id": doc_id, "legacy_missing": legacy is None}


# ── data → text for the answer model ───────────────────────────────────────

def _money(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _when(v) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)[:16]


def _qty(v) -> str:
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "-"


def _cell(v, width: int = 60) -> str:
    return re.sub(r"\s+", " ", str(v if v not in (None, "") else "-")).replace("|", "/").strip()[:width]


def list_table(p: dict, rows: list[dict], count: int, title: str | None = None) -> str:
    """A Markdown table of a list answer, built here rather than by the model:
    on prod the model wrote one without its |---| line and it showed as a
    paragraph of bars (2026-11-18). 15 or fewer → all; more → the newest 10;
    every row fetched when the user asked for everything (full_list)."""
    if not rows:
        return ""
    shown = rows if count <= 15 or p.get("full_list") else rows[:10]
    approve = p.get("status") == "approve"
    head = (f"| เลขที่ | ชื่องาน | ลูกค้า | จำนวน | ราคา (ก่อน VAT) | {'อนุมัติเมื่อ' if approve else 'วันที่'} |\n"
            "|---|---|---|---:|---:|---|")
    body = []
    for r in shown:
        ex, qty = r.get("price_ex"), r.get("price_qty")
        if not (ex and float(ex) > 0):
            ex, qty = r.get("mid_total_ex_vat"), r.get("mid_qty")
        # baht for every job; an export job also shows what it is in the customer's currency
        # (it showed "580,637.94 USD" for 580,637.94 baht, 2026-11-18)
        fx = _fx(r)
        price = (f"{_money(ex)} THB" + (f" (≈ {fx[0]:,.2f} {fx[1]})" if fx else "")
                 if ex and float(ex) > 0 else "ยังไม่มีราคา")
        when = r.get("estimate_date") if approve else (r.get("updated") or r.get("created"))
        body.append(f"| {_cell(r.get('job_id'), 14)} | {_cell(r.get('job_name'))} | {_cell(r.get('custName'), 40)} | "
                    f"{_qty(qty)} | {price} | {_when(when)} |")
    if title is None and count > len(shown):
        title = f"รายการล่าสุด {len(shown):,} จากทั้งหมด {count:,} งาน"
    elif title is None and p.get("full_list"):
        title = f"รายการทั้งหมด {count:,} งาน"
    return (f"**{title}**\n\n" if title else "") + head + "\n" + "\n".join(body)


def _fx(r: dict) -> tuple[float, str, float] | None:
    """(amount in the customer's currency, currency, baht per unit) for an export
    job priced in USD / EUR — its stored price is baht at its own rate."""
    cur = (r.get("currency_no") or "THB").strip().upper()
    ex, rate = _f(r.get("price_ex")), _f(r.get("exchange_rate"))
    if cur == "THB" or not ex or ex <= 0 or not rate or rate <= 0:
        return None
    return ex / rate, cur, rate


def _price_text(r: dict) -> str:
    """The price line for one job, before tax and in baht: the selling price at
    its main quantity (old-system price lines, tax taken off), else the new
    system's ex-VAT price at its middle quantity. Both systems agree on this basis."""
    ex, qty = r.get("price_ex"), r.get("price_qty")
    if ex and float(ex) > 0:
        per = f" (≈ {float(ex) / float(qty):,.2f} บาท/ชิ้น)" if qty else ""
        tiers = r.get("tiers") or 0
        more = f" · มีราคาทั้งหมด {tiers} ระดับจำนวน" if tiers and int(tiers) > 1 else ""
        fx = _fx(r)
        export = (f" · เสนอลูกค้าเป็น {fx[1]}: ≈ {fx[0]:,.2f} {fx[1]}"
                  + (f" (≈ {fx[0] / float(qty):,.4f} {fx[1]}/ชิ้น)" if qty else "")
                  + f" ที่อัตรา {fx[2]:,.2f} บาท/{fx[1]}" if fx else "")
        return f"{_money(ex)} บาท ก่อน VAT ที่จำนวน {_qty(qty)} ชิ้น{per}{export}{more}"
    mid_total, mid_qty = r.get("mid_total_ex_vat"), r.get("mid_qty")
    if mid_total and float(mid_total) > 0:
        per = f" (≈ {float(mid_total) / float(mid_qty):,.2f} บาท/ชิ้น)" if mid_qty else ""
        return f"{_money(mid_total)} บาท ก่อน VAT ที่จำนวน {_qty(mid_qty)} ชิ้น{per}"
    return "ยังไม่มีราคา"


def _row_text(r: dict) -> str:
    status = STATUS_NAME.get(r.get("status_id"), str(r.get("status_id")))
    system = {"V1": "ระบบประเมินราคาเดิม", "V2": "ระบบประเมินราคาใหม่ (MI2)"}.get(str(r.get("source_system")), "-")
    approved = f" · อนุมัติเมื่อ {_when(r.get('estimate_date'))}" if r.get("status_id") == 3 else ""
    # one labelled field per line: a one-line "a | b | c" record got copied
    # into the answer as is (prod, 2026-11-18)
    return "\n".join([
        f"[{r.get('job_id')}] {(r.get('job_name') or '-').strip()}",   # no "ใบประเมิน": it wrapped the number column
        f"  ลูกค้า: {(r.get('custName') or '-').strip()} ({r.get('customer_id') or '-'})",
        f"  ประเภทงาน: {r.get('est_type') or '-'} · บันทึกใน: {system}",
        f"  สถานะ: {status}{approved}",
        f"  ราคา: {_price_text(r)}",
        f"  สร้างใบ: {_when(r.get('created'))} · แก้ไขล่าสุด: {_when(r.get('updated'))}",
    ])


def describe_filters(p: dict) -> str:
    names = {"approve": STATUS_NAME[3], "pending": STATUS_NAME[1], "reject": STATUS_NAME[2], "draft": STATUS_NAME[0]}
    parts = [f"สถานะ: {names.get(p.get('status'), 'ทุกสถานะ (ไม่รวมร่าง)')}"]
    if p.get("date_from") or p.get("date_to"):
        parts.append(f"ช่วงวันที่: {p.get('date_from') or 'เริ่มต้น'} ถึง {p.get('date_to') or 'วันนี้'}")
    else:
        parts.append("ช่วงวันที่: ไม่ระบุ (ทุกช่วงเวลา)")
    if p.get("keyword"):
        parts.append(f"คำค้น: {p['keyword']}")
    return " · ".join(parts)


def to_context(p: dict, data: dict) -> str:
    now = _bangkok_now().strftime("%Y-%m-%d %H:%M")
    head = f"ข้อมูลจากระบบประเมินราคา (ทั้งระบบเดิมและระบบใหม่ MI2) ณ {now}\n"
    if data.get("legacy_missing"):
        head = (f"ข้อมูลจากระบบประเมินราคาใหม่ MI2 เท่านั้น ณ {now} — อ่านฐานข้อมูลระบบประเมินราคาเดิมไม่ได้ "
                "(อาจถูกปิดหรือย้ายไปแล้ว) ตัวเลขด้านล่างจึงไม่รวมใบจากระบบเดิม: ต้องบอกผู้ใช้ข้อนี้ในประโยคแรก\n")
    if "prices" in data:
        if not data["rows"]:
            return head + f"ไม่พบใบประเมินราคา {p.get('doc_id') or p.get('quotation_id')} ในระบบ"
        r = dict(data["rows"][0])
        if data["prices"]:
            first = data["prices"][0]
            r.update(price_qty=first.get("qty"), price_ex=first.get("price_ex"),
                     total_price=first.get("total_price"), currency_no=first.get("currency_no"),
                     exchange_rate=first.get("exchange_rate"), tiers=len(data["prices"]))
        if data.get("detail"):
            r.update(mid_qty=data["detail"][0].get("mid_qty"), mid_total_ex_vat=data["detail"][0].get("mid_total_ex_vat"))
        out = [head, "ใบประเมินราคา:", _row_text(r)]
        if r.get("credit_term_name"):
            out.append(f"  เครดิต: {r['credit_term_name']}")
        if data["prices"]:
            out.append("ราคาขายตามจำนวน:")
            for x in data["prices"]:
                fx = _fx(x)
                out.append(f"  - {_qty(x.get('qty'))} ชิ้น: {_money(x.get('price_ex'))} บาท ก่อน VAT"
                           + (f" (≈ {float(x['price_ex']) / float(x['qty']):,.2f} บาท/ชิ้น)"
                              if x.get("price_ex") and x.get("qty") else "")
                           + (f" · ≈ {fx[0]:,.2f} {fx[1]}" if fx else ""))
        if data.get("detail"):
            d = data["detail"][0]
            out.append(f"รายละเอียดในระบบใหม่: จำนวนที่ขอราคา {d.get('qty_list') or '-'} · งานพิมพ์ {d.get('print_type') or '-'} "
                       f"· แบบกล่อง {d.get('box_type') or '-'} · ประกบลูกฟูก {d.get('component_type') or '-'}")
        if data.get("history"):
            out.append("ประวัติสถานะ:")
            out += [f"  - {_when(h.get('created'))}: {STATUS_NAME.get(h.get('to_status_id'), h.get('to_status_id'))}"
                    for h in data["history"]]
        if data.get("quotes"):
            out.append("ใบเสนอราคาที่ออกจากใบนี้:")
            out += [f"  - {q.get('quotation_id')} (แก้ไขครั้งที่ {q.get('revision') or 0}): {q.get('status_label') or '-'}, "
                    f"จำนวน {q.get('qty_list') or '-'}, ยอดรวม {_money(q.get('grand_total'))} {q.get('currency') or ''}, "
                    f"ออก {_when(q.get('issue_date'))}, ใช้ได้ถึง {_when(q.get('expiry_date'))}" for q in data["quotes"]]
        return "\n".join(out)
    lines = [head, f"เงื่อนไขที่ค้น: {describe_filters(p)} (ไม่รวมรายการทดสอบ)"]
    if p.get("status") == "approve":
        lines.append("นิยาม (ยืนยันกับทีมแล้ว): 'เคาะราคาแล้ว' = ใบประเมินราคาที่หัวหน้าอนุมัติราคาแล้ว "
                     "(Approved ในระบบประเมินราคา) — ยังไม่ใช่งานที่ลูกค้าสั่ง")
    lines.append(f"พบทั้งหมด: {data['count']} งาน")
    if data["count"]:
        unpriced = data["count"] - data["priced"]
        lines.append(f"มูลค่ารวม (ผลรวมราคาประเมินก่อน VAT เป็นเงินบาท ที่จำนวนหลักของแต่ละงาน จาก {data['priced']} งาน"
                     f"ที่มีราคา{f' — อีก {unpriced} งานยังไม่มีราคา' if unpriced > 0 else ''} — เป็นมูลค่าราคาที่เสนอ "
                     f"ไม่ใช่ยอดขายจริง): {_money(data['total'])} บาท")
        if data.get("other_currency"):
            lines.append(f"ในจำนวนนี้มี {data['other_currency']} งานที่เสนอราคาลูกค้าเป็นเงินต่างประเทศ (USD/EUR) — "
                         "ระบบเก็บราคาของงานเหล่านี้เป็นเงินบาทตามอัตราแลกเปลี่ยนในใบประเมินอยู่แล้ว "
                         "จึงรวมอยู่ในมูลค่าบาทข้างบนด้วย")
        if data.get("by_type"):
            lines.append("แยกตามประเภทงาน: " + " · ".join(
                f"{b.get('est_type') or '-'} {b.get('n') or 0} งาน "
                + (f"(มูลค่า {_money(b.get('total'))} บาท)" if b.get("total") else "(ยังไม่มีราคา)")
                for b in data["by_type"]))
        if len(data.get("by_currency") or []) > 1:
            # total is baht for every currency (export jobs keep baht at their own rate, 2026-11-18)
            lines.append("แยกตามสกุลเงินที่เสนอลูกค้า: " + " · ".join(
                f"{b.get('currency') or 'THB'} {b.get('n') or 0} งาน ({_money(b.get('total'))} บาท"
                + (f" ≈ {_money(b.get('total_fx'))} {b.get('currency')}"
                   if (b.get("currency") or "THB") != "THB" and b.get("total_fx") else "") + ")"
                for b in data["by_currency"]))
        if data.get("top_customers"):
            lines.append("ลูกค้าที่มูลค่ารวมสูงสุด: " + " · ".join(
                f"{i}) {(b.get('customer') or '-').strip()}"
                + (" (รวมลูกค้าที่ยังไม่ลงทะเบียนหลายราย)" if b.get("customer_id") == "C9999998" else "")
                + f" {b.get('n') or 0} งาน {_money(b.get('total'))} บาท"
                for i, b in enumerate(data["top_customers"], 1)))
        days = ((p["date_to"] - p["date_from"]).days + 1) if isinstance(p.get("date_from"), dt.date) \
            and isinstance(p.get("date_to"), dt.date) else 0
        if days > 1:
            lines.append(f"เฉลี่ยวันละ {data['count'] / days:,.1f} งาน (ช่วง {days} วัน)"
                         + (f" · ลูกค้า {data['customers']:,} ราย" if data.get("customers") else "")
                         + " · นับทุกใบที่อนุมัติ รวมใบแก้ไข/หลายเวอร์ชันของงานเดียวกัน")
        if p.get("full_list"):
            lines.append(f"(ผู้ใช้ขอดูรายการทั้งหมด — ระบบจะแนบตารางครบทุกงาน (สูงสุด {factory.FULL_MAX:,} งาน) "
                         "ต่อท้ายคำตอบให้เอง และผู้ใช้กดปุ่ม Excel ใต้คำตอบเพื่อโหลดเป็นไฟล์ได้: ตอบสั้น ๆ ไม่เกิน "
                         "2 ประโยค เช่น 'ได้ค่ะ นี่คือรายการทั้งหมด … งาน (ช่วง …)' ไม่ต้องแจกแจงประเภท/สกุลเงิน/ลูกค้าซ้ำ "
                         "ห้ามเขียนตาราง ห้ามบอกว่าแสดงได้ไม่ครบ)")
        elif p.get("show_list", True):
            lines.append("(ระบบจะแนบตารางรายการงานต่อท้ายคำตอบให้เอง — ห้ามเขียนตารางหรือไล่รายการงานทีละงานเอง "
                         "ให้เขียนแค่สรุป)")
        else:
            lines.append("(ผู้ใช้ไม่ได้ขอดูรายการ — เขาถามยืนยันหรือถามเจาะจงจากคำตอบก่อนหน้า: ตอบเฉพาะสิ่งที่ถาม "
                         "ตรง ๆ เช่น 'ใช่ค่ะ…' แล้วอธิบายสั้น ๆ ว่าตัวเลขนี้นับอะไร ห้ามทวนคำตอบเดิมทั้งหมด "
                         "และไม่ต้องแสดงรายการหรือตาราง)")
        if data.get("priciest"):
            lines.append(f"5 งานที่ราคาสูงสุดในเงื่อนไขนี้ (เทียบเป็นเงินบาทจากทั้งหมด {data['count']} งาน — "
                         "ใช้ตอบ 'งานไหนแพงสุด'):")
            lines += [_row_text(r) for r in data["priciest"]]
        rows = data["rows"][:MAX_ROWS]   # a full list goes to the table, not to the model
        lines.append(f"ข้อมูลประกอบ: {len(rows)} งานล่าสุด (ไว้ตอบคำถามเรื่องงานล่าสุด — เป็นข้อมูลภายใน "
                     "ห้ามบอกผู้ใช้ว่าระบบดึงหรือแสดงได้แค่จำนวนนี้)")
        lines += [_row_text(r) for r in rows]
    elif data.get("latest"):
        lines.append("งานล่าสุดที่ตรงสถานะ/คำค้นนี้ (อยู่นอกช่วงวันที่ที่ถาม):")
        lines += [_row_text(r) for r in data["latest"]]
    return "\n".join(lines)
