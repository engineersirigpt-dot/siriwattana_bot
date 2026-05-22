from auth import hash_password, verify_password
from auth_pg import (
    create_user_pg,
    get_user_by_id_pg,
    get_user_by_username_pg,
    list_users_pg,
    set_user_role_pg,
    username_exists_pg,
)
from db_pg import init_pg_schema, get_pg_conn


USERNAME = "pg_auth_smoke"
PASSWORD = "pgtest123"


def cleanup() -> None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE username = %s", (USERNAME,))


def main() -> None:
    init_pg_schema()
    cleanup()

    assert username_exists_pg(USERNAME) is False

    password_hash = hash_password(PASSWORD)
    user = create_user_pg(USERNAME, password_hash, role="user")

    assert user["username"] == USERNAME
    assert user["role"] == "user"
    assert username_exists_pg(USERNAME) is True

    loaded_by_id = get_user_by_id_pg(user["id"])
    assert loaded_by_id is not None
    assert loaded_by_id["username"] == USERNAME

    loaded_by_username = get_user_by_username_pg(USERNAME)
    assert loaded_by_username is not None
    assert verify_password(PASSWORD, loaded_by_username["password_hash"]) is True

    updated = set_user_role_pg(USERNAME, "admin")
    assert updated is True

    admin_user = get_user_by_username_pg(USERNAME)
    assert admin_user is not None
    assert admin_user["role"] == "admin"

    users = list_users_pg()
    assert any(u["username"] == USERNAME for u in users)

    print("PostgreSQL auth adapter smoke test passed")
    print(admin_user)


if __name__ == "__main__":
    main()