from db_pg import get_pg_conn


def get_user_by_id_pg(user_id: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, role
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
    }


def get_user_by_username_pg(username: str) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, password_hash, role, COALESCE(is_disabled, false)
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            row = cur.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "is_disabled": bool(row[4]),
    }


def username_exists_pg(username: str) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            row = cur.fetchone()

    return row is not None


def create_user_pg(username: str, password_hash: str, role: str = "user") -> dict:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, %s)
                RETURNING id, username, role
                """,
                (username, password_hash, role),
            )
            row = cur.fetchone()

    return {
        "id": row[0],
        "username": row[1],
        "role": row[2],
    }


def set_user_role_pg(username: str, role: str) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET role = %s
                WHERE username = %s
                """,
                (role, username),
            )
            updated = cur.rowcount

    return updated > 0


def list_users_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, role, created_at
                FROM users
                ORDER BY id
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "username": row[1],
            "role": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        }
        for row in rows
    ]