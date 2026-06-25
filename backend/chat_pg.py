import secrets

from db_pg import get_pg_conn


def title_from_question_pg(question: str, max_len: int = 40) -> str:
    one_line = " ".join((question or "").split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 1] + "…"


def ensure_session_pg(
    user_id: int,
    session_id: int | None,
    first_question: str,
    mode: str = "normal",
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
                INSERT INTO chat_sessions (user_id, title, mode)
                VALUES (%s, %s, %s)
                RETURNING id, title
                """,
                (user_id, title, mode),
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
    mode: str = "normal",
    usage: dict | None = None,
) -> int:
    """Insert a chat_history row, optionally with token-cost data.

    `usage` is the dict from `llm.accumulate_usage`:
        {"model_used", "prompt_tokens", "completion_tokens", "cost_usd"}

    When usage is None (export_offer, blocked, brain-only with no LLM call,
    legacy callers) the new token/cost columns stay NULL — the dashboard
    falls back to the flat-rate estimate for NULL rows.
    """
    u = usage or {}
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_history
                    (user_id, session_id, question, answer, source, knowledge_id,
                     prompt_tokens, completion_tokens, model_used, cost_usd)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id, session_id, question, answer, source, knowledge_id,
                    u.get("prompt_tokens"),
                    u.get("completion_tokens"),
                    u.get("model_used"),
                    u.get("cost_usd"),
                ),
            )
            message_id = cur.fetchone()[0]

            # Sync session-level state on every message: timestamp + the mode the
            # user is currently in. The UI uses `mode` to restore the toggle when
            # the session is reopened.
            cur.execute(
                """
                UPDATE chat_sessions
                SET updated_at = now(),
                    mode = %s
                WHERE id = %s
                """,
                (mode, session_id),
            )

    return message_id


def purge_unsaved_sessions_pg(user_id: int, keep: int) -> tuple[int, list[str]]:
    """
    Delete unsaved chat_sessions for `user_id` beyond the `keep` most-recent.
    Returns (sessions_deleted, file_paths) so the caller can unlink files from disk.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM chat_sessions
                WHERE user_id = %s AND is_saved = false
                ORDER BY updated_at DESC
                OFFSET %s
                """,
                (user_id, keep),
            )
            expired_ids = [row[0] for row in cur.fetchall()]

            if not expired_ids:
                return 0, []

            cur.execute(
                """
                SELECT a.file_path
                FROM attachments a
                JOIN chat_history h ON h.id = a.message_id
                WHERE h.session_id = ANY(%s)
                """,
                (expired_ids,),
            )
            file_paths = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                DELETE FROM attachments
                WHERE message_id IN (
                    SELECT id FROM chat_history WHERE session_id = ANY(%s)
                )
                """,
                (expired_ids,),
            )
            cur.execute(
                "DELETE FROM chat_history WHERE session_id = ANY(%s)",
                (expired_ids,),
            )
            cur.execute(
                "DELETE FROM chat_sessions WHERE id = ANY(%s)",
                (expired_ids,),
            )

    return len(expired_ids), file_paths


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
                SELECT id, title, created_at, COALESCE(mode, 'normal') AS mode,
                       shared_token
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
                SELECT h.id, h.question, h.answer, h.source, h.asked_at,
                       a.id, a.filename, a.content_type, a.size_bytes,
                       h.knowledge_id, k.source_file,
                       COALESCE(h.is_forked, false) AS is_forked,
                       af.vote
                FROM chat_history h
                LEFT JOIN attachments a ON a.message_id = h.id
                LEFT JOIN knowledge k ON k.id = h.knowledge_id
                LEFT JOIN answer_feedback af
                       ON af.message_id = h.id AND af.user_id = %s
                WHERE h.session_id = %s
                ORDER BY h.id ASC, a.id ASC
                """,
                (user_id, session_id),
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
                # Carry the source doc info so the chat UI can re-render the
                # "📎 ดาวน์โหลดเอกสารต้นฉบับ" button when an old session is reopened.
                "source_knowledge_id": row[9],
                "source_file": row[10],
                "is_forked": bool(row[11]),
                "my_vote": row[12],  # 'up' | 'down' | None
            }
        if row[5] is not None:
            messages[msg_id]["attachments"].append({
                "id": row[5],
                "filename": row[6],
                "content_type": row[7],
                "size_bytes": row[8],
            })

    # turn_count for the per-session quota UI. Excludes:
    #   - export_offer rows (user's "ขอ PDF" intent, not a real Q)
    #   - is_forked rows    (cloned from a shared chat, didn't cost this user)
    # Must match the WHERE clause in main._count_session_turns.
    turn_count = sum(
        1
        for m in messages.values()
        if m.get("source") != "export_offer" and not m.get("is_forked")
    )

    return {
        "id": session[0],
        "title": session[1],
        "created_at": session[2].isoformat() if session[2] else None,
        "mode": session[3],
        "shared_token": session[4],
        "messages": list(messages.values()),
        "turn_count": turn_count,
    }
def rename_session_pg(session_id: int, user_id: int, title: str) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chat_sessions
                SET title = %s,
                    updated_at = now()
                WHERE id = %s AND user_id = %s
                """,
                (title, session_id, user_id),
            )
            updated = cur.rowcount

    return updated > 0


