from auth import hash_password
from auth_pg import create_user_pg, get_user_by_username_pg
from chat_pg import (
    ensure_session_pg,
    get_attachment_pg,
    save_attachment_pg,
    save_chat_message_pg,
)
from db_pg import get_pg_conn, init_pg_schema


USERNAME = "pg_attachment_smoke"
PASSWORD = "pgtest123"


def cleanup() -> None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM attachments
                WHERE user_id IN (
                    SELECT id FROM users WHERE username = %s
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

    session_id, _ = ensure_session_pg(
        user_id=user["id"],
        session_id=None,
        first_question="ทดสอบไฟล์แนบ",
    )

    message_id = save_chat_message_pg(
        user_id=user["id"],
        session_id=session_id,
        question="[ไฟล์แนบ]",
        answer="รับไฟล์แล้ว",
        source="files",
        knowledge_id=None,
    )

    attachment = save_attachment_pg(
        message_id=message_id,
        user_id=user["id"],
        filename="test.txt",
        content_type="text/plain",
        size_bytes=12,
        file_path="uploads/test.txt",
        extracted_text="hello upload",
    )

    loaded = get_attachment_pg(attachment["id"])

    assert loaded is not None
    assert loaded["user_id"] == user["id"]
    assert loaded["filename"] == "test.txt"
    assert loaded["content_type"] == "text/plain"
    assert loaded["file_path"] == "uploads/test.txt"

    print("PostgreSQL attachment adapter smoke test passed")
    print({"session_id": session_id, "message_id": message_id, "attachment": attachment})


if __name__ == "__main__":
    main()