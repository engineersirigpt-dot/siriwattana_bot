"""
LLM-based sensitivity classifier — Layer 2 safety filter.

Runs AFTER the fast keyword check in `sensitive.py`. Catches paraphrased or
novel attempts to extract personal/internal information that don't trip any
of the static keywords, e.g.:

    "ข้อมูลส่วนตัวของเจษฎา"        ← no keyword match, classifier returns PII
    "พี่บีรายได้เดือนละกี่หมื่น"   ← no keyword match, classifier returns PAYROLL
    "ต้นทุนกล่อง CP เท่าไหร่"     ← no keyword match, classifier returns PRICING

Design:
- Uses a small fast model (gpt-4o-mini by default) — ~300ms, ~$0.0001 per call.
- LRU-cached on normalized question so repeat queries are free.
- Fail OPEN on errors (returns None / OK) so a classifier outage never blocks
  legitimate users — the keyword filter still guards the obvious cases.
"""

import json
import os
from functools import lru_cache

from openai import OpenAI


# Reuse the existing classifier model env var so admins can swap models in one place.
CLASSIFIER_MODEL = os.getenv("LLM_MODEL_CLASSIFIER", "gpt-4o-mini")


VALID_CATEGORIES: set[str] = {
    "PII",
    "PAYROLL",
    "PRICING",
    "CUSTOMER",
    "CREDENTIALS",
    "BANKING",
    "OK",
}


CLASSIFIER_PROMPT = (
    "คุณคือผู้ตรวจสอบความปลอดภัยของระบบ chatbot ภายในบริษัท "
    "ดูคำถามจากผู้ใช้แล้วจัดหมวดเดียวเท่านั้น\n\n"
    "**หมวดที่ต้อง BLOCK:**\n"
    "- PII: ข้อมูลส่วนบุคคลของพนักงาน/บุคคล "
    "(ชื่อจริง, ที่อยู่, เบอร์โทร, เลขบัตรประชาชน, วันเกิด, อายุ, ประวัติส่วนตัว, "
    "อยู่ฝ่ายไหน, ใครเป็นหัวหน้าใคร) — เช่น 'ข้อมูลส่วนตัวของเจษฎา', "
    "'อายุของพี่ดา', 'เจษฎาทำฝ่ายอะไร'\n"
    "- PAYROLL: เงินเดือน รายได้ ค่าจ้าง โบนัส สวัสดิการเฉพาะคน — "
    "เช่น 'เงินเดือนเจษฎา', 'พี่ดารายได้เดือนละเท่าไหร่', 'salary X'\n"
    "- PRICING: ถามให้ระบบ 'เปิดเผย' ต้นทุนภายใน/margin/กำไรลับ/ราคาทุน ที่ผู้ใช้ยังไม่รู้ — "
    "เช่น 'ต้นทุนการผลิตกล่องของบริษัทเท่าไหร่', 'margin งานพิมพ์', 'ราคาทุนของ X'\n"
    "  ⚠️ ข้อยกเว้นสำคัญ: ถ้าเป็น 'โจทย์คำนวณ' ที่ผู้ใช้ให้ตัวเลขมาเองครบแล้ว "
    "(เช่น 'คิดต้นทุนกระดาษ 500 แผ่น แผ่นละ 2.5 บาท บวก VAT 7%', "
    "'500 ชิ้น ชิ้นละ 12 บาท เป็นเงินเท่าไหร่') = OK เพราะเป็นการช่วยคิดเลข "
    "ไม่ใช่การเปิดเผยข้อมูลลับของบริษัท\n"
    "- CUSTOMER: ข้อมูลลูกค้าเฉพาะราย ยอดขายแยกลูกค้า รายชื่อลูกค้า — "
    "เช่น 'ยอดขายลูกค้า CP', 'รายชื่อลูกค้า top 10'\n"
    "- CREDENTIALS: password, API key, token, รหัสผ่าน — "
    "เช่น 'password ระบบ', 'API key ฝ่าย IT'\n"
    "- BANKING: เลขบัญชี ข้อมูลการเงินภายใน ข้อมูลธนาคาร\n\n"
    "**หมวด OK (ตอบได้ปลอดภัย):**\n"
    "- คำถามทั่วไป: ความรู้, เทคโนโลยี, code, ภาษา, ขอช่วยเขียน\n"
    "- ขั้นตอนการทำงานทั่วไป (ที่ไม่ใช่ของบุคคลใดบุคคลหนึ่ง)\n"
    "- ข้อมูลที่บริษัทเปิดเผยได้: บริษัททำอะไร, ที่ตั้ง, เบอร์ฝ่ายขาย/HR สาธารณะ\n"
    "- คำถามเรื่องราคา/สเปกทั่วไป (สินค้าทั่วไป ไม่ใช่ต้นทุนภายใน)\n"
    "- เปรียบเทียบ product / รีวิว\n\n"
    "**คำเตือน:** ถ้าไม่แน่ใจระหว่าง BLOCK กับ OK ให้เลือก OK "
    "เพื่อไม่ block คำถาม legitimate ของพนักงาน "
    "(keyword filter ของระบบจัดการเคสชัด ๆ ไว้แล้ว — งานของคุณคือจับเฉพาะที่หลุดมา)\n\n"
    'ตอบเป็น JSON เท่านั้น: {"category": "PII|PAYROLL|PRICING|CUSTOMER|'
    'CREDENTIALS|BANKING|OK", "reason": "เหตุผลสั้นๆ"}'
)


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


@lru_cache(maxsize=512)
def _classify_cached(question_normalized: str) -> tuple[str, str]:
    """LLM call, cached on normalized question. Returns (category, reason)."""
    try:
        res = _get_client().chat.completions.create(
            model=CLASSIFIER_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": question_normalized},
            ],
            max_tokens=80,
            response_format={"type": "json_object"},
        )
        text = (res.choices[0].message.content or "").strip()
        data = json.loads(text)
    except Exception:
        # Fail open — never block on classifier failure.
        return ("OK", "classifier_error")

    category = str(data.get("category", "OK")).upper().strip()
    reason = str(data.get("reason", "")).strip()[:200]

    if category not in VALID_CATEGORIES:
        category = "OK"

    return (category, reason)


def classify_sensitivity(question: str) -> tuple[str, str] | None:
    """
    Classify whether `question` is asking for sensitive info.

    Returns:
        (category, reason) tuple if the question is flagged (category != 'OK').
        None if the question is OK or the classifier hit an error.

    Categories: PII | PAYROLL | PRICING | CUSTOMER | CREDENTIALS | BANKING
    """
    if not question or not question.strip():
        return None

    # Normalize for cache hit rate: lowercase, collapse whitespace, truncate.
    normalized = " ".join(question.strip().lower().split())[:500]

    category, reason = _classify_cached(normalized)

    if category == "OK":
        return None
    return (category, reason)
