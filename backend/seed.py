"""
Seed the knowledge base from seed_knowledge.json.

Supports both SQLite (default) and Postgres (set DB_ENGINE=postgres in .env).

Run inside the running container:
    docker exec siriwattana-backend python seed.py

Or locally (with .env set up):
    python seed.py

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

SEED_FILE = Path(__file__).parent / "seed_knowledge.json"


def fetch_admin():
    """Return {'id': int, 'username': str} of the first admin, or None."""
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

    if not SEED_FILE.exists():
        print(f"ERROR: ไม่พบไฟล์ {SEED_FILE}")
        sys.exit(1)

    entries = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    print(f"DB_ENGINE: {DB_ENGINE}")
    print(f"โหลด {len(entries)} รายการจาก {SEED_FILE.name}")
    print(f"Seed เป็น admin: {admin['username']} (id={admin['id']})")
    print()

    added = 0
    skipped = 0
    for i, entry in enumerate(entries, 1):
        q = entry["question"].strip()
        a = entry["answer"].strip()

        if question_exists(q):
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
