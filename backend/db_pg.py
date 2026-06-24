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

            # Backfill `shared_token` — opaque, URL-safe per-session token used
            # to share a chat read-only with teammates. NULL = not shared.
            cur.execute(
                """
                ALTER TABLE chat_sessions
                ADD COLUMN IF NOT EXISTS shared_token VARCHAR(64) UNIQUE;
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
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_shared_token
                ON chat_sessions(shared_token) WHERE shared_token IS NOT NULL;
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
                    asked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    is_forked BOOLEAN NOT NULL DEFAULT false
                );
                """
            )

            # Backfill is_forked for tables created before the share/fork feature.
            cur.execute(
                """
                ALTER TABLE chat_history
                ADD COLUMN IF NOT EXISTS is_forked BOOLEAN NOT NULL DEFAULT false;
                """
            )

            # Per-message token + cost tracking (2026-06-18). Lets the Dashboard
            # compute real ฿ cost from openai usage instead of the flat-rate
            # estimate. Legacy rows stay NULL — admin_pg falls back to flat rate
            # for those so totals don't drop.
            cur.execute(
                """
                ALTER TABLE chat_history
                ADD COLUMN IF NOT EXISTS prompt_tokens INT,
                ADD COLUMN IF NOT EXISTS completion_tokens INT,
                ADD COLUMN IF NOT EXISTS model_used TEXT,
                ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10, 6);
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

            # Answer feedback (👍/👎 + optional reason). One vote per
            # (message, user); re-voting overwrites via ON CONFLICT upsert.
            # A 👎 also seeds a pending question for admins.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS answer_feedback (
                    id BIGSERIAL PRIMARY KEY,
                    message_id BIGINT NOT NULL
                        REFERENCES chat_history(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    vote TEXT NOT NULL CHECK (vote IN ('up', 'down')),
                    reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (message_id, user_id)
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_answer_feedback_vote
                ON answer_feedback(vote, created_at);
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

            # Document-translation jobs (Phase 3) — persisted so the history
            # survives backend restarts and users can re-download past results.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS translation_jobs (
                    id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id),
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    total_pages INTEGER NOT NULL DEFAULT 0,
                    translated_pages INTEGER NOT NULL DEFAULT 0,
                    done INTEGER NOT NULL DEFAULT 0,
                    exceeds_cap BOOLEAN NOT NULL DEFAULT false,
                    max_pages INTEGER NOT NULL DEFAULT 150,
                    docx_path TEXT,
                    pdf_path TEXT,
                    review_path TEXT,
                    review_flagged INTEGER NOT NULL DEFAULT 0,
                    cost_usd NUMERIC(10, 4) NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_translation_jobs_user
                ON translation_jobs(user_id, created_at DESC);
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