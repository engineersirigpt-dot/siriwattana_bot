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
                SELECT h.id, h.question, h.answer, h.source, h.asked_at,
                       a.id, a.filename, a.content_type, a.size_bytes
                FROM chat_history h
                LEFT JOIN attachments a ON a.message_id = h.id
                WHERE h.session_id = %s
                ORDER BY h.id ASC, a.id ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()

    messages: dict[int, dict] = {}
    for row in rows:
        msg_id = row[0]
        if msg_id not in messages:
            messages[msg_id] = {
                "id": msg_id,
                "question": row[1],
                "answer": row[2],
                "source": row[3],
                "asked_at": row[4].isoformat() if row[4] else None,
                "attachments": [],
            }
        if row[5] is not None:
            messages[msg_id]["attachments"].append({
                "id": row[5],
                "filename": row[6],
                "content_type": row[7],
                "size_bytes": row[8],
            })

    return {
        "id": session[0],
        "title": session[1],
        "user_id": session[2],
        "username": session[3],
        "created_at": session[4].isoformat() if session[4] else None,
        "messages": list(messages.values()),
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


def admin_delete_session_pg(session_id: int) -> dict | None:
    """
    Delete any chat session (admin override — bypasses user_id check).

    Returns:
        {"username": ..., "title": ..., "user_id": ..., "file_paths": [...]}
            on success — caller uses file_paths to unlink attachments from disk.
        None if the session doesn't exist.

    Mirrors delete_session_pg but without the WHERE user_id = ... guard so
    admins can clean up any user's chat.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id, s.title, s.user_id, u.username
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
                SELECT a.file_path
                FROM attachments a
                JOIN chat_history h ON h.id = a.message_id
                WHERE h.session_id = %s
                """,
                (session_id,),
            )
            file_paths = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                DELETE FROM attachments
                WHERE message_id IN (
                    SELECT id FROM chat_history WHERE session_id = %s
                )
                """,
                (session_id,),
            )
            cur.execute(
                "DELETE FROM chat_history WHERE session_id = %s",
                (session_id,),
            )
            cur.execute(
                "DELETE FROM chat_sessions WHERE id = %s",
                (session_id,),
            )

    return {
        "id": session[0],
        "title": session[1],
        "user_id": session[2],
        "username": session[3],
        "file_paths": file_paths,
    }


# ───────────────────────── User management (admin only) ─────────────────────


def admin_list_users_pg() -> list[dict]:
    """List all users with role, status, chat count, and last activity."""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.role,
                    COALESCE(u.is_disabled, false) AS is_disabled,
                    u.created_at,
                    (SELECT COUNT(*) FROM chat_sessions s WHERE s.user_id = u.id) AS chat_count,
                    (SELECT MAX(s.updated_at) FROM chat_sessions s WHERE s.user_id = u.id) AS last_active
                FROM users u
                ORDER BY u.id ASC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "username": row[1],
            "role": row[2],
            "is_disabled": bool(row[3]),
            "created_at": row[4].isoformat() if row[4] else None,
            "chat_count": row[5] or 0,
            "last_active": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]


def admin_get_user_pg(user_id: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, role, COALESCE(is_disabled, false)
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "role": row[2],
        "is_disabled": bool(row[3]),
    }


def admin_count_active_admins_pg(exclude_user_id: int | None = None) -> int:
    """Number of admins that aren't disabled (optionally excluding one)."""
    query = (
        "SELECT COUNT(*) FROM users "
        "WHERE role = 'admin' AND COALESCE(is_disabled, false) = false"
    )
    params: tuple = ()
    if exclude_user_id is not None:
        query += " AND id <> %s"
        params = (exclude_user_id,)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
    return int(row[0]) if row else 0


def admin_set_user_role_pg(user_id: int, role: str) -> bool:
    if role not in {"user", "admin"}:
        raise ValueError("role must be 'user' or 'admin'")
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = %s WHERE id = %s",
                (role, user_id),
            )
            updated = cur.rowcount
    return updated > 0