def toggle_save_session_pg(session_id: int, user_id: int, is_saved: bool) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE chat_sessions
                SET is_saved = %s,
                    updated_at = now()
                WHERE id = %s AND user_id = %s
                """,
                (is_saved, session_id, user_id),
            )
            updated = cur.rowcount

    return updated > 0


def delete_session_pg(session_id: int, user_id: int) -> list[str] | None:
    """
    Delete a chat session and return file paths of attachments so the caller can
    unlink them from disk. Returns None if the session does not belong to the user.
    Returns an empty list when there are no attachments.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM chat_sessions
                WHERE id = %s AND user_id = %s
                """,
                (session_id, user_id),
            )
            if not cur.fetchone():
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
                """
                DELETE FROM chat_history
                WHERE session_id = %s
                """,
                (session_id,),
            )
            cur.execute(
                """
                DELETE FROM chat_sessions
                WHERE id = %s AND user_id = %s
                """,
                (session_id, user_id),
            )

    return file_paths


# ─────────────────────── Sharing (read-only + fork) ─────────────────────────


def list_shared_with_me_pg(user_id: int) -> list[dict]:
    """Sessions shared TO this user (they are an explicit recipient), newest first.

    Powers the "แชร์ร่วมกันในทีม" panel — read-only + forkable. Targeted: a chat
    shows up only for the users its owner chose; everyone else (and the owner's
    own panel) never sees it. Private chats never appear.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id, s.title, s.user_id, u.username,
                       s.shared_token, s.updated_at,
                       (SELECT COUNT(*) FROM chat_history
                        WHERE session_id = s.id) AS message_count
                FROM chat_session_recipients r
                JOIN chat_sessions s ON s.id = r.session_id
                JOIN users u ON u.id = s.user_id
                WHERE r.recipient_user_id = %s AND s.shared_token IS NOT NULL
                ORDER BY s.updated_at DESC
                LIMIT 500
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "title": r[1],
            "user_id": r[2],
            "username": r[3],
            "shared_token": r[4],
            "updated_at": r[5].isoformat() if r[5] else None,
            "message_count": int(r[6] or 0),
        }
        for r in rows
    ]


