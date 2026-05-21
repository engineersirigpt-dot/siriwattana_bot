import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from audit import audit_log
from db import get_db

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
JWT_EXPIRES_HOURS = int(os.getenv("JWT_EXPIRES_HOURS", "8"))

DEFAULT_JWT_SECRETS = {
    "",
    "dev-secret-change-me",
    "change-me",
    "change-me-to-a-long-random-string-at-least-32-chars",
    "your-secret-key",
    "secret",
    "jwt-secret",
}

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


def validate_jwt_secret() -> None:
    """
    Validate JWT_SECRET at app startup.

    Production must not use default/weak JWT_SECRET because attackers could forge tokens.
    """
    if JWT_SECRET in DEFAULT_JWT_SECRETS or len(JWT_SECRET) < 32:
        audit_log(
            "insecure_jwt_secret_detected",
            detail={
                "reason": "JWT_SECRET is default, empty, or shorter than 32 characters",
                "secret_length": len(JWT_SECRET or ""),
            },
        )
        raise RuntimeError(
            "JWT_SECRET is insecure. Please set a strong random JWT_SECRET "
            "in backend/.env with at least 32 characters."
        )


def hash_password(plain: str) -> str:
    return pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


PASSWORD_RULES = (
    "รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร "
    "มีทั้งตัวอักษรและตัวเลข"
)


def validate_password(password: str) -> str | None:
    """Return error message in Thai if password is weak, else None."""
    if len(password) < 8:
        return "รหัสผ่านสั้นเกินไป — ต้องอย่างน้อย 8 ตัวอักษร"

    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)

    if not has_letter:
        return "รหัสผ่านต้องมีตัวอักษรอย่างน้อย 1 ตัว"

    if not has_digit:
        return "รหัสผ่านต้องมีตัวเลขอย่างน้อย 1 ตัว"

    return None


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRES_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _credential_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(request: Request, token: str = Depends(oauth2)) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError) as e:
        audit_log(
            "invalid_or_expired_token",
            detail={
                "reason": "token_decode_failed_or_expired",
                "error_type": e.__class__.__name__,
            },
            request=request,
        )
        raise _credential_exception()

    row = get_db().execute(
        "SELECT id, username, role FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    if not row:
        audit_log(
            "invalid_or_expired_token",
            detail={
                "reason": "token_user_not_found",
                "user_id": user_id,
            },
            request=request,
        )
        raise _credential_exception()

    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
    }


def require_admin(request: Request, user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        audit_log(
            "admin_access_denied",
            user=user,
            detail={"reason": "user_role_is_not_admin"},
            request=request,
        )
        raise HTTPException(status_code=403, detail="Admin only")

    return user