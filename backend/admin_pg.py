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