"""
MI (Master Information) authentication integration.

Verifies user credentials against the MI MSSQL database.
Source view: HRM.dbo.v_user_account (MD5 lowercase hashed passwords).

If MI env vars are NOT set, MI verification is silently skipped.
This makes the feature opt-in per environment.
"""

import hashlib
import logging
import os

logger = logging.getLogger(__name__)


MI_DB_SERVER = os.getenv("MI_DB_SERVER", "").strip()
MI_DB_USER = os.getenv("MI_DB_USER", "").strip()
MI_DB_PASSWORD = os.getenv("MI_DB_PASSWORD", "").strip()
MI_DB_NAME = os.getenv("MI_DB_NAME", "HRM").strip()
MI_DB_PORT = int(os.getenv("MI_DB_PORT", "1433"))
MI_CONNECT_TIMEOUT = int(os.getenv("MI_CONNECT_TIMEOUT", "5"))


def mi_enabled() -> bool:
    """True if MI integration env vars are configured."""
    return bool(MI_DB_SERVER and MI_DB_USER and MI_DB_PASSWORD)


def _md5_lower(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest().lower()


def verify_mi_credentials(username: str, password: str) -> dict | None:
    """
    Verify username/password against MI HRM.dbo.v_user_account.

    Returns dict {username, display_name, emp_id} if credentials match
    an active (user_expired = 0) MI user, otherwise None.

    Returns None silently on any error (DB down, bad query, etc.) so
    that the caller can fall through to its normal "invalid credentials"
    response rather than leaking infrastructure details.
    """
    if not mi_enabled():
        return None

    if not username or not password:
        return None

    try:
        import pymssql  # imported lazily so the dependency is only needed when MI is enabled
    except ImportError:
        logger.warning("MI integration is enabled but pymssql is not installed")
        return None

    md5_hash = _md5_lower(password)

    try:
        conn = pymssql.connect(
            server=MI_DB_SERVER,
            port=MI_DB_PORT,
            user=MI_DB_USER,
            password=MI_DB_PASSWORD,
            database=MI_DB_NAME,
            timeout=MI_CONNECT_TIMEOUT,
            login_timeout=MI_CONNECT_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("MI DB connect failed: %s", exc)
        return None

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TOP 1 user_account, user_name, emp_id
            FROM dbo.v_user_account
            WHERE (user_account = %s OR emp_id = %s)
              AND LOWER(user_password) = %s
              AND user_expired = 0
            """,
            (username, username, md5_hash),
        )
        row = cur.fetchone()
    except Exception as exc:
        logger.warning("MI DB query failed: %s", exc)
        row = None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not row:
        return None

    return {
        "username": str(row[0]).strip(),
        "display_name": (row[1] or row[0] or "").strip(),
        "emp_id": str(row[2]).strip() if row[2] is not None else None,
    }
