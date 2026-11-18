"""Priced jobs ("งานเคาะราคา") — read-only lookups in the MI estimate system.

Source: MI2_ESTIMATE.dbo.tb_rfq — one row per estimate request (doc_id E + 8
digits, e.g. E26110001). estimate_status: approve (= "เคาะราคาแล้ว"), pending
(waiting for approval), reject, draft. Quotations (QE…) hang off it by doc_id.

Only fixed, parameterised SELECTs on a few non-personal columns; nothing is
ever written. Owner decision 2026-11-17: every signed-in user may ask, prices
included. Test rows (test customer, "test"/"เทส" in the name) are left out.

Uses the MI login connection settings (mi_auth). PRICED_JOBS_MODE=off disables it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from functools import lru_cache

PRICED_JOBS_MODE = os.getenv("PRICED_JOBS_MODE", "on").strip().lower()
PRICED_JOBS_DB = os.getenv("PRICED_JOBS_DB", "MI2_ESTIMATE").strip()
TEST_CUSTOMERS = tuple(
    c.strip() for c in os.getenv("PRICED_JOBS_TEST_CUSTOMERS", "C9999998").split(",") if c.strip()
)
MAX_ROWS = 40
_DB_TIMEOUT = 20

STATUS_TH = {"approve": "อนุมัติแล้ว (เคาะราคาแล้ว)", "pending": "รออนุมัติ", "reject": "ไม่อนุมัติ",
             "draft": "ร่าง (ยังไม่ส่งอนุมัติ)", "over_limit_approve": "เกินวงเงิน รออนุมัติพิเศษ"}

DOC_RE = re.compile(r"\bE\d{8}\b", re.IGNORECASE)
QUOTE_RE = re.compile(r"\bQE\d{7}\b", re.IGNORECASE)
# Cheap pre-filter: only questions that mention estimates / approved prices go on
# to the (small) AI planner, which decides whether it really is a lookup.
TRIGGER_RE = re.compile(
    r"เคาะราคา|เคาะแล้ว|ประเมินราคา|อนุมัติราคา|ราคา.{0,12}(อนุมัติ|ผ่านแล้ว)|"
    r"ใบประเมิน|ใบเสนอราคา|ใบขอราคา|\brfq\b|estimate|quotation|"
    r"\bE\d{8}\b|\bQE\d{7}\b",
    re.IGNORECASE,
)


def enabled() -> bool:
    if PRICED_JOBS_MODE in {"off", "0", "false", "no"}:
        return False
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
    "(เลข E ตามด้วยตัวเลข 8 หลัก เช่น E26110001) แต่ละใบมีสถานะ: approve = อนุมัติแล้ว/เคาะราคาแล้ว, "
    "pending = รออนุมัติ/รอเคาะ, reject = ไม่อนุมัติ/ตีกลับ, draft = ร่าง และอาจมีใบเสนอราคา (เลข QE + 7 หลัก)\n"
    "ตัดสินว่าผู้ใช้ต้องการ 'ดูข้อมูลที่บันทึกในระบบประเมินราคา' หรือไม่ (รายการ จำนวน มูลค่ารวม "
    "หรือรายละเอียดใบใดใบหนึ่ง) แล้วตอบ JSON เท่านั้น:\n"
    '{"lookup": true|false, "doc_id": "E…"|null, "quotation_id": "QE…"|null, '
    '"status": "approve"|"pending"|"reject"|"draft"|"any", "date_from": "YYYY-MM-DD"|null, '
    '"date_to": "YYYY-MM-DD"|null, "keyword": string|null}\n'
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
             "keyword": None}
    if p is not None:
        if doc:
            p["doc_id"] = doc.group(0).upper()
        if quote:
            p["quotation_id"] = quote.group(0).upper()
    return p, usage


# ── database (fixed SELECTs only) ──────────────────────────────────────────

def _query(sql: str, params: tuple = ()) -> list[dict]:
    import pymssql

    from mi_auth import MI_DB_PASSWORD, MI_DB_PORT, MI_DB_SERVER, MI_DB_USER

    conn = pymssql.connect(server=MI_DB_SERVER, port=MI_DB_PORT, user=MI_DB_USER, password=MI_DB_PASSWORD,
                           database=PRICED_JOBS_DB, login_timeout=10, timeout=_DB_TIMEOUT, charset="UTF-8")
    try:
        cur = conn.cursor(as_dict=True)
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cur.execute(sql, params)
        return list(cur.fetchall())
    finally:
        conn.close()


_COLS = ("doc_id, estimate_name, customer_id, customer_name, ae_team_name, estimate_status, estimate_date, "
         "request_for_approve_at, approved_at, qty_list, mid_qty, mid_total_ex_vat, print_type, box_type, "
         "component_type, credit_term")
# the date that matters for each status
_DATE_COL = {"approve": "approved_at", "pending": "request_for_approve_at", "reject": "updated_at",
             "draft": "created_at", "any": "COALESCE(approved_at, request_for_approve_at, created_at)"}


def _not_test_sql() -> tuple[str, tuple]:
    # always at least one parameter: pymssql only turns %% into % when params are given
    ids = TEST_CUSTOMERS or ("__none__",)
    marks = ", ".join(["%s"] * len(ids))
    return (f"customer_id NOT IN ({marks}) AND estimate_name NOT LIKE N'%%test%%' "
            f"AND estimate_name NOT LIKE N'%%เทส%%'"), ids


def build_search(p: dict) -> tuple[str, str, tuple]:
    """(rows SQL, totals SQL, params) for a list question."""
    status = p.get("status") or "any"
    date_col = _DATE_COL[status]
    where = ["ISNULL(is_deleted, 0) = 0"]
    params: list = []
    if status == "any":
        where.append("estimate_status IN ('approve', 'pending', 'reject', 'over_limit_approve')")
    else:
        where.append("estimate_status = %s")
        params.append(status)
    nt_sql, nt_params = _not_test_sql()
    where.append(nt_sql)
    params += list(nt_params)
    if p.get("date_from"):
        where.append(f"{date_col} >= %s")
        params.append(p["date_from"].isoformat())
    if p.get("date_to"):
        where.append(f"{date_col} < %s")
        params.append((p["date_to"] + dt.timedelta(days=1)).isoformat())
    if p.get("keyword"):
        where.append("(estimate_name LIKE %s OR customer_name LIKE %s OR ae_team_name LIKE %s OR customer_id = %s)")
        like = f"%{p['keyword']}%"
        params += [like, like, like, p["keyword"]]
    w = " AND ".join(where)
    rows_sql = (f"SELECT TOP {MAX_ROWS} {_COLS} FROM dbo.tb_rfq WITH (NOLOCK) WHERE {w} "
                f"ORDER BY {date_col} DESC")
    totals_sql = (f"SELECT COUNT(*) AS n, SUM(CASE WHEN mid_total_ex_vat > 0 THEN mid_total_ex_vat END) AS total, "
                  f"SUM(CASE WHEN mid_total_ex_vat > 0 THEN 1 ELSE 0 END) AS priced "
                  f"FROM dbo.tb_rfq WITH (NOLOCK) WHERE {w}")
    return rows_sql, totals_sql, tuple(params)


def search(p: dict) -> dict:
    rows_sql, totals_sql, params = build_search(p)
    rows = _query(rows_sql, params)
    tot = (_query(totals_sql, params) or [{}])[0]
    out = {"rows": rows, "count": int(tot.get("n") or 0), "total": tot.get("total"),
           "priced": int(tot.get("priced") or 0)}
    if not out["count"] and (p.get("date_from") or p.get("date_to")):
        # nothing in that range: give the newest matching ones instead of a bare "0"
        latest_sql, _, latest_params = build_search(dict(p, date_from=None, date_to=None))
        out["latest"] = _query(latest_sql.replace(f"TOP {MAX_ROWS} ", "TOP 3 ", 1), latest_params)
    return out


def lookup_doc(doc_id: str | None, quotation_id: str | None) -> dict:
    """One estimate (any status, test rows included — the user named it) and
    its quotations."""
    if not doc_id and quotation_id:
        got = _query("SELECT TOP 1 doc_id FROM dbo.tb_quotation WITH (NOLOCK) WHERE quotation_id = %s",
                     (quotation_id,))
        doc_id = got[0]["doc_id"] if got else None
    if not doc_id:
        return {"rows": [], "quotes": []}
    rows = _query(f"SELECT TOP 1 {_COLS} FROM dbo.tb_rfq WITH (NOLOCK) "
                  "WHERE doc_id = %s AND ISNULL(is_deleted, 0) = 0", (doc_id,))
    quotes = _query(
        "SELECT quotation_id, revision, status_label, currency, qty_list, grand_total, issue_date, expiry_date, "
        "approved_at FROM dbo.tb_quotation WITH (NOLOCK) WHERE doc_id = %s AND ISNULL(is_deleted, 0) = 0 "
        "ORDER BY created_at DESC", (doc_id,))
    return {"rows": rows, "quotes": quotes, "doc_id": doc_id}


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


def _row_text(r: dict) -> str:
    price = r.get("mid_total_ex_vat")
    mid = r.get("mid_qty")
    if price and float(price) > 0:
        per = f" (≈ {float(price) / float(mid):,.2f} บาท/ชิ้น)" if mid else ""
        price_s = f"{_money(price)} บาท ก่อน VAT ที่จำนวน {int(mid):,} ชิ้น{per}" if mid else f"{_money(price)} บาท ก่อน VAT"
    else:
        price_s = "ยังไม่มีราคา"
    status = STATUS_TH.get(str(r.get("estimate_status") or ""), r.get("estimate_status") or "-")
    # one labelled field per line: a one-line "a | b | c" record got copied
    # into the answer as is (prod, 2026-11-18)
    return "\n".join([
        f"[ใบประเมิน {r.get('doc_id')}] {r.get('estimate_name') or '-'}",
        f"  ลูกค้า: {r.get('customer_name') or '-'} ({r.get('customer_id') or '-'})",
        f"  ทีมขาย: {r.get('ae_team_name') or '-'}",
        f"  สถานะ: {status}",
        f"  จำนวนที่ขอราคา: {r.get('qty_list') or '-'}",
        f"  ราคา: {price_s}",
        f"  งานพิมพ์: {r.get('print_type') or '-'} · แบบกล่อง: {r.get('box_type') or '-'} · "
        f"ประกบลูกฟูก: {r.get('component_type') or '-'}",
        f"  เครดิต: {r.get('credit_term') or '-'}",
        f"  วันที่ประเมิน: {_when(r.get('estimate_date'))} · ส่งขออนุมัติ: {_when(r.get('request_for_approve_at'))}"
        f" · อนุมัติ: {_when(r.get('approved_at'))}",
    ])


def describe_filters(p: dict) -> str:
    parts = [f"สถานะ: {STATUS_TH.get(p.get('status'), 'ทุกสถานะ (ไม่รวมร่าง)')}"]
    if p.get("date_from") or p.get("date_to"):
        parts.append(f"ช่วงวันที่: {p.get('date_from') or 'เริ่มต้น'} ถึง {p.get('date_to') or 'วันนี้'}")
    else:
        parts.append(f"ช่วงวันที่: ไม่ระบุ (แสดงล่าสุดไม่เกิน {MAX_ROWS} รายการ)")
    if p.get("keyword"):
        parts.append(f"คำค้น: {p['keyword']}")
    return " · ".join(parts)


def to_context(p: dict, data: dict) -> str:
    now = _bangkok_now().strftime("%Y-%m-%d %H:%M")
    head = f"ข้อมูลจากระบบประเมินราคา (MI) ณ {now}\n"
    if "quotes" in data:
        if not data["rows"]:
            return head + f"ไม่พบใบประเมินราคา {p.get('doc_id') or p.get('quotation_id')} ในระบบ"
        out = [head, "ใบประเมินราคา:", _row_text(data["rows"][0])]
        if data["quotes"]:
            out.append("ใบเสนอราคาที่ออกจากใบนี้:")
            for q in data["quotes"]:
                out.append(
                    f"- {q.get('quotation_id')} (แก้ไขครั้งที่ {q.get('revision') or 0}) | สถานะ: {q.get('status_label') or '-'} "
                    f"| จำนวน: {q.get('qty_list') or '-'} | ยอดรวม: {_money(q.get('grand_total'))} {q.get('currency') or ''} "
                    f"| วันที่ออก: {_when(q.get('issue_date'))} | ใช้ได้ถึง: {_when(q.get('expiry_date'))} "
                    f"| อนุมัติ: {_when(q.get('approved_at'))}")
        else:
            out.append("ยังไม่มีใบเสนอราคาที่ออกจากใบนี้")
        return "\n".join(out)
    lines = [head, f"เงื่อนไขที่ค้น: {describe_filters(p)} (ไม่รวมรายการทดสอบ)",
             f"พบทั้งหมด: {data['count']} งาน"]
    if data["count"]:
        lines.append(f"มูลค่ารวม (ราคาก่อน VAT ที่จำนวนกลางของแต่ละงาน, เฉพาะ {data['priced']} งานที่มีราคา): "
                     f"{_money(data['total'])} บาท")
        if data["count"] > len(data["rows"]):
            lines.append(f"(แสดงรายละเอียด {len(data['rows'])} งานล่าสุด)")
        lines.append("รายการ:")
        lines += [_row_text(r) for r in data["rows"]]
    elif data.get("latest"):
        lines.append("งานล่าสุดที่ตรงสถานะ/คำค้นนี้ (อยู่นอกช่วงวันที่ที่ถาม):")
        lines += [_row_text(r) for r in data["latest"]]
    return "\n".join(lines)
