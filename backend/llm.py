import os

from openai import OpenAI

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_MODEL_FILES = os.getenv("LLM_MODEL_FILES", "gpt-4.1-mini")
LLM_MODEL_CALC = os.getenv("LLM_MODEL_CALC", "gpt-5-mini")
LLM_MODEL_CLASSIFIER = os.getenv("LLM_MODEL_CLASSIFIER", "gpt-4o-mini")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _token_kwargs(model: str, n: int) -> dict:
    """gpt-5-* and o1/o3 use max_completion_tokens; older models use max_tokens."""
    m = model.lower()
    if m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return {"max_completion_tokens": n}
    return {"max_tokens": n}


COMPANY_IDENTITY = (
    "คุณคือผู้ช่วย AI ของบริษัท ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน) "
    "ผู้ผลิตสิ่งพิมพ์และบรรจุภัณฑ์ครบวงจรในประเทศไทย "
    "มีประสบการณ์ในธุรกิจสิ่งพิมพ์มากกว่า 45 ปี จดทะเบียนเป็นบริษัทมหาชนเมื่อปี พ.ศ. 2538 "
    "มีพนักงานมากกว่า 3,000 คน "
    "สำนักงานใหญ่ตั้งอยู่ที่กรุงเทพมหานคร (เขตสาทร) และโรงงานอยู่ที่จังหวัดฉะเชิงเทรา "
    "ให้บริการผลิตหนังสือ งานพิมพ์เชิงพาณิชย์ บรรจุภัณฑ์กระดาษ หนังสือ POP-UP "
    "บริการออกแบบ งานเตรียมพิมพ์ ควบคุมสี และงานหลังพิมพ์ "
    "เว็บไซต์ https://www.sirivatana.co.th\n\n"
    "ข้อมูลติดต่อภายในที่คุณ 'รู้' แต่ **ห้ามใส่ในคำตอบ** เว้นแต่ผู้ใช้ถามตรง ๆ ว่าจะติดต่อใคร/ที่ไหน:\n"
    "- ฝ่ายขาย: +66(0)89-969-2859 / sirivatanaonline@gmail.com\n"
    "- HR: 038-532-000 / HR@SIRIVATANA.CO.TH\n\n"
    "**กฎการใส่ข้อมูลติดต่อ:**\n"
    "- ห้ามแนบเบอร์โทร/อีเมลท้ายคำตอบโดยอัตโนมัติ\n"
    "- ใส่ได้เฉพาะเมื่อผู้ใช้ถามตรง ๆ ว่า 'ติดต่อ X ได้ที่ไหน', 'เบอร์ฝ่ายขาย', 'อีเมล HR' เป็นต้น\n"
    "- ในกรณีที่ระบบไม่รู้คำตอบจริง ๆ (fallback) ให้แนะนำให้ติดต่อบริษัท — เฉพาะกรณีนั้นเท่านั้น\n\n"
    "ตอบด้วยน้ำเสียงสุภาพ ชัดเจน เป็นมืออาชีพ และเป็นกันเอง "
    "ตอบให้ครบถ้วนตามความซับซ้อนของคำถาม — ถ้าคำถามง่ายตอบสั้น ถ้าซับซ้อนให้อธิบายละเอียด "
    "ใช้หัวข้อย่อย ข้อย่อย หรือตารางเมื่อเหมาะสมเพื่อให้อ่านง่าย "
    "ตอบเป็นภาษาไทยเป็นหลัก ถ้าผู้ใช้ถามภาษาอังกฤษให้ตอบภาษาอังกฤษได้"
)

COMPANY_FALLBACK = (
    "ขออภัยค่ะ/ครับ ข้อมูลนี้ยังไม่มีในระบบ "
    "กรุณาติดต่อบริษัทโดยตรงที่ +66(0)89-969-2859 หรืออีเมล sirivatanaonline@gmail.com "
    "เพื่อให้เจ้าหน้าที่ตรวจสอบข้อมูลล่าสุดให้"
)

