"""
Pre-filter: detect questions that ask for sensitive company / personal data.

Blocks the request before any RAG or LLM call so the chatbot:
  - never leaks confidential info to the user
  - never spends OpenAI tokens on blocked attempts
  - always logs the attempt for admin review (via audit.py)

The keyword list is a starter. Expand based on real chat logs after go-live —
just edit SENSITIVE_KEYWORDS, rebuild backend, no migration needed.
"""

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
    "ต้นทุน",          # catches "ต้นทุนงานกล่อง", "ต้นทุนการผลิต", "ต้นทุนสินค้า"
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

BLOCKED_RESPONSE = (
    "ข้อมูลนี้เป็นข้อมูลส่วนบุคคลหรือข้อมูลภายในบริษัท "
    "ระบบไม่สามารถเปิดเผยผ่าน Chatbot ได้ "
    "กรุณาติดต่อฝ่ายที่เกี่ยวข้องหรือผู้มีอำนาจโดยตรง"
)


def is_sensitive(text: str) -> str | None:
    """
    Return the first sensitive keyword found in `text`, or None if clean.

    Matching is case-insensitive substring. "เงินเดือนคุณ A" matches "เงินเดือน";
    "What's the API_KEY" matches "api_key".
    """
    if not text:
        return None
    needle = text.lower()
    for kw in SENSITIVE_KEYWORDS:
        if kw.lower() in needle:
            return kw
    return None
