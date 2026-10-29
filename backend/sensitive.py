"""
Pre-filter: detect questions that ask for sensitive company / personal data.

Blocks the request before any RAG or LLM call so the chatbot:
  - never leaks confidential info to the user
  - never spends OpenAI tokens on blocked attempts
  - always logs the attempt for admin review (via audit.py)

The keyword list is a starter. Expand based on real chat logs after go-live —
just edit SENSITIVE_KEYWORDS, rebuild backend, no migration needed.
"""

import re

# Case-insensitive substring match. Add new keywords anywhere; order doesn't matter.
SENSITIVE_KEYWORDS: set[str] = {
    # --- Payroll / compensation ---
    "เงินเดือน",
    "สลิปเงินเดือน",
    "โบนัส",
    "ค่าจ้าง",
    "ค่าตอบแทน",
    "payroll",
    "salary",

    # --- Employee personal info ---
    "ข้อมูลพนักงาน",
    "เลขบัตรประชาชน",
    "เลขประจำตัวประชาชน",
    "เบอร์โทรพนักงาน",
    "ที่อยู่พนักงาน",
    "national id",

    # --- Internal financial / pricing secrets ---
    # NOTE: bare "ต้นทุน" was removed — it false-blocked legitimate math where the
    # user supplies their own numbers (e.g. "คิดต้นทุนกระดาษ 500 แผ่น แผ่นละ 2.5
    # บวก VAT"), very common for printing-quote work. The Layer-2 classifier
    # (sensitivity_classifier.py) still blocks requests to REVEAL the company's
    # internal cost/margin, which is the actual thing we protect.
    "ราคาทุน",
    "ราคาลับ",
    "margin",
    "กำไรขั้นต้น",
    "กำไรสุทธิ",

    # --- Customer / sales secrets ---
    "ข้อมูลลูกค้า",
    "ยอดขายรายลูกค้า",
    "รายชื่อลูกค้า",

    # --- Credentials / API ---
    "รหัสผ่าน",
    "password",
    "api key",
    "api_key",
    "access token",

    # --- Banking / accounts ---
    "ข้อมูลบัญชี",
    "เลขบัญชี",
    "ข้อมูลการเงินภายใน",
}

# Pay words a job ad carries ("รับสมัครช่างพิมพ์ เงินเดือน 20,000"): in clearly
# recruitment text they don't block here — the user is publishing a position's
# pay, not asking for someone's. The Layer-2 classifier still runs and blocks a
# named person's pay or the company's real internal pay (sensitivity_classifier
# PAYROLL rule). "สลิปเงินเดือน" and "payroll" always block.
RECRUITMENT_PAY_KEYWORDS: set[str] = {"เงินเดือน", "ค่าจ้าง", "ค่าตอบแทน", "โบนัส", "salary"}
_RECRUITMENT_RE = re.compile(
    r"รับสมัคร|สมัครงาน|ประกาศ(?:รับ|หา|งาน)|เปิดรับ(?:พนักงาน|สมัคร)|รับพนักงาน|"
    r"\b(?:hiring|recruit\w*|job (?:ad|post|opening)s?)\b",
    re.IGNORECASE,
)

BLOCKED_RESPONSE = (
    "ข้อมูลนี้เป็นข้อมูลส่วนบุคคลหรือข้อมูลภายในบริษัท "
    "ระบบไม่สามารถเปิดเผยผ่าน Chatbot ได้ "
    "กรุณาติดต่อฝ่ายที่เกี่ยวข้องหรือผู้มีอำนาจโดยตรง"
)


def is_sensitive(text: str) -> str | None:
    """
    Return the first sensitive keyword found in `text`, or None if clean.

    Matching is case-insensitive substring. "เงินเดือนคุณ A" matches "เงินเดือน";
    "What's the API_KEY" matches "api_key". In a job ad the pay words are
    skipped (RECRUITMENT_PAY_KEYWORDS) — "สลิปเงินเดือน" still matches.
    """
    if not text:
        return None
    needle = text.lower()
    recruitment = _RECRUITMENT_RE.search(needle) is not None
    for kw in SENSITIVE_KEYWORDS:
        if kw.lower() in needle:
            if recruitment and kw in RECRUITMENT_PAY_KEYWORDS:
                continue
            return kw
    return None