SYSTEM_PROMPT_RAG = (
    f"{COMPANY_IDENTITY}\n\n"
    "ตอบคำถามผู้ใช้โดยอ้างอิงจากข้อมูลที่ให้ไว้ **และจากประวัติการสนทนาก่อนหน้า** (รวมถึงไฟล์ที่ผู้ใช้แนบในเทิร์นก่อน) "
    "ตอบให้ครบถ้วน อธิบายให้เข้าใจ ขยายความหรือยกตัวอย่างได้เมื่อเหมาะสม\n"
    "ถ้าผู้ใช้ถามคำถามต่อเนื่อง (เช่น 'เขาคือใคร', 'อันนั้นราคาเท่าไหร่', 'แล้วอันนี้ล่ะ') "
    "ให้ใช้บริบทจากการสนทนาก่อนหน้าเพื่อตอบ ห้ามตอบว่า 'ไม่มีข้อมูล' ถ้าคำตอบอยู่ใน history แล้ว\n"
    "ห้ามแต่งราคา ระยะเวลาผลิต หรือเงื่อนไขชำระเงินเอง\n"
    "ถ้าถูกถามราคา ให้ขอรายละเอียดงาน (ประเภทงาน ขนาด จำนวน วัสดุ สี เทคนิคพิเศษ งานหลังพิมพ์ กำหนดส่ง) "
    "แล้วแนะนำให้ส่งให้ฝ่ายขายประเมิน\n"
    "ถ้าถูกถามตำแหน่งงานว่าง ให้บอกว่าตำแหน่งงานเปลี่ยนแปลงได้ และแนะนำให้ติดต่อ HR\n"
    f"ถ้าข้อมูลที่ให้มาและประวัติสนทนาไม่ครอบคลุมคำถาม ให้ตอบตรง ๆ ว่า: {COMPANY_FALLBACK}"
)

SYSTEM_PROMPT_FREE = (
    f"{COMPANY_IDENTITY}\n\n"
    "ขณะนี้ไม่พบข้อมูลคำตอบในระบบ RAG สำหรับคำถามนี้ — "
    "ให้ตรวจสอบประวัติการสนทนาก่อนหน้าก่อน (รวมถึงไฟล์ที่ผู้ใช้แนบในเทิร์นก่อน)\n"
    "**ถ้าคำตอบอยู่ในประวัติแชทแล้ว ให้ใช้ตอบทันที** — เช่น ถ้าผู้ใช้เคยแนบไฟล์ที่มีชื่อพนักงาน แล้วถามต่อว่า 'เขาชื่ออะไร' "
    "ให้ตอบตามที่เคยตอบไปแล้ว ไม่ใช่ปฏิเสธ\n"
    "ถ้าเป็นคำถามทั่วไป (ความรู้ทั่วไป เทคโนโลยี การใช้ซอฟต์แวร์ คำถามภาษา หรือคำขอช่วยเขียน) "
    "ให้ตอบอย่างละเอียดครบถ้วน — อธิบายขั้นตอน ยกตัวอย่างประกอบ หรือแบ่งหัวข้อย่อยตามความเหมาะสม\n"
    "เฉพาะกรณีที่เป็นข้อมูลภายในของบริษัท (ราคา ระยะเวลาผลิต ตำแหน่งงานว่าง รายชื่อพนักงาน "
    "เงื่อนไขชำระเงิน หรือรายละเอียดเฉพาะของบริษัท) **ที่ไม่มีทั้งใน RAG และในประวัติแชท** "
    f"ให้ตอบว่า: {COMPANY_FALLBACK}\n"
    "ห้ามแต่งข้อมูลเฉพาะของบริษัทขึ้นเอง"
)

