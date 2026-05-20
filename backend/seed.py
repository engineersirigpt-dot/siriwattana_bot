"""
Seed the knowledge base from seed_knowledge.json.

Run inside the running container:
    docker exec siriwattan_chatbot-backend-1 python seed.py

Or locally (with .env set up):
    python seed.py

Idempotent: rows whose `question` already exists are skipped.
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from db import get_db
from rag import add_knowledge

SEED_FILE = Path(__file__).parent / "seed_knowledge.json"


def main() -> None:
    conn = get_db()
    admin = conn.execute(
        "SELECT id, username FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"
    ).fetchone()
    if not admin:
        print("ERROR: ยังไม่มี admin user")
        print("กรุณาไปสมัครบัญชีแรกที่ http://localhost:3002 ก่อน (คนแรก = admin อัตโนมัติ)")
        sys.exit(1)

    if not SEED_FILE.exists():
        print(f"ERROR: ไม่พบไฟล์ {SEED_FILE}")
        sys.exit(1)

    entries = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    print(f"โหลด {len(entries)} รายการจาก {SEED_FILE.name}")
    print(f"Seed เป็น admin: {admin['username']} (id={admin['id']})")
    print()

    added = 0
    skipped = 0
    for i, entry in enumerate(entries, 1):
        q = entry["question"].strip()
        a = entry["answer"].strip()

        existing = conn.execute(
            "SELECT id FROM knowledge WHERE question = ?", (q,)
        ).fetchone()
        if existing:
            print(f"  [{i:>2}/{len(entries)}] SKIP (มีอยู่แล้ว): {q[:60]}")
            skipped += 1
            continue

        kid = add_knowledge(q, a, admin["id"])
        print(f"  [{i:>2}/{len(entries)}] ADD  id={kid}: {q[:60]}")
        added += 1

    print()
    print(f"เสร็จสิ้น — เพิ่มใหม่ {added} รายการ, ข้าม {skipped} รายการ (มีอยู่แล้ว)")


if __name__ == "__main__":
    main()
