import os
import sqlite3
import struct
from pathlib import Path

import sqlite_vec

DB_PATH = os.getenv("DB_PATH", "./data/chatbot.db")


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute("PRAGMA journal_mode=WAL")    # readers don't block writers
    conn.execute("PRAGMA synchronous=NORMAL")  # safe but faster than FULL
    conn.execute("PRAGMA cache_size=-32000")   # 32MB page cache in RAM
    conn.execute("PRAGMA busy_timeout=5000")   # wait up to 5s instead of failing instantly

    return conn


_conn: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        init_schema(_conn)
    return _conn


def serialize_vector(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            approved_by INTEGER REFERENCES users(id),
            approved_at TEXT NOT NULL DEFAULT (datetime('now')),
            hit_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'admin'
        );

        CREATE TABLE IF NOT EXISTS pending_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            cluster_id INTEGER,
            ask_count INTEGER NOT NULL DEFAULT 1,
            first_asked_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_asked_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            source TEXT NOT NULL,
            knowledge_id INTEGER REFERENCES knowledge(id),
            asked_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER REFERENCES chat_history(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_attachments_message
            ON attachments(message_id);

        CREATE INDEX IF NOT EXISTS idx_attachments_user
            ON attachments(user_id);

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL DEFAULT 'แชทใหม่',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
            ON chat_sessions(user_id, updated_at DESC);

        CREATE TABLE IF NOT EXISTS embedding_cache (
            text_hash TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            embedding TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used_at TEXT,
            hit_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_embedding_cache_model_dimensions
            ON embedding_cache(model, dimensions);

        CREATE INDEX IF NOT EXISTS idx_embedding_cache_created_at
            ON embedding_cache(created_at);
        """
    )

    # Migration: ensure `session_id` column exists on legacy chat_history rows.
    try:
        cur.execute(
            "ALTER TABLE chat_history "
            "ADD COLUMN session_id INTEGER REFERENCES chat_sessions(id)"
        )
    except sqlite3.OperationalError:
        pass

    # Migration: track which sessions the user has explicitly saved.
    # Unsaved sessions get auto-purged after 24h.
    try:
        cur.execute(
            "ALTER TABLE chat_sessions "
            "ADD COLUMN is_saved INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # Migration: remember whether the session was last used in 'normal' or
    # 'company' mode, so the UI can restore the right toggle on revisit.
    try:
        cur.execute(
            "ALTER TABLE chat_sessions "
            "ADD COLUMN mode TEXT NOT NULL DEFAULT 'normal'"
        )
    except sqlite3.OperationalError:
        pass

    # Migration: admin can disable a user to revoke chatbot access without
    # losing their chat history. Login checks this flag before issuing a JWT.
    try:
        cur.execute(
            "ALTER TABLE users "
            "ADD COLUMN is_disabled INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # Migration: shared_token lets the session owner mint a read-only link
    # other signed-in users can open + fork into their own chat.
    try:
        cur.execute(
            "ALTER TABLE chat_sessions ADD COLUMN shared_token TEXT"
        )
    except sqlite3.OperationalError:
        pass
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_sessions_shared_token "
        "ON chat_sessions(shared_token) WHERE shared_token IS NOT NULL"
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_history_session "
        "ON chat_history(session_id, asked_at)"
    )

    # Backfill: any chat_history rows without a session_id get grouped into one
    # "ประวัติเก่า" session per user so they show up in the new sidebar UI.
    orphan_users = [
        r[0]
        for r in cur.execute(
            "SELECT DISTINCT user_id FROM chat_history WHERE session_id IS NULL"
        ).fetchall()
    ]

    for uid in orphan_users:
        cur.execute(
            "INSERT INTO chat_sessions (user_id, title) VALUES (?, ?)",
            (uid, "ประวัติเก่า"),
        )
        new_sid = cur.lastrowid
        cur.execute(
            "UPDATE chat_history "
            "SET session_id = ? "
            "WHERE user_id = ? AND session_id IS NULL",
            (new_sid, uid),
        )

    # Migration: ensure `source` column exists on legacy DBs created before this column was added.
    try:
        cur.execute(
            "ALTER TABLE knowledge "
            "ADD COLUMN source TEXT NOT NULL DEFAULT 'admin'"
        )
    except sqlite3.OperationalError:
        pass

    # Migration: cache extracted text from PDF attachments so follow-up turns in the same
    # session can see the file content without re-parsing.
    try:
        cur.execute("ALTER TABLE attachments ADD COLUMN extracted_text TEXT")
    except sqlite3.OperationalError:
        pass

    # Migration: ensure embedding_cache columns exist if table was created by an older patch.
    try:
        cur.execute("ALTER TABLE embedding_cache ADD COLUMN last_used_at TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cur.execute(
            "ALTER TABLE embedding_cache "
            "ADD COLUMN hit_count INTEGER NOT NULL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    # Backfill: extract text for legacy PDF attachments whose text was never cached.
    legacy_pdfs = cur.execute(
        "SELECT id, file_path FROM attachments "
        "WHERE content_type = 'application/pdf' AND extracted_text IS NULL"
    ).fetchall()

    if legacy_pdfs:
        try:
            import attachments as _att  # local import to avoid circular at module top

            for r in legacy_pdfs:
                if not Path(r["file_path"]).exists():
                    continue

                text = _att.extract_pdf_text(r["file_path"])
                cur.execute(
                    "UPDATE attachments SET extracted_text = ? WHERE id = ?",
                    (text if text.strip() else None, r["id"]),
                )
        except Exception:
            pass

    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_vec USING vec0(
            knowledge_id INTEGER PRIMARY KEY,
            embedding FLOAT[1024]
        );
        """
    )

    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS pending_vec USING vec0(
            pending_id INTEGER PRIMARY KEY,
            embedding FLOAT[1024]
        );
        """
    )

    conn.commit()