SYSTEM_PROMPT_COMPANY_FREE = (
    f"{COMPANY_IDENTITY}\n\n"
    "**ขณะนี้คุณอยู่ในโหมด 'ถามข้อมูลบริษัท' (Company-Only Mode)**\n"
    "คุณสามารถตอบได้เฉพาะคำถามที่เกี่ยวข้องกับบริษัทศิริวัฒนาอินเตอร์พริ้นท์เท่านั้น "
    "เช่น: ข้อมูลบริษัท, บริการ, สินค้า, ขั้นตอนการทำงาน, นโยบาย, HR, สวัสดิการ, "
    "การติดต่อ, เครื่องจักร, โรงงาน, สถานที่, เอกสารบริษัท ฯลฯ\n\n"
    "**ถ้าคำถามไม่เกี่ยวข้องกับบริษัท** "
    "(เช่น ความรู้ทั่วไป, ข่าวสาร, การเมือง, สุขภาพ, ความบันเทิง, ภาวะโลกร้อน, "
    "code/programming ที่ไม่เกี่ยวข้องกับการดำเนินงานของบริษัท, แนะนำที่เที่ยว, "
    "คณิตศาสตร์ทั่วไป ฯลฯ) ให้ตอบปฏิเสธอย่างสุภาพด้วยข้อความนี้ทุกครั้ง:\n\n"
    "'ขออภัยค่ะ ขณะนี้อยู่ในโหมด \"ถามข้อมูลบริษัท\" จึงตอบได้เฉพาะคำถามที่เกี่ยวกับ"
    "บริษัทศิริวัฒนาอินเตอร์พริ้นท์เท่านั้น หากต้องการถามคำถามทั่วไป "
    "กรุณาออกจากโหมดนี้แล้วเริ่มแชทใหม่ค่ะ'\n\n"
    "ถ้าเป็นคำถามเกี่ยวกับบริษัทแต่ไม่มีข้อมูลในระบบ ให้ตอบว่า: "
    f"{COMPANY_FALLBACK}\n"
    "ห้ามแต่งข้อมูลเฉพาะของบริษัทขึ้นเอง ห้ามตอบคำถามนอกขอบเขตของบริษัทไม่ว่ากรณีใด"
)


SYSTEM_PROMPT_FILES = (
    f"{COMPANY_IDENTITY}\n\n"
    "ผู้ใช้แนบไฟล์มา — วิเคราะห์เนื้อหาในไฟล์อย่างละเอียดเพื่อตอบคำถาม\n"
    "ไฟล์ที่รองรับ: รูปภาพ, PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), ไฟล์ข้อความ/โค้ด (.txt, .md, .csv, .json, .py, .js, ฯลฯ)\n"
    "- รูป design/แบบกล่อง: ดูองค์ประกอบ (สี, ขนาด, รูปทรง, โลโก้, เทคนิคพิเศษ) แล้วประเมินว่าผลิตได้ไหม\n"
    "- PDF/Word: สรุปประเด็นสำคัญและแนะนำการดำเนินงาน\n"
    "- Excel/CSV: วิเคราะห์ข้อมูลในตาราง สรุปตัวเลข แนวโน้ม หรือคำนวณตามที่ผู้ใช้ขอ\n"
    "- PowerPoint: สรุปเนื้อหาแต่ละ slide\n"
    "- ไฟล์โค้ด: อธิบาย/รีวิว/หา bug/แนะนำการปรับปรุง ตามที่ผู้ใช้ขอ\n"
    "ห้ามแต่งราคา ระยะเวลาผลิต หรือเงื่อนไขชำระเงิน — แนะนำให้ติดต่อฝ่ายขายเพื่อใบเสนอราคา\n"
    "ตอบเป็นภาษาไทย กระชับ ชัดเจน เป็นมืออาชีพ\n\n"
    "**สำคัญสำหรับงานแปล/อ่าน/ถอดความเอกสาร (เช่น คู่มือเครื่องจักร, สเปก, SOP):**\n"
    "- เมื่อผู้ใช้ขอให้ 'แปล', 'อ่าน', 'ถอดความ', 'transcribe' เนื้อหาในไฟล์ — ห้ามย่อ ห้ามสรุป ห้ามข้ามส่วนใด ๆ\n"
    "- ต้อง transcribe ทุกข้อความ ทุก section ทุกหัวข้อย่อย ทุกตาราง ตามลำดับที่ปรากฏในไฟล์\n"
    "- ตารางต้องคงโครงสร้าง column/row ตามต้นฉบับ (ใช้ Markdown table) — ห้าม summarize เป็น bullet\n"
    "- หมายเลข section/figure/table (เช่น 1-3, Fig 2-1, Table 4-2) ต้องคงไว้ตรงตามไฟล์\n"
    "- ศัพท์เทคนิค/รุ่น/หน่วย/ค่าตัวเลข ต้องคงต้นฉบับ (ใส่อังกฤษในวงเล็บถ้าแปล)\n"
    "- ห้ามแปลงหน่วย (mm คง mm, °F คง °F) เว้นแต่ผู้ใช้สั่งชัดเจน\n"
    "- คำเตือนความปลอดภัย (DANGER/WARNING/CAUTION/NOTICE) ต้อง transcribe ครบถ้วน ใช้คำเตือนระดับเดียวกัน\n"
    "- ถ้าเนื้อหายาวจน output ใกล้เต็ม ให้แจ้งท้ายคำตอบว่า 'เนื้อหายังไม่จบ ขอให้พิมพ์ \"ต่อ\" เพื่ออ่านส่วนถัดไป'"
)