def admin_set_user_status_pg(user_id: int, is_disabled: bool) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_disabled = %s WHERE id = %s",
                (is_disabled, user_id),
            )
            updated = cur.rowcount
    return updated > 0


def admin_delete_user_chats_pg(user_id: int) -> dict:
    """
    Delete every chat session owned by `user_id`.

    Returns {"sessions_deleted": N, "file_paths": [...]} so the caller can
    unlink attachment files from disk.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM chat_sessions WHERE user_id = %s",
                (user_id,),
            )
            session_ids = [row[0] for row in cur.fetchall()]

            if not session_ids:
                return {"sessions_deleted": 0, "file_paths": []}

            cur.execute(
                """
                SELECT a.file_path
                FROM attachments a
                JOIN chat_history h ON h.id = a.message_id
                WHERE h.session_id = ANY(%s)
                """,
                (session_ids,),
            )
            file_paths = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                DELETE FROM attachments
                WHERE message_id IN (
                    SELECT id FROM chat_history WHERE session_id = ANY(%s)
                )
                """,
                (session_ids,),
            )
            cur.execute(
                "DELETE FROM chat_history WHERE session_id = ANY(%s)",
                (session_ids,),
            )
            cur.execute(
                "DELETE FROM chat_sessions WHERE id = ANY(%s)",
                (session_ids,),
            )

    return {"sessions_deleted": len(session_ids), "file_paths": file_paths}


def admin_analytics_pg() -> dict:
    """Aggregate usage stats for the admin dashboard (postgres).

    Read-only. Mirrors the sqlite branch in main._admin_analytics_sqlite —
    keep the two in sync when changing the shape.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_history")
            total_messages = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM chat_sessions")
            total_sessions = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM chat_history "
                "WHERE asked_at >= now() - interval '7 days'"
            )
            messages_7d = cur.fetchone()[0]

            cur.execute("SELECT vote, COUNT(*) FROM answer_feedback GROUP BY vote")
            votes = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                "SELECT source, COUNT(*) c FROM chat_history "
                "GROUP BY source ORDER BY c DESC"
            )
            source_breakdown = [
                {"source": r[0], "count": r[1]} for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT to_char(date_trunc('day', asked_at), 'YYYY-MM-DD') d,
                       COUNT(*) c
                FROM chat_history
                WHERE asked_at >= now() - interval '13 days'
                GROUP BY d ORDER BY d
                """
            )
            daily_volume = [{"day": r[0], "count": r[1]} for r in cur.fetchall()]

            cur.execute(
                "SELECT question, ask_count FROM pending_questions "
                "WHERE status = 'pending' "
                "ORDER BY ask_count DESC, last_asked_at DESC LIMIT 10"
            )
            top_unanswered = [
                {"question": r[0], "ask_count": r[1]} for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT h.question, af.reason, u.username, af.created_at
                FROM answer_feedback af
                JOIN chat_history h ON h.id = af.message_id
                JOIN users u ON u.id = af.user_id
                WHERE af.vote = 'down'
                ORDER BY af.created_at DESC LIMIT 10
                """
            )
            recent_downvotes = [
                {
                    "question": r[0],
                    "reason": r[1],
                    "username": r[2],
                    "created_at": r[3].isoformat() if r[3] else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT u.username, COUNT(*) c
                FROM chat_history h JOIN users u ON u.id = h.user_id
                GROUP BY u.username ORDER BY c DESC LIMIT 10
                """
            )
            top_users = [{"username": r[0], "count": r[1]} for r in cur.fetchall()]

    return {
        "totals": {
            "messages": total_messages,
            "sessions": total_sessions,
            "users": total_users,
            "messages_7d": messages_7d,
            "feedback_up": votes.get("up", 0),
            "feedback_down": votes.get("down", 0),
        },
        "source_breakdown": source_breakdown,
        "daily_volume": daily_volume,
        "top_unanswered": top_unanswered,
        "recent_downvotes": recent_downvotes,
        "top_users": top_users,
    }