def share_session_pg(session_id: int, user_id: int, recipient_ids: list[int]) -> str | None:
    """Share a session the user owns. Returns a stable token, or None if not owner.

    Two access models, decided by whether recipients are set:
      * recipient_ids empty  → LINK-ONLY: anyone signed in who has the link opens
        it (what regular users get).
      * recipient_ids given  → TARGETED: only those users (+owner) can open it and
        it shows in their team panel (what admins can do).

    Always sets/keeps a token (never unshares — unshare is revoke_share_pg).
    The owner is never added as a recipient (they own it already).
    """
    recipient_ids = sorted({int(r) for r in (recipient_ids or []) if int(r) != user_id})
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, shared_token FROM chat_sessions "
                "WHERE id = %s AND user_id = %s",
                (session_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            token = row[1] or secrets.token_urlsafe(16)
            if not row[1]:
                cur.execute(
                    "UPDATE chat_sessions SET shared_token = %s WHERE id = %s",
                    (token, session_id),
                )
            # Replace the recipient set (empty = link-only share).
            cur.execute(
                "DELETE FROM chat_session_recipients WHERE session_id = %s",
                (session_id,),
            )
            for rid in recipient_ids:
                cur.execute(
                    "INSERT INTO chat_session_recipients (session_id, recipient_user_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (session_id, rid),
                )
    return token


def list_share_recipients_pg(session_id: int, owner_id: int) -> list[int]:
    """recipient user_ids ที่แชทนี้ถูกแชร์ให้ (เฉพาะเจ้าของถึงเรียกดูได้)"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM chat_sessions WHERE id = %s AND user_id = %s",
                (session_id, owner_id),
            )
            if not cur.fetchone():
                return []
            cur.execute(
                "SELECT recipient_user_id FROM chat_session_recipients WHERE session_id = %s",
                (session_id,),
            )
            return [r[0] for r in cur.fetchall()]


def list_shareable_users_pg(exclude_id: int) -> list[dict]:
    """รายชื่อผู้ใช้สำหรับเลือกเป็นผู้รับ (ไม่รวมตัวเอง / ผู้ใช้ที่ถูกปิด)"""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username FROM users "
                "WHERE is_disabled = false AND id <> %s ORDER BY username",
                (exclude_id,),
            )
            return [{"id": r[0], "username": r[1]} for r in cur.fetchall()]


def revoke_share_pg(session_id: int, user_id: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chat_sessions SET shared_token = NULL "
                "WHERE id = %s AND user_id = %s AND shared_token IS NOT NULL",
                (session_id, user_id),
            )
            revoked = cur.rowcount > 0
            if revoked:
                cur.execute(
                    "DELETE FROM chat_session_recipients WHERE session_id = %s",
                    (session_id,),
                )
            return revoked


def get_shared_session_pg(token: str, viewer_id: int) -> dict | None:
    """Look up a shared session by token — only the owner or an explicit
    recipient may read it (targeted sharing).

    Returns owner info + the full message thread. Returns None if the token is
    unknown/revoked, or the viewer isn't allowed to see it.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at,
                       COALESCE(s.mode, 'normal') AS mode,
                       s.user_id, u.username
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.shared_token = %s
                """,
                (token,),
            )
            session = cur.fetchone()
            if not session:
                return None

            # access: เจ้าของเปิดได้เสมอ; ถ้ามีผู้รับ (targeted) ต้องเป็นผู้รับ;
            # ถ้าไม่มีผู้รับ (link-only) ใครมีลิงก์ + login ก็เปิดได้
            if viewer_id != session[5]:
                cur.execute(
                    "SELECT recipient_user_id FROM chat_session_recipients "
                    "WHERE session_id = %s",
                    (session[0],),
                )
                recips = [r[0] for r in cur.fetchall()]
                if recips and viewer_id not in recips:
                    return None

            cur.execute(
                """
                SELECT h.id, h.question, h.answer, h.source, h.asked_at,
                       h.knowledge_id, k.source_file
                FROM chat_history h
                LEFT JOIN knowledge k ON k.id = h.knowledge_id
                WHERE h.session_id = %s
                ORDER BY h.id ASC
                """,
                (session[0],),
            )
            rows = cur.fetchall()

    messages = [
        {
            "id": r[0],
            "question": r[1],
            "answer": r[2],
            "source": r[3],
            "asked_at": r[4].isoformat() if r[4] else None,
            "source_knowledge_id": r[5],
            "source_file": r[6],
        }
        for r in rows
    ]

    return {
        "id": session[0],
        "title": session[1],
        "created_at": session[2].isoformat() if session[2] else None,
        "updated_at": session[3].isoformat() if session[3] else None,
        "mode": session[4],
        "owner_user_id": session[5],
        "owner_username": session[6],
        "messages": messages,
    }


def fork_shared_session_pg(token: str, new_user_id: int) -> int | None:
    """Clone a shared session into a new chat owned by `new_user_id`.

    The clone copies every message (question + answer + source link) into a
    brand-new session, prefixed "📋 " so the recipient can spot forked chats
    at a glance. Returns the new session id, or None if the token is invalid.
    Attachments are NOT copied — they belong to the original owner.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, COALESCE(mode, 'normal'), user_id "
                "FROM chat_sessions WHERE shared_token = %s",
                (token,),
            )
            src = cur.fetchone()
            if not src:
                return None
            src_id, src_title, src_mode, owner_id = src

            # access: เจ้าของ + ผู้รับ (targeted) หรือใครก็ได้ที่มีลิงก์ (link-only) fork ได้
            if new_user_id != owner_id:
                cur.execute(
                    "SELECT recipient_user_id FROM chat_session_recipients "
                    "WHERE session_id = %s",
                    (src_id,),
                )
                recips = [r[0] for r in cur.fetchall()]
                if recips and new_user_id not in recips:
                    return None

            forked_title = ("📋 " + (src_title or "บทสนทนา"))[:80]

            cur.execute(
                """
                INSERT INTO chat_sessions (user_id, title, mode)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (new_user_id, forked_title, src_mode),
            )
            new_session_id = cur.fetchone()[0]

            # is_forked=TRUE on every cloned row so the 20-turn budget on the
            # forked chat starts fresh — these messages weren't asked by the
            # new owner, they're context inherited from the shared link.
            cur.execute(
                """
                INSERT INTO chat_history
                    (user_id, session_id, question, answer, source, knowledge_id, is_forked)
                SELECT %s, %s, question, answer, source, knowledge_id, TRUE
                FROM chat_history
                WHERE session_id = %s
                ORDER BY id ASC
                """,
                (new_user_id, new_session_id, src_id),
            )

    return new_session_id


import csv
import io


def search_chat_pg(user_id: int, q: str) -> list[dict]:
    like = f"%{q.lower()}%"

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    h.id,
                    h.session_id,
                    s.title AS session_title,
                    h.question,
                    h.answer,
                    h.asked_at
                FROM chat_history h
                JOIN chat_sessions s ON s.id = h.session_id
                WHERE h.user_id = %s
                  AND (LOWER(h.question) LIKE %s OR LOWER(h.answer) LIKE %s)
                ORDER BY h.id DESC
                LIMIT 50
                """,
                (user_id, like, like),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "session_id": row[1],
            "session_title": row[2],
            "question": row[3],
            "answer": row[4],
            "asked_at": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]


