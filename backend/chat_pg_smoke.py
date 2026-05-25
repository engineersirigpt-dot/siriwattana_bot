from auth import hash_password
from auth_pg import create_user_pg, get_user_by_username_pg
from chat_pg import (
    ensure_session_pg,
    get_session_history_pg,
    get_session_messages_pg,
    list_sessions_pg,
    save_chat_message_pg,
)
from db_pg import get_pg_conn, init_pg_schema


USERNAME = "pg_chat_smoke"
PASSWORD = "pgtest123"


def cleanup() -> None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM attachments
                WHERE message_id IN (
                    SELECT id FROM chat_history
                    WHERE user_id IN (
                        SELECT id FROM users WHERE username = %s
                    )
                )
                """,
                (USERNAME,),
            )
            cur.execute(
                """
                DELETE FROM chat_history
                WHERE user_id IN (
                    SELECT id FROM users WHERE username = %s
                )
                """,
                (USERNAME,),
            )
            cur.execute(
                """
                DELETE FROM chat_sessions
                WHERE user_id IN (
                    SELECT id FROM users WHERE username = %s
                )
                """,
                (USERNAME,),
            )
            cur.execute("DELETE FROM users WHERE username = %s", (USERNAME,))


def main() -> None:
    init_pg_schema()
    cleanup()

    created = create_user_pg(USERNAME, hash_password(PASSWORD), role="user")
    user = get_user_by_username_pg(USERNAME)

    assert user is not None
    assert user["id"] == created["id"]

    session_id, title = ensure_session_pg(
        user_id=user["id"],
        session_id=None,
        first_question="บริษัททำเกี่ยวกับอะไร",
    )

    assert session_id
    assert title == "บริษัททำเกี่ยวกับอะไร"

    message_id = save_chat_message_pg(
        user_id=user["id"],
        session_id=session_id,
        question="บริษัททำเกี่ยวกับอะไร",
        answer="บริษัทให้บริการด้านสิ่งพิมพ์และบรรจุภัณฑ์ครบวงจร",
        source="rag",
        knowledge_id=None,
    )

    assert message_id

    history = get_session_history_pg(session_id, user["id"])
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"

    sessions = list_sessions_pg(user["id"])
    assert len(sessions) >= 1
    assert sessions[0]["message_count"] >= 1

    session_detail = get_session_messages_pg(session_id, user["id"])
    assert session_detail is not None
    assert session_detail["id"] == session_id
    assert len(session_detail["messages"]) == 1

    print("PostgreSQL chat adapter smoke test passed")
    print(
        {
            "user_id": user["id"],
            "session_id": session_id,
            "message_id": message_id,
            "title": title,
        }
    )


if __name__ == "__main__":
    main()