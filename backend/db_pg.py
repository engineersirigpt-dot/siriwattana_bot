import os
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector


DATABASE_URL = os.getenv("DATABASE_URL")


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Example: postgresql://user:password@localhost:5433/chatbot_test"
        )
    return DATABASE_URL


def connect_pg() -> psycopg.Connection:
    conn = psycopg.connect(require_database_url(), autocommit=False)
    register_vector(conn)
    return conn


@contextmanager
def get_pg_conn():
    conn = connect_pg()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_pg_schema() -> None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    is_disabled BOOLEAN NOT NULL DEFAULT false
                );
                """
            )

            # Backfill is_disabled for DBs created before this column existed.
            cur.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS is_disabled BOOLEAN NOT NULL DEFAULT false;
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge (
                    id BIGSERIAL PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    approved_by BIGINT REFERENCES users(id),
                    approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'admin'
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_vec (
                    knowledge_id BIGINT PRIMARY KEY REFERENCES knowledge(id) ON DELETE CASCADE,
                    embedding vector(1024) NOT NULL
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_questions (
                    id BIGSERIAL PRIMARY KEY,
                    question TEXT NOT NULL,
                    cluster_id BIGINT,
                    ask_count INTEGER NOT NULL DEFAULT 1,
                    first_asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    status TEXT NOT NULL DEFAULT 'pending'
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_vec (
                    pending_id BIGINT PRIMARY KEY REFERENCES pending_questions(id) ON DELETE CASCADE,
                    embedding vector(1024) NOT NULL
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    title TEXT NOT NULL DEFAULT 'แชทใหม่',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    is_saved BOOLEAN NOT NULL DEFAULT false,
                    mode VARCHAR(20) NOT NULL DEFAULT 'normal'
                );
                """
            )

            # Backfill `mode` for DBs created before this column existed.
            cur.execute(
                """
                ALTER TABLE chat_sessions
                ADD COLUMN IF NOT EXISTS mode VARCHAR(20) NOT NULL DEFAULT 'normal';
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
                ON chat_sessions(user_id, updated_at DESC);
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    session_id BIGINT REFERENCES chat_sessions(id),
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    source TEXT NOT NULL,
                    knowledge_id BIGINT REFERENCES knowledge(id),
                    asked_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_history_session
                ON chat_history(session_id, asked_at);
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS attachments (
                    id BIGSERIAL PRIMARY KEY,
                    message_id BIGINT REFERENCES chat_history(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    file_path TEXT NOT NULL,
                    extracted_text TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachments_message
                ON attachments(message_id);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachments_user
                ON attachments(user_id);
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    text_hash TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    last_used_at TIMESTAMPTZ,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_embedding_cache_model_dimensions
                ON embedding_cache(model, dimensions);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_embedding_cache_created_at
                ON embedding_cache(created_at);
                """
            )


def smoke_test_pg() -> None:
    init_pg_schema()

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            vector_row = cur.fetchone()

            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                ORDER BY table_name;
                """
            )
            tables = [row[0] for row in cur.fetchall()]

    print({"pgvector": vector_row[0] if vector_row else None, "tables": tables})


if __name__ == "__main__":
    smoke_test_pg()