def export_session_csv_pg(session_id: int, user_id: int) -> tuple[str, str] | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title
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
                SELECT asked_at, question, answer, source
                FROM chat_history
                WHERE session_id = %s
                ORDER BY id ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "question", "answer", "source"])

    for row in rows:
        writer.writerow(
            [
                row[0].isoformat() if row[0] else "",
                row[1],
                row[2],
                row[3],
            ]
        )

    filename = f"session-{session_id}.csv"
    return filename, buf.getvalue()
def save_attachment_pg(
    message_id: int,
    user_id: int,
    filename: str,
    content_type: str,
    size_bytes: int,
    file_path: str,
    extracted_text: str | None = None,
) -> dict:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO attachments
                    (message_id, user_id, filename, content_type, size_bytes, file_path, extracted_text)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, filename, content_type, size_bytes
                """,
                (
                    message_id,
                    user_id,
                    filename,
                    content_type,
                    size_bytes,
                    file_path,
                    extracted_text,
                ),
            )
            row = cur.fetchone()

    return {
        "id": row[0],
        "filename": row[1],
        "content_type": row[2],
        "size_bytes": row[3],
    }


def list_message_attachments_pg(message_id: int) -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, content_type, size_bytes
                FROM attachments
                WHERE message_id = %s
                ORDER BY id
                """,
                (message_id,),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "filename": row[1],
            "content_type": row[2],
            "size_bytes": row[3],
        }
        for row in rows
    ]


def get_attachment_pg(aid: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, filename, content_type, file_path
                FROM attachments
                WHERE id = %s
                """,
                (aid,),
            )
            row = cur.fetchone()

    if not row:
        return None

    return {
        "user_id": row[0],
        "filename": row[1],
        "content_type": row[2],
        "file_path": row[3],
    }