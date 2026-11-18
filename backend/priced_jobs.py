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
    '"date_to": "YYYY-MM-DD"|null, "keyword": string|null, "show_list": true|false}\n'
    "กติกา:\n"
    "- lookup=false ถ้าผู้ใช้ขอให้คำนวณ/ประเมินราคาเอง, ร่างหรือเขียนใบเสนอราคา, ถามความหมาย/วิธีทำ, "
    "หรือถามความรู้ทั่วไป\n"
    "- 'เคาะราคาแล้ว', 'อนุมัติราคาแล้ว', 'ราคาผ่านแล้ว', 'งานใหม่ที่ได้ราคาแล้ว' → approve; "
    "'รอเคาะ', 'รออนุมัติ' → pending; 'ไม่ผ่าน', 'ตีกลับ', 'reject' → reject; "
    "ถามถึงใบเดียว (เลข E/QE) โดยไม่ระบุสถานะ → any\n"
    "- วันที่ (รวมวันสุดท้าย) ใช้ตามนี้: {anchors}. เดือน/วันที่ระบุเป็น พ.ศ. ให้ลบ 543. "
    "ถ้าไม่ได้ระบุช่วงเวลา ให้ date_from และ date_to เป็น null (= ล่าสุด)\n"
    "- keyword: ชื่อลูกค้า แบรนด์ สินค้า ชื่องาน หรือทีม/AE ที่ผู้ใช้ต้องการกรอง (เช่น 'AROMA', 'ศรีวรรณ') "
    "ไม่ใส่คำบอกสถานะหรือเวลาใน keyword ถ้าไม่มีให้ null\n"
    "- ถ้ามี 'คำถามก่อนหน้า' และคำถามนี้เป็นคำถามต่อเนื่อง (เช่น 'แล้วเดือนที่แล้วล่ะ', 'อันไหนแพงสุด', "
    "'มีของลูกค้า X ไหม') ให้ lookup=true และใช้เงื่อนไขเดิมเว้นแต่ผู้ใช้เปลี่ยน"
    "\n- show_list: true เมื่อผู้ใช้ต้องการดูรายการงาน (ถามใหม่ หรือเปลี่ยนเงื่อนไข เช่น ช่วงเวลา/สถานะ/ลูกค้า); false เมื่อถามยืนยันหรือสงสัยคำตอบก่อนหน้า (เช่น 'มี 661 ใบเลยหรอ', 'จริงเหรอ', 'ทำไมเยอะจัง') หรือถามเจาะจงข้อเดียว (เช่น 'อันไหนแพงสุด', 'ลูกค้าไหนเยอะสุด', 'รวมเท่าไหร่') — ยังต้อง lookup=true"
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
        # false: a check or a pointed question about the last answer ("มี 661 ใบเลยหรอ") —
        # answer it, don't repeat the whole list and its table (prod, 2026-11-18)
        "show_list": data.get("show_list") is not False,
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


def plan(question: str, previous: str = "") -> tuple[dict | None, dict]:
    """(filters or None when it isn't a lookup, usage of the planner call).
    An E-/QE-number in the question is taken as-is even if the AI is unsure."""
    try:
        raw, usage = _plan_cached(question.strip(), (previous or "").strip(), _bangkok_today().isoformat())
        p = _clean_plan(json.loads(raw))
        usage = json.loads(usage)
    except Exception:
        p, usage = None, {}
    doc, quote = DOC_RE.search(question or ""), QUOTE_RE.search(question or "")
    if (doc or quote) and p is None:
        p = {"doc_id": None, "quotation_id": None, "status": "any", "date_from": None, "date_to": None,
             "keyword": None, "show_list": True}
    if p is not None:
        if doc:
            p["doc_id"] = doc.group(0).upper()
        if quote:
            p["quotation_id"] = quote.group(0).upper()
    return p, usage


# ── database (fixed SELECTs only) ──────────────────────────────────────────
# Everything is read on the LIVE server 192.168.5.19. (The MI2_ESTIMATE on the
# MI login server .10 is a test copy — the first version read it by mistake.)
# One master list covers both estimate systems: ESTIMATE.dbo.tb_rfq_list,
# source_system V1 (old app) / V2 (new MI2 app, synced in). OPENQUERY takes no
# parameters, so every value below is validated or reduced to safe characters.

