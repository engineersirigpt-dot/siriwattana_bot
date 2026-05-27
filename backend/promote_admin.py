"""
Promote a user to admin (or demote back to user).

Works with both SQLite and PostgreSQL — switches based on DB_ENGINE.

Usage:
    python promote_admin.py <username>            # promote to admin
    python promote_admin.py <username> --demote   # demote to user
    python promote_admin.py --list                # list all users

In Docker:
    sudo docker exec -it siriwattana-backend python promote_admin.py <username>
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

DB_ENGINE = os.getenv("DB_ENGINE", "sqlite").strip().lower()


def _use_postgres() -> bool:
    return DB_ENGINE in {"postgres", "postgresql", "pg"}


def list_users() -> None:
    if _use_postgres():
        from auth_pg import list_users_pg

        users = list_users_pg()
    else:
        from db import get_db

        rows = get_db().execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id"
        ).fetchall()
        users = [dict(r) for r in rows]

    if not users:
        print("(no users yet)")
        return

    print(f"{'ID':<6} {'USERNAME':<24} {'ROLE':<8} CREATED")
    print("-" * 60)
    for u in users:
        print(f"{u['id']:<6} {u['username']:<24} {u['role']:<8} {u['created_at']}")


def set_role(username: str, role: str) -> int:
    if _use_postgres():
        from auth_pg import get_user_by_username_pg, set_user_role_pg

        user = get_user_by_username_pg(username)
        if not user:
            print(f"ERROR: user '{username}' not found", file=sys.stderr)
            return 2

        if user["role"] == role:
            print(f"User '{username}' already has role '{role}'. No change.")
            return 0

        if not set_user_role_pg(username, role):
            print(f"ERROR: failed to update role for '{username}'", file=sys.stderr)
            return 1

        print(f"OK: '{username}' role changed from '{user['role']}' to '{role}'")
        return 0

    from db import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id, role FROM users WHERE username = ?", (username,)
    ).fetchone()

    if not row:
        print(f"ERROR: user '{username}' not found", file=sys.stderr)
        return 2

    if row["role"] == role:
        print(f"User '{username}' already has role '{role}'. No change.")
        return 0

    conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    conn.commit()

    print(f"OK: '{username}' role changed from '{row['role']}' to '{role}'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", nargs="?", help="Username to promote/demote")
    parser.add_argument("--demote", action="store_true", help="Demote to 'user' instead of promoting to 'admin'")
    parser.add_argument("--list", action="store_true", help="List all users and exit")
    args = parser.parse_args()

    print(f"DB_ENGINE: {DB_ENGINE}")

    if args.list:
        list_users()
        return 0

    if not args.username:
        parser.print_help()
        return 1

    role = "user" if args.demote else "admin"
    return set_role(args.username, role)


if __name__ == "__main__":
    sys.exit(main())
