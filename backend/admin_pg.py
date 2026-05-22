from db_pg import get_pg_conn


def list_pending_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, question, ask_count, first_asked_at, last_asked_at
                FROM pending_questions
                WHERE status = 'pending'
                ORDER BY ask_count DESC, last_asked_at DESC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "question": row[1],
            "ask_count": row[2],
            "first_asked_at": row[3].isoformat() if row[3] else None,
            "last_asked_at": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


def ignore_pending_pg(pending_id: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_questions
                SET status = 'ignored'
                WHERE id = %s
                """,
                (pending_id,),
            )
            updated = cur.rowcount

            cur.execute(
                """
                DELETE FROM pending_vec
                WHERE pending_id = %s
                """,
                (pending_id,),
            )

    return updated > 0


def list_knowledge_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, question, answer, hit_count, approved_at, source
                FROM knowledge
                ORDER BY id DESC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "question": row[1],
            "answer": row[2],
            "hit_count": row[3],
            "approved_at": row[4].isoformat() if row[4] else None,
            "source": row[5],
        }
        for row in rows
    ]


def verify_knowledge_pg(kid: int, approved_by: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE knowledge
                SET source = 'admin',
                    approved_by = %s,
                    approved_at = now()
                WHERE id = %s
                """,
                (approved_by, kid),
            )
            updated = cur.rowcount

    return updated > 0


def delete_knowledge_pg(kid: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM knowledge
                WHERE id = %s
                """,
                (kid,),
            )
            exists = cur.fetchone()

            if not exists:
                return False

            cur.execute(
                """
                DELETE FROM knowledge_vec
                WHERE knowledge_id = %s
                """,
                (kid,),
            )

            cur.execute(
                """
                DELETE FROM knowledge
                WHERE id = %s
                """,
                (kid,),
            )

    return True
import csv
import io


def admin_chat_history_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.user_id,
                    u.username,
                    s.created_at,
                    s.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM chat_history h
                        WHERE h.session_id = s.id
                    ) AS message_count
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                ORDER BY s.updated_at DESC
                LIMIT 500
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "user_id": row[2],
            "username": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "updated_at": row[5].isoformat() if row[5] else None,
            "message_count": row[6],
        }
        for row in rows
    ]


def admin_session_messages_pg(session_id: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.user_id,
                    u.username,
                    s.created_at
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.id = %s
                """,
                (session_id,),
            )
            session = cur.fetchone()

            if not session:
                return None

            cur.execute(
                """
                SELECT id, question, answer, source, asked_at
                FROM chat_history
                WHERE session_id = %s
                ORDER BY id ASC
                """,
                (session_id,),
            )
            messages = cur.fetchall()

    return {
        "id": session[0],
        "title": session[1],
        "user_id": session[2],
        "username": session[3],
        "created_at": session[4].isoformat() if session[4] else None,
        "messages": [
            {
                "id": row[0],
                "question": row[1],
                "answer": row[2],
                "source": row[3],
                "asked_at": row[4].isoformat() if row[4] else None,
                "attachments": [],
            }
            for row in messages
        ],
    }


def admin_export_all_chat_history_pg() -> tuple[str, str]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.username,
                    s.title AS session_title,
                    h.asked_at,
                    h.question,
                    h.answer,
                    h.source
                FROM chat_history h
                JOIN chat_sessions s ON s.id = h.session_id
                JOIN users u ON u.id = h.user_id
                ORDER BY h.id ASC
                """
            )
            rows = cur.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["username", "session_title", "timestamp", "question", "answer", "source"])

    for row in rows:
        writer.writerow(
            [
                row[0],
                row[1],
                row[2].isoformat() if row[2] else "",
                row[3],
                row[4],
                row[5],
            ]
        )

    return "all-chat-history.csv", buf.getvalue()