_LIST = "ESTIMATE.dbo.tb_rfq_list"
_PRICE = "ESTIMATE.dbo.tb_rfq_price1"
_LOG = "ESTIMATE.dbo.tb_rfq_status_log"
_MI2_RFQ = "MI2_ESTIMATE.dbo.tb_rfq"
_MI2_QUOTE = "MI2_ESTIMATE.dbo.tb_quotation"

STATUS_ID = {"draft": 0, "pending": 1, "reject": 2, "approve": 3}
STATUS_NAME = {0: "ร่าง (ยังไม่ส่งอนุมัติ)", 1: "ส่งขออนุมัติแล้ว รออนุมัติ", 2: "ไม่อนุมัติ",
               3: "อนุมัติแล้ว (เคาะราคาแล้ว)", 4: "เกินวงเงิน รออนุมัติพิเศษ"}
# the date that matters for each status (estimate_date = when it was approved)
_DATE_COL = {"approve": "l.estimate_date", "pending": "l.updated", "reject": "l.updated", "draft": "l.created",
             "any": "COALESCE(l.estimate_date, l.updated, l.created)"}


def _run(sqls: list[str]) -> list[list[dict]]:
    import factory

    with factory.session() as s:
        return [s.run(q) for q in sqls]


# the price shown for a job: the line at its main quantity, else its smallest quantity
# price_ex: the old system stores the price WITH its tax line (E26110464: 27,094.95 = 26,305.78 + 789.17
# tax), the new one before it (mid_total_ex_vat 26,305.78) — both are shown before tax.
_PRICE_APPLY = f"""OUTER APPLY (SELECT TOP 1 x.qty AS price_qty, x.total_price - ISNULL(x.tax, 0) AS price_ex,
            x.total_price, x.currency_no
        FROM {_PRICE} x WITH (NOLOCK) WHERE x.job_id = l.job_id
        ORDER BY CASE WHEN x.qty = x.main_qty THEN 0 ELSE 1 END, x.qty) p
    OUTER APPLY (SELECT COUNT(*) AS tiers FROM {_PRICE} y WITH (NOLOCK) WHERE y.job_id = l.job_id) t
    OUTER APPLY (SELECT TOP 1 r.mid_qty, r.mid_total_ex_vat FROM {_MI2_RFQ} r WITH (NOLOCK)
        WHERE l.source_system = 'V2' AND r.doc_id COLLATE DATABASE_DEFAULT = l.job_id COLLATE DATABASE_DEFAULT) v"""

_LIST_COLS = ("l.job_id, l.job_name, l.customer_id, c.custName, l.est_type, l.status_id, l.source_system, "
              "l.estimate_date, l.created, l.updated")


def _not_test_sql() -> str:
    ids = [c for c in TEST_CUSTOMERS if re.fullmatch(r"[A-Za-z0-9_\-]{1,20}", c)]
    parts = [f"l.customer_id NOT IN ({', '.join(repr(c) for c in ids)})"] if ids else []
    # C9999998 is NOT a test customer: it is "ลูกค้าใหม่" (a customer not registered
    # yet). Every approved estimate on the new system used it, so excluding it
    # gave "0 งาน" (2026-11-18). Test rows are recognised by their name only.
    parts += ["l.job_name NOT LIKE N'%test%'", "l.job_name NOT LIKE N'%เทส%'", "l.job_name NOT LIKE N'%ทดสอบ%'"]
    return " AND ".join(parts)


_AMOUNT = "COALESCE(p.price_ex, v.mid_total_ex_vat)"
_AMOUNT_THB = f"CASE WHEN ISNULL(p.currency_no, 'THB') = 'THB' AND {_AMOUNT} > 0 THEN {_AMOUNT} END"


