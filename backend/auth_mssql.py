import hashlib
import os

import pymssql

MSSQL_SERVER   = os.getenv("MSSQL_SERVER", "")
MSSQL_PORT     = int(os.getenv("MSSQL_PORT", "1433"))
MSSQL_USER     = os.getenv("MSSQL_USER", "")
MSSQL_PASSWORD = os.getenv("MSSQL_PASSWORD", "")
MSSQL_DB       = os.getenv("MSSQL_DB", "MI_AUTHEN")


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def mssql_enabled() -> bool:
    return bool(MSSQL_SERVER and MSSQL_USER and MSSQL_PASSWORD)


def authenticate_company_user(username: str, password: str) -> dict | None:
    """
    เช็ค username + password กับ MI_AUTHEN.tb_user_account
    คืนค่า dict ข้อมูล user ถ้าผ่าน, None ถ้าไม่ผ่าน
    """
    if not mssql_enabled():
        return None

    try:
        conn = pymssql.connect(
            server=MSSQL_SERVER,
            port=MSSQL_PORT,
            user=MSSQL_USER,
            password=MSSQL_PASSWORD,
            database=MSSQL_DB,
            timeout=5,
        )
        cur = conn.cursor(as_dict=True)
        cur.execute(
            """
            SELECT user_id, user_name, emp_id, actived
            FROM tb_user_account
            WHERE user_name = %s
              AND user_password = %s
              AND actived = 1
            """,
            (username, _md5(password)),
        )
        row = cur.fetchone()
        conn.close()
        return row if row else None
    except Exception:
        return None
