from db_pg import get_pg_conn


def title_from_question_pg(question: str, max_len: int = 40) -> str:
    one_line = " ".join((question or "").split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 1] + "…"


def ensure_session_pg(
    user_id: int,
    session_id: int | None,
    first_question: str,
) -> tuple[int, str]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            if session_id is not None:
                cur.execute(
                    """
                    SELECT id, title
                    FROM chat_sessions
                    WHERE id = %s AND user_id = %s
                    """,
                    (session_id, user_id),
                )
                row = cur.fetchone()

                if row:
                    return row[0], row[1]

            title = title_from_question_pg(first_question)
            cur.execute(
                """
                INSERT INTO chat_sessions (user_id, title)
                VALUES (%s, %s)
                RETURNING id, title
                """,
                (user_id, title),
            )
            row = cur.fetchone()

    return row[0], row[1]


def get_session_history_pg(
    session_id: int | None,
    user_id: int,
    limit: int = 6,
) -> list[dict]:
    if not session_id:
        return []

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, question, answer
                FROM chat_history
                WHERE session_id = %s AND user_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (session_id, user_id, limit),
            )
            rows = cur.fetchall()

    history: list[dict] = []

    for row in reversed(rows):
        history.append({"role": "user", "content": row[1]})
        history.append({"role": "assistant", "content": row[2]})

    return history


def save_chat_message_pg(
    user_id: int,
    session_id: int,
    question: str,
    answer: str,
    source: str,
    knowledge_id: int | None,
) -> int:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_history
                    (user_id, session_id, question, answer, source, knowledge_id)
                VALUES
                    (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, session_id, question, answer, source, knowledge_id),
            )
            message_id = cur.fetchone()[0]

            cur.execute(
                """
                UPDATE chat_sessions
                SET updated_at = now()
                WHERE id = %s
                """,
                (session_id,),
            )

    return message_id


def list_sessions_pg(user_id: int) -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.created_at,
                    s.updated_at,
                    s.is_saved,
                    (
                        SELECT COUNT(*)
                        FROM chat_history h
                        WHERE h.session_id = s.id
                    ) AS message_count,
                    (
                        SELECT h.question
                        FROM chat_history h
                        WHERE h.session_id = s.id
                        ORDER BY h.id DESC
                        LIMIT 1
                    ) AS last_preview
                FROM chat_sessions s
                WHERE s.user_id = %s
                ORDER BY s.updated_at DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "created_at": row[2].isoformat() if row[2] else None,
            "updated_at": row[3].isoformat() if row[3] else None,
            "is_saved": row[4],
            "message_count": row[5],
            "last_preview": row[6],
        }
        for row in rows
    ]


def get_session_messages_pg(session_id: int, user_id: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, created_at
                FROM chat_sessions
                WHERE id = %s AND user_id = %s
                """,
                (session_id, user_id),
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
        "created_at": session[2].isoformat() if session[2] else None,
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