def _base(p: dict) -> tuple[str, str]:
    """(FROM … WHERE … for a list question, its date column) — values inlined, all validated."""
    import factory

    status = p.get("status") if p.get("status") in _DATE_COL else "any"
    date_col = _DATE_COL[status]
    where = ["l.status_id IN (1, 2, 3)" if status == "any" else f"l.status_id = {STATUS_ID[status]}", _not_test_sql()]
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
    """(rows SQL, totals SQL) for a list question."""
    base, date_col = _base(p)
    rows_sql = (f"SELECT TOP {MAX_ROWS} {_LIST_COLS}, p.price_qty, p.price_ex, p.total_price, p.currency_no, "
                f"t.tiers, v.mid_qty, v.mid_total_ex_vat {base} ORDER BY {date_col} DESC")
    totals_sql = (f"SELECT COUNT(*) AS n, COUNT(DISTINCT l.customer_id) AS customers, SUM({_AMOUNT_THB}) AS total, "
                  f"SUM(CASE WHEN ISNULL(p.currency_no, 'THB') = 'THB' AND {_AMOUNT} > 0 THEN 1 ELSE 0 END) AS priced, "
                  f"SUM(CASE WHEN ISNULL(p.currency_no, 'THB') <> 'THB' THEN 1 ELSE 0 END) AS other_currency "
                  f"{base}")
    return rows_sql, totals_sql


def build_breakdowns(p: dict) -> list[str]:
    """For a long list (660 approvals in half a month): counts and values by job
    type, by currency, and the five customers with the highest value."""
    base, _ = _base(p)
    return [
        f"SELECT ISNULL(l.est_type, '-') AS est_type, COUNT(*) AS n, SUM({_AMOUNT_THB}) AS total {base} "
        f"GROUP BY ISNULL(l.est_type, '-') ORDER BY COUNT(*) DESC",
        f"SELECT ISNULL(p.currency_no, 'THB') AS currency, COUNT(*) AS n, "
        f"SUM(CASE WHEN {_AMOUNT} > 0 THEN {_AMOUNT} END) AS total {base} "
        f"GROUP BY ISNULL(p.currency_no, 'THB') ORDER BY COUNT(*) DESC",
        f"SELECT TOP 5 ISNULL(c.custName, l.customer_id) AS customer, MAX(l.customer_id) AS customer_id, "
        f"COUNT(*) AS n, SUM({_AMOUNT_THB}) AS total {base} "
        f"GROUP BY ISNULL(c.custName, l.customer_id) ORDER BY SUM({_AMOUNT_THB}) DESC",
    ]


def search(p: dict) -> dict:
    rows_sql, totals_sql = build_search(p)
    sqls = [rows_sql, totals_sql] + build_breakdowns(p)
    if p.get("date_from") or p.get("date_to"):
        # in case nothing is in that range: the newest matching ones instead of a bare "0"
        latest_sql, _ = build_search(dict(p, date_from=None, date_to=None))
        sqls.append(latest_sql.replace(f"TOP {MAX_ROWS} ", "TOP 3 ", 1))
    got = _run(sqls)
    rows, tot = got[0], (got[1] or [{}])[0]
    out = {"rows": rows, "count": int(tot.get("n") or 0), "total": tot.get("total"),
           "priced": int(tot.get("priced") or 0), "other_currency": int(tot.get("other_currency") or 0),
           "customers": int(tot.get("customers") or 0),
           "by_type": got[2] if len(got) > 2 else [], "by_currency": got[3] if len(got) > 3 else [],
           "top_customers": got[4] if len(got) > 4 else []}
    if not out["count"] and len(got) > 5:
        out["latest"] = got[5]
    return out


def lookup_doc(doc_id: str | None, quotation_id: str | None) -> dict:
    """One estimate (any status, test rows included — the user named it): its
    list entry, price per quantity, status history, and — for new-system (V2)
    estimates — print details and quotations."""
    doc_id = doc_id.upper() if doc_id and DOC_RE.fullmatch(doc_id) else None
    quotation_id = quotation_id.upper() if quotation_id and QUOTE_RE.fullmatch(quotation_id) else None
    if not doc_id and quotation_id:
        got = _run([f"SELECT TOP 1 doc_id FROM {_MI2_QUOTE} WITH (NOLOCK) WHERE quotation_id = '{quotation_id}'"])[0]
        found = str((got[0].get("doc_id") or "") if got else "")
        doc_id = found.upper() if DOC_RE.fullmatch(found) else None
    if not doc_id:
        return {"rows": [], "prices": [], "history": [], "detail": [], "quotes": []}
    rows, prices, history, detail, quotes = _run([
        f"SELECT TOP 1 {_LIST_COLS}, l.credit_term_name FROM {_LIST} l WITH (NOLOCK) "
        f"LEFT JOIN mi.dbo.customer c WITH (NOLOCK) ON c.custID = l.customer_id WHERE l.job_id = '{doc_id}'",
        f"SELECT TOP 12 qty, total_price - ISNULL(tax, 0) AS price_ex, total_price, currency_no FROM {_PRICE} WITH (NOLOCK) "
        f"WHERE job_id = '{doc_id}' ORDER BY qty",
        f"SELECT TOP 20 from_status_id, to_status_id, created FROM {_LOG} WITH (NOLOCK) "
        f"WHERE job_id = '{doc_id}' ORDER BY created",
        f"SELECT TOP 1 qty_list, mid_qty, mid_total_ex_vat, print_type, box_type, component_type "
        f"FROM {_MI2_RFQ} WITH (NOLOCK) WHERE doc_id = '{doc_id}' AND ISNULL(is_deleted, 0) = 0",
        f"SELECT quotation_id, revision, status_label, currency, qty_list, grand_total, issue_date, expiry_date, "
        f"approved_at FROM {_MI2_QUOTE} WITH (NOLOCK) WHERE doc_id = '{doc_id}' AND ISNULL(is_deleted, 0) = 0 "
        f"ORDER BY created_at DESC",
    ])
    return {"rows": rows, "prices": prices, "history": history, "detail": detail, "quotes": quotes,
            "doc_id": doc_id}


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
    paragraph of bars (2026-11-18). 15 or fewer → all; more → the newest 10."""
    if not rows:
        return ""
    shown = rows if count <= 15 else rows[:10]
    approve = p.get("status") == "approve"
    head = (f"| เลขที่ | ชื่องาน | ลูกค้า | จำนวน | ราคา (ก่อน VAT) | {'อนุมัติเมื่อ' if approve else 'วันที่'} |\n"
            "|---|---|---|---:|---:|---|")
    body = []
    for r in shown:
        ex, qty, cur = r.get("price_ex"), r.get("price_qty"), r.get("currency_no") or "THB"
        if not (ex and float(ex) > 0):
            ex, qty, cur = r.get("mid_total_ex_vat"), r.get("mid_qty"), "THB"
        price = f"{_money(ex)} {cur}" if ex and float(ex) > 0 else "ยังไม่มีราคา"
        when = r.get("estimate_date") if approve else (r.get("updated") or r.get("created"))
        body.append(f"| {_cell(r.get('job_id'), 14)} | {_cell(r.get('job_name'))} | {_cell(r.get('custName'), 40)} | "
                    f"{_qty(qty)} | {price} | {_when(when)} |")
    if title is None and count > len(shown):
        title = f"รายการล่าสุด {len(shown)} จากทั้งหมด {count:,} งาน"
    return (f"**{title}**\n\n" if title else "") + head + "\n" + "\n".join(body)


def _price_text(r: dict) -> str:
    """The price line for one job, before tax: the selling price at its main
    quantity (old-system price lines, tax taken off), else the new system's
    ex-VAT price at its middle quantity. Both systems agree on this basis."""
    ex, qty = r.get("price_ex"), r.get("price_qty")
    if ex and float(ex) > 0:
        cur = r.get("currency_no") or "THB"
        per = f" (≈ {float(ex) / float(qty):,.2f} {cur}/ชิ้น)" if qty else ""
        tiers = r.get("tiers") or 0
        more = f" · มีราคาทั้งหมด {tiers} ระดับจำนวน" if tiers and int(tiers) > 1 else ""
        return f"{_money(ex)} {cur} ก่อน VAT ที่จำนวน {_qty(qty)} ชิ้น{per}{more}"
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
        parts.append(f"ช่วงวันที่: ไม่ระบุ (แสดงล่าสุดไม่เกิน {MAX_ROWS} รายการ)")
    if p.get("keyword"):
        parts.append(f"คำค้น: {p['keyword']}")
    return " · ".join(parts)


def to_context(p: dict, data: dict) -> str:
    now = _bangkok_now().strftime("%Y-%m-%d %H:%M")
    head = f"ข้อมูลจากระบบประเมินราคา (ทั้งระบบเดิมและระบบใหม่ MI2) ณ {now}\n"
    if "prices" in data:
        if not data["rows"]:
            return head + f"ไม่พบใบประเมินราคา {p.get('doc_id') or p.get('quotation_id')} ในระบบ"
        r = dict(data["rows"][0])
        if data["prices"]:
            first = data["prices"][0]
            r.update(price_qty=first.get("qty"), price_ex=first.get("price_ex"),
                     total_price=first.get("total_price"), currency_no=first.get("currency_no"),
                     tiers=len(data["prices"]))
        if data.get("detail"):
            r.update(mid_qty=data["detail"][0].get("mid_qty"), mid_total_ex_vat=data["detail"][0].get("mid_total_ex_vat"))
        out = [head, "ใบประเมินราคา:", _row_text(r)]
        if r.get("credit_term_name"):
            out.append(f"  เครดิต: {r['credit_term_name']}")
        if data["prices"]:
            out.append("ราคาขายตามจำนวน:")
            out += [f"  - {_qty(x.get('qty'))} ชิ้น: {_money(x.get('price_ex'))} {x.get('currency_no') or 'THB'} ก่อน VAT"
                    + (f" (≈ {float(x['price_ex']) / float(x['qty']):,.2f} ต่อชิ้น)" if x.get("price_ex") and x.get("qty")
                       else "") for x in data["prices"]]
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
        lines.append(f"มูลค่ารวม (ผลรวมราคาประเมินก่อน VAT ที่จำนวนหลักของแต่ละงาน รวมเฉพาะ {data['priced']} งาน"
                     f"ที่มีราคาเป็นบาท — เป็นมูลค่าราคาที่เสนอ ไม่ใช่ยอดขายจริง): {_money(data['total'])} บาท")
        if data.get("other_currency"):
            lines.append(f"มี {data['other_currency']} งานที่ราคาเป็นสกุลเงินอื่น ไม่ได้รวมในยอดบาทข้างบน")
        if data.get("by_type"):
            lines.append("แยกตามประเภทงาน: " + " · ".join(
                f"{b.get('est_type') or '-'} {b.get('n') or 0} งาน "
                + (f"(มูลค่า {_money(b.get('total'))} บาท)" if b.get("total") else "(ราคาไม่ได้เป็นเงินบาท)")
                for b in data["by_type"]))
        if len(data.get("by_currency") or []) > 1:
            # counts only: "USD 52 งาน รวม 41,110,437.31 USD" (2026-11-18) is ~790k USD a job — the
            # amount is probably in baht with a foreign currency label. Not shown until checked.
            lines.append("แยกตามสกุลเงิน (จำนวนงาน): " + " · ".join(
                f"{b.get('currency') or 'THB'} {b.get('n') or 0} งาน" for b in data["by_currency"])
                + " — ยังไม่แสดงมูลค่าของสกุลเงินอื่น")
        if data.get("top_customers"):
            lines.append("ลูกค้าที่มูลค่ารวมสูงสุด (เฉพาะราคาเป็นบาท): " + " · ".join(
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
        if p.get("show_list", True):
            lines.append("(ระบบจะแนบตารางรายการงานต่อท้ายคำตอบให้เอง — ห้ามเขียนตารางหรือไล่รายการงานทีละงานเอง "
                         "ให้เขียนแค่สรุป)")
        else:
            lines.append("(ผู้ใช้ไม่ได้ขอดูรายการ — เขาถามยืนยันหรือถามเจาะจงจากคำตอบก่อนหน้า: ตอบเฉพาะสิ่งที่ถาม "
                         "ตรง ๆ เช่น 'ใช่ค่ะ…' แล้วอธิบายสั้น ๆ ว่าตัวเลขนี้นับอะไร ห้ามทวนคำตอบเดิมทั้งหมด "
                         "และไม่ต้องแสดงรายการหรือตาราง)")
        lines.append(f"รายการด้านล่าง (ไว้ตอบคำถามเจาะจง เช่น งานไหนแพงสุด): {len(data['rows'])} งานล่าสุด "
                     f"จากทั้งหมด {data['count']} งาน")
        lines += [_row_text(r) for r in data["rows"]]
    elif data.get("latest"):
        lines.append("งานล่าสุดที่ตรงสถานะ/คำค้นนี้ (อยู่นอกช่วงวันที่ที่ถาม):")
        lines += [_row_text(r) for r in data["latest"]]
    return "\n".join(lines)
