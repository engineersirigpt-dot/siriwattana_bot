"""
Import Sirivatana company knowledge from sirivatana_kb.json into the RAG table.

The source JSON is a structured document (company profile, products, contacts,
production capabilities, FAQ, etc.). This script flattens it into Q&A pairs that
match the `knowledge` schema.

Run inside the container:
    docker exec siriwattan_chatbot-backend-1 python import_company_kb.py

Idempotent: rows whose `question` already exists are skipped.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from rag import add_knowledge

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").strip().lower()
USE_PG = DB_ENGINE in {"postgres", "postgresql", "pg"}

KB_FILE = Path(__file__).parent / "sirivatana_kb.json"


def fetch_admin():
    if USE_PG:
        from db_pg import connect_pg

        conn = connect_pg()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, username FROM users WHERE role = 'admin' "
                    "ORDER BY id LIMIT 1"
                )
                row = cur.fetchone()
                return {"id": row[0], "username": row[1]} if row else None
        finally:
            conn.close()

    from db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id, username FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def question_exists(question: str) -> bool:
    if USE_PG:
        from db_pg import connect_pg

        conn = connect_pg()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM knowledge WHERE question = %s", (question,)
                )
                return cur.fetchone() is not None
        finally:
            conn.close()

    from db import get_db

    conn = get_db()
    return (
        conn.execute(
            "SELECT id FROM knowledge WHERE question = ?", (question,)
        ).fetchone()
        is not None
    )


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


def generate_qa_pairs(data: dict) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for item in data.get("faq", []):
        pairs.append((item["question"], item["answer"]))

    cp = data.get("company_profile", {})
    if cp:
        if cp.get("legal_name_th"):
            pairs.append(
                (
                    "ชื่อเต็มของบริษัทคืออะไร",
                    f"{cp['legal_name_th']}"
                    + (f"\nภาษาอังกฤษ: {cp['legal_name_en']}" if cp.get("legal_name_en") else "")
                    + (f"\nชื่อเรียกอื่น: {', '.join(cp['aliases'])}" if cp.get("aliases") else ""),
                )
            )
        if cp.get("company_type"):
            pairs.append(("บริษัทเป็นบริษัทประเภทอะไร", cp["company_type"]))
        if cp.get("registration_number"):
            status = cp.get("status", "")
            pairs.append(
                (
                    "เลขทะเบียนนิติบุคคลของบริษัทคืออะไร",
                    f"เลขทะเบียน: {cp['registration_number']}\nสถานะ: {status}",
                )
            )
        if cp.get("registration_date"):
            pairs.append(("บริษัทจดทะเบียนเป็นบริษัทมหาชนเมื่อไหร่", cp["registration_date"]))
        if cp.get("registered_capital_thb"):
            pairs.append(
                (
                    "ทุนจดทะเบียนของบริษัทเท่าไหร่",
                    f"{cp['registered_capital_thb']:,} บาท",
                )
            )
        if cp.get("industry"):
            pairs.append(("บริษัทอยู่ในอุตสาหกรรมอะไร", cp["industry"]))
        if cp.get("website"):
            pairs.append(("เว็บไซต์ของบริษัทคืออะไร", cp["website"]))
        if cp.get("short_description"):
            pairs.append(("บริษัทประกอบกิจการอะไรบ้าง", cp["short_description"]))
        if cp.get("positioning"):
            pairs.append(("บริษัทมีจุดเด่นอย่างไร", _bullets(cp["positioning"])))
        if cp.get("history_note"):
            pairs.append(
                (
                    "บริษัทมีประสบการณ์กี่ปี ก่อตั้งมานานเท่าไหร่",
                    cp["history_note"],
                )
            )

    bo = data.get("business_overview", {})
    if bo.get("main_business"):
        pairs.append(("ธุรกิจหลักของบริษัทคืออะไร", _bullets(bo["main_business"])))
    if bo.get("target_customers"):
        pairs.append(("ลูกค้าหลักของบริษัทคือกลุ่มไหน", _bullets(bo["target_customers"])))
    if bo.get("service_model"):
        pairs.append(("รูปแบบการให้บริการของบริษัทเป็นยังไง", bo["service_model"]))

    for prod in data.get("products_and_services", []):
        name = prod.get("name", "").strip()
        desc = prod.get("description", "").strip()
        examples = prod.get("examples", [])
        if not name or not desc:
            continue
        answer = desc
        if examples:
            answer += "\n\nตัวอย่างงาน:\n" + _bullets(examples)
        pairs.append((f"บริการ {name} คืออะไร", answer))

    pc = data.get("production_capabilities", {})
    if pc.get("printing_systems"):
        pairs.append(("บริษัทมีระบบการพิมพ์อะไรบ้าง", _bullets(pc["printing_systems"])))
    if pc.get("technologies"):
        pairs.append(("บริษัทใช้เทคโนโลยีหรือซอฟต์แวร์อะไรในการพิมพ์", _bullets(pc["technologies"])))
    if pc.get("machine_capacity_notes"):
        pairs.append(
            (
                "บริษัทมีเครื่องจักรการพิมพ์กี่เครื่อง รองรับงานอะไรบ้าง",
                _bullets(pc["machine_capacity_notes"]),
            )
        )

    sc = data.get("standards_and_certifications", {})
    if sc.get("certifications"):
        guide = sc.get("answer_guideline", "")
        answer = _bullets(sc["certifications"])
        if guide:
            answer += f"\n\nหมายเหตุ: {guide}"
        pairs.append(("บริษัทมีมาตรฐานและใบรับรองอะไรบ้าง", answer))

    for loc in data.get("locations_and_contacts", []):
        name = loc.get("name", "").strip()
        parts = []
        if loc.get("address_th"):
            parts.append(f"ที่อยู่: {loc['address_th']}")
        if loc.get("tel"):
            parts.append(f"โทรศัพท์: {loc['tel']}")
        if loc.get("fax"):
            parts.append(f"แฟกซ์: {loc['fax']}")
        if loc.get("email"):
            parts.append(f"อีเมล: {loc['email']}")
        if name and parts:
            pairs.append((f"ติดต่อ{name}ได้อย่างไร", "\n".join(parts)))

    for chunk in data.get("rag_chunks", []):
        tags = chunk.get("tags", [])
        content = chunk.get("content", "").strip()
        if not content:
            continue
        topic = " / ".join(tags) if tags else chunk.get("id", "ข้อมูลบริษัท")
        pairs.append((f"ข้อมูลเกี่ยวกับ {topic}", content))

    refs = data.get("source_references", [])
    if refs:
        lines = [f"- {r.get('title', '')}: {r.get('url', '')}" for r in refs if r.get("url")]
        if lines:
            pairs.append(("แหล่งข้อมูลอ้างอิงของบริษัทมีที่ไหนบ้าง", "\n".join(lines)))

    return pairs


def main() -> None:
    admin = fetch_admin()
    if not admin:
        print("ERROR: ยังไม่มี admin user")
        print("วิธีสร้าง admin:")
        print("  1. Register บัญชีปกติผ่านหน้า /login")
        print("  2. Promote เป็น admin ผ่าน DB:")
        if USE_PG:
            print(
                "     docker exec siriwattana-postgres-test psql -U chatbot "
                "-d chatbot_test -c \"UPDATE users SET role='admin' "
                "WHERE username='<ชื่อ>';\""
            )
        else:
            print(
                "     sqlite3 backend/data/chatbot.db "
                "\"UPDATE users SET role='admin' WHERE username='<ชื่อ>';\""
            )
        sys.exit(1)

    if not KB_FILE.exists():
        print(f"ERROR: ไม่พบไฟล์ {KB_FILE}")
        sys.exit(1)

    data = json.loads(KB_FILE.read_text(encoding="utf-8"))
    pairs = generate_qa_pairs(data)

    print(f"DB_ENGINE: {DB_ENGINE}")
    print(f"แปลงเป็น {len(pairs)} คู่ Q&A จาก {KB_FILE.name}")
    print(f"Import เป็น admin: {admin['username']} (id={admin['id']})")
    print()

    added = 0
    skipped = 0
    for i, (q, a) in enumerate(pairs, 1):
        q, a = q.strip(), a.strip()
        if question_exists(q):
            print(f"  [{i:>2}/{len(pairs)}] SKIP: {q[:60]}")
            skipped += 1
            continue
        kid = add_knowledge(q, a, admin["id"], source="admin")
        print(f"  [{i:>2}/{len(pairs)}] ADD  id={kid}: {q[:60]}")
        added += 1

    print()
    print(f"เสร็จสิ้น — เพิ่มใหม่ {added} รายการ, ข้าม {skipped} รายการ")


if __name__ == "__main__":
    main()