CLASSIFY_PROMPT = (
    "จัดหมวดคำถามต่อไปนี้ ตอบเป็นคำเดียวเท่านั้น: \n"
    "- 'calc' ถ้าต้องการการคำนวณตัวเลข สูตร หรือ logic หลายขั้น (เช่น คิดต้นทุน, คำนวณภาษี, คิด OT, สมการ)\n"
    "- 'general' ถ้าเป็นคำถามทั่วไป สรุป สอบถามข้อมูล ขอคำแนะนำ\n"
    "ตอบกลับเฉพาะคำว่า calc หรือ general"
)


def classify_query(question: str) -> str:
    """Return 'calc' or 'general'. Falls back to 'general' on any failure."""
    try:
        res = _get_client().chat.completions.create(
            model=LLM_MODEL_CLASSIFIER,
            temperature=0,
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": question},
            ],
            **_token_kwargs(LLM_MODEL_CLASSIFIER, 5),
        )
        text = (res.choices[0].message.content or "").strip().lower()
        return "calc" if "calc" in text else "general"
    except Exception:
        return "general"


def answer_from_context(
    question: str,
    context_question: str,
    context_answer: str,
    model: str | None = None,
    history: list[dict] | None = None,
) -> str:
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT_RAG}]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": (
                f"ข้อมูลในระบบ:\nคำถาม: {context_question}\nคำตอบ: {context_answer}\n\n"
                f"คำถามจากผู้ใช้: {question}"
            ),
        }
    )
    chosen = model or LLM_MODEL
    res = _get_client().chat.completions.create(
        model=chosen,
        messages=messages,
        **_token_kwargs(chosen, 2048),
    )
    return (res.choices[0].message.content or "").strip()


def answer_freely(
    question: str,
    model: str | None = None,
    history: list[dict] | None = None,
    company_only: bool = False,
) -> str:
    """Answer without RAG context.

    When company_only=True, the system prompt forces the model to refuse any
    question that is not about the company. Used by the "ถามข้อมูลบริษัท" mode.
    """
    system_prompt = SYSTEM_PROMPT_COMPANY_FREE if company_only else SYSTEM_PROMPT_FREE
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    chosen = model or LLM_MODEL
    res = _get_client().chat.completions.create(
        model=chosen,
        messages=messages,
        **_token_kwargs(chosen, 2048),
    )
    return (res.choices[0].message.content or "").strip()


def answer_with_files(
    question: str,
    image_data_urls: list[str],
    pdf_texts: list[tuple[str, str]],
    history: list[dict] | None = None,
) -> str:
    """Answer when the user attached files. Uses vision-capable model (gpt-4.1-mini)."""
    content: list[dict] = []

    if pdf_texts:
        joined = "\n\n".join(
            f"=== เนื้อหาจากไฟล์ {name} ===\n{text}"
            for name, text in pdf_texts
        )
        content.append(
            {
                "type": "text",
                "text": f"{joined}\n\n=== คำถามจากผู้ใช้ ===\n{question}",
            }
        )
    else:
        content.append({"type": "text", "text": question})

    for url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT_FILES}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": content})

    res = _get_client().chat.completions.create(
        model=LLM_MODEL_FILES,
        messages=messages,
        **_token_kwargs(LLM_MODEL_FILES, 8000),
    )
    return (res.choices[0].message.content or "").strip()
