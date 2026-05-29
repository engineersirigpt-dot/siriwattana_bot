"""
Add metadata columns to the knowledge table for KB document tracking.

Idempotent: safe to run multiple times. Detects existing columns and only
adds the missing ones. Supports both SQLite (DB_PATH) and Postgres
(DATABASE_URL) — the engine is selected by DB_ENGINE in backend/.env.

Columns added to `knowledge`:
    source_file       VARCHAR(255)   filename the chunk came from
    source_dept       VARCHAR(50)    department label (HR, Production, ...)
    document_type     VARCHAR(50)    SOP, Policy, Manual, FAQ, Catalog, ...
    confidentiality   VARCHAR(20)    public | internal | confidential
                                     (NULL is treated as 'internal' at query time)
    allowed_groups    TEXT           comma-separated group names (RBAC, future)
    last_updated      DATE           when the source doc was last updated
    note              TEXT           freeform notes

Run inside the backend container:
    docker exec siriwattana-backend python migrate_kb_metadata.py
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").strip().lower()
USE_PG = DB_ENGINE in {"postgres", "postgresql", "pg"}


NEW_COLUMNS: list[tuple[str, str]] = [
    ("source_file", "VARCHAR(255)"),
    ("source_dept", "VARCHAR(50)"),
    ("document_type", "VARCHAR(50)"),
    ("confidentiality", "VARCHAR(20)"),
    ("allowed_groups", "TEXT"),
    ("last_updated", "DATE"),
    ("note", "TEXT"),
]


def existing_columns_pg() -> set[str]:
    from db_pg import connect_pg

    conn = connect_pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'knowledge'
                """
            )
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def existing_columns_sqlite() -> set[str]:
    from db import get_db

    conn = get_db()
    rows = conn.execute("PRAGMA table_info(knowledge)").fetchall()
    return {row["name"] for row in rows}


def add_column_pg(name: str, sql_type: str) -> None:
    from db_pg import connect_pg

    conn = connect_pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"ALTER TABLE knowledge ADD COLUMN IF NOT EXISTS {name} {sql_type}"
            )
        conn.commit()
    finally:
        conn.close()


def add_column_sqlite(name: str, sql_type: str) -> None:
    from db import get_db

    conn = get_db()
    conn.execute(f"ALTER TABLE knowledge ADD COLUMN {name} {sql_type}")
    conn.commit()


def main() -> None:
    print(f"DB_ENGINE: {DB_ENGINE}")

    existing = existing_columns_pg() if USE_PG else existing_columns_sqlite()
    print(f"Existing knowledge columns: {sorted(existing)}")
    print()

    added = 0
    skipped = 0
    errors = 0

    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            print(f"  SKIP {col_name:<18} (already exists)")
            skipped += 1
            continue
        try:
            if USE_PG:
                add_column_pg(col_name, col_type)
            else:
                add_column_sqlite(col_name, col_type)
            print(f"  ADD  {col_name:<18} ({col_type})")
            added += 1
        except Exception as exc:
            print(f"  ERR  {col_name:<18} {exc!r}")
            errors += 1

    print()
    print(f"=== Summary: added={added}, skipped={skipped}, errors={errors} ===")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
