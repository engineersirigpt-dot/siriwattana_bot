import csv
import io
import json
import os
import re
import time

import anyio
from audit import audit_log
from dotenv import load_dotenv

load_dotenv()

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.concurrency import iterate_in_threadpool, run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import attachments as att
import doc_translation as doctr
import proofread
from auth import (
    create_token,
    current_user,
    hash_password,
    require_admin,
    use_postgres_auth,
    validate_jwt_secret,
    verify_password,
)
from db import get_db
from llm import (
    LLM_MODEL,
    LLM_MODEL_CALC,
    WEB_SEARCH_MODEL,
    accumulate_usage,
    answer_freely,
    answer_from_context,
    answer_with_files,
    answer_with_web_search,
    classify_query,
    edit_image,
    generate_image,
    stream_freely,
    stream_from_context,
)
from mi_auth import mi_enabled, verify_mi_credentials
# Imported for its import-time side effect: registers the linsip host in
# url_reader's allowlist so pasted linsip URLs are readable too.
import linsip  # noqa: F401
from rag import add_knowledge, embed, log_pending_question, resolve_pending, search_knowledge
from sensitive import BLOCKED_RESPONSE, is_sensitive
from sensitivity_classifier import classify_sensitivity
from brain_client import (
    brain_enabled,
    build_brain_context,
    search_brain,
)


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Sirivatana AI Chatbot API")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    audit_log(
        "rate_limit_exceeded",
        detail={
            "path": request.url.path,
            "method": request.method,
            "limit": str(exc),
        },
        request=request,
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    audit_log(
        "unhandled_error",
        detail={
            "path": request.url.path,
            "method": request.method,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        },
        request=request,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        os.getenv("CORS_ORIGIN", "http://localhost:3000"),
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    validate_jwt_secret()
    get_db()
    # Apply postgres migrations on every boot so a deploy that adds a column or
    # table (e.g. answer_feedback, shared_token) is live without a manual step.
    # init_pg_schema is fully idempotent (CREATE/ALTER ... IF NOT EXISTS).
    if use_postgres_auth():
        try:
            from db_pg import init_pg_schema

            init_pg_schema()
            audit_log("pg_schema_migrated", detail={"status": "ok"})
        except Exception as e:
            audit_log(
                "pg_schema_migrate_failed",
                detail={"error_type": e.__class__.__name__, "error": str(e)},
            )
    # งานแปลที่ค้างตอน backend ดับ -> ตั้งเป็น error (worker หายไปแล้ว)
    try:
        doctr.mark_orphans_interrupted()
    except Exception:
        pass
    audit_log("app_startup", detail={"status": "ok"})


@app.get("/health")
async def health():
    """Liveness probe for the Docker HEALTHCHECK.

    Deliberately `async` with no I/O: it runs on the event loop itself, so if
    the single uvicorn worker gets wedged (every request hangs, incl. MI SSO
    login) this stops answering and `docker ps` shows the container unhealthy.
    """
    return {"ok": True}


class ChatResponse(BaseModel):
    answer: str
    source: str
    similarity: float | None = None
    session_id: int
    session_title: str
    attachments: list[dict] = []
    # Set when the answer came from a KB chunk (source='rag' / 'rag-calc').
    # Frontend uses these to render the "📎 ดาวน์โหลดเอกสารต้นฉบับ" button.
    source_knowledge_id: int | None = None
    source_file: str | None = None
    # Per-session turn budget — frontend renders a "5/20" counter and nudges
    # the user toward a new chat as the limit approaches.
    turn_count: int | None = None
    turn_limit: int | None = None
    # chat_history row id of this answer, so the UI can attach 👍/👎 feedback.
    # None for short-circuit replies (blocked / export-offer) that aren't saved.
    message_id: int | None = None


class KnowledgeIn(BaseModel):
    question: str
    answer: str


class AnswerPendingIn(BaseModel):
    answer: str


class SessionRenameIn(BaseModel):
    title: str


class SessionSaveIn(BaseModel):
    is_saved: bool


class UserRoleIn(BaseModel):
    role: str  # 'user' | 'admin'


class UserStatusIn(BaseModel):
    is_disabled: bool


class ExportPdfIn(BaseModel):
    content: str            # markdown body
    title: str | None = None
    user_question: str | None = None


class ExportXlsxIn(BaseModel):
    content: str            # markdown body (its tables are exported)
    title: str | None = None


class ExportDocxIn(BaseModel):
    content: str            # markdown body
    title: str | None = None


@app.post("/auth/register")
@limiter.limit("5/minute")
def register(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    conn = get_db()

    if use_postgres_auth():
        from auth_pg import create_user_pg, username_exists_pg

        existing = username_exists_pg(form.username)
    else:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE username = ?",
            (form.username,),
        ).fetchone()

    if existing:
        audit_log(
            "register_failed_username_taken",
            detail={"username": form.username},
            request=request,
        )
        raise HTTPException(400, "username already taken")

    role = "user"
    password_hash = hash_password(form.password)

    if use_postgres_auth():
        create_user_pg(form.username, password_hash, role)
    else:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (form.username, password_hash, role),
        )
        conn.commit()

    audit_log(
        "user_registered",
        detail={
            "username": form.username,
            "role": role,
            "db_engine": "postgres" if use_postgres_auth() else "sqlite",
        },
        request=request,
    )

    return {"ok": True, "role": role}


@app.post("/auth/login")
@limiter.limit("10/minute")
def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    # 1. Try local chatbot user first (siriadmin and any other locally-created users).
    if use_postgres_auth():
        from auth_pg import get_user_by_username_pg

        row = get_user_by_username_pg(form.username)
    else:
        row = get_db().execute(
            "SELECT id, username, password_hash, role, "
            "COALESCE(is_disabled, 0) AS is_disabled "
            "FROM users WHERE username = ?",
            (form.username,),
        ).fetchone()

    if row and verify_password(form.password, row["password_hash"]):
        if row["is_disabled"]:
            audit_log(
                "login_blocked_disabled",
                user={"id": row["id"], "username": row["username"], "role": row["role"]},
                detail={"auth_source": "local"},
                request=request,
            )
            raise HTTPException(403, "บัญชีนี้ถูกระงับการใช้งาน กรุณาติดต่อ admin")

        token = create_token(row["id"], row["username"], row["role"])

        audit_log(
            "login_success",
            user={"id": row["id"], "username": row["username"], "role": row["role"]},
            detail={
                "auth_source": "local",
                "db_engine": "postgres" if use_postgres_auth() else "sqlite",
            },
            request=request,
        )

        return {
            "access_token": token,
            "token_type": "bearer",
            "username": row["username"],
            "role": row["role"],
        }

    # 2. Fall back to MI (HRM.dbo.v_user_account, MD5 lowercase) if configured.
    if mi_enabled():
        mi_user = verify_mi_credentials(form.username, form.password)
        if mi_user:
            user_row = _ensure_mi_user_provisioned(mi_user["username"])

            if user_row.get("is_disabled"):
                audit_log(
                    "login_blocked_disabled",
                    user={
                        "id": user_row["id"],
                        "username": user_row["username"],
                        "role": user_row["role"],
                    },
                    detail={"auth_source": "mi", "mi_emp_id": mi_user.get("emp_id")},
                    request=request,
                )
                raise HTTPException(403, "บัญชีนี้ถูกระงับการใช้งาน กรุณาติดต่อ admin")

            token = create_token(
                user_row["id"], user_row["username"], user_row["role"]
            )

            audit_log(
                "login_success",
                user={
                    "id": user_row["id"],
                    "username": user_row["username"],
                    "role": user_row["role"],
                },
                detail={
                    "auth_source": "mi",
                    "mi_emp_id": mi_user.get("emp_id"),
                    "mi_display_name": mi_user.get("display_name"),
                    "db_engine": "postgres" if use_postgres_auth() else "sqlite",
                },
                request=request,
            )

            return {
                "access_token": token,
                "token_type": "bearer",
                "username": user_row["username"],
                "role": user_row["role"],
            }

    audit_log(
        "login_failed",
        detail={
            "username": form.username,
            "mi_enabled": mi_enabled(),
            "db_engine": "postgres" if use_postgres_auth() else "sqlite",
        },
        request=request,
    )
    raise HTTPException(401, "invalid credentials")


def _ensure_mi_user_provisioned(username: str) -> dict:
    """Look up the local chatbot user record for an MI user, creating it on first login.

    MI-sourced users always get role='user'. The local password_hash is a sentinel
    bcrypt hash that cannot match any submitted password, so these users can only
    authenticate via MI going forward.
    """
    sentinel_hash = hash_password(
        "__mi_sso_no_local_password__:" + os.urandom(16).hex()
    )

    if use_postgres_auth():
        from auth_pg import (
            create_user_pg,
            get_user_by_username_pg,
        )

        existing = get_user_by_username_pg(username)
        if existing:
            return {
                "id": existing["id"],
                "username": existing["username"],
                "role": existing["role"],
                "is_disabled": existing.get("is_disabled", False),
            }

        created = create_user_pg(username, sentinel_hash, role="user")
        created.setdefault("is_disabled", False)
        return created

    conn = get_db()
    existing = conn.execute(
        "SELECT id, username, role, COALESCE(is_disabled, 0) AS is_disabled "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if existing:
        return {
            "id": existing["id"],
            "username": existing["username"],
            "role": existing["role"],
            "is_disabled": bool(existing["is_disabled"]),
        }

    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, sentinel_hash, "user"),
    )
    conn.commit()
    new_id = cur.lastrowid
    return {"id": new_id, "username": username, "role": "user", "is_disabled": False}


class MiSsoIn(BaseModel):
    token: str


@app.post("/auth/mi-sso")
@limiter.limit("30/minute")
def mi_sso_login(request: Request, body: MiSsoIn):
    """Single-sign-on entry from the central MI portal.

    The frontend /sso page forwards the MI-issued JWT here. We verify it with
    the shared MI_JWT_SECRET, provision/find the local user by emp_id, and
    return our own session token — same response shape as /auth/login.
    Roles come from the local user record (existing admins keep 'admin';
    first-time MI users get 'user').
    """
    from mi_auth import decode_mi_sso_token, mi_sso_enabled

    if not mi_sso_enabled():
        raise HTTPException(503, "MI SSO ยังไม่ได้เปิดใช้งาน (ยังไม่ได้ตั้ง MI_JWT_SECRET)")

    payload = decode_mi_sso_token(body.token)
    if not payload:
        audit_log(
            "mi_sso_failed",
            detail={"reason": "invalid_or_expired_token"},
            request=request,
        )
        raise HTTPException(401, "Token จาก MI ไม่ถูกต้องหรือหมดอายุ")

    # Reject MI accounts flagged expired (matches MI's `user_expired = 0` = active).
    try:
        expired = int(payload.get("user_expired", 0)) != 0
    except (TypeError, ValueError):
        expired = False
    if expired:
        raise HTTPException(403, "บัญชี MI นี้หมดอายุการใช้งาน")

    emp_id = str(payload.get("emp_id") or payload.get("user_account") or "").strip()
    if not emp_id:
        audit_log("mi_sso_failed", detail={"reason": "no_emp_id"}, request=request)
        raise HTTPException(401, "Token ไม่มี emp_id")

    user_row = _ensure_mi_user_provisioned(emp_id)

    if user_row.get("is_disabled"):
        audit_log(
            "login_blocked_disabled",
            user={
                "id": user_row["id"],
                "username": user_row["username"],
                "role": user_row["role"],
            },
            detail={"auth_source": "mi_sso", "emp_id": emp_id},
            request=request,
        )
        raise HTTPException(403, "บัญชีนี้ถูกระงับการใช้งาน กรุณาติดต่อ admin")

    token = create_token(user_row["id"], user_row["username"], user_row["role"])

    audit_log(
        "login_success",
        user={
            "id": user_row["id"],
            "username": user_row["username"],
            "role": user_row["role"],
        },
        detail={
            "auth_source": "mi_sso",
            "emp_id": emp_id,
            "mi_user_name": payload.get("user_name"),
            "db_engine": "postgres" if use_postgres_auth() else "sqlite",
        },
        request=request,
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user_row["username"],
        "role": user_row["role"],
    }


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


# V5 chat UI rollout: UI_V5_MODE=off|admin|all (+ UI_V5_USERS), default off →
# everyone keeps the classic chat page. Purely which page the frontend shows;
# no data or permissions depend on it.
UI_V5_MODE = os.getenv("UI_V5_MODE", "off").strip().lower()
UI_V5_USERS = {u.strip() for u in os.getenv("UI_V5_USERS", "").split(",") if u.strip()}


@app.get("/ui/config")
def ui_config(user: dict = Depends(current_user)):
    v5 = UI_V5_MODE == "all" or (
        UI_V5_MODE == "admin" and (user.get("role") == "admin" or user.get("username") in UI_V5_USERS)
    )
    return {"ui_v5": v5}


@app.post("/transcribe")
@limiter.limit("30/minute")
async def transcribe(
    request: Request,
    audio: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    """Voice input: transcribe a short recording to text for the chat box.

    The frontend mic button records audio and posts it here; we return the text
    so the user can review/edit before sending.
    """
    import os as _os
    import tempfile

    from llm import transcribe_audio

    data = await audio.read()
    if not data:
        raise HTTPException(400, "ไม่พบเสียง")
    if len(data) > 25 * 1024 * 1024:  # Whisper API hard limit
        raise HTTPException(400, "ไฟล์เสียงใหญ่เกิน 25MB")

    suffix = _os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        text = transcribe_audio(tmp.name)
    finally:
        try:
            _os.unlink(tmp.name)
        except OSError:
            pass

    audit_log(
        "voice_transcribed",
        user=user,
        detail={"bytes": len(data), "chars": len(text)},
        request=request,
    )
    return {"text": text}


def _title_from_question(question: str, max_len: int = 40) -> str:
    one_line = " ".join(question.split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 1] + "…"


# Phrases that signal "I want the previous answer exported as PDF". Matched
# case-insensitively as substrings, so e.g. "ขอเป็น pdf ได้มั้ย" still hits
# "ขอเป็น pdf". Length capped at 80 chars on the input side so the user
# can't slip an export keyword into the middle of a normal long question.
_EXPORT_INTENT_KEYWORDS = (
    # Thai
    "ขอ pdf",
    "ขอเป็น pdf",
    "ขอไฟล์ pdf",
    "ขอเป็นไฟล์ pdf",
    "ทำเป็น pdf",
    "ส่งออกเป็น pdf",
    "เป็นไฟล์ pdf",
    "ดาวน์โหลด pdf",
    "ดาวน์โหลดเป็น pdf",
    "เซฟเป็น pdf",
    "เซฟไฟล์ pdf",
    "บันทึกเป็น pdf",
    "ขอไฟล์",
    "ขอเซฟ",
    "ขอดาวน์โหลด",
    "ขอ export",
    "ขอ download",
    # English
    "export pdf",
    "export as pdf",
    "export to pdf",
    "save as pdf",
    "download as pdf",
    "download pdf",
    "give me a pdf",
    "make a pdf",
)


# A format word anywhere means the user wants a file. A *content verb* means
# they're also asking to produce something new ("สรุปเป็น pdf") — in that case
# we GENERATE the answer (and the UI shows a download button) instead of
# short-circuiting to "export the previous answer".
_EXPORT_FORMAT_RE = re.compile(
    r"\b(pdf|docx?|word|xlsx|excel)\b|พีดีเอฟ|เวิร์ด|เอ็กเซล|สเปรดชี",
    re.IGNORECASE,
)
_CONTENT_VERB_RE = re.compile(
    r"สรุป|แปล|เขียน|ร่าง|อธิบาย|เปรียบเทียบ|ตาราง|วิเคราะห์|คำนวณ|ออกแบบ|"
    r"แต่ง|แปลง|รายการ|ขั้นตอน|จัดทำ|บอก|แนะนำ|ช่วย|list|summar|translat|write|"
    r"explain|compar",
    re.IGNORECASE,
)


def _is_export_request(text: str) -> bool:
    """True only for a *bare* request to export the previous answer (e.g.
    "ขอ PDF", "export word"). Messages that also ask to produce content
    ("สรุปเป็น pdf ให้หน่อย") return False so they go through normal generation
    — the UI then offers a download button for the requested format.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > 60:
        return False
    if not _EXPORT_FORMAT_RE.search(stripped):
        return False
    if _CONTENT_VERB_RE.search(stripped):
        return False
    return True


# ── Proofreading attachments ────────────────────────────────────────────────
# "ตรวจคำผิดในรูปนี้" needs more than the normal vision answer: vision models
# read Thai typos as the correct word, so images are read literally (OCR),
# checked as text, and every reported typo is verified against the image
# (see proofread.py). Keep in sync with PROOFREAD_RE in frontend/app/chat/page.tsx.
_PROOFREAD_RE = re.compile(
    r"คำผิด|พิมพ์ผิด|สะกดผิด|เขียนผิด|ตัวสะกด|การสะกด|ตรวจ(?:ทาน|อักษร|ภาษา|คำ)|"
    r"พิสูจน์อักษร|ไวยากรณ์|proof\s*-?\s*read|typo|spell|grammar",
    re.IGNORECASE,
)


def _is_proofread_request(text: str) -> bool:
    return bool(text) and _PROOFREAD_RE.search(text) is not None


def _answer_files(
    question: str,
    image_urls: list[str],
    text_attachments: list[tuple[str, str]],
    history: list[dict] | None,
) -> tuple[str, dict]:
    """Answer about attached files — proofreading mode when asked to check
    spelling, otherwise the normal vision/document answer."""
    if _is_proofread_request(question):
        return proofread.proofread(question, image_urls, text_attachments)
    return answer_with_files(question, image_urls, text_attachments, history=history)


# ── AI image generation ─────────────────────────────────────────────────────
# Detect a "please draw/generate an image" request so we route it to the image
# model instead of the text LLM. A create-verb must sit right before the
# image-noun (Thai is written without spaces, so they're contiguous; English
# allows an article/pronoun). Keeps ordinary questions like "รูปแบบการทำงาน" or
# "ขอข้อมูลรูปเครื่องจักร" from tripping it.
_IMAGE_INTENT_RE = re.compile(
    # create-verb + any image noun (posters/cards/banners included now)
    r"(?:สร้าง|วาด|ออกแบบ|เจน)\s*"
    # "โป/โพ[สร]เตอร์" also catches the common misspelling "โปรเตอร์"
    r"(?:รูปภาพ|รูป|ภาพประกอบ|ภาพ|โลโก้|อินโฟกราฟิก|"
    r"โ[ปพ][สร]เตอร์|การ์ด|แบนเนอร์|ใบปลิว|แผ่นพับ|ปฏิทิน|สติกเกอร์)"
    # "ทำ" only before unambiguous graphic nouns — so "ทำไม/ทำงาน/ทำรูปแบบ" don't trip it
    r"|ทำ\s*(?:โ[ปพ][สร]เตอร์|การ์ด|แบนเนอร์|ใบปลิว|แผ่นพับ|ปฏิทิน|สติกเกอร์|อินโฟกราฟิก|โลโก้)"
    r"|(?:generate|create|draw|make|gen|design)\s+"
    r"(?:an?\s+|the\s+|me\s+(?:an?\s+)?|us\s+(?:an?\s+)?)?"
    r"(?:image|picture|logo|illustration|drawing|photo|graphic|"
    r"poster|banner|flyer|card|calendar|sticker)",
    re.IGNORECASE,
)

# Per-user cap on generated images per calendar day (Asia/Bangkok).
IMAGE_GEN_DAILY_LIMIT = int(os.getenv("IMAGE_GEN_DAILY_LIMIT", "10"))


# Layer B: phrasings the regex misses ("ทำรูปโปสเตอร์…", "ขอโปสเตอร์…", "ทำประกาศ
# นี้เป็นรูป") go to a small classifier — but only when the message names an
# image/graphic at all, so ordinary chat never pays for the extra call.
IMAGE_AUTO_DECIDE = os.getenv("IMAGE_AUTO_DECIDE", "1").strip().lower() not in {"0", "false", "no"}
_IMAGE_NOUN_RE = re.compile(
    r"รูป(?!แบบ)|ภาพ(?!รวม)|โ[ปพ][สร]เตอร์|ป้าย|แบนเนอร์|การ์ด|ใบปลิว|แผ่นพับ|"
    r"อาร์ตเวิร์[กค]|โลโก้|สติกเกอร์|อินโฟกราฟิก|"
    r"\b(?:image|picture|poster|banner|flyer|artwork|graphic|illustration|logo)s?\b",
    re.IGNORECASE,
)


def _is_image_request(text: str) -> bool:
    """True when the user is asking the bot to CREATE an image.

    Layer A: fast create-verb+noun regex. Layer B: if that misses but the
    message mentions an image/graphic, let the classifier decide.
    """
    if not text:
        return False
    stripped = text.strip()
    if _IMAGE_INTENT_RE.search(stripped) is not None:
        return True
    if IMAGE_AUTO_DECIDE and _IMAGE_NOUN_RE.search(stripped):
        from llm import wants_image_generation

        return wants_image_generation(stripped)
    return False


# Edit-intent: a modification instruction. Only acted on when the session
# already has an image to edit (see routing), so these common verbs don't
# misfire on ordinary chat.
_IMAGE_EDIT_RE = re.compile(
    r"เปลี่ยน(?:สี|พื้นหลัง|ฉาก|ให้)?|แก้(?:รูป|ภาพ|ไข)?|เพิ่ม|ใส่(?:ให้)?|ลบ|เอา\S*ออก|"
    r"ตัด\S*ออก|ปรับ|ทำให้|เปลี่ยนเป็น|สลับ|ย้าย|ขยาย|ย่อ|เบลอ|เปลี่ยนข้อความ|"
    r"\bedit\b|\bchange\b|\breplace\b|\bremove\b|\badd\b|\bmake it\b|\bturn it\b|"
    r"\brecolor\b|\brecolour\b|\bswap\b|\bblur\b",
    re.IGNORECASE,
)


def _is_image_edit_request(text: str) -> bool:
    """True when the message looks like an instruction to edit an image."""
    if not text:
        return False
    return _IMAGE_EDIT_RE.search(text.strip()) is not None


# When an image is ATTACHED, these verbs mean "make a new image FROM it"
# (reference-image generation) rather than "describe it". Broader than the
# create/edit sets because an attached image already supplies the subject
# (e.g. "สร้างรถจากโลโก้นี้" has no image-noun). Gated by "an image is attached"
# so it never fires on document questions ("สรุปไฟล์นี้" has no create verb).
_IMAGE_FROM_REF_RE = re.compile(
    r"สร้าง|ทำ(?!ไม)|วาด|ออกแบบ|เจน|แก้|เปลี่ยน|ใส่|เพิ่ม|ลบ|เอา\S*ออก|ปรับ|ทำให้|"
    r"ใช้รูป|จากรูป|จากภาพนี้|โลโก้นี้|"
    r"\bgenerate\b|\bcreate\b|\bmake\b|\bdraw\b|\bdesign\b|\bedit\b|\bchange\b|"
    r"\badd\b|\bremove\b|\buse this\b|\bfrom this\b|\bbased on\b",
    re.IGNORECASE,
)


_LOGO_WORD_RE = re.compile(r"โลโก|logo", re.IGNORECASE)
_DESIGN_NOUN_RE = re.compile(
    r"โ[ปพ][สร]เตอร์|ป้าย|ใบปลิว|แผ่นพับ|แบนเนอร์|การ์ด|ปฏิทิน|poster|flyer|banner|card",
    re.IGNORECASE,
)


def _wants_logo_on_new_design(text: str) -> bool:
    """True when the attached image is a logo to put on a new poster/sign/card
    ("ทำโปสเตอร์ … ใส่โลโก้ตามไฟล์"), not a picture to edit ("สร้างรถจากโลโก้นี้")."""
    return bool(text) and bool(_LOGO_WORD_RE.search(text)) and bool(_DESIGN_NOUN_RE.search(text))


def _wants_image_from_reference(text: str) -> bool:
    """True when (an image is attached and) the user wants a new/edited image
    made from it, not a description."""
    if not text:
        return False
    return _IMAGE_FROM_REF_RE.search(text.strip()) is not None


def _handle_image_edit(
    user: dict, question: str, session_id: int | None, mode: str, last_img: dict
) -> dict:
    """Edit the session's most recent image per `question`. Shares the daily
    image quota. Returns the same dict shape as _handle_image_generation."""
    import attachments as att
    from chat_pg import (
        count_images_today_pg,
        ensure_session_pg,
        record_image_generation_pg,
        save_attachment_pg,
        save_chat_message_pg,
    )

    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=session_id, first_question=question, mode=mode,
    )

    used = count_images_today_pg(user["id"])
    if used >= IMAGE_GEN_DAILY_LIMIT:
        text = (
            f"ขออภัยค่ะ วันนี้สร้าง/แก้รูปครบ {IMAGE_GEN_DAILY_LIMIT} รูปแล้ว "
            "กรุณาลองใหม่พรุ่งนี้ค่ะ 🙏"
        )
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source="image_limit", knowledge_id=None, mode=mode,
        )
        return {"answer": text, "source": "image_limit", "session_id": sid,
                "session_title": stitle, "message_id": mid, "attachment": None}

    try:
        src_path = att.validate_upload_path(last_img["file_path"])
        data, usage = edit_image(str(src_path), question)
    except Exception as e:
        import sys

        print(f"IMAGE_EDIT_FAILED: {e!r}", file=sys.stderr)
        text = "ขออภัยค่ะ แก้ไขรูปไม่สำเร็จ ลองพิมพ์คำสั่งใหม่ หรือสร้างรูปใหม่ก็ได้ค่ะ"
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source="image_error", knowledge_id=None, mode=mode,
        )
        return {"answer": text, "source": "image_error", "session_id": sid,
                "session_title": stitle, "message_id": mid, "attachment": None}

    display, ctype, size, path = att.save_generated_image(data, "ai-image-edited.png")
    remaining = IMAGE_GEN_DAILY_LIMIT - used - 1
    caption = (
        "นี่คือรูปที่แก้ไขตามคำขอของคุณค่ะ 🎨\n\n"
        f"_(วันนี้สร้าง/แก้ได้อีก {remaining} รูป)_"
    )
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question,
        answer=caption, source="image_edit", knowledge_id=None, mode=mode, usage=usage,
    )
    arow = save_attachment_pg(
        message_id=mid, user_id=user["id"], filename=display,
        content_type=ctype, size_bytes=size, file_path=path,
    )
    record_image_generation_pg(user["id"], "[edit] " + question, usage["cost_usd"])
    return {"answer": caption, "source": "image_edit", "session_id": sid,
            "session_title": stitle, "message_id": mid, "attachment": arow}


def _handle_image_generation(
    user: dict, question: str, session_id: int | None, mode: str,
    logo_path: str | None = None,
) -> dict:
    """Generate an image (or refuse if over the daily quota) and persist it.

    Postgres-only (mirrors the streaming pipeline). Returns a dict the caller
    turns into a ChatResponse / stream 'done' event:
        {answer, source, session_id, session_title, message_id, attachment}
    `attachment` is the saved-image row dict (or None when limited/failed).
    `logo_path` is a user-attached logo to place on the new design.
    """
    import attachments as att
    from chat_pg import (
        count_images_today_pg,
        ensure_session_pg,
        record_image_generation_pg,
        save_attachment_pg,
        save_chat_message_pg,
    )

    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=session_id,
        first_question=question, mode=mode,
    )

    used = count_images_today_pg(user["id"])
    if used >= IMAGE_GEN_DAILY_LIMIT:
        text = (
            f"ขออภัยค่ะ วันนี้คุณสร้างรูปครบ {IMAGE_GEN_DAILY_LIMIT} รูปแล้ว "
            "(จำกัด 10 รูป/วัน/บัญชี) กรุณาลองใหม่พรุ่งนี้ค่ะ 🙏"
        )
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source="image_limit", knowledge_id=None, mode=mode,
        )
        return {
            "answer": text, "source": "image_limit", "session_id": sid,
            "session_title": stitle, "message_id": mid, "attachment": None,
        }

    try:
        data, usage = generate_image(question, logo_path=logo_path)
    except Exception as e:
        import sys

        print(f"IMAGE_GEN_FAILED: {e!r}", file=sys.stderr)
        # Distinguish OpenAI's content-policy rejection (copyrighted characters,
        # brands, real people, unsafe content) from a real system error so the
        # user knows to reword instead of just "try again". Neither consumes the
        # daily quota — record_image_generation_pg only runs on success below.
        msg = str(e).lower()
        blocked = any(
            k in msg
            for k in (
                "safety", "moderation", "content_policy", "content policy",
                "rejected", "blocked", "not allowed",
            )
        )
        if blocked:
            text = (
                "ขออภัยค่ะ ไม่สามารถสร้างรูปนี้ได้ เนื่องจากติดนโยบายเนื้อหาของ AI "
                "(เช่น ตัวการ์ตูน/แบรนด์/โลโก้ที่มีลิขสิทธิ์ บุคคลจริง หรือเนื้อหาที่ไม่เหมาะสม) "
                "🙏 ลองเปลี่ยนเป็นคำอธิบายทั่วไปดูนะคะ เช่น 'รถเก๋งสีแดง' แทนชื่อแบรนด์/ตัวละคร"
            )
            source = "image_blocked"
        else:
            text = "ขออภัยค่ะ ระบบสร้างรูปไม่สำเร็จ กรุณาลองใหม่อีกครั้งค่ะ"
            source = "image_error"
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source=source, knowledge_id=None, mode=mode,
        )
        return {
            "answer": text, "source": source, "session_id": sid,
            "session_title": stitle, "message_id": mid, "attachment": None,
        }

    display, ctype, size, path = att.save_generated_image(data)
    remaining = IMAGE_GEN_DAILY_LIMIT - used - 1
    caption = (
        "นี่คือรูปที่สร้างตามคำขอของคุณค่ะ 🎨\n\n"
        f"_(วันนี้สร้างได้อีก {remaining} รูป)_"
    )
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question,
        answer=caption, source="image_gen", knowledge_id=None, mode=mode,
        usage=usage,
    )
    arow = save_attachment_pg(
        message_id=mid, user_id=user["id"], filename=display,
        content_type=ctype, size_bytes=size, file_path=path,
    )
    record_image_generation_pg(user["id"], question, usage["cost_usd"])
    return {
        "answer": caption, "source": "image_gen", "session_id": sid,
        "session_title": stitle, "message_id": mid, "attachment": arow,
    }


HISTORY_TURNS = 6

# Hard cap on number of question turns per chat session. Once exceeded, /chat
# returns 429 and the UI nudges the user to open a fresh chat. Bounds token
# cost (history is already capped at HISTORY_TURNS but the cap doesn't help
# if someone hammers the same session) and keeps long chats from hurting
# page-render performance. Export-offer turns don't count.
MAX_TURNS_PER_SESSION = 20


def _count_session_turns(session_id: int) -> int:
    """Number of real Q&A turns in a session.

    Excludes:
    - export_offer rows  — they're "ขอ PDF" requests, not real questions.
    - is_forked rows     — messages cloned from a shared chat aren't ones
      this user asked, so they shouldn't burn the 20-turn budget.
    """
    if use_postgres_auth():
        from db_pg import get_pg_conn

        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM chat_history "
                    "WHERE session_id = %s "
                    "  AND source <> 'export_offer' "
                    "  AND COALESCE(is_forked, false) = false",
                    (session_id,),
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0

    row = get_db().execute(
        "SELECT COUNT(*) AS n FROM chat_history "
        "WHERE session_id = ? "
        "  AND source <> 'export_offer' "
        "  AND COALESCE(is_forked, 0) = 0",
        (session_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


# ── Live web search ─────────────────────────────────────────────────────────
# Route to a web-searching model only when the question clearly wants FRESH
# info (news, today's prices, latest rules). Keeps normal/company questions on
# the fast, cheap pipeline. Over-triggering is low-risk (capped per day), so we
# lean slightly inclusive on the obvious "live info" signals.
_WEB_SEARCH_INTENT_RE = re.compile(
    # time / freshness signals
    r"ล่าสุด|ตอนนี้|ขณะนี้|วันนี้|เมื่อวาน|เมื่อวานนี้|คืนนี้|สัปดาห์นี้|เดือนนี้|ปีนี้|"
    r"ปัจจุบัน|เดี๋ยวนี้|ทุกวันนี้|ช่วงนี้|อัปเดต|อัพเดท|เปิดตัว|เพิ่งออก|ออกใหม่|รุ่นใหม่|"
    r"เวอร์ชันล่าสุด|เวอร์ชั่นล่าสุด|กี่โมงแล้ว|เวลาตอนนี้|"
    # news / events / weather / traffic
    r"ข่าว|พยากรณ์อากาศ|อากาศ|ฝนตก|รถติด|สภาพจราจร|สถานการณ์|"
    r"ผลบอล|ผลการแข่งขัน|ผลแข่ง|ตารางแข่ง|ผลสลาก|ผลหวย|หวยออก|ตรวจหวย|"
    # explicit web-search asks
    r"ค้นเว็บ|ค้นในเน็ต|หาในเน็ต|เสิร์ช|เสิร์ชหา|"
    # live financial / prices
    r"ราคาทอง|ราคาน้ำมัน|ราคาหุ้น|ราคาบิทคอยน์|บิทคอยน์|บิตคอยน์|คริปโต|"
    r"ค่าเงิน|อัตราแลกเปลี่ยน|เรทเงิน|หุ้น|ดอกเบี้ยนโยบาย|เงินเฟ้อ|"
    # facts that drift over time (population, stats, leaders, rankings)
    r"ประชากร|จำนวนประชากร|สถิติ|จีดีพี|\bgdp\b|ผู้นำประเทศ|นายกรัฐมนตรี|นายกฯ|"
    r"ประธานาธิบดี|รัฐมนตรี|ผู้ว่า|อันดับโลก|ค่าครองชีพ|อัตราการว่างงาน|"
    r"population|statistics|ranking|\bceo\b|"
    # someone's current team / club / affiliation (sports rosters, transfers)
    r"อยู่ทีม|สังกัด|ย้ายทีม|เล่นให้|แข่งให้|ทีมปัจจุบัน|\bplays for\b|\bdrives for\b|"
    # explicit years
    r"25[67]\d|20[23]\d|"
    # english
    r"\blatest\b|\btoday\b|\btonight\b|\byesterday\b|\bright now\b|\bcurrent(?:ly)?\b|"
    r"\bnews\b|\bweather\b|\bforecast\b|\bscore\b|\bresult(?:s)?\b|\bstanding(?:s)?\b|"
    r"\bstock price\b|\bexchange rate\b|\bbitcoin\b|\bcrypto\b|\breleased?\b|\bnew version\b|"
    r"\bthis (?:year|week|month|season)\b|\bas of\b|\bnowadays\b|\bup[- ]?to[- ]?date\b|"
    r"\bwho is the current\b|\bprime minister\b|\bpresident\b|"
    r"\bsearch (?:the )?(?:web|internet)\b|\bgoogle\b",
    re.IGNORECASE,
)

# Per-user cap on live web searches per calendar day (Asia/Bangkok).
WEB_SEARCH_DAILY_LIMIT = int(os.getenv("WEB_SEARCH_DAILY_LIMIT", "20"))

# Layer B: when the keyword list misses, let the model decide if the question
# needs live data (ChatGPT-style). Skipped for questions that clearly never
# need the web, so the extra classifier call only runs on plausible cases.
WEB_SEARCH_AUTO_DECIDE = os.getenv("WEB_SEARCH_AUTO_DECIDE", "1").strip().lower() not in {"0", "false", "no"}
_NON_WEB_RE = re.compile(
    r"^\s*(สวัสดี|หวัดดี|ดีครับ|ดีค่ะ|ขอบคุณ|โอเค|hello|hi|hey|thanks|thank you)\b|"
    r"เขียน\s*code|เขียนโค้ด|โค้ด|โปรแกรม|function|ฟังก์ชัน|debug|"
    r"แต่งกลอน|แต่งเพลง|เขียนอีเมล|ช่วยร่าง|ช่วยเขียน|สรุปให้|แปลว่า|แปลเป็น|"
    r"สร้างรูป|วาดรูป|ทำโปสเตอร์",
    re.IGNORECASE,
)


def _is_web_search_request(text: str) -> bool:
    """True when the question likely needs fresh/live info from the web.

    Layer A: fast keyword match. Layer B: for anything the keywords miss, ask
    the model (unless it's an obvious non-web request) — so a question like
    "ประชากรโตเกียวเท่าไหร่" gets current data without the user naming a year.
    """
    if not text:
        return False
    if _WEB_SEARCH_INTENT_RE.search(text) is not None:
        return True
    if WEB_SEARCH_AUTO_DECIDE and not _NON_WEB_RE.search(text):
        from llm import wants_web_search

        return wants_web_search(text)
    return False


# Company-specific FACTUAL questions — HR/benefits, specs, prices, policy, SOPs.
# For these, an answer the model produced WITHOUT grounding (source llm/llm-calc)
# gets an honesty note so general knowledge is never mistaken for official
# company data. General questions (sport, people, news, code…) don't match, so
# they stay clean (no disclaimer leak).
_COMPANY_FACT_RE = re.compile(
    r"ลาป่วย|ลากิจ|ลาพักร้อน|วันลา|ลาคลอด|วันหยุด|ลาหยุด|"
    r"โบนัส|เงินเดือน|ค่าจ้าง|ค่าแรง|สวัสดิการ|ประกันสังคม|กองทุนสำรอง|"
    r"เบี้ยขยัน|เบี้ยเลี้ยง|ค่าล่วงเวลา|โอที|ค่ากะ|เงินพิเศษ|"
    r"นโยบาย|ระเบียบบริษัท|ระเบียบข้อบังคับ|ข้อบังคับ|กฎบริษัท|\bsop\b|"
    r"ขั้นตอนการผลิต|ขั้นตอนการทำงาน|กระบวนการผลิต|"
    r"สเปก|สเป็ก|สเปค|รุ่นเครื่อง|เครื่องจักร|เครื่องพิมพ์|กำลังการผลิต|"
    r"ความเร็วเครื่อง|ความเร็วในการพิมพ์|หัวพิมพ์|จำนวนเครื่อง|"
    r"ราคา|ค่าบริการ|ค่าพิมพ์|ใบเสนอราคา|ต้นทุน|ค่าใช้จ่ายในการ|"
    r"ตำแหน่งงาน|สมัครงาน|อัตรากำลัง|จำนวนพนักงาน",
    re.IGNORECASE,
)

# NOT "llm-calc": a calculation where the user supplied the numbers (e.g.
# "500 แผ่น แผ่นละ 2.75 รวมเท่าไหร่") is self-contained arithmetic, not a
# company-fact guess — the note there just confuses people.
_UNGROUNDED_SOURCES = {"llm"}

_COMPANY_FACT_NOTE = (
    "\n\n---\n"
    "> ℹ️ *หมายเหตุ: ข้อมูลด้านบนเป็นความรู้ทั่วไป — ระบบยังไม่มีข้อมูลเฉพาะของบริษัทในเรื่องนี้ "
    "กรุณายืนยันกับฝ่ายที่เกี่ยวข้อง (เช่น HR / หัวหน้างาน / ฝ่ายขาย) ก่อนนำไปใช้อ้างอิงค่ะ*"
)


def _company_fact_note(question: str, source: str) -> str:
    """Return an honesty note for an UNGROUNDED answer to a company-specific
    factual question, else empty. Grounded answers (brain/rag/web/url/files) and
    non-company questions get nothing."""
    if source not in _UNGROUNDED_SOURCES:
        return ""
    if not question or not _COMPANY_FACT_RE.search(question):
        return ""
    return _COMPANY_FACT_NOTE


def _format_citations(citations: list[dict]) -> str:
    """Render url citations as a Markdown source list appended to the answer."""
    if not citations:
        return ""
    lines = ["\n\n**แหล่งอ้างอิง:**"]
    for c in citations[:5]:
        title = (c.get("title") or c.get("url") or "").strip()
        url = c.get("url") or ""
        lines.append(f"- [{title}]({url})")
    return "\n".join(lines)


def _try_web_search_answer(
    user: dict, question: str, session_id: int | None, mode: str, request: Request
) -> dict | None:
    """Handle a live-web-search question, or return None to fall through.

    Returns None when: not a web-search request, company mode, or the search
    failed (so the caller runs the normal brain/KB/LLM pipeline instead).
    On success/quota-hit returns a result dict:
        {answer, source, session_id, session_title, message_id}
    """
    if mode == "company" or not _is_web_search_request(question):
        return None

    from chat_pg import (
        count_web_searches_today_pg,
        ensure_session_pg,
        get_session_history_pg,
        record_web_search_pg,
        save_chat_message_pg,
    )

    used = count_web_searches_today_pg(user["id"])
    if used >= WEB_SEARCH_DAILY_LIMIT:
        sid, stitle = ensure_session_pg(
            user_id=user["id"], session_id=session_id,
            first_question=question, mode=mode,
        )
        text = (
            f"ขออภัยค่ะ วันนี้ค้นเว็บครบ {WEB_SEARCH_DAILY_LIMIT} ครั้งแล้ว "
            "(จำกัดเพื่อคุมค่าใช้จ่าย) กรุณาลองใหม่พรุ่งนี้ค่ะ 🙏"
        )
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source="web_limit", knowledge_id=None, mode=mode,
        )
        return {
            "answer": text, "source": "web_limit", "session_id": sid,
            "session_title": stitle, "message_id": mid,
        }

    history = get_session_history_pg(
        session_id=session_id, user_id=user["id"], limit=HISTORY_TURNS,
    )
    try:
        answer, citations, ws_usage = answer_with_web_search(question, history=history)
    except Exception as e:
        audit_log(
            "web_search_failed",
            user=user,
            detail={"error": str(e)[:300], "prompt": question[:200]},
            request=request,
        )
        return None  # fall through to the normal pipeline

    answer = (answer + _format_citations(citations)).strip()
    usage_row = accumulate_usage(ws_usage, primary_model=WEB_SEARCH_MODEL)
    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=session_id,
        first_question=question, mode=mode,
    )
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question,
        answer=answer, source="web", knowledge_id=None, mode=mode,
        usage=usage_row,
    )
    record_web_search_pg(user["id"], question, ws_usage.get("cost_usd", 0.0))
    audit_log(
        "web_search",
        user=user,
        detail={
            "session_id": sid, "message_id": mid,
            "n_citations": len(citations), "prompt": question[:200],
        },
        request=request,
    )
    return {
        "answer": answer, "source": "web", "session_id": sid,
        "session_title": stitle, "message_id": mid,
    }


def _agent_tools(user: dict, mode: str) -> tuple[bool, bool]:
    """(web, code): the tool groups the agent router may offer this user.
    Agent V1 (web search / read link, AGENT_V1_MODE) and Code Interpreter V2
    (CODE_INTERPRETER_MODE) have separate flags, both off by default. Postgres
    only, never in company-only mode."""
    import agent
    import code_interpreter

    if mode == "company" or not use_postgres_auth():
        return False, False
    return agent.agent_enabled_for(user), code_interpreter.ci_enabled_for(user)


def _doc_enabled(user: dict, mode: str) -> bool:
    """Document Engine V3 (DOCUMENT_ENGINE_MODE, off by default) — its own flag,
    same rules as the others: postgres only, never in company-only mode."""
    import doc_engine

    return mode != "company" and use_postgres_auth() and doc_engine.doc_enabled_for(user)


def _agent_enabled(user: dict, mode: str) -> bool:
    """The agent router runs when Agent V1, Code Interpreter or the Document
    Engine is on for this user."""
    return any(_agent_tools(user, mode)) or _doc_enabled(user, mode)


# ── V4.1 model routing / V4.2 memory & context — both flags off → legacy ────

def _route_for(user: dict, question: str, company_only: bool):
    """V4.1 (MODEL_ROUTING_MODE): model + effort for the normal answer from
    cheap rules, or None when off for this user — the legacy classifier picks."""
    import model_router

    return model_router.pick(question, company=company_only) if model_router.routing_enabled_for(user) else None


def _route_grounded(route):
    """An answer grounded in company knowledge (RAG / AI Brain hit) gets at least the standard tier."""
    import model_router

    return model_router.for_context(route) if route else None


def _effort_kw(route) -> dict:
    # unrouted → the llm call keeps its exact legacy arguments
    return {"effort": route.effort} if route and route.effort else {}


def _route_stats(route, category: str, model: str, answer_usage: dict, started: float,
                 first_token: float | None = None) -> dict:
    """How this answer was routed and how long it took, for the usage log —
    also when routing is off ("legacy"), as the before/after baseline."""
    import model_router
    from llm import LLM_REASONING_EFFORT

    if route is None:
        stats = {"route": "legacy", "route_reason": f"classifier: {category}",
                 "reasoning_effort": model_router.effort_for(model, LLM_REASONING_EFFORT)}
    else:
        stats = {**route.meta(), "reasoning_effort": answer_usage.get("reasoning_effort", route.effort)}
        if answer_usage.get("fallback_from"):
            stats["route_reason"] = (f"{route.reason} | API rejected {answer_usage['fallback_from']} "
                                     f"→ {answer_usage.get('model')}")[:200]
    stats["latency_ms"] = int((time.monotonic() - started) * 1000)
    if first_token is not None:
        stats["first_token_ms"] = int((first_token - started) * 1000)
    return stats


def _turn_history(user: dict, session_id: int | None, legacy: list[dict] | None = None) -> list[dict]:
    """History for the model. V4.2 (MEMORY_MODE): this user's saved memories,
    a summary of the older turns, then the recent turns (earlier ones trimmed).
    Off — or on any error — the legacy history: `legacy` if already loaded,
    else the last HISTORY_TURNS turns."""
    import memory
    from chat_pg import get_session_history_pg

    def old() -> list[dict]:
        if legacy is not None:
            return legacy
        return get_session_history_pg(session_id=session_id, user_id=user["id"], limit=HISTORY_TURNS)

    if not memory.memory_enabled_for(user):
        return old()
    try:
        import context_manager

        mem = memory.prompt_message(user["id"])
        return ([mem] if mem else []) + context_manager.history(session_id, user["id"])
    except Exception as e:  # noqa: BLE001 — memory must never cost an answer
        audit_log("memory_context_failed", user=user, detail={"error": f"{type(e).__name__}: {e}"[:300]})
        return old()


def _after_answer(user: dict, session_id: int | None) -> None:
    """V4.2: fold turns that left the recent window into the session summary —
    in the background, so the reply never waits."""
    import memory

    if session_id and memory.memory_enabled_for(user):
        import context_manager

        context_manager.schedule_summary(session_id, user["id"])


def _memory_reply(user: dict, question: str, session_id: int | None, mode: str,
                  request: Request | None = None) -> dict | None:
    """V4.2: a memory command typed by the user ("จำไว้ว่า…", "ลืมข้อมูลนี้",
    "ดูความจำ" …) is carried out here — no AI call — and saved as a chat turn.
    None → not a memory command (or memory is off for this user)."""
    import memory

    if not (use_postgres_auth() and memory.memory_enabled_for(user)):
        return None
    cmd = memory.parse_command(question)
    if cmd is None or (session_id and _count_session_turns(session_id) >= MAX_TURNS_PER_SESSION):
        return None                       # a full chat gets the usual turn-limit answer
    from chat_pg import ensure_session_pg, get_session_history_pg, save_chat_message_pg

    previous = None
    if cmd.kind == "save" and not cmd.content and session_id:
        # "จำข้อมูลนี้" → the user's previous message (their own text, never a file or web page)
        hist = get_session_history_pg(session_id=session_id, user_id=user["id"], limit=1)
        previous = next((m["content"] for m in hist if m["role"] == "user"), None)
    sid, stitle = ensure_session_pg(user_id=user["id"], session_id=session_id, first_question=question, mode=mode)
    answer = memory.handle(user["id"], cmd, session_id=sid, previous_question=previous)
    mid = save_chat_message_pg(user_id=user["id"], session_id=sid, question=question, answer=answer,
                               source="memory", knowledge_id=None, mode=mode)
    audit_log("memory_command", user=user, detail={"kind": cmd.kind, "session_id": sid}, request=request)
    return {"answer": answer, "source": "memory", "session_id": sid, "session_title": stitle, "message_id": mid}


def _memory_chat_response(r: dict) -> "ChatResponse":
    return ChatResponse(
        answer=r["answer"], source=r["source"], similarity=None, session_id=r["session_id"],
        session_title=r["session_title"], attachments=[], turn_count=_count_session_turns(r["session_id"]),
        turn_limit=MAX_TURNS_PER_SESSION, message_id=r["message_id"],
    )


def _memory_done_event(r: dict) -> dict:
    return {"type": "done", "source": r["source"], "session_id": r["session_id"],
            "session_title": r["session_title"], "message_id": r["message_id"],
            "turn_count": _count_session_turns(r["session_id"]), "turn_limit": MAX_TURNS_PER_SESSION}


def _memory_scoped_reply(user: dict, question: str, session_id: int | None, mode: str,
                         request: Request | None = None) -> dict | None:
    """V4.2: a question about the user themself ("ฉันอยู่ฝ่ายไหน") that the
    safety classifier flagged is answered from THIS user's saved memories only
    — the model gets nothing else, so nothing else can leak (the same idea as
    Code Interpreter's file-scoped turns). None → blocked as before: memory off,
    nothing saved, or not a question about the user."""
    import memory

    if not (use_postgres_auth() and memory.memory_enabled_for(user)) or not memory.about_self(question):
        return None
    if session_id and _count_session_turns(session_id) >= MAX_TURNS_PER_SESSION:
        return None
    msg = memory.prompt_message(user["id"])
    if msg is None:
        return None
    from chat_pg import ensure_session_pg, save_chat_message_pg

    answer, usage = memory.answer_from_memory(question, msg)
    sid, stitle = ensure_session_pg(user_id=user["id"], session_id=session_id, first_question=question, mode=mode)
    mid = save_chat_message_pg(user_id=user["id"], session_id=sid, question=question, answer=answer,
                               source="memory_scoped", knowledge_id=None, mode=mode,
                               usage=accumulate_usage(usage))
    audit_log("memory_scoped_answer", user=user, detail={"session_id": sid, "prompt": question[:200]},
              request=request)
    return {"answer": answer, "source": "memory_scoped", "session_id": sid, "session_title": stitle,
            "message_id": mid}


def _owned_documents(
    user: dict, session_id: int | None, uploads: list[dict] | None = None
) -> list[tuple[str, str]]:
    """Documents the Document Engine may open this turn, as [(filename, path)]:
    this message's uploads, else the newest documents of this session — the
    user's uploads AND files the bot made, so "แก้สไลด์ 3" edits the deck it just
    produced (session, message and file ownership checked in SQL)."""
    import attachments as att
    import code_interpreter
    import doc_engine
    from chat_pg import get_session_documents_pg

    if uploads:
        sources = uploads
    elif session_id:
        sources = get_session_documents_pg(session_id, user["id"])
    else:
        sources = []
    out: list[tuple[str, str]] = []
    for f in sources:
        if not doc_engine.is_doc_input(f["filename"]):
            continue
        try:
            out.append((f["filename"], str(att.validate_upload_path(f["file_path"]))))
        except HTTPException:
            continue  # file purged from disk
    return out[: code_interpreter.CI_MAX_INPUT_FILES]


def _owned_spreadsheets(
    user: dict, session_id: int | None, uploads: list[dict] | None = None
) -> list[tuple[str, str]]:
    """Excel/CSV the sandbox may read this turn, as [(filename, path)]: this
    message's uploads, else the ones this user uploaded earlier in this session
    (session, message and file ownership checked in SQL). Paths are re-checked
    to be inside UPLOAD_DIR; purged files are skipped."""
    import attachments as att
    import code_interpreter
    from chat_pg import get_session_spreadsheets_pg

    if uploads:
        sources = uploads
    elif session_id:
        sources = get_session_spreadsheets_pg(session_id, user["id"])
    else:
        sources = []
    out: list[tuple[str, str]] = []
    for f in sources:
        if not code_interpreter.is_ci_input(f["filename"]):
            continue
        try:
            out.append((f["filename"], str(att.validate_upload_path(f["file_path"]))))
        except HTTPException:
            continue  # file purged from disk
    return out[: code_interpreter.CI_MAX_INPUT_FILES]


# Classifier categories never exempted, even for the user's own file.
_FILE_SCOPE_NEVER = {"CREDENTIALS"}
FILE_SCOPE_FAILED = "ขออภัยค่ะ วิเคราะห์ข้อมูลจากไฟล์ไม่สำเร็จ กรุณาลองถามใหม่อีกครั้ง หรือแนบไฟล์ใหม่ค่ะ"
# Shown instead of the sensitive-data block when a file question passed every
# check but the user has no Code Interpreter runs left today.
CODE_LIMIT_TEXT = "คุณใช้สิทธิ์วิเคราะห์ข้อมูลครบจำนวนครั้งที่กำหนดสำหรับวันนี้แล้ว กรุณาลองใหม่ในวันถัดไป"


def _safety_cost_detail(usages: list[dict]) -> dict:
    """Audit-log fields for the classifier calls of a BLOCKED question. A
    blocked question saves no chat_history row (by design), so its audit entry
    is where that cost is recorded; the dashboard adds it to the total."""
    if not usages:
        return {}
    u = accumulate_usage(*usages)
    return {
        "cost_usd": u["cost_usd"], "model": u["model_used"],
        "prompt_tokens": u["prompt_tokens"], "completion_tokens": u["completion_tokens"],
    }


def _log_blocked_usage(user: dict, usages: list[dict]) -> None:
    """A blocked turn saves no chat row, so its classifier usage goes straight
    to the AI usage log (as its own request)."""
    if not usages or not use_postgres_auth():
        return
    from usage_log import log_usage_pg

    log_usage_pg(user["id"], "safety", "blocked", accumulate_usage(*usages))


def _charge_safety_usage(message_id: int | None, usages: list[dict]) -> None:
    """Add this turn's classifier usage to the row the turn saved — once: the
    list is emptied so a second call can't count it again."""
    if not usages or not message_id or not use_postgres_auth():
        return
    from chat_pg import add_usage_to_message_pg

    usage = accumulate_usage(*usages)
    usages.clear()
    add_usage_to_message_pg(message_id, usage)


def _file_scope_decision(
    user: dict, question: str, session_id: int | None, mode: str, category: str,
    request: Request, uploads: list[dict] | None = None, usages: list[dict] | None = None,
) -> str:
    """The AI classifier flagged `question`. Returns:
        "allowed" — analysis of the user's own spreadsheets: Code Interpreter on
                    for this user, owned Excel/CSV in this message or session,
                    not credentials, a second file-aware check
                    (classify_file_question) agrees, and runs left today
        "quota"   — every one of those checks passed except the daily cap
        "denied"  — anything else: the sensitive-data block stands

    "quota" is decided LAST, so a question only ever sees the quota message
    after passing the same security checks as an allowed one — a truly
    sensitive question gets the sensitive block even with no runs left.

    An approval is narrow: the caller answers with _agent_turn(scoped=True) —
    the Python sandbox over those files only, never KB / AI Brain / web / other
    users' files. The keyword filter has already run and is never relaxed."""
    import code_interpreter
    from chat_pg import count_code_runs_today_pg
    from sensitivity_classifier import classify_file_question

    if category in _FILE_SCOPE_NEVER or not _agent_tools(user, mode)[1]:
        return "denied"
    files = _owned_spreadsheets(user, session_id, uploads)
    if not files:
        return "denied"
    allowed, reason = classify_file_question(
        question, [code_interpreter.outline(name, path) for name, path in files], usages
    )
    detail = {
        "category": category, "reason": reason,
        "files": [name for name, _ in files], "prompt": question[:200],
    }
    if not allowed:
        audit_log("sensitive_file_scope_denied", user=user, detail=detail, request=request)
        return "denied"
    if count_code_runs_today_pg(user["id"]) >= code_interpreter.CI_DAILY_LIMIT:
        audit_log(
            "code_limit_hit", user=user,
            detail={**detail, "limit": code_interpreter.CI_DAILY_LIMIT, "via": "file_scope"},
            request=request,
        )
        return "quota"
    audit_log("sensitive_file_scope_allowed", user=user, detail=detail, request=request)
    return "allowed"


def _code_limit_reply(
    user: dict, question: str, session_id: int | None, mode: str,
    uploads: list[dict] | None = None,
) -> dict:
    """Save and return the daily-quota reply for a file question that passed
    every check (see _file_scope_decision). Saved like the other limit replies
    (image_limit / web_limit) — source "code_limit" does not count toward the
    quota. This turn's uploads stay attached, so the user can ask again in the
    same chat tomorrow without re-uploading."""
    from chat_pg import ensure_session_pg, save_attachment_pg, save_chat_message_pg

    uploads = uploads or []
    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=session_id,
        first_question=question or uploads[0]["filename"], mode=mode,
    )
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question or "[ไฟล์แนบ]",
        answer=CODE_LIMIT_TEXT, source="code_limit", knowledge_id=None, mode=mode,
    )
    rows = [
        save_attachment_pg(
            message_id=mid, user_id=user["id"], filename=u["filename"],
            content_type=u["content_type"], size_bytes=u["size"], file_path=u["file_path"],
        )
        for u in uploads
    ]
    return {
        "answer": CODE_LIMIT_TEXT, "tail": "", "source": "code_limit", "session_id": sid,
        "session_title": stitle, "message_id": mid, "attachments": rows,
    }


def _agent_turn(
    user: dict, question: str, session_id: int | None, mode: str, request: Request,
    uploads: list[dict] | None = None, scoped: bool = False, session_ref: dict | None = None,
):
    """Run the agent router for one question. Replaces the legacy URL-reading and
    web-search steps (the agent decides which tools to use, if any). Yields:
        ("status", state) / ("delta", text) — progress and streamed answer text
        ("done", result)    — answer saved; `result` holds the response fields,
                              incl. `tail` (source list) not yet sent as a delta
                              and `attachments` (uploads + generated files)
        ("standard", info)  — no tool needed: run the normal pipeline, adding
                              info["usage"] to its cost; the legacy URL/web steps
                              run only if info["legacy_tools"]
        ("fallback", None)  — failed before any text: run the legacy pipeline
        ("error", None)     — failed after text was already streamed
    `uploads` are this message's saved files (dicts from the /chat files path):
    their Excel/CSV go into the Python sandbox and, on "done", all are saved
    with the answer. With uploads only run_python is offered.
    `scoped=True` (a flagged question approved by _file_scope_decision): only
    run_python over the owned spreadsheets, no standard route, and anything but
    a successful sandbox answer ends as "fallback" with no text sent.
    `session_ref`: /chat/stream creates a new chat when the first answer text
    goes out (so a stopped answer has a chat to be saved in) and puts its id in
    ["id"] — the finished answer is saved there too.
    """
    import agent
    import attachments as att
    import code_interpreter
    from chat_pg import (
        count_code_runs_today_pg,
        count_web_searches_today_pg,
        ensure_session_pg,
        record_web_search_pg,
        save_attachment_pg,
        save_chat_message_pg,
    )

    uploads = uploads or []
    web, code = _agent_tools(user, mode)
    doc = _doc_enabled(user, mode) and not scoped
    web = web and not uploads and not scoped
    if (code or doc) and count_code_runs_today_pg(user["id"]) >= code_interpreter.CI_DAILY_LIMIT:
        # daily cap (shared by Code Interpreter and Document Engine) reached —
        # the normal pipeline still answers
        code = doc = False
        audit_log("code_limit_hit", user=user, detail={"limit": code_interpreter.CI_DAILY_LIMIT}, request=request)
    if not (web or code or doc):
        yield "fallback", None
        return

    # Files for the sandbox: this message's uploads, or — for a text follow-up —
    # the ones earlier in this session (run_python: the user's spreadsheets;
    # make_document: documents, including the ones the bot made).
    file_note = ""
    input_files = _owned_spreadsheets(user, session_id, uploads) if code else []
    doc_files = _owned_documents(user, session_id, uploads) if doc else []
    if uploads:
        file_note = "\n\n[ผู้ใช้แนบไฟล์: " + ", ".join(u["filename"] for u in uploads) + "]"
    elif doc_files:
        file_note = "\n\n[ไฟล์ในแชทนี้ (ล่าสุดก่อน): " + ", ".join(n for n, _ in doc_files) + "]"
    elif input_files:
        file_note = "\n\n[ไฟล์ Excel/CSV ที่แนบไว้ก่อนหน้าในแชทนี้: " + ", ".join(n for n, _ in input_files) + "]"
    if code:
        if scoped:
            file_note += (
                "\n[ตอบจากข้อมูลในไฟล์ที่แนบเท่านั้น — ถ้าคำถามต้องใช้ข้อมูลที่ไม่อยู่ในไฟล์ "
                "ให้บอกว่าไม่มีข้อมูลนั้นในไฟล์]"
            )

    started = time.monotonic()
    history = _turn_history(user, session_id)
    quota_left = max(0, WEB_SEARCH_DAILY_LIMIT - count_web_searches_today_pg(user["id"])) if web else 0
    prompt = question or "ช่วยวิเคราะห์ไฟล์ที่แนบ"
    emitted = False
    result = None
    try:
        for kind, payload in agent.run_agent(
            prompt, history, web_quota_left=quota_left,
            on_web_search=lambda q, cost: record_web_search_pg(user["id"], q, cost),
            web_enabled=web, ci_enabled=code, input_files=input_files, file_note=file_note,
            allow_standard=not scoped, doc_enabled=doc, doc_files=doc_files,
        ):
            if kind == "done":
                result = payload
                break
            if kind == "standard":
                yield "standard", {"usage": payload, "legacy_tools": not web}
                return
            if kind == "delta" and scoped:
                continue  # sent below, only if the sandbox really answered
            emitted = emitted or kind == "delta"
            yield kind, payload
    except Exception as e:
        audit_log(
            "agent_failed",
            user=user,
            detail={
                "error_type": e.__class__.__name__, "error": str(e)[:300],
                "after_text": emitted, "prompt": question[:200],
            },
            request=request,
        )
        yield ("error" if emitted else "fallback"), None
        return

    asked = any(t["tool"] == "ask_user" for t in result.tools)   # a question back is an answer
    if (uploads or scoped) and not (result.used_code or result.used_document or asked):
        # The sandbox failed on the attached file — the normal file answer
        # (extracted text) beats an apology; a scoped question must not get a
        # model-only answer at all. Uploads only come from the non-stream path
        # and scoped deltas are held back, so no text has reached the client.
        audit_log(
            "code_failed_fallback", user=user,
            detail={"tools": result.tools[:10], "scoped": scoped, "prompt": question[:200]},
            request=request,
        )
        yield "fallback", None
        return
    if scoped:
        yield "delta", result.answer

    tail = _format_citations(result.citations)
    answer = (result.answer + tail).strip()
    # "code" / "document" also feed the shared daily cap (count_code_runs_today_pg).
    source = "document" if result.used_document else "code" if result.used_code else "agent"
    sandbox_used = result.used_document or result.used_code
    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=(session_ref or {}).get("id") or session_id,
        first_question=question or uploads[0]["filename"], mode=mode,
    )
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question or "[ไฟล์แนบ]",
        answer=answer, source=source, knowledge_id=None, mode=mode,
        usage=accumulate_usage(
            *result.usage,
            primary_model=code_interpreter.CI_MODEL if sandbox_used else agent.AGENT_MODEL,
        ),
    )
    rows = [
        save_attachment_pg(
            message_id=mid, user_id=user["id"], filename=u["filename"],
            content_type=u["content_type"], size_bytes=u["size"], file_path=u["file_path"],
        )
        for u in uploads
    ]
    # Sandbox outputs are stored like uploads, so they're only reachable through
    # the owner-checked /attachments/{id} route — never an OpenAI link.
    for f in result.files:
        display, ctype, size, path = att.save_generated_file(f.data, f.name, f.content_type)
        rows.append(save_attachment_pg(
            message_id=mid, user_id=user["id"], filename=display,
            content_type=ctype, size_bytes=size, file_path=path, generated=True,
        ))
    audit_log(
        "agent_answer",
        user=user,
        detail={
            "session_id": sid, "message_id": mid, "steps": result.steps,
            "tools": result.tools[:10], "n_citations": len(result.citations),
            "used_code": result.used_code, "used_document": result.used_document,
            "n_files": len(result.files), "n_inputs": len(input_files) + len(doc_files),
            "ms": int((time.monotonic() - started) * 1000), "prompt": question[:200],
        },
        request=request,
    )
    _after_answer(user, sid)
    yield "done", {
        "answer": answer, "tail": tail, "source": source, "session_id": sid,
        "session_title": stitle, "message_id": mid, "attachments": rows,
    }


def _agent_done_event(payload: dict) -> dict:
    """/chat/stream `done` event for a finished agent turn (the `done` payload)."""
    evt = {
        "type": "done", "source": payload["source"],
        "session_id": payload["session_id"],
        "session_title": payload["session_title"],
        "message_id": payload["message_id"],
        "turn_count": _count_session_turns(payload["session_id"]),
        "turn_limit": MAX_TURNS_PER_SESSION,
    }
    if payload["attachments"]:
        evt["attachments"] = payload["attachments"]
    return evt


def _agent_chat_response(payload: dict) -> "ChatResponse":
    """/chat response for a finished agent turn (the `done` payload)."""
    return ChatResponse(
        answer=payload["answer"],
        source=payload["source"],
        similarity=None,
        session_id=payload["session_id"],
        session_title=payload["session_title"],
        message_id=payload["message_id"],
        attachments=payload["attachments"],
        turn_count=_count_session_turns(payload["session_id"]),
        turn_limit=MAX_TURNS_PER_SESSION,
    )


def _try_url_answer(
    user: dict, question: str, session_id: int | None, mode: str, request: Request
) -> dict | None:
    """Read the web page(s) the user pasted a link to and answer about them.

    Different from web *search*: here the user gave the exact URL, so we fetch
    that page, extract its text, and answer with it as context. Returns None
    when there is no URL in the question (→ normal pipeline). If a URL IS present
    but none could be read, returns a polite explanation instead of silently
    hallucinating an answer about a page we never saw.
    """
    from url_reader import extract_urls, fetch_url_text

    urls = extract_urls(question)
    if not urls:
        return None

    from chat_pg import (
        ensure_session_pg,
        get_session_history_pg,
        save_chat_message_pg,
    )

    pages: list[tuple[str, str]] = []
    failed: list[str] = []
    for u in urls:
        got = fetch_url_text(u)
        if got:
            pages.append(got)
        else:
            failed.append(u)

    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=session_id,
        first_question=question, mode=mode,
    )

    if not pages:
        text = (
            "ขออภัยค่ะ อ่านเนื้อหาจากลิงก์ที่ส่งมาไม่สำเร็จ 🙏 "
            "(อาจเป็นหน้าที่ต้องล็อกอิน เว็บบล็อกบอท หรือเป็นไฟล์/รูปที่ไม่ใช่ข้อความ) "
            "ถ้าเป็นไฟล์ ลองแนบไฟล์เข้ามาโดยตรงได้เลยค่ะ"
        )
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source="url_error", knowledge_id=None, mode=mode,
        )
        audit_log(
            "url_read_failed",
            user=user,
            detail={"session_id": sid, "urls": urls},
            request=request,
        )
        return {
            "answer": text, "source": "url_error", "session_id": sid,
            "session_title": stitle, "message_id": mid,
        }

    history = get_session_history_pg(
        session_id=session_id, user_id=user["id"], limit=HISTORY_TURNS,
    )
    prompt_question = (
        question
        or "ช่วยสรุปเนื้อหาจากลิงก์ที่แนบ และให้ข้อมูลที่เกี่ยวข้อง"
    )
    try:
        answer, files_usage = answer_with_files(
            prompt_question, [], pages, history=history,
        )
    except Exception as e:
        audit_log(
            "url_read_failed",
            user=user,
            detail={"session_id": sid, "urls": urls, "error": str(e)[:300]},
            request=request,
        )
        return None  # fall through to the normal pipeline

    if failed:
        answer += (
            "\n\n_หมายเหตุ: อ่านบางลิงก์ไม่สำเร็จ ("
            + ", ".join(failed)
            + ")_"
        )
    usage_row = accumulate_usage(files_usage)
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question,
        answer=answer, source="url", knowledge_id=None, mode=mode,
        usage=usage_row,
    )
    audit_log(
        "url_read",
        user=user,
        detail={
            "session_id": sid, "message_id": mid,
            "n_pages": len(pages), "n_failed": len(failed), "urls": urls,
        },
        request=request,
    )
    return {
        "answer": answer, "source": "url", "session_id": sid,
        "session_title": stitle, "message_id": mid,
    }


def _session_job_no(session_id: int | None, user_id: int) -> str | None:
    """Most recent job number the user mentioned in this session, else None —
    so follow-ups ('AE คือใคร', 'ใช้กาวอะไร') can reuse it without retyping."""
    if not session_id:
        return None
    try:
        from chat_pg import get_session_history_pg
        from linsip import extract_job_no

        hist = get_session_history_pg(
            session_id=session_id, user_id=user_id, limit=HISTORY_TURNS,
        )
    except Exception:
        return None
    for m in reversed(hist or []):
        if m.get("role") == "user":
            j = extract_job_no(m.get("content") or "")
            if j:
                return j
    return None


def _resolve_linsip_job(
    question: str, session_id: int | None, user_id: int
) -> str | None:
    """The job this question is about: the number in the question, or — for a
    job follow-up with no number — the most recent one from the conversation."""
    try:
        from linsip import LINSIP_ENABLED, extract_job_no, looks_like_job_followup
    except Exception:
        return None
    if not LINSIP_ENABLED or not question:
        return None
    j = extract_job_no(question)
    if j:
        return j
    if looks_like_job_followup(question):
        return _session_job_no(session_id, user_id)
    return None


def _is_linsip_query(
    question: str, session_id: int | None = None, user: dict | None = None
) -> bool:
    """True when the question is a job lookup (direct or a follow-up). Such
    answers come ONLY from that job's WI page (authorized operational data), so
    we skip the sensitivity CLASSIFIER for them — otherwise legit job fields like
    the customer or AE name get blocked. The hard keyword filter still runs."""
    uid = user["id"] if user else None
    if uid is None:
        # Without a user we can't resolve session history — fall back to the
        # question alone.
        try:
            from linsip import LINSIP_ENABLED, extract_job_no
        except Exception:
            return False
        return bool(LINSIP_ENABLED and question and extract_job_no(question))
    return _resolve_linsip_job(question, session_id, uid) is not None


def _try_linsip_answer(
    user: dict, question: str, session_id: int | None, mode: str, request: Request
) -> dict | None:
    """Answer about a job: fetch that job's linsip WI page and answer from it.
    The job number comes from the question, or — for a follow-up with no number
    ('AE คือใคร') — the most recent one in the conversation. Returns None when
    linsip is off or no job is in scope. If a job IS in scope but the WI can't be
    fetched, returns a clear message rather than a guessed answer."""
    from linsip import fetch_job_wi

    job = _resolve_linsip_job(question, session_id, user["id"])
    if not job:
        return None

    from chat_pg import (
        ensure_session_pg,
        get_session_history_pg,
        save_chat_message_pg,
    )

    sid, stitle = ensure_session_pg(
        user_id=user["id"], session_id=session_id,
        first_question=question, mode=mode,
    )
    got = fetch_job_wi(job)

    if not got:
        text = (
            f"ขออภัยค่ะ ดึงข้อมูลงาน {job} จากระบบ linsip ไม่สำเร็จ "
            "(อาจไม่พบเลขงานนี้ หรือเปิดหน้าเอกสารไม่ได้ชั่วคราว) "
            "ลองตรวจสอบเลขงานอีกครั้ง หรือเปิดดูในระบบ linsip โดยตรงค่ะ"
        )
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text, source="linsip_error", knowledge_id=None, mode=mode,
        )
        audit_log(
            "linsip_lookup_failed",
            user=user,
            detail={"session_id": sid, "job": job},
            request=request,
        )
        return {
            "answer": text, "source": "linsip_error", "session_id": sid,
            "session_title": stitle, "message_id": mid,
        }

    history = get_session_history_pg(
        session_id=session_id, user_id=user["id"], limit=HISTORY_TURNS,
    )
    prompt_question = question or f"สรุปข้อมูลงาน {job}"
    try:
        from llm import SYSTEM_PROMPT_LINSIP

        answer, files_usage = answer_with_files(
            prompt_question, [], [got], history=history,
            system_prompt=SYSTEM_PROMPT_LINSIP,
        )
    except Exception as e:
        audit_log(
            "linsip_lookup_failed",
            user=user,
            detail={"session_id": sid, "job": job, "error": str(e)[:300]},
            request=request,
        )
        return None  # fall through to the normal pipeline

    usage_row = accumulate_usage(files_usage)
    mid = save_chat_message_pg(
        user_id=user["id"], session_id=sid, question=question,
        answer=answer, source="linsip", knowledge_id=None, mode=mode,
        usage=usage_row,
    )
    audit_log(
        "linsip_lookup",
        user=user,
        detail={"session_id": sid, "message_id": mid, "job": job},
        request=request,
    )
    return {
        "answer": answer, "source": "linsip", "session_id": sid,
        "session_title": stitle, "message_id": mid,
    }


def _get_session_history(conn, session_id: int | None, user_id: int) -> list[dict]:
    if not session_id:
        return []

    rows = conn.execute(
        "SELECT id, question, answer FROM chat_history "
        "WHERE session_id = ? AND user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (session_id, user_id, HISTORY_TURNS),
    ).fetchall()

    history: list[dict] = []

    for r in reversed(rows):
        atts = conn.execute(
            "SELECT filename, content_type, extracted_text FROM attachments "
            "WHERE message_id = ?",
            (r["id"],),
        ).fetchall()

        user_content = r["question"]

        if atts:
            parts: list[str] = []
            for a in atts:
                if a["extracted_text"]:
                    parts.append(
                        f"[ผู้ใช้แนบไฟล์ {a['filename']} เนื้อหา:\n{a['extracted_text']}\n]"
                    )
                else:
                    parts.append(f"[ผู้ใช้แนบไฟล์ {a['filename']} ({a['content_type']})]")

            user_content = "\n".join(parts) + "\n\n" + user_content

        history.append({"role": "user", "content": user_content})
        history.append({"role": "assistant", "content": r["answer"]})

    return history


def _ensure_session(
    conn,
    user_id: int,
    session_id: int | None,
    first_question: str,
    mode: str = "normal",
) -> tuple[int, str]:
    if session_id is not None:
        row = conn.execute(
            "SELECT id, title FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if row:
            return row["id"], row["title"]

    title = _title_from_question(first_question)
    cur = conn.execute(
        "INSERT INTO chat_sessions (user_id, title, mode) VALUES (?, ?, ?)",
        (user_id, title, mode),
    )
    return cur.lastrowid, title


def _safe_unlink_attachment(file_path: str) -> bool:
    """
    Delete uploaded attachment only if it is inside UPLOAD_DIR.

    This protects against accidental deletion of files outside upload storage
    if DB data is corrupted or tampered with.
    """
    try:
        safe_path = att.validate_upload_path(file_path)
        safe_path.unlink(missing_ok=True)
        return True
    except HTTPException:
        return False
    except OSError:
        return False


@app.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    message: str = Form(...),
    session_id: int | None = Form(None),
    mode: str = Form("normal"),
    files: list[UploadFile] = File([]),
    user: dict = Depends(current_user),
):
    # The safety classifiers run before any answer exists. Their usage is
    # collected here and added once to whichever row the turn saved (a blocked
    # turn saves none — its audit entry carries the cost instead).
    safety_usages: list[dict] = []
    res = await _chat_turn(request, message, session_id, mode, files, user, safety_usages)
    if safety_usages and res.message_id:
        await run_in_threadpool(_charge_safety_usage, res.message_id, safety_usages)
    return res


async def _chat_turn(
    request: Request,
    message: str,
    session_id: int | None,
    mode: str,
    files: list[UploadFile],
    user: dict,
    safety_usages: list[dict],
) -> ChatResponse:
    question = message.strip()
    company_only = mode == "company"

    if not question and not files:
        raise HTTPException(400, "empty message")

    # Pre-filter: block sensitive questions before any upload / RAG / LLM call.
    # Saves OpenAI tokens and prevents the chatbot from ever attempting to answer.
    #
    # Two layers — keyword filter catches the obvious cases instantly and for free;
    # LLM classifier catches paraphrased / novel attempts that don't trip any
    # keyword (e.g. "ข้อมูลส่วนตัวของ X", "พี่ B รายได้กี่หมื่น").
    matched_kw = is_sensitive(question)
    if matched_kw is not None:
        audit_log(
            "sensitive_blocked",
            user=user,
            detail={
                "matched_keyword": matched_kw,
                "message_length": len(question),
                "had_files": bool(files),
            },
            request=request,
        )
        return ChatResponse(
            answer=BLOCKED_RESPONSE,
            source="blocked",
            similarity=None,
            session_id=session_id or 0,
            session_title="",
            attachments=[],
        )

    # V4.2 memory command ("จำไว้ว่า…" / "ลืมข้อมูลนี้" / "ดูความจำ") — the
    # user's own typed text (never with files), carried out without an AI call,
    # so before the AI classifier; it has its own refusal of sensitive data.
    if question and not files:
        mr = await run_in_threadpool(_memory_reply, user, question, session_id, mode, request)
        if mr is not None:
            return _memory_chat_response(mr)

    # A classifier flag on analysis of the user's OWN spreadsheets doesn't block:
    # the turn becomes file-scoped (sandbox over those files only — see
    # _file_scope_decision), or gets the daily-quota reply when no runs are
    # left. For uploads that's decided once they're saved (`pending_scope`);
    # for follow-ups, from the session's files now.
    import code_interpreter

    file_scoped = False
    code_limited = False
    pending_scope: str | None = None
    if question and not _is_linsip_query(question, session_id, user):
        classification = classify_sensitivity(question, safety_usages)
        if classification is not None:
            category, reason = classification
            decision = "denied"
            if files and any(code_interpreter.is_ci_input(f.filename or "") for f in files) \
                    and _agent_tools(user, mode)[1]:
                pending_scope = category
                decision = "pending"
            elif not files:
                decision = await run_in_threadpool(
                    _file_scope_decision, user, question, session_id, mode, category, request,
                    usages=safety_usages,
                )
            file_scoped = decision == "allowed"
            code_limited = decision == "quota"
            if decision == "denied" and not files:
                # V4.2: about the user themself → from their own memories only
                ms = await run_in_threadpool(_memory_scoped_reply, user, question, session_id, mode, request)
                if ms is not None:
                    return _memory_chat_response(ms)
            if decision == "denied":
                audit_log(
                    "sensitive_blocked_classifier",
                    user=user,
                    detail={
                        "category": category,
                        "reason": reason,
                        "message_length": len(question),
                        "had_files": bool(files),
                        **_safety_cost_detail(safety_usages),
                    },
                    request=request,
                )
                _log_blocked_usage(user, safety_usages)
                return ChatResponse(
                    answer=BLOCKED_RESPONSE,
                    source="blocked",
                    similarity=None,
                    session_id=session_id or 0,
                    session_title="",
                    attachments=[],
                )

    # Enforce per-session turn cap. Export-intent messages are allowed even
    # at the limit so the user can still save their work, then start a fresh
    # chat. Counted on session_id supplied by the client — a brand-new chat
    # (session_id=None) is never blocked.
    if session_id is not None and question and not _is_export_request(question):
        used = _count_session_turns(session_id)
        if used >= MAX_TURNS_PER_SESSION:
            audit_log(
                "session_turn_limit_hit",
                user=user,
                detail={
                    "session_id": session_id,
                    "used": used,
                    "limit": MAX_TURNS_PER_SESSION,
                },
                request=request,
            )
            raise HTTPException(
                429,
                f"แชทนี้ครบ {MAX_TURNS_PER_SESSION} คำถามแล้ว — กรุณาเปิด 'แชทใหม่' "
                "เพื่อถามต่อค่ะ (ช่วยให้ระบบเร็วและประหยัด token)",
            )

    # File question that passed every check but has no Code Interpreter runs
    # left today — say so (not the sensitive-data block); nothing else runs.
    if code_limited:
        return _agent_chat_response(
            await run_in_threadpool(_code_limit_reply, user, question, session_id, mode)
        )

    # File-scoped follow-up (approved above): only the sandbox over the user's
    # own spreadsheets may answer it; nothing else in the pipeline runs.
    if file_scoped:
        events = await run_in_threadpool(
            lambda: list(_agent_turn(user, question, session_id, mode, request, scoped=True))
        )
        for kind, payload in events:
            if kind == "done":
                return _agent_chat_response(payload)
        return ChatResponse(
            answer=FILE_SCOPE_FAILED,
            source="code_error",
            similarity=None,
            session_id=session_id or 0,
            session_title="",
            attachments=[],
        )

    # Intent: user is asking to export the previous answer as PDF.
    # Short-circuit before RAG/LLM so we don't waste tokens; the frontend
    # renders the export controls when it sees source="export_offer".
    if question and not files and _is_export_request(question):
        audit_log(
            "export_intent_detected",
            user=user,
            detail={"message": question[:200]},
            request=request,
        )
        # We still create a session row + save the offer message so the
        # interaction shows up in chat history with a proper id.
        offer_text = (
            "ได้ค่ะ! กดปุ่มด้านล่างเพื่อบันทึกคำตอบล่าสุดเป็น PDF — "
            "หรือดาวน์โหลดเอกสารต้นฉบับ (ถ้ามี) ก็ได้ค่ะ"
        )
        offer_mid: int | None = None
        if use_postgres_auth():
            from chat_pg import ensure_session_pg, save_chat_message_pg

            sid, stitle = ensure_session_pg(
                user_id=user["id"],
                session_id=session_id,
                first_question=question,
                mode=mode,
            )
            offer_mid = save_chat_message_pg(
                user_id=user["id"],
                session_id=sid,
                question=question,
                answer=offer_text,
                source="export_offer",
                knowledge_id=None,
                mode=mode,
            )
        else:
            conn = get_db()
            sid, stitle = _ensure_session(
                conn, user["id"], session_id, question, mode=mode
            )
            conn.execute(
                "INSERT INTO chat_history "
                "(user_id, session_id, question, answer, source, knowledge_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], sid, question, offer_text, "export_offer", None),
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = datetime('now'), mode = ? "
                "WHERE id = ?",
                (mode, sid),
            )
            conn.commit()

        return ChatResponse(
            answer=offer_text,
            source="export_offer",
            similarity=None,
            session_id=sid,
            session_title=stitle,
            attachments=[],
            message_id=offer_mid,
        )

    # Image EDIT intent (postgres only — mirrors /chat/stream).
    if (
        question and not files and use_postgres_auth() and session_id
        and _is_image_edit_request(question) and not _is_image_request(question)
    ):
        from chat_pg import get_last_image_attachment_pg

        last_img = get_last_image_attachment_pg(session_id)
        if last_img:
            res = _handle_image_edit(user, question, session_id, mode, last_img)
            return ChatResponse(
                answer=res["answer"],
                source=res["source"],
                similarity=None,
                session_id=res["session_id"],
                session_title=res["session_title"],
                message_id=res["message_id"],
                attachments=[res["attachment"]] if res["attachment"] else [],
                turn_count=_count_session_turns(res["session_id"]),
                turn_limit=MAX_TURNS_PER_SESSION,
            )

    # Image generation intent (postgres only — mirrors /chat/stream). Handled
    # here too so the streaming client's non-stream fallback still works.
    if question and not files and use_postgres_auth() and _is_image_request(question):
        audit_log(
            "image_generation_requested",
            user=user,
            detail={"prompt": question[:300], "via": "chat"},
            request=request,
        )
        res = _handle_image_generation(user, question, session_id, mode)
        return ChatResponse(
            answer=res["answer"],
            source=res["source"],
            similarity=None,
            session_id=res["session_id"],
            session_title=res["session_title"],
            message_id=res["message_id"],
            attachments=[res["attachment"]] if res["attachment"] else [],
            turn_count=_count_session_turns(res["session_id"]),
            turn_limit=MAX_TURNS_PER_SESSION,
        )

    # linsip job lookup — the user mentioned a job number (e.g. J12600091).
    # Fetch that job's WI page and answer from it. Runs before URL/web search.
    if question and not files and use_postgres_auth():
        li = _try_linsip_answer(user, question, session_id, mode, request)
        if li is not None:
            return ChatResponse(
                answer=li["answer"],
                source=li["source"],
                similarity=None,
                session_id=li["session_id"],
                session_title=li["session_title"],
                message_id=li["message_id"],
                turn_count=_count_session_turns(li["session_id"]),
                turn_limit=MAX_TURNS_PER_SESSION,
            )

    # Agent router (AGENT_V1_MODE / CODE_INTERPRETER_MODE flags) — the model
    # decides whether to search the web / read links / run Python, over several
    # steps, replacing the two legacy steps below when web tools are on. If it
    # needs no tool, the normal pipeline runs. Off the event loop: a code run
    # takes 10-60s and must not stall other users' requests.
    legacy_tools = True
    agent_router_usage: dict = {}
    if question and not files and _agent_enabled(user, mode):
        events = await run_in_threadpool(
            lambda: list(_agent_turn(user, question, session_id, mode, request))
        )
        for kind, payload in events:
            if kind == "done":
                return _agent_chat_response(payload)
            if kind == "standard":
                legacy_tools = payload["legacy_tools"]
                agent_router_usage = payload["usage"]
            # "fallback"/"error": nothing has reached the client on this
            # non-stream path, so the legacy pipeline below simply runs.

    # URL reading — the user pasted a link; fetch that page and answer about it.
    # Runs BEFORE web search so an explicit link is read directly rather than
    # re-searched. Returns None (no URL) to fall through.
    if question and not files and use_postgres_auth() and legacy_tools:
        ur = _try_url_answer(user, question, session_id, mode, request)
        if ur is not None:
            return ChatResponse(
                answer=ur["answer"],
                source=ur["source"],
                similarity=None,
                session_id=ur["session_id"],
                session_title=ur["session_title"],
                message_id=ur["message_id"],
                turn_count=_count_session_turns(ur["session_id"]),
                turn_limit=MAX_TURNS_PER_SESSION,
            )

    # Live web search intent (postgres only — mirrors /chat/stream). Returns
    # None to fall through to the normal pipeline on non-match / failure.
    if question and not files and use_postgres_auth() and legacy_tools:
        ws = _try_web_search_answer(user, question, session_id, mode, request)
        if ws is not None:
            return ChatResponse(
                answer=ws["answer"],
                source=ws["source"],
                similarity=None,
                session_id=ws["session_id"],
                session_title=ws["session_title"],
                message_id=ws["message_id"],
                turn_count=_count_session_turns(ws["session_id"]),
                turn_limit=MAX_TURNS_PER_SESSION,
            )

    saved_files: list[dict] = []
    # Initialised here so it's defined regardless of which branch (files vs RAG
    # vs LLM-only) runs below. Only the RAG-hit path sets a real value.
    knowledge_id: int | None = None
    source_file_hit: str | None = None

    try:
        for f in files or []:
            if not f.filename:
                continue

            filename, content_type, size, file_path = await att.save_upload(f)

            saved_files.append(
                {
                    "filename": filename,
                    "content_type": content_type,
                    "size": size,
                    "file_path": file_path,
                }
            )

            audit_log(
                "file_uploaded",
                user=user,
                detail={
                    "filename": filename,
                    "content_type": content_type,
                    "size": size,
                },
                request=request,
            )

        # Flagged question on an attached spreadsheet: allowed only as analysis
        # of that file (decided now that its sheets/headers can be read).
        if pending_scope is not None:
            decision = await run_in_threadpool(
                _file_scope_decision, user, question, session_id, mode, pending_scope, request, saved_files,
                usages=safety_usages,
            )
            if decision == "quota":
                return _agent_chat_response(await run_in_threadpool(
                    _code_limit_reply, user, question, session_id, mode, saved_files,
                ))
            if decision != "allowed":
                for sf in saved_files:  # blocked → keep nothing of this upload
                    try:
                        os.remove(sf["file_path"])
                    except OSError:
                        pass
                audit_log(
                    "sensitive_blocked_classifier",
                    user=user,
                    detail={
                        "category": pending_scope, "message_length": len(question), "had_files": True,
                        **_safety_cost_detail(safety_usages),
                    },
                    request=request,
                )
                _log_blocked_usage(user, safety_usages)
                return ChatResponse(
                    answer=BLOCKED_RESPONSE,
                    source="blocked",
                    similarity=None,
                    session_id=session_id or 0,
                    session_title="",
                    attachments=[],
                )
            file_scoped = True

        if use_postgres_auth():
            from chat_pg import (
                ensure_session_pg,
                get_session_history_pg,
                save_attachment_pg,
                save_chat_message_pg,
            )

            sid, stitle = ensure_session_pg(
                user_id=user["id"],
                session_id=session_id,
                first_question=question or saved_files[0]["filename"],
                mode=mode,
            )

            history = get_session_history_pg(
                session_id=session_id,
                user_id=user["id"],
                limit=HISTORY_TURNS,
            )

            if saved_files:
                # Reference-image generation: an image is attached AND the user
                # wants a new/edited image FROM it ("สร้างรถจากโลโก้นี้",
                # "เพิ่มแมวอีกตัวในรูป") → run the image edit on the uploaded
                # image instead of just describing it. Pure "อธิบาย/สรุปรูปนี้"
                # (no create verb) still goes to the vision path below.
                # A logo + a design request ("ทำโปสเตอร์ … ใส่โลโก้ตามไฟล์") makes a
                # NEW design carrying that logo instead of editing the logo itself.
                # A picture sent WITH a Word/Excel/PowerPoint/PDF file (Document
                # Engine on) is material for that document, not an image to edit.
                import doc_engine

                _doc_upload = _doc_enabled(user, mode) and any(
                    doc_engine.is_doc_file(sf["filename"]) for sf in saved_files)
                _ref_images = [] if _doc_upload else [
                    sf for sf in saved_files if att.is_image(sf["content_type"])]
                _logo_design = bool(_ref_images) and _wants_logo_on_new_design(question)
                if _logo_design or (_ref_images and _wants_image_from_reference(question)):
                    if _logo_design:
                        res = _handle_image_generation(
                            user, question, sid, mode,
                            logo_path=_ref_images[0]["file_path"],
                        )
                    else:
                        res = _handle_image_edit(
                            user, question, sid, mode,
                            {"file_path": _ref_images[0]["file_path"]},
                        )
                    return ChatResponse(
                        answer=res["answer"],
                        source=res["source"],
                        similarity=None,
                        session_id=res["session_id"],
                        session_title=res["session_title"],
                        message_id=res["message_id"],
                        attachments=[res["attachment"]] if res["attachment"] else [],
                        turn_count=_count_session_turns(res["session_id"]),
                        turn_limit=MAX_TURNS_PER_SESSION,
                    )

                # Code Interpreter (CODE_INTERPRETER_MODE) / Document Engine
                # (DOCUMENT_ENGINE_MODE): an Excel/CSV — or, with the Document
                # Engine, a Word/PowerPoint/PDF — is attached → the router picks
                # run_python / make_document, or hands back to the normal file
                # answer below ("สรุปไฟล์นี้"). Image-only uploads and proofreading
                # never reach this branch. A file-scoped question may only fall
                # back to the answer about these files.
                _ci_upload = _agent_tools(user, mode)[1] and any(
                    code_interpreter.is_ci_input(sf["filename"]) for sf in saved_files)
                if not _is_proofread_request(question) and (_ci_upload or _doc_upload):
                    events = await run_in_threadpool(
                        lambda: list(_agent_turn(
                            user, question, sid, mode, request, uploads=saved_files, scoped=file_scoped,
                        ))
                    )
                    for kind, payload in events:
                        if kind == "done":
                            return _agent_chat_response(payload)
                        if kind == "standard":
                            agent_router_usage = payload["usage"]

                image_urls: list[str] = []
                text_attachments: list[tuple[str, str]] = []
                truncation_notes: list[str] = []
                total_text_chars = 0

                for sf in saved_files:
                    if att.is_image(sf["content_type"]):
                        image_urls.append(
                            att.encode_image_data_url(sf["file_path"], sf["content_type"])
                        )
                        sf["extracted_text"] = None
                        continue

                    # Audio → transcribe (Whisper) → treat the transcript as the
                    # file's text so the model can summarise/answer about it.
                    if att.is_audio(sf["content_type"], sf["filename"]):
                        from llm import transcribe_audio

                        transcript = transcribe_audio(sf["file_path"])
                        text = (
                            f"[ถอดเสียงจากไฟล์เสียง: {sf['filename']}]\n{transcript}"
                            if transcript
                            else ""
                        )
                        sf["extracted_text"] = text or None
                        total_text_chars += len(text)
                        if text:
                            text_attachments.append((sf["filename"], text))
                        continue

                    extracted = att.extract_any_text(
                        sf["file_path"], sf["content_type"], sf["filename"]
                    )

                    sf["extracted_text"] = extracted if extracted.strip() else None
                    total_text_chars += len(extracted)

                    if att.is_pdf(sf["content_type"]) and att.is_text_sparse(extracted):
                        rendered = att.render_pdf_pages_as_images(sf["file_path"])
                        image_urls.extend(rendered)

                        note = att.pdf_truncation_note(sf["filename"], sf["file_path"])
                        if note:
                            truncation_notes.append(note)

                        if extracted.strip():
                            text_attachments.append((sf["filename"], extracted))
                    elif extracted.strip():
                        text_attachments.append((sf["filename"], extracted))

                prompt_question = question or "ช่วยอธิบายเนื้อหาในไฟล์ที่แนบ และให้คำแนะนำที่เกี่ยวข้อง"
                # Off the event loop: proofreading takes 10-40s, and running it
                # inline froze every other request (4 concurrent proofreads ran
                # one at a time; a plain "สวัสดี" waited 103s for its first byte).
                answer, files_usage = await run_in_threadpool(
                    _answer_files, prompt_question, image_urls, text_attachments, history
                )
                # Prepend notices: (1) long-document translation may be incomplete,
                # (2) scanned PDF exceeded the 50-page vision cap — so the user
                # knows the answer's limits and why.
                notices = list(truncation_notes)
                warn = att.long_translation_warning(question, total_text_chars, len(image_urls))
                if warn:
                    notices.insert(0, warn)
                if notices:
                    answer = "\n\n".join(notices) + "\n\n---\n\n" + answer
                # Files path doesn't run the classifier so usage = just the
                # vision call. Wrap in accumulate_usage for consistent shape
                # with the keys save_chat_message_pg expects.
                usage_row = accumulate_usage(agent_router_usage, files_usage)
                source = "files"
                knowledge_id = None
                similarity = None

            else:
                # V4.1/V4.2 (flags off → exactly the legacy history and classifier)
                history = _turn_history(user, session_id, history)
                t_answer = time.monotonic()
                route = _route_for(user, question, company_only)
                if route is None:
                    category, classify_usage = classify_query(question)
                    chosen_model = LLM_MODEL_CALC if category == "calc" else LLM_MODEL
                else:
                    category, classify_usage = route.category, {}
                    chosen_model = route.model

                # Layer 1: ask the colleague's company-ai-brain first.
                # Cheaper (no OpenAI embed call) and centralised — if it has
                # a confident match we skip the local pgvector search entirely.
                # Fails OPEN on transport errors so chat keeps working when
                # the brain is down.
                # 0.7 because the brain often returns heading-only previews
                # which score 0.55–0.65 even for irrelevant queries; below 0.7
                # we'd accept weak hits and the LLM gets confusing context.
                brain_threshold = float(os.getenv("AI_BRAIN_THRESHOLD", "0.7"))
                brain_results: list[dict] = []
                if brain_enabled():
                    try:
                        raw_hits = search_brain(question, top_k=3)
                    except Exception as e:
                        audit_log(
                            "brain_search_failed",
                            user=user,
                            detail={"error": str(e)[:300], "via": "chat-pg"},
                            request=request,
                        )
                        raw_hits = []
                    brain_results = [
                        r for r in raw_hits
                        if float(r.get("score") or 0) >= brain_threshold
                    ]

                if brain_results:
                    top = brain_results[0]
                    audit_log(
                        "company_brain_hit",
                        user=user,
                        detail={
                            "score": top.get("score"),
                            "source": top.get("source"),
                            "collection": top.get("collection"),
                            "n_chunks": len(brain_results),
                        },
                        request=request,
                    )
                    route = _route_grounded(route)
                    chosen_model = route.model if route else chosen_model
                    answer, answer_usage = answer_from_context(
                        question,
                        "เอกสารส่วนกลาง (AI Brain)",
                        build_brain_context(brain_results),
                        model=chosen_model,
                        history=history,
                        **_effort_kw(route),
                    )
                    source = "brain" if category == "general" else "brain-calc"
                    knowledge_id = None
                    similarity = float(top.get("score") or 0)
                    source_file_hit = top.get("source")
                    usage_row = accumulate_usage(
                        classify_usage, answer_usage, agent_router_usage,
                        primary_model=answer_usage.get("model") or chosen_model,
                    )
                    usage_row.update(_route_stats(route, category, chosen_model, answer_usage, t_answer))
                else:
                    # Layer 2: fall back to local pgvector (admin Q&A +
                    # kb_import in company mode).
                    vec = embed(question)
                    hit = search_knowledge(
                        vec,
                        include_confidential=(user.get("role") == "admin"),
                        company_mode=company_only,
                    )

                    if hit:
                        route = _route_grounded(route)
                        chosen_model = route.model if route else chosen_model
                        answer, answer_usage = answer_from_context(
                            question,
                            hit["question"],
                            hit["answer"],
                            model=chosen_model,
                            history=history,
                            **_effort_kw(route),
                        )
                        source = "rag" if category == "general" else "rag-calc"
                        knowledge_id = hit["id"]
                        similarity = hit["similarity"]
                        source_file_hit = hit.get("source_file")
                    else:
                        log_pending_question(question, vec)

                        audit_log(
                            "pending_question_created",
                            user=user,
                            detail={"question": question[:300], "db_engine": "postgres"},
                            request=request,
                        )

                        answer, answer_usage = answer_freely(
                            question, model=chosen_model, history=history,
                            company_only=company_only, **_effort_kw(route),
                        )
                        source = "llm" if category == "general" else "llm-calc"
                        knowledge_id = None
                        similarity = None
                        source_file_hit = None
                    usage_row = accumulate_usage(
                        classify_usage, answer_usage, agent_router_usage,
                        primary_model=answer_usage.get("model") or chosen_model,
                    )
                    usage_row.update(_route_stats(route, category, chosen_model, answer_usage, t_answer))

            # Honesty note for an ungrounded answer to a company-specific factual
            # question (no-op otherwise).
            answer = answer + _company_fact_note(question, source)

            message_id = save_chat_message_pg(
                user_id=user["id"],
                session_id=sid,
                question=question or "[ไฟล์แนบ]",
                answer=answer,
                source=source,
                knowledge_id=knowledge_id,
                mode=mode,
                usage=usage_row,
            )
            _after_answer(user, sid)

            attachment_rows: list[dict] = []

            for sf in saved_files:
                attachment_rows.append(
                    save_attachment_pg(
                        message_id=message_id,
                        user_id=user["id"],
                        filename=sf["filename"],
                        content_type=sf["content_type"],
                        size_bytes=sf["size"],
                        file_path=sf["file_path"],
                        extracted_text=sf.get("extracted_text"),
                    )
                )

            audit_log(
                "chat_message",
                user=user,
                detail={
                    "session_id": sid,
                    "message_id": message_id,
                    "source": source,
                    "similarity": similarity,
                    "has_files": bool(saved_files),
                    "file_count": len(saved_files),
                    "message_length": len(question),
                    "db_engine": "postgres",
                },
                request=request,
            )

            return ChatResponse(
                answer=answer,
                source=source,
                similarity=similarity,
                session_id=sid,
                session_title=stitle,
                attachments=attachment_rows,
                source_knowledge_id=knowledge_id,
                source_file=source_file_hit,
                turn_count=_count_session_turns(sid),
                turn_limit=MAX_TURNS_PER_SESSION,
                message_id=message_id,
            )
        conn = get_db()
        sid, stitle = _ensure_session(
            conn, user["id"], session_id, question or saved_files[0]["filename"], mode=mode
        )

        history = _get_session_history(conn, session_id, user["id"])

        if saved_files:
            image_urls: list[str] = []
            text_attachments: list[tuple[str, str]] = []
            truncation_notes: list[str] = []
            total_text_chars = 0

            for sf in saved_files:
                if att.is_image(sf["content_type"]):
                    image_urls.append(
                        att.encode_image_data_url(sf["file_path"], sf["content_type"])
                    )
                    sf["extracted_text"] = None
                    continue

                extracted = att.extract_any_text(
                    sf["file_path"], sf["content_type"], sf["filename"]
                )

                sf["extracted_text"] = extracted if extracted.strip() else None
                total_text_chars += len(extracted)

                if att.is_pdf(sf["content_type"]) and att.is_text_sparse(extracted):
                    rendered = att.render_pdf_pages_as_images(sf["file_path"])
                    image_urls.extend(rendered)

                    note = att.pdf_truncation_note(sf["filename"], sf["file_path"])
                    if note:
                        truncation_notes.append(note)

                    if extracted.strip():
                        text_attachments.append((sf["filename"], extracted))
                elif extracted.strip():
                    text_attachments.append((sf["filename"], extracted))

            prompt_question = question or "ช่วยอธิบายเนื้อหาในไฟล์ที่แนบ และให้คำแนะนำที่เกี่ยวข้อง"
            answer, _files_usage = await run_in_threadpool(
                _answer_files, prompt_question, image_urls, text_attachments, history
            )
            notices = list(truncation_notes)
            warn = att.long_translation_warning(question, total_text_chars, len(image_urls))
            if warn:
                notices.insert(0, warn)
            if notices:
                answer = "\n\n".join(notices) + "\n\n---\n\n" + answer
            # sqlite path doesn't persist token cost — only the postgres deploy
            # is the one production cares about. Ignore the usage dict here.
            source = "files"
            knowledge_id = None
            similarity = None

        else:
            category, _classify_usage = classify_query(question)
            chosen_model = LLM_MODEL_CALC if category == "calc" else LLM_MODEL

            vec = embed(question)
            hit = search_knowledge(vec)

            if hit:
                answer, _answer_usage = answer_from_context(
                    question,
                    hit["question"],
                    hit["answer"],
                    model=chosen_model,
                    history=history,
                )
                source = "rag" if category == "general" else "rag-calc"
                knowledge_id = hit["id"]
                similarity = hit["similarity"]
                source_file_hit = hit.get("source_file")

            else:
                log_pending_question(question, vec)

                audit_log(
                    "pending_question_created",
                    user=user,
                    detail={"question": question[:300]},
                    request=request,
                )

                answer, _answer_usage = answer_freely(question, model=chosen_model, history=history)
                source = "llm" if category == "general" else "llm-calc"
                knowledge_id = None
                similarity = None
                source_file_hit = None

        cur = conn.execute(
            "INSERT INTO chat_history (user_id, session_id, question, answer, source, knowledge_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], sid, question or "[ไฟล์แนบ]", answer, source, knowledge_id),
        )
        message_id = cur.lastrowid

        attachment_rows: list[dict] = []

        for sf in saved_files:
            cur2 = conn.execute(
                "INSERT INTO attachments "
                "(message_id, user_id, filename, content_type, size_bytes, file_path, extracted_text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    user["id"],
                    sf["filename"],
                    sf["content_type"],
                    sf["size"],
                    sf["file_path"],
                    sf.get("extracted_text"),
                ),
            )
            attachment_rows.append(
                {
                    "id": cur2.lastrowid,
                    "filename": sf["filename"],
                    "content_type": sf["content_type"],
                    "size_bytes": sf["size"],
                }
            )

        conn.execute(
            "UPDATE chat_sessions SET updated_at = datetime('now'), mode = ? WHERE id = ?",
            (mode, sid),
        )
        conn.commit()

        audit_log(
            "chat_message",
            user=user,
            detail={
                "session_id": sid,
                "source": source,
                "similarity": similarity,
                "has_files": bool(saved_files),
                "file_count": len(saved_files),
                "message_length": len(question),
            },
            request=request,
        )

        return ChatResponse(
            answer=answer,
            source=source,
            similarity=similarity,
            session_id=sid,
            session_title=stitle,
            attachments=attachment_rows,
            source_knowledge_id=knowledge_id,
            source_file=source_file_hit,
            turn_count=_count_session_turns(sid),
            turn_limit=MAX_TURNS_PER_SESSION,
            message_id=message_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        audit_log(
            "chat_error",
            user=user,
            detail={
                "error_type": e.__class__.__name__,
                "error": str(e),
                "has_files": bool(saved_files),
                "file_count": len(saved_files),
            },
            request=request,
        )
        raise


# ── Stop button on /chat/stream ─────────────────────────────────────────────
# A stopped answer used to vanish: when the client goes away Starlette only
# cancels the response, and the paused generator lingered until GC, so nothing
# after its last yield ran. _close_on_disconnect closes it right away; gen()
# then saves the text it had already sent, marked with STOPPED_NOTE (the next
# turn's history sees it was cut short). Closing also ends the OpenAI stream.

STOPPED_NOTE = "\n\n_(หยุดก่อนตอบจบ — คำตอบอาจไม่ครบ)_"


async def _close_on_disconnect(it):
    try:
        async for chunk in iterate_in_threadpool(it):
            yield chunk
    finally:
        # we are being cancelled — shield the close; it runs in a worker
        # thread because it may write to the DB (_save_stopped)
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(it.close)


def _save_stopped(user: dict, question: str, mode: str, request: Request, live: dict) -> None:
    """Save the answer text a stopped stream had sent (`live`, kept by gen()).
    Nothing when no answer text went out or the answer was already saved."""
    text = "".join(live["parts"]).strip()
    if not live["source"] or not text:
        return
    from chat_pg import ensure_session_pg, save_chat_message_pg

    try:
        sid, _ = ensure_session_pg(
            user_id=user["id"], session_id=live["sid"], first_question=question, mode=mode,
        )
        # no usage: the final chunk that carries it never came (the dashboard
        # falls back to its estimate for rows without one)
        mid = save_chat_message_pg(
            user_id=user["id"], session_id=sid, question=question,
            answer=text + STOPPED_NOTE, source=live["source"], knowledge_id=live["kid"], mode=mode,
        )
        _after_answer(user, sid)
        audit_log(
            "chat_stream_stopped",
            user=user,
            detail={"session_id": sid, "message_id": mid, "source": live["source"], "chars": len(text)},
            request=request,
        )
    except Exception as e:
        audit_log(
            "chat_stream_stop_save_failed",
            user=user,
            detail={"error_type": e.__class__.__name__, "error": str(e)[:300]},
            request=request,
        )


@app.post("/chat/stream")
@limiter.limit("30/minute")
async def chat_stream(
    request: Request,
    message: str = Form(...),
    session_id: int | None = Form(None),
    mode: str = Form("normal"),
    user: dict = Depends(current_user),
):
    """Streaming twin of /chat for text questions (no file uploads).

    Emits NDJSON: {"type":"delta","v":"..."} per token, then a final
    {"type":"done", ...metadata} or {"type":"error","detail":...}. Reuses the
    exact same pipeline (safety → turn budget → export intent → RAG → LLM) as
    /chat; only the LLM call streams. Postgres only — the client falls back to
    /chat for sqlite or when files are attached.
    """
    if not use_postgres_auth():
        raise HTTPException(501, "streaming is only available on postgres deployment")

    question = message.strip()
    company_only = mode == "company"
    if not question:
        raise HTTPException(400, "empty message")

    # Safety-classifier usage for this question: added once to the row named by
    # the final `done` event (whatever step saved it). Blocked → audit entry.
    safety_usages: list[dict] = []

    def ev(obj: dict) -> str:
        if obj.get("type") == "done" and obj.get("message_id"):
            _charge_safety_usage(obj["message_id"], safety_usages)
        return json.dumps(obj, ensure_ascii=False) + "\n"

    def gen():
        from chat_pg import (
            ensure_session_pg,
            save_chat_message_pg,
        )

        # Answer text sent while the model streams, saved by _save_stopped if
        # the client stops before it is done. source=None: nothing to save.
        live: dict = {"sid": session_id, "source": None, "kid": None, "parts": []}

        try:
            # 1) Safety — keyword then classifier. Mirror /chat: blocked answers
            #    are NOT persisted to history. A job-number lookup skips the
            #    classifier (answered from the authorized WI page) but still runs
            #    the hard keyword filter. A classifier flag on analysis of the
            #    user's own spreadsheets becomes a file-scoped turn (2.5) — or the
            #    daily-quota reply when no runs are left — instead.
            keyword_hit = is_sensitive(question) is not None
            # V4.2 memory command — the user's own text, no AI call, its own
            # refusal of sensitive data → after the keyword filter, before the
            # AI classifier (MEMORY_MODE).
            mr = None if keyword_hit else _memory_reply(user, question, session_id, mode, request)
            if mr is not None:
                yield ev({"type": "delta", "v": mr["answer"]})
                yield ev(_memory_done_event(mr))
                return
            flagged = None
            if not keyword_hit and not _is_linsip_query(question, session_id, user):
                flagged = classify_sensitivity(question, safety_usages)
            decision = "denied" if flagged is None else _file_scope_decision(
                user, question, session_id, mode, flagged[0], request, usages=safety_usages
            )
            file_scoped = decision == "allowed"
            code_limited = decision == "quota"
            if flagged is not None and decision == "denied" and not keyword_hit:
                # V4.2: about the user themself → from their own memories only
                ms = _memory_scoped_reply(user, question, session_id, mode, request)
                if ms is not None:
                    yield ev({"type": "delta", "v": ms["answer"]})
                    yield ev(_memory_done_event(ms))
                    return
            if keyword_hit or (flagged is not None and decision == "denied"):
                audit_log(
                    "sensitive_blocked_stream",
                    user=user,
                    detail={"message_length": len(question), **_safety_cost_detail(safety_usages)},
                    request=request,
                )
                _log_blocked_usage(user, safety_usages)
                yield ev({"type": "delta", "v": BLOCKED_RESPONSE})
                yield ev({
                    "type": "done",
                    "source": "blocked",
                    "session_id": session_id or 0,
                    "session_title": "",
                    "message_id": None,
                    "turn_count": None,
                    "turn_limit": MAX_TURNS_PER_SESSION,
                })
                return

            # 2) Per-session turn cap.
            if session_id is not None and not _is_export_request(question):
                used = _count_session_turns(session_id)
                if used >= MAX_TURNS_PER_SESSION:
                    yield ev({
                        "type": "error",
                        "code": "turn_limit",
                        "detail": f"แชทนี้ครบ {MAX_TURNS_PER_SESSION} คำถามแล้ว — "
                        "กรุณาเปิด 'แชทใหม่' เพื่อถามต่อค่ะ",
                    })
                    return

            # 2.4) File question that passed every check, but no Code Interpreter
            #      runs are left today — say so (not the sensitive-data block).
            if code_limited:
                res = _code_limit_reply(user, question, session_id, mode)
                yield ev({"type": "delta", "v": res["answer"]})
                yield ev(_agent_done_event(res))
                return

            # 2.5) File-scoped analysis — a flagged question approved by
            #      _file_scope_decision. Only the sandbox over the user's own
            #      spreadsheets may answer it; nothing else in the pipeline runs.
            if file_scoped:
                for kind, payload in _agent_turn(user, question, session_id, mode, request, scoped=True):
                    if kind == "status":
                        yield ev({"type": "status", "state": payload})
                    elif kind == "delta":
                        yield ev({"type": "delta", "v": payload})
                    elif kind == "done":
                        yield ev(_agent_done_event(payload))
                        return
                yield ev({"type": "delta", "v": FILE_SCOPE_FAILED})
                yield ev({
                    "type": "done", "source": "code_error",
                    "session_id": session_id or 0, "session_title": "",
                    "message_id": None, "turn_count": None,
                    "turn_limit": MAX_TURNS_PER_SESSION,
                })
                return

            # 3) Export intent — save the offer, no token streaming.
            if _is_export_request(question):
                offer_text = (
                    "ได้ค่ะ! กดปุ่มด้านล่างเพื่อบันทึกคำตอบล่าสุดเป็น PDF — "
                    "หรือดาวน์โหลดเอกสารต้นฉบับ (ถ้ามี) ก็ได้ค่ะ"
                )
                sid, stitle = ensure_session_pg(
                    user_id=user["id"], session_id=session_id,
                    first_question=question, mode=mode,
                )
                mid = save_chat_message_pg(
                    user_id=user["id"], session_id=sid, question=question,
                    answer=offer_text, source="export_offer",
                    knowledge_id=None, mode=mode,
                )
                yield ev({"type": "delta", "v": offer_text})
                yield ev({
                    "type": "done", "source": "export_offer", "session_id": sid,
                    "session_title": stitle, "message_id": mid,
                    "turn_count": _count_session_turns(sid),
                    "turn_limit": MAX_TURNS_PER_SESSION,
                })
                return

            # 3.5) Image generation intent — "วาดรูป / สร้างภาพ ...". Runs the
            #      image model (slow, no token stream), saves the PNG as an
            #      attachment on the answer row, and hands the frontend the
            #      attachment via the 'done' event so it renders inline.
            # Image EDIT — a modification instruction ("เปลี่ยนพื้นหลังเป็นชมพู")
            # when the session already has an image. Checked before generation;
            # a fresh "สร้างรูป..." always generates instead.
            if _is_image_edit_request(question) and not _is_image_request(question) and session_id:
                from chat_pg import get_last_image_attachment_pg

                last_img = get_last_image_attachment_pg(session_id)
                if last_img:
                    yield ev({"type": "status", "state": "generating_image"})
                    res = _handle_image_edit(user, question, session_id, mode, last_img)
                    yield ev({"type": "delta", "v": res["answer"]})
                    done_evt = {
                        "type": "done", "source": res["source"],
                        "session_id": res["session_id"],
                        "session_title": res["session_title"],
                        "message_id": res["message_id"],
                        "turn_count": _count_session_turns(res["session_id"]),
                        "turn_limit": MAX_TURNS_PER_SESSION,
                    }
                    if res["attachment"]:
                        done_evt["attachments"] = [res["attachment"]]
                    yield ev(done_evt)
                    return
                # no image to edit → fall through to the normal pipeline

            if _is_image_request(question):
                audit_log(
                    "image_generation_requested",
                    user=user,
                    detail={"prompt": question[:300], "via": "stream"},
                    request=request,
                )
                # Tell the client to show a "creating image" placeholder — the
                # generation below is slow (10-20s) and emits no tokens.
                yield ev({"type": "status", "state": "generating_image"})
                res = _handle_image_generation(user, question, session_id, mode)
                yield ev({"type": "delta", "v": res["answer"]})
                done_evt = {
                    "type": "done", "source": res["source"],
                    "session_id": res["session_id"],
                    "session_title": res["session_title"],
                    "message_id": res["message_id"],
                    "turn_count": _count_session_turns(res["session_id"]),
                    "turn_limit": MAX_TURNS_PER_SESSION,
                }
                if res["attachment"]:
                    done_evt["attachments"] = [res["attachment"]]
                yield ev(done_evt)
                return

            # 3.5.4) linsip job lookup — the user mentioned a job number
            #        (e.g. J12600091). Fetch that job's WI page and answer from
            #        it. Non-streaming — emitted as one delta.
            li = _try_linsip_answer(user, question, session_id, mode, request)
            if li is not None:
                yield ev({"type": "delta", "v": li["answer"]})
                yield ev({
                    "type": "done", "source": li["source"],
                    "session_id": li["session_id"],
                    "session_title": li["session_title"],
                    "message_id": li["message_id"],
                    "turn_count": _count_session_turns(li["session_id"]),
                    "turn_limit": MAX_TURNS_PER_SESSION,
                })
                return

            # 3.54) Agent router (AGENT_V1_MODE / CODE_INTERPRETER_MODE flags) —
            #       the model decides whether to search the web / read links /
            #       run Python, over several steps, and the final answer streams.
            #       Replaces 3.55/3.6 when web tools are on; if no tool is
            #       needed the normal pipeline runs.
            legacy_tools = True
            agent_router_usage: dict = {}
            if _agent_enabled(user, mode):
                ref = {"id": session_id}
                for kind, payload in _agent_turn(user, question, session_id, mode, request, session_ref=ref):
                    if kind == "status":
                        yield ev({"type": "status", "state": payload})
                    elif kind == "delta":
                        if live["source"] is None:
                            # the answer starts: from here on the chat exists
                            sid, stitle = ensure_session_pg(
                                user_id=user["id"], session_id=session_id,
                                first_question=question, mode=mode,
                            )
                            ref["id"] = live["sid"] = sid
                            live["source"] = "agent"
                            yield ev({"type": "session", "session_id": sid, "session_title": stitle})
                        live["parts"].append(payload)
                        yield ev({"type": "delta", "v": payload})
                    elif kind == "done":
                        live["source"] = None  # saved by _agent_turn
                        if payload["tail"]:
                            yield ev({"type": "delta", "v": payload["tail"]})
                        yield ev(_agent_done_event(payload))
                        return
                    elif kind == "standard":
                        legacy_tools = payload["legacy_tools"]
                        agent_router_usage = payload["usage"]
                    elif kind == "error":
                        yield ev({"type": "error", "detail": "เกิดข้อผิดพลาดในการสร้างคำตอบ"})
                        return
                    # "fallback" → legacy_tools stays True

            # 3.55) URL reading — the user pasted a link. Fetch that page and
            #       answer about it (before web search, so an explicit link is
            #       read directly). Non-streaming — emitted as one delta.
            ur = _try_url_answer(user, question, session_id, mode, request) if legacy_tools else None
            if ur is not None:
                yield ev({"type": "delta", "v": ur["answer"]})
                yield ev({
                    "type": "done", "source": ur["source"],
                    "session_id": ur["session_id"],
                    "session_title": ur["session_title"],
                    "message_id": ur["message_id"],
                    "turn_count": _count_session_turns(ur["session_id"]),
                    "turn_limit": MAX_TURNS_PER_SESSION,
                })
                return

            # 3.6) Live web search — questions wanting fresh info ("ข่าวล่าสุด",
            #      "ราคาทองวันนี้"). Skipped in company mode; falls through to
            #      the normal pipeline if the search fails or quota is hit-and-
            #      returned. Non-streaming (search is slow) — emitted as one delta.
            ws = _try_web_search_answer(user, question, session_id, mode, request) if legacy_tools else None
            if ws is not None:
                yield ev({"type": "delta", "v": ws["answer"]})
                yield ev({
                    "type": "done", "source": ws["source"],
                    "session_id": ws["session_id"],
                    "session_title": ws["session_title"],
                    "message_id": ws["message_id"],
                    "turn_count": _count_session_turns(ws["session_id"]),
                    "turn_limit": MAX_TURNS_PER_SESSION,
                })
                return

            # 4) Normal answer — stream the LLM tokens.
            sid, stitle = ensure_session_pg(
                user_id=user["id"], session_id=session_id,
                first_question=question, mode=mode,
            )
            live["sid"] = sid
            # the chat exists now — a client that stops mid-answer stays in it
            yield ev({"type": "session", "session_id": sid, "session_title": stitle})
            history = _turn_history(user, session_id)
            knowledge_id: int | None = None
            source_file_hit: str | None = None
            similarity = None

            # NOTE: The legacy `if mode == "brain":` toggle has been removed —
            # the AI Brain is now queried automatically on every chat as a
            # Layer-1 retrieval source (see the brain_results_s block below).
            # Anything that was sent with mode="brain" by older clients now
            # gets the same flow as mode="normal".
            # V4.1: with model routing on, rules pick model + effort (no
            # classifier call); off → the classifier, as before.
            t_answer = time.monotonic()
            route = _route_for(user, question, company_only)
            if route is None:
                category, classify_usage = classify_query(question)
                chosen_model = LLM_MODEL_CALC if category == "calc" else LLM_MODEL
            else:
                category, classify_usage = route.category, {}
                chosen_model = route.model
            # Streaming usage is captured into this holder by the LLM stream
            # functions (populated on the final chunk). We then accumulate it
            # with the classifier usage and persist once the stream finishes.
            stream_usage: dict = {}

            # Layer 1: ask the colleague's company-ai-brain first.
            # Same flow as the non-streaming /chat — saves an OpenAI
            # embedding call when the brain has a confident hit. Fails
            # OPEN so a brain outage drops through to local search.
            brain_threshold_s = float(os.getenv("AI_BRAIN_THRESHOLD", "0.7"))
            brain_results_s: list[dict] = []
            if brain_enabled():
                try:
                    raw_hits_s = search_brain(question, top_k=3)
                except Exception as e:
                    audit_log(
                        "brain_search_failed",
                        user=user,
                        detail={"error": str(e)[:300], "via": "stream"},
                        request=request,
                    )
                    raw_hits_s = []
                brain_results_s = [
                    r for r in raw_hits_s
                    if float(r.get("score") or 0) >= brain_threshold_s
                ]

            if brain_results_s:
                top_s = brain_results_s[0]
                audit_log(
                    "company_brain_hit",
                    user=user,
                    detail={
                        "score": top_s.get("score"),
                        "source": top_s.get("source"),
                        "collection": top_s.get("collection"),
                        "n_chunks": len(brain_results_s),
                        "via": "stream",
                    },
                    request=request,
                )
                route = _route_grounded(route)
                chosen_model = route.model if route else chosen_model
                tokens = stream_from_context(
                    question,
                    "เอกสารส่วนกลาง (AI Brain)",
                    build_brain_context(brain_results_s),
                    model=chosen_model,
                    history=history,
                    usage_holder=stream_usage,
                    **_effort_kw(route),
                )
                source = "brain" if category == "general" else "brain-calc"
                knowledge_id = None
                similarity = float(top_s.get("score") or 0)
                source_file_hit = top_s.get("source")
            else:
                vec = embed(question)
                hit = search_knowledge(
                    vec,
                    include_confidential=(user.get("role") == "admin"),
                    company_mode=company_only,
                )

                if hit:
                    route = _route_grounded(route)
                    chosen_model = route.model if route else chosen_model
                    tokens = stream_from_context(
                        question, hit["question"], hit["answer"],
                        model=chosen_model, history=history,
                        usage_holder=stream_usage, **_effort_kw(route),
                    )
                    source = "rag" if category == "general" else "rag-calc"
                    knowledge_id = hit["id"]
                    similarity = hit["similarity"]
                    source_file_hit = hit.get("source_file")
                else:
                    log_pending_question(question, vec)
                    audit_log(
                        "pending_question_created",
                        user=user,
                        detail={"question": question[:300], "db_engine": "postgres", "via": "stream"},
                        request=request,
                    )
                    tokens = stream_freely(
                        question, model=chosen_model, history=history,
                        company_only=company_only, usage_holder=stream_usage,
                        **_effort_kw(route),
                    )
                    source = "llm" if category == "general" else "llm-calc"

            parts: list[str] = []
            live.update(source=source, kid=knowledge_id, parts=parts)
            first_token_at: float | None = None
            for delta in tokens:
                if first_token_at is None:
                    first_token_at = time.monotonic()
                parts.append(delta)
                yield ev({"type": "delta", "v": delta})
            answer = "".join(parts).strip()

            # Honesty note for an ungrounded answer to a company-specific factual
            # question — streamed as a final delta and saved with the answer.
            fact_note = _company_fact_note(question, source)
            if fact_note:
                yield ev({"type": "delta", "v": fact_note})
                answer = answer + fact_note

            # `stream_usage` was populated in-place by the final chunk of
            # whichever stream we entered above. Combine with the classifier
            # call so cost_usd reflects all OpenAI work for this question.
            usage_row = accumulate_usage(
                classify_usage, stream_usage, agent_router_usage,
                primary_model=stream_usage.get("model") or chosen_model,   # ≠ chosen if the API rejected a route
            )
            usage_row.update(_route_stats(route, category, chosen_model, stream_usage, t_answer, first_token_at))

            mid = save_chat_message_pg(
                user_id=user["id"], session_id=sid, question=question,
                answer=answer, source=source, knowledge_id=knowledge_id,
                mode=mode, usage=usage_row,
            )
            live["source"] = None  # saved
            _after_answer(user, sid)
            audit_log(
                "chat_message",
                user=user,
                detail={
                    "session_id": sid, "message_id": mid, "source": source,
                    "similarity": similarity, "has_files": False, "file_count": 0,
                    "message_length": len(question), "db_engine": "postgres", "via": "stream",
                },
                request=request,
            )
            yield ev({
                "type": "done", "source": source, "session_id": sid,
                "session_title": stitle, "message_id": mid,
                "source_knowledge_id": knowledge_id, "source_file": source_file_hit,
                "turn_count": _count_session_turns(sid),
                "turn_limit": MAX_TURNS_PER_SESSION,
            })
        except GeneratorExit:  # the client stopped (see _close_on_disconnect)
            _save_stopped(user, question, mode, request, live)
            raise
        except Exception as e:
            audit_log(
                "chat_stream_error",
                user=user,
                detail={"error_type": e.__class__.__name__, "error": str(e)},
                request=request,
            )
            yield ev({"type": "error", "detail": "เกิดข้อผิดพลาดในการสร้างคำตอบ"})

    return StreamingResponse(
        _close_on_disconnect(gen()),
        media_type="application/x-ndjson",
        # Defeat reverse-proxy/response buffering so tokens reach the browser
        # as they're produced rather than all at once at the end.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


# ── V4.2 long-term memory: the user's own list (MEMORY_MODE) ────────────────

class MemoryIn(BaseModel):
    content: str


class MemorySettingsIn(BaseModel):
    enabled: bool


def _memory_for(user: dict):
    import memory

    if not (use_postgres_auth() and memory.memory_enabled_for(user)):
        raise HTTPException(404, "memory is not available")
    return memory


@app.get("/memory")
def get_memory(user: dict = Depends(current_user)):
    """This user's memories. available=False → the UI hides the menu."""
    import memory

    if not (use_postgres_auth() and memory.memory_enabled_for(user)):
        return {"available": False, "enabled": False, "items": [], "limit": memory.MAX_ITEMS}
    return {"available": True, "enabled": memory.STORE.enabled(user["id"]),
            "items": memory.STORE.items(user["id"]), "limit": memory.MAX_ITEMS}


@app.post("/memory")
@limiter.limit("30/minute")
def add_memory(body: MemoryIn, request: Request, user: dict = Depends(current_user)):
    memory = _memory_for(user)
    ok, msg = memory.save(user["id"], body.content)
    if not ok:
        raise HTTPException(400, msg)
    audit_log("memory_saved", user=user, detail={"via": "menu"}, request=request)
    return {"ok": True, "message": msg}


@app.delete("/memory/{memory_id}")
def delete_memory(memory_id: int, request: Request, user: dict = Depends(current_user)):
    memory = _memory_for(user)
    if not memory.STORE.delete(user["id"], memory_id):      # another user's id → not found
        raise HTTPException(404, "not found")
    audit_log("memory_deleted", user=user, detail={"memory_id": memory_id}, request=request)
    return {"ok": True}


@app.delete("/memory")
def clear_memory(request: Request, user: dict = Depends(current_user)):
    memory = _memory_for(user)
    n = memory.STORE.clear(user["id"])
    audit_log("memory_cleared", user=user, detail={"deleted": n}, request=request)
    return {"ok": True, "deleted": n}


@app.put("/memory/settings")
def memory_settings(body: MemorySettingsIn, request: Request, user: dict = Depends(current_user)):
    memory = _memory_for(user)
    memory.STORE.set_enabled(user["id"], body.enabled)
    audit_log("memory_settings", user=user, detail={"enabled": body.enabled}, request=request)
    return {"ok": True, "enabled": body.enabled}


class FeedbackIn(BaseModel):
    message_id: int
    vote: str  # 'up' | 'down'
    reason: str | None = None


def _record_feedback(message_id: int, user_id: int, vote: str, reason: str | None) -> str | None:
    """Upsert a 👍/👎 vote on an answer the user asked. Returns the question
    text (used to seed a pending entry on 👎), or None if the message doesn't
    exist or isn't the user's own."""
    reason = (reason or "").strip()[:500] or None

    if use_postgres_auth():
        from db_pg import get_pg_conn

        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, question FROM chat_history WHERE id = %s",
                    (message_id,),
                )
                row = cur.fetchone()
                if not row or row[0] != user_id:
                    return None
                question = row[1]
                cur.execute(
                    """
                    INSERT INTO answer_feedback (message_id, user_id, vote, reason)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (message_id, user_id)
                    DO UPDATE SET vote = EXCLUDED.vote,
                                  reason = EXCLUDED.reason,
                                  created_at = now()
                    """,
                    (message_id, user_id, vote, reason),
                )
        return question

    conn = get_db()
    row = conn.execute(
        "SELECT user_id, question FROM chat_history WHERE id = ?",
        (message_id,),
    ).fetchone()
    if not row or row["user_id"] != user_id:
        return None
    question = row["question"]
    conn.execute(
        """
        INSERT INTO answer_feedback (message_id, user_id, vote, reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (message_id, user_id)
        DO UPDATE SET vote = excluded.vote,
                      reason = excluded.reason,
                      created_at = datetime('now')
        """,
        (message_id, user_id, vote, reason),
    )
    conn.commit()
    return question


@app.post("/chat/feedback")
@limiter.limit("60/minute")
def submit_feedback(
    body: FeedbackIn, request: Request, user: dict = Depends(current_user)
):
    """Record 👍/👎 on an answer. A 👎 also seeds a pending question so admins
    can spot weak answers and improve the knowledge base."""
    if body.vote not in ("up", "down"):
        raise HTTPException(400, "vote must be 'up' or 'down'")

    question = _record_feedback(body.message_id, user["id"], body.vote, body.reason)
    if question is None:
        raise HTTPException(404, "message not found")

    audit_log(
        "answer_feedback",
        user=user,
        detail={
            "message_id": body.message_id,
            "vote": body.vote,
            "has_reason": bool(body.reason and body.reason.strip()),
        },
        request=request,
    )

    # A thumbs-down means the answer missed — log the question for admins to
    # review/answer (reuses the existing pending-question dedup clustering).
    # Best-effort: never fail the feedback call if embedding/logging hiccups.
    if body.vote == "down":
        try:
            vec = embed(question)
            log_pending_question(question, vec)
        except Exception as e:
            audit_log(
                "feedback_pending_log_failed",
                user=user,
                detail={"message_id": body.message_id, "error": str(e)},
                request=request,
            )

    return {"ok": True}


@app.get("/attachments/{aid}")
@limiter.limit("60/minute")
def get_attachment(request: Request, aid: int, user: dict = Depends(current_user)):
    if use_postgres_auth():
        from chat_pg import get_attachment_pg

        row = get_attachment_pg(aid)
    else:
        row = get_db().execute(
            "SELECT user_id, filename, content_type, file_path FROM attachments WHERE id = ?",
            (aid,),
        ).fetchone()

    if not row:
        raise HTTPException(404, "attachment not found")

    if row["user_id"] != user["id"] and user["role"] != "admin":
        audit_log(
            "attachment_forbidden",
            user=user,
            detail={"attachment_id": aid},
            request=request,
        )
        raise HTTPException(403, "forbidden")

    safe_path = att.validate_upload_path(row["file_path"])

    audit_log(
        "attachment_downloaded",
        user=user,
        detail={"attachment_id": aid, "filename": row["filename"]},
        request=request,
    )

    return FileResponse(
        path=str(safe_path),
        media_type=row["content_type"],
        filename=row["filename"],
    )


UNSAVED_LIMIT = 20


def _purge_unsaved_sessions(conn, user_id: int) -> None:
    all_unsaved = conn.execute(
        """
        SELECT id FROM chat_sessions
        WHERE user_id = ? AND is_saved = 0
        ORDER BY updated_at DESC
        """,
        (user_id,),
    ).fetchall()

    if len(all_unsaved) <= UNSAVED_LIMIT:
        return

    expired_ids = [r["id"] for r in all_unsaved[UNSAVED_LIMIT:]]
    placeholders = ",".join("?" * len(expired_ids))

    expired = conn.execute(
        f"SELECT id FROM chat_sessions WHERE id IN ({placeholders})",
        expired_ids,
    ).fetchall()

    if not expired:
        return

    expired_ids = [r["id"] for r in expired]
    placeholders = ",".join("?" * len(expired_ids))

    file_paths = [
        r["file_path"]
        for r in conn.execute(
            f"""
            SELECT a.file_path FROM attachments a
            JOIN chat_history h ON h.id = a.message_id
            WHERE h.session_id IN ({placeholders})
            """,
            expired_ids,
        ).fetchall()
    ]

    conn.execute(
        f"DELETE FROM attachments WHERE message_id IN "
        f"(SELECT id FROM chat_history WHERE session_id IN ({placeholders}))",
        expired_ids,
    )
    conn.execute(
        f"DELETE FROM chat_history WHERE session_id IN ({placeholders})",
        expired_ids,
    )
    conn.execute(
        f"DELETE FROM chat_sessions WHERE id IN ({placeholders})",
        expired_ids,
    )
    conn.commit()

    deleted_files = 0
    for fp in file_paths:
        if _safe_unlink_attachment(fp):
            deleted_files += 1

    audit_log(
        "unsaved_sessions_purged",
        detail={
            "user_id": user_id,
            "session_count": len(expired_ids),
            "deleted_files": deleted_files,
        },
    )


@app.get("/chat/sessions")
def list_sessions(user: dict = Depends(current_user)):
    if use_postgres_auth():
        from chat_pg import list_sessions_pg, purge_unsaved_sessions_pg

        sessions_deleted, file_paths = purge_unsaved_sessions_pg(user["id"], UNSAVED_LIMIT)
        if sessions_deleted:
            deleted_files = sum(1 for fp in file_paths if _safe_unlink_attachment(fp))
            audit_log(
                "unsaved_sessions_purged",
                detail={
                    "user_id": user["id"],
                    "session_count": sessions_deleted,
                    "deleted_files": deleted_files,
                    "db_engine": "postgres",
                },
            )

        return list_sessions_pg(user["id"])

    conn = get_db()
    _purge_unsaved_sessions(conn, user["id"])

    rows = conn.execute(
        """
        SELECT s.id, s.title, s.created_at, s.updated_at, s.is_saved,
               (SELECT COUNT(*) FROM chat_history WHERE session_id = s.id) AS message_count,
               (SELECT question FROM chat_history WHERE session_id = s.id ORDER BY id DESC LIMIT 1) AS last_preview
        FROM chat_sessions s
        WHERE s.user_id = ?
        ORDER BY s.updated_at DESC
        """,
        (user["id"],),
    ).fetchall()

    return [dict(r) for r in rows]


def _messages_with_attachments(
    conn, session_id: int, user_id: int | None = None
) -> list[dict]:
    msgs = conn.execute(
        "SELECT id, question, answer, source, asked_at FROM chat_history "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()

    # The viewer's own 👍/👎 per message, so reopening a chat restores the vote.
    votes: dict[int, str] = {}
    if user_id is not None:
        for r in conn.execute(
            "SELECT message_id, vote FROM answer_feedback af "
            "JOIN chat_history h ON h.id = af.message_id "
            "WHERE h.session_id = ? AND af.user_id = ?",
            (session_id, user_id),
        ).fetchall():
            votes[r["message_id"]] = r["vote"]

    result: list[dict] = []

    for m in msgs:
        atts = conn.execute(
            "SELECT id, filename, content_type, size_bytes FROM attachments WHERE message_id = ?",
            (m["id"],),
        ).fetchall()
        result.append({
            **dict(m),
            "attachments": [dict(a) for a in atts],
            "my_vote": votes.get(m["id"]),
        })

    return result


@app.get("/chat/sessions/{session_id}")
def get_session(session_id: int, user: dict = Depends(current_user)):
    if use_postgres_auth():
        from chat_pg import get_session_messages_pg

        session = get_session_messages_pg(session_id, user["id"])
        if not session:
            raise HTTPException(404, "session not found")
        return session

    conn = get_db()

    s = conn.execute(
        "SELECT id, title, created_at, COALESCE(mode, 'normal') AS mode "
        "FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()

    if not s:
        raise HTTPException(404, "session not found")

    return {
        **dict(s),
        "messages": _messages_with_attachments(conn, session_id, user["id"]),
    }


class ShareIn(BaseModel):
    recipient_ids: list[int] = []


@app.post("/chat/sessions/{session_id}/share")
@limiter.limit("20/minute")
def share_session(
    session_id: int, request: Request, body: ShareIn, user: dict = Depends(current_user)
):
    """Share a session the user owns with SPECIFIC recipients (targeted).

    Empty recipient_ids → unshare. Returns the stable token + path when shared.
    """
    if not use_postgres_auth():
        raise HTTPException(501, "share is only available on postgres deployment")

    from chat_pg import share_session_pg

    # empty recipient_ids = link-only share; non-empty = targeted. Always shares.
    token = share_session_pg(session_id, user["id"], body.recipient_ids)
    if not token:
        raise HTTPException(404, "session not found")

    audit_log(
        "chat_session_shared",
        user=user,
        detail={
            "session_id": session_id,
            "token_prefix": token[:6],
            "recipients": len(set(body.recipient_ids)),
        },
        request=request,
    )

    return {
        "ok": True,
        "shared": True,
        "token": token,
        "path": f"/chat/shared/{token}",
    }


@app.get("/chat/sessions/{session_id}/recipients")
def session_recipients(session_id: int, user: dict = Depends(current_user)):
    """recipient user_ids ที่แชทนี้ถูกแชร์ให้ (เฉพาะเจ้าของ) — ให้ dialog ติ๊กไว้ล่วงหน้า"""
    if not use_postgres_auth():
        raise HTTPException(501, "share is only available on postgres deployment")
    from chat_pg import list_share_recipients_pg

    return {"recipient_ids": list_share_recipients_pg(session_id, user["id"])}


@app.get("/users/list")
def users_list(user: dict = Depends(current_user)):
    """รายชื่อผู้ใช้สำหรับเลือกเป็นผู้รับการแชร์ (ไม่รวมตัวเอง)"""
    if not use_postgres_auth():
        raise HTTPException(501, "only available on postgres deployment")
    from chat_pg import list_shareable_users_pg

    return list_shareable_users_pg(user["id"])


@app.delete("/chat/sessions/{session_id}/share")
def revoke_session_share(
    session_id: int, request: Request, user: dict = Depends(current_user)
):
    if not use_postgres_auth():
        raise HTTPException(501, "share is only available on postgres deployment")

    from chat_pg import revoke_share_pg

    revoked = revoke_share_pg(session_id, user["id"])
    if not revoked:
        raise HTTPException(404, "session not found or not currently shared")

    audit_log(
        "chat_session_share_revoked",
        user=user,
        detail={"session_id": session_id},
        request=request,
    )
    return {"ok": True}


@app.get("/chat/shared-sessions")
def list_shared_sessions(user: dict = Depends(current_user)):
    """All chats teammates have shared, newest first. Any signed-in user.

    Powers the "แชร์ร่วมกันในทีม" sidebar panel — every user (not just admins)
    can browse shared chats read-only and fork them into their own. Each item
    carries `shared_token` so the client can open /chat/shared/{token}.
    """
    if not use_postgres_auth():
        raise HTTPException(501, "share is only available on postgres deployment")

    from chat_pg import list_shared_with_me_pg

    return list_shared_with_me_pg(user["id"])


@app.get("/chat/shared/{token}")
def get_shared_session(token: str, user: dict = Depends(current_user)):
    """Read-only view of a shared chat — only owner or recipient can open it."""
    if not use_postgres_auth():
        raise HTTPException(501, "share is only available on postgres deployment")

    from chat_pg import get_shared_session_pg

    session = get_shared_session_pg(token, user["id"])
    if not session:
        raise HTTPException(404, "ลิงค์นี้ใช้งานไม่ได้แล้ว — เจ้าของอาจยกเลิกการแชร์")
    # Flag whether the viewer is the owner so the UI can show "your own chat".
    session["is_owner"] = session["owner_user_id"] == user["id"]
    return session


@app.post("/chat/shared/{token}/fork")
@limiter.limit("10/minute")
def fork_shared_session(
    token: str, request: Request, user: dict = Depends(current_user)
):
    """Clone a shared chat into the viewer's own session list."""
    if not use_postgres_auth():
        raise HTTPException(501, "share is only available on postgres deployment")

    from chat_pg import fork_shared_session_pg, get_shared_session_pg

    original = get_shared_session_pg(token, user["id"])
    if not original:
        raise HTTPException(404, "ลิงค์นี้ใช้งานไม่ได้แล้ว")

    new_session_id = fork_shared_session_pg(token, user["id"])
    if not new_session_id:
        raise HTTPException(404, "ลิงค์นี้ใช้งานไม่ได้แล้ว")

    audit_log(
        "chat_session_forked",
        user=user,
        detail={
            "source_session_id": original["id"],
            "source_owner": original["owner_username"],
            "new_session_id": new_session_id,
        },
        request=request,
    )

    return {"session_id": new_session_id}


@app.patch("/chat/sessions/{session_id}")
def rename_session(session_id: int, body: SessionRenameIn, user: dict = Depends(current_user)):
    title = body.title.strip()[:80]

    if not title:
        raise HTTPException(400, "title required")

    if use_postgres_auth():
        from chat_pg import rename_session_pg

        updated = rename_session_pg(session_id, user["id"], title)
        if not updated:
            raise HTTPException(404, "session not found")
        return {"ok": True, "title": title}

    conn = get_db()
    res = conn.execute(
        "UPDATE chat_sessions SET title = ? WHERE id = ? AND user_id = ?",
        (title, session_id, user["id"]),
    )
    conn.commit()

    if res.rowcount == 0:
        raise HTTPException(404, "session not found")

    return {"ok": True, "title": title}


@app.patch("/chat/sessions/{session_id}/save")
def toggle_save_session(
    session_id: int,
    body: SessionSaveIn,
    user: dict = Depends(current_user),
):
    if use_postgres_auth():
        from chat_pg import toggle_save_session_pg

        updated = toggle_save_session_pg(session_id, user["id"], body.is_saved)
        if not updated:
            raise HTTPException(404, "session not found")
        return {"ok": True, "is_saved": body.is_saved}

    conn = get_db()
    res = conn.execute(
        "UPDATE chat_sessions SET is_saved = ? WHERE id = ? AND user_id = ?",
        (1 if body.is_saved else 0, session_id, user["id"]),
    )
    conn.commit()

    if res.rowcount == 0:
        raise HTTPException(404, "session not found")

    return {"ok": True, "is_saved": body.is_saved}


@app.delete("/chat/sessions/{session_id}")
def delete_session(request: Request, session_id: int, user: dict = Depends(current_user)):
    if use_postgres_auth():
        from chat_pg import delete_session_pg

        file_paths = delete_session_pg(session_id, user["id"])
        if file_paths is None:
            raise HTTPException(404, "session not found")

        deleted_files = 0
        for fp in file_paths:
            if _safe_unlink_attachment(fp):
                deleted_files += 1

        audit_log(
            "chat_session_deleted",
            user=user,
            detail={
                "session_id": session_id,
                "deleted_files": deleted_files,
                "db_engine": "postgres",
            },
            request=request,
        )

        return {"ok": True, "deleted_files": deleted_files}

    conn = get_db()

    owned = conn.execute(
        "SELECT 1 FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()

    if not owned:
        raise HTTPException(404, "session not found")

    file_paths = [
        r["file_path"]
        for r in conn.execute(
            """
            SELECT a.file_path FROM attachments a
            JOIN chat_history h ON h.id = a.message_id
            WHERE h.session_id = ?
            """,
            (session_id,),
        ).fetchall()
    ]

    conn.execute(
        "DELETE FROM attachments WHERE message_id IN "
        "(SELECT id FROM chat_history WHERE session_id = ?)",
        (session_id,),
    )
    conn.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
    conn.execute(
        "DELETE FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    )
    conn.commit()

    deleted_files = 0

    for fp in file_paths:
        if _safe_unlink_attachment(fp):
            deleted_files += 1

    audit_log(
        "chat_session_deleted",
        user=user,
        detail={"session_id": session_id, "deleted_files": deleted_files},
        request=request,
    )

    return {"ok": True, "deleted_files": deleted_files}


@app.get("/chat/source/{knowledge_id}")
@limiter.limit("30/minute")
def download_source_document(
    knowledge_id: int,
    request: Request,
    user: dict = Depends(current_user),
):
    """Stream the original .docx / .pdf / .pptx that produced a KB chunk.

    `source_file` is stored as a bare filename; we walk `data/kb_sources/`
    (preferring the subfolder named by `source_dept`) to find the actual file
    on disk so the resolution is robust to subfolder renames.
    """
    import mimetypes
    from pathlib import Path
    from urllib.parse import quote

    if use_postgres_auth():
        from db_pg import get_pg_conn

        with get_pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_file, source_dept, COALESCE(confidentiality, 'internal')
                    FROM knowledge
                    WHERE id = %s
                    """,
                    (knowledge_id,),
                )
                row = cur.fetchone()
    else:
        conn = get_db()
        sql_row = conn.execute(
            """
            SELECT source_file, source_dept,
                   COALESCE(confidentiality, 'internal') AS confidentiality
            FROM knowledge WHERE id = ?
            """,
            (knowledge_id,),
        ).fetchone()
        row = (
            (sql_row["source_file"], sql_row["source_dept"], sql_row["confidentiality"])
            if sql_row
            else None
        )

    if not row or not row[0]:
        raise HTTPException(404, "ไม่พบเอกสารต้นฉบับ")

    source_file, source_dept, confidentiality = row

    # Confidential rows: admin-only download. Internal/public: any signed-in
    # user — same rule we already use elsewhere for KB visibility.
    if confidentiality == "confidential" and user.get("role") != "admin":
        audit_log(
            "source_download_denied_confidential",
            user=user,
            detail={"knowledge_id": knowledge_id, "source_file": source_file},
            request=request,
        )
        raise HTTPException(403, "เอกสารนี้เป็น confidential — เฉพาะ admin เท่านั้น")

    # Resolve the file. Start under <KB_ROOT>/<source_dept>/ if dept is set;
    # fall back to a recursive walk so dept renames / nested subfolders don't
    # break things.
    kb_root = Path("data/kb_sources").resolve()
    if not kb_root.exists():
        raise HTTPException(500, "KB source directory not configured")

    resolved: Path | None = None

    def _candidate_match(p: Path) -> bool:
        return p.is_file() and p.name == source_file

    if source_dept:
        # Search inside data/kb_sources/**/<source_dept>/...
        for parent in kb_root.rglob(source_dept):
            if not parent.is_dir():
                continue
            for child in parent.rglob(source_file):
                if _candidate_match(child):
                    resolved = child
                    break
            if resolved:
                break

    if resolved is None:
        # Last-resort fallback: scan the whole tree.
        for child in kb_root.rglob(source_file):
            if _candidate_match(child):
                resolved = child
                break

    if resolved is None:
        audit_log(
            "source_download_missing",
            user=user,
            detail={
                "knowledge_id": knowledge_id,
                "source_file": source_file,
                "source_dept": source_dept,
            },
            request=request,
        )
        raise HTTPException(404, f"เอกสารต้นฉบับหายไปจาก server: {source_file}")

    # Path-traversal guard: the resolved path must still be inside kb_root.
    try:
        resolved.resolve().relative_to(kb_root)
    except ValueError:
        raise HTTPException(400, "invalid source path")

    mime, _ = mimetypes.guess_type(resolved.name)
    if not mime:
        mime = "application/octet-stream"

    audit_log(
        "source_download",
        user=user,
        detail={
            "knowledge_id": knowledge_id,
            "source_file": source_file,
            "source_dept": source_dept,
            "size_bytes": resolved.stat().st_size,
        },
        request=request,
    )

    # RFC 5987 filename* keeps Thai filenames intact across browsers.
    safe_ascii = source_file.encode("ascii", "replace").decode("ascii")
    cd = (
        f'attachment; filename="{safe_ascii}"; '
        f"filename*=UTF-8''{quote(source_file)}"
    )

    return FileResponse(
        path=str(resolved),
        media_type=mime,
        filename=source_file,
        headers={"Content-Disposition": cd},
    )


@app.post("/chat/export-pdf")
@limiter.limit("20/minute")
def export_pdf(
    request: Request,
    body: ExportPdfIn,
    user: dict = Depends(current_user),
):
    """Render arbitrary markdown content as a styled PDF (Thai + emoji ready).

    Any signed-in user can export their own chat answers — there's no admin
    gate. The endpoint takes the markdown straight from the client (the bot's
    answer is the typical caller) so the request payload is self-contained
    and we don't have to fetch + authorize a specific message id.
    """
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content is required")

    from pdf_export import export_markdown_to_pdf

    try:
        pdf_bytes = export_markdown_to_pdf(
            content_md=content,
            title=body.title,
            user_question=body.user_question,
            generated_by=user.get("username"),
        )
    except Exception as exc:
        audit_log(
            "pdf_export_failed",
            user=user,
            detail={
                "error": str(exc)[:300],
                "content_length": len(content),
            },
            request=request,
        )
        raise HTTPException(500, f"pdf generation failed: {exc}") from exc

    audit_log(
        "pdf_export_success",
        user=user,
        detail={
            "content_length": len(content),
            "had_question": bool(body.user_question),
            "pdf_bytes": len(pdf_bytes),
        },
        request=request,
    )

    safe_filename = "Sirivatana_chat.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@app.post("/chat/export-xlsx")
@limiter.limit("20/minute")
def export_xlsx(
    request: Request,
    body: ExportXlsxIn,
    user: dict = Depends(current_user),
):
    """Export the Markdown tables in an answer as a real .xlsx workbook.

    Any signed-in user can export their own answers (no admin gate). The client
    posts the answer markdown; each table becomes a worksheet.
    """
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content is required")

    from xlsx_export import export_tables_to_xlsx

    try:
        xlsx_bytes = export_tables_to_xlsx(content, title=body.title)
    except Exception as exc:
        audit_log(
            "xlsx_export_failed",
            user=user,
            detail={"error": str(exc)[:300], "content_length": len(content)},
            request=request,
        )
        raise HTTPException(500, f"xlsx generation failed: {exc}") from exc

    audit_log(
        "xlsx_export_success",
        user=user,
        detail={"content_length": len(content), "xlsx_bytes": len(xlsx_bytes)},
        request=request,
    )

    return StreamingResponse(
        iter([xlsx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="Sirivatana_table.xlsx"',
            "Content-Length": str(len(xlsx_bytes)),
        },
    )


@app.post("/chat/export-docx")
@limiter.limit("20/minute")
def export_docx(
    request: Request,
    body: ExportDocxIn,
    user: dict = Depends(current_user),
):
    """Export an answer (markdown) as an editable Word .docx. Any signed-in
    user can export their own answers."""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content is required")

    from docx_export import export_markdown_to_docx

    try:
        docx_bytes = export_markdown_to_docx(content, title=body.title)
    except Exception as exc:
        audit_log(
            "docx_export_failed",
            user=user,
            detail={"error": str(exc)[:300], "content_length": len(content)},
            request=request,
        )
        raise HTTPException(500, f"docx generation failed: {exc}") from exc

    audit_log(
        "docx_export_success",
        user=user,
        detail={"content_length": len(content), "docx_bytes": len(docx_bytes)},
        request=request,
    )

    return StreamingResponse(
        iter([docx_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": 'attachment; filename="Sirivatana_chat.docx"',
            "Content-Length": str(len(docx_bytes)),
        },
    )


@app.get("/chat/search")
def search_chat(q: str = Query(..., min_length=1), user: dict = Depends(current_user)):
    if use_postgres_auth():
        from chat_pg import search_chat_pg

        return search_chat_pg(user["id"], q)
    like = f"%{q.lower()}%"
    rows = get_db().execute(
        """
        SELECT h.id, h.session_id, s.title AS session_title, h.question, h.answer, h.asked_at
        FROM chat_history h
        JOIN chat_sessions s ON s.id = h.session_id
        WHERE h.user_id = ?
          AND (LOWER(h.question) LIKE ? OR LOWER(h.answer) LIKE ?)
        ORDER BY h.id DESC
        LIMIT 50
        """,
        (user["id"], like, like),
    ).fetchall()

    return [dict(r) for r in rows]


@app.get("/chat/sessions/{session_id}/export")
@limiter.limit("10/minute")
def export_session(request: Request, session_id: int, user: dict = Depends(current_user)):
    if use_postgres_auth():
        from chat_pg import export_session_csv_pg

        exported = export_session_csv_pg(session_id, user["id"])
        if not exported:
            raise HTTPException(404, "session not found")

        filename, csv_text = exported

        audit_log(
            "chat_session_exported",
            user=user,
            detail={"session_id": session_id, "db_engine": "postgres"},
            request=request,
        )

        return StreamingResponse(
            iter([csv_text]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    conn = get_db()
    s = conn.execute(
        "SELECT id, title FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()

    if not s:
        raise HTTPException(404, "session not found")

    msgs = conn.execute(
        "SELECT asked_at, question, answer, source FROM chat_history "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "question", "answer", "source"])

    for m in msgs:
        writer.writerow([m["asked_at"], m["question"], m["answer"], m["source"]])

    audit_log(
        "chat_session_exported",
        user=user,
        detail={"session_id": session_id},
        request=request,
    )

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'},
    )


@app.get("/admin/chat-history")
def admin_chat_history(user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import admin_chat_history_pg

        return admin_chat_history_pg()
    rows = get_db().execute(
        """
        SELECT s.id, s.title, s.user_id, u.username, s.created_at, s.updated_at,
               (SELECT COUNT(*) FROM chat_history WHERE session_id = s.id) AS message_count
        FROM chat_sessions s
        JOIN users u ON u.id = s.user_id
        ORDER BY s.updated_at DESC
        LIMIT 500
        """
    ).fetchall()

    return [dict(r) for r in rows]


@app.get("/admin/chat-history/{session_id}")
def admin_session_messages(session_id: int, user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import admin_session_messages_pg

        session = admin_session_messages_pg(session_id)
        if not session:
            raise HTTPException(404, "session not found")
        return session
    conn = get_db()
    s = conn.execute(
        """
        SELECT s.id, s.title, s.user_id, u.username, s.created_at
        FROM chat_sessions s JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()

    if not s:
        raise HTTPException(404, "session not found")

    return {**dict(s), "messages": _messages_with_attachments(conn, session_id)}


@app.delete("/admin/chat-history/{session_id}")
def admin_delete_session(
    session_id: int,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Admin override delete — bypasses user_id check so admins can clean up
    any team member's session. The owner's attachments on disk are unlinked
    after the DB rows are gone (best-effort)."""
    if use_postgres_auth():
        from admin_pg import admin_delete_session_pg

        result = admin_delete_session_pg(session_id)
        if result is None:
            raise HTTPException(404, "session not found")

        deleted_files = sum(
            1 for fp in result["file_paths"] if _safe_unlink_attachment(fp)
        )

        audit_log(
            "admin_session_deleted",
            user=user,
            detail={
                "deleted_session_id": session_id,
                "owner_user_id": result["user_id"],
                "owner_username": result["username"],
                "session_title": result["title"],
                "deleted_files": deleted_files,
                "db_engine": "postgres",
            },
            request=request,
        )
        return {"ok": True, "deleted_files": deleted_files}

    conn = get_db()
    row = conn.execute(
        "SELECT s.id, s.title, s.user_id, u.username "
        "FROM chat_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.id = ?",
        (session_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "session not found")

    file_paths = [
        r["file_path"]
        for r in conn.execute(
            "SELECT a.file_path FROM attachments a "
            "JOIN chat_history h ON h.id = a.message_id "
            "WHERE h.session_id = ?",
            (session_id,),
        ).fetchall()
    ]

    conn.execute(
        "DELETE FROM attachments WHERE message_id IN "
        "(SELECT id FROM chat_history WHERE session_id = ?)",
        (session_id,),
    )
    conn.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    conn.commit()

    deleted_files = sum(1 for fp in file_paths if _safe_unlink_attachment(fp))

    audit_log(
        "admin_session_deleted",
        user=user,
        detail={
            "deleted_session_id": session_id,
            "owner_user_id": row["user_id"],
            "owner_username": row["username"],
            "session_title": row["title"],
            "deleted_files": deleted_files,
        },
        request=request,
    )
    return {"ok": True, "deleted_files": deleted_files}


@app.get("/admin/chat-history/export/all")
@limiter.limit("5/minute")
def admin_export_all(request: Request, user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import admin_export_all_chat_history_pg

        filename, csv_text = admin_export_all_chat_history_pg()

        audit_log(
            "admin_export_all_chat_history",
            user=user,
            detail={"db_engine": "postgres"},
            request=request,
        )

        return StreamingResponse(
            iter([csv_text]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    rows = get_db().execute(
        """
        SELECT u.username, s.title AS session_title, h.asked_at, h.question, h.answer, h.source
        FROM chat_history h
        JOIN chat_sessions s ON s.id = h.session_id
        JOIN users u ON u.id = h.user_id
        ORDER BY h.id ASC
        """
    ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["username", "session_title", "timestamp", "question", "answer", "source"])

    for r in rows:
        writer.writerow(
            [
                r["username"],
                r["session_title"],
                r["asked_at"],
                r["question"],
                r["answer"],
                r["source"],
            ]
        )

    audit_log(
        "admin_export_all_chat_history",
        user=user,
        detail={"row_count": len(rows)},
        request=request,
    )

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="all-chat-history.csv"'},
    )


def _admin_analytics_sqlite() -> dict:
    """SQLite mirror of admin_pg.admin_analytics_pg — keep shapes in sync."""
    conn = get_db()

    def scalar(sql: str) -> int:
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    total_messages = scalar("SELECT COUNT(*) FROM chat_history")
    total_sessions = scalar("SELECT COUNT(*) FROM chat_sessions")
    total_users = scalar("SELECT COUNT(*) FROM users")
    messages_7d = scalar(
        "SELECT COUNT(*) FROM chat_history "
        "WHERE asked_at >= datetime('now', '-7 days')"
    )

    votes = {
        r["vote"]: r["c"]
        for r in conn.execute(
            "SELECT vote, COUNT(*) c FROM answer_feedback GROUP BY vote"
        ).fetchall()
    }

    source_breakdown = [
        {"source": r["source"], "count": r["c"]}
        for r in conn.execute(
            "SELECT source, COUNT(*) c FROM chat_history "
            "GROUP BY source ORDER BY c DESC"
        ).fetchall()
    ]

    daily_volume = [
        {"day": r["d"], "count": r["c"]}
        for r in conn.execute(
            "SELECT date(asked_at) d, COUNT(*) c FROM chat_history "
            "WHERE asked_at >= datetime('now', '-13 days') "
            "GROUP BY d ORDER BY d"
        ).fetchall()
    ]

    top_unanswered = [
        {"question": r["question"], "ask_count": r["ask_count"]}
        for r in conn.execute(
            "SELECT question, ask_count FROM pending_questions "
            "WHERE status = 'pending' "
            "ORDER BY ask_count DESC, last_asked_at DESC LIMIT 10"
        ).fetchall()
    ]

    recent_downvotes = [
        {
            "question": r["question"],
            "reason": r["reason"],
            "username": r["username"],
            "created_at": r["created_at"],
        }
        for r in conn.execute(
            """
            SELECT h.question, af.reason, u.username, af.created_at
            FROM answer_feedback af
            JOIN chat_history h ON h.id = af.message_id
            JOIN users u ON u.id = af.user_id
            WHERE af.vote = 'down'
            ORDER BY af.created_at DESC LIMIT 10
            """
        ).fetchall()
    ]

    top_users = [
        {"username": r["username"], "count": r["c"]}
        for r in conn.execute(
            "SELECT u.username, COUNT(*) c FROM chat_history h "
            "JOIN users u ON u.id = h.user_id "
            "GROUP BY u.username ORDER BY c DESC LIMIT 10"
        ).fetchall()
    ]

    return {
        "totals": {
            "messages": total_messages,
            "sessions": total_sessions,
            "users": total_users,
            "messages_7d": messages_7d,
            "feedback_up": votes.get("up", 0),
            "feedback_down": votes.get("down", 0),
        },
        "source_breakdown": source_breakdown,
        "daily_volume": daily_volume,
        "top_unanswered": top_unanswered,
        "recent_downvotes": recent_downvotes,
        "top_users": top_users,
    }


@app.get("/admin/analytics")
def admin_analytics(user: dict = Depends(require_admin)):
    """Usage + answer-quality overview for the admin dashboard."""
    if use_postgres_auth():
        from admin_pg import admin_analytics_pg

        return admin_analytics_pg()
    return _admin_analytics_sqlite()


@app.get("/admin/dashboard/overview")
def admin_dashboard_overview(
    range: str = Query("7d", description="Preset range: today / 7d / 30d"),
    user: dict = Depends(require_admin),
):
    """Powers the polished KPI Dashboard tab.

    Returns KPI cards (with previous-period comparison for trend arrows),
    source distribution (brain/rag/llm/files/blocked), 7-day usage trend
    for the line chart, top questions + users + pending count, and a
    safety summary parsed out of audit.log.
    """
    if not use_postgres_auth():
        raise HTTPException(501, "Dashboard requires postgres deployment")

    from admin_pg import admin_dashboard_overview_pg

    if range not in {"today", "7d", "30d"}:
        range = "7d"
    return admin_dashboard_overview_pg(range_label=range)


@app.get("/admin/dashboard/export-pdf")
def admin_dashboard_export_pdf(
    range: str = Query("7d"),
    user: dict = Depends(require_admin),
):
    """Render the KPI dashboard as a polished A4 PDF.

    Re-uses the same overview payload + the existing WeasyPrint pipeline by
    flattening the dashboard into a markdown report (tables for KPI / top
    questions / top users), so we don't have to maintain a second template.
    """
    if not use_postgres_auth():
        raise HTTPException(501, "Dashboard requires postgres deployment")

    from admin_pg import admin_dashboard_overview_pg, dashboard_to_markdown
    from pdf_export import export_markdown_to_pdf

    if range not in {"today", "7d", "30d"}:
        range = "7d"
    payload = admin_dashboard_overview_pg(range_label=range)
    md = dashboard_to_markdown(payload)

    try:
        pdf_bytes = export_markdown_to_pdf(
            content_md=md,
            title=f"KPI Dashboard — {payload['range']['human']}",
            generated_by=user.get("username"),
        )
    except Exception as exc:
        raise HTTPException(500, f"pdf generation failed: {exc}") from exc

    safe_filename = f"Sirivatana_Dashboard_{range}.pdf"
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _usage_period(since: str | None, until: str | None) -> tuple:
    """Parse the usage-report range (Bangkok calendar days, both inclusive).
    Default: the current month so far."""
    from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

    today = _dt.now(_tz(_td(hours=7))).date()
    try:
        start = _date.fromisoformat(since) if since else today.replace(day=1)
        end = _date.fromisoformat(until) if until else today
    except ValueError:
        raise HTTPException(400, "since/until must be YYYY-MM-DD")
    if start > end or (end - start).days > 800:
        raise HTTPException(400, "invalid range (since ≤ until, at most ~2 years)")
    return start, end


@app.get("/admin/usage/report")
def admin_usage_report(
    since: str | None = Query(None, description="YYYY-MM-DD (Bangkok), default 1st of this month"),
    until: str | None = Query(None, description="YYYY-MM-DD (Bangkok, inclusive), default today"),
    seat_usd: float | None = Query(None, ge=0, le=1000, description="ChatGPT Business USD/user/month"),
    user: dict = Depends(require_admin),
):
    """AI usage (requests, tokens, cost) by month / user / feature / model,
    compared with ChatGPT Business seats. Reads the AI usage log."""
    if not use_postgres_auth():
        raise HTTPException(501, "Usage report requires postgres deployment")
    import usage_log

    start, end = _usage_period(since, until)
    seat = usage_log.CHATGPT_BUSINESS_SEAT_USD if seat_usd is None else seat_usd
    return usage_log.usage_report_pg(start, end, seat)


@app.get("/admin/usage/export.xlsx")
def admin_usage_export(
    request: Request,
    since: str | None = Query(None),
    until: str | None = Query(None),
    seat_usd: float | None = Query(None, ge=0, le=1000),
    user: dict = Depends(require_admin),
):
    """The usage report as an Excel workbook (summary, monthly, per user,
    per feature, per model, every log row)."""
    if not use_postgres_auth():
        raise HTTPException(501, "Usage report requires postgres deployment")
    import usage_log

    start, end = _usage_period(since, until)
    seat = usage_log.CHATGPT_BUSINESS_SEAT_USD if seat_usd is None else seat_usd
    data = usage_log.build_usage_xlsx(
        usage_log.usage_report_pg(start, end, seat), usage_log.usage_rows_pg(start, end)
    )
    audit_log("usage_report_export", user=user,
              detail={"since": start.isoformat(), "until": end.isoformat()}, request=request)
    filename = f"SiriChat_AI_usage_{start.isoformat()}_{end.isoformat()}.xlsx"
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )


@app.get("/admin/pending")
def list_pending(user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import list_pending_pg

        return list_pending_pg()
    rows = get_db().execute(
        """
        SELECT id, question, ask_count, first_asked_at, last_asked_at
        FROM pending_questions
        WHERE status = 'pending'
        ORDER BY ask_count DESC, last_asked_at DESC
        """
    ).fetchall()

    return [dict(r) for r in rows]


@app.post("/admin/pending/{pending_id}/answer")
@limiter.limit("30/minute")
def answer_pending(
    request: Request,
    pending_id: int,
    body: AnswerPendingIn,
    user: dict = Depends(require_admin),
):
    try:
        kid = resolve_pending(pending_id, body.answer.strip(), user["id"])
    except ValueError as e:
        raise HTTPException(404, str(e))

    audit_log(
        "admin_answered_pending_question",
        user=user,
        detail={"pending_id": pending_id, "knowledge_id": kid},
        request=request,
    )

    return {"knowledge_id": kid}


@app.post("/admin/pending/{pending_id}/ignore")
@limiter.limit("30/minute")
def ignore_pending(request: Request, pending_id: int, user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import ignore_pending_pg

        ignored = ignore_pending_pg(pending_id)
        if not ignored:
            raise HTTPException(404, "pending question not found")

        audit_log(
            "admin_ignored_pending_question",
            user=user,
            detail={"pending_id": pending_id, "db_engine": "postgres"},
            request=request,
        )

        return {"ok": True}
    conn = get_db()
    conn.execute("UPDATE pending_questions SET status = 'ignored' WHERE id = ?", (pending_id,))
    conn.execute("DELETE FROM pending_vec WHERE pending_id = ?", (pending_id,))
    conn.commit()

    audit_log(
        "admin_ignored_pending_question",
        user=user,
        detail={"pending_id": pending_id},
        request=request,
    )

    return {"ok": True}


@app.get("/admin/knowledge")
def list_knowledge(user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import list_knowledge_pg

        return list_knowledge_pg()
    rows = get_db().execute(
        "SELECT id, question, answer, hit_count, approved_at, source FROM knowledge ORDER BY id DESC"
    ).fetchall()

    return [dict(r) for r in rows]


@app.post("/admin/knowledge/{kid}/verify")
@limiter.limit("30/minute")
def verify_knowledge(request: Request, kid: int, user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import verify_knowledge_pg

        verified = verify_knowledge_pg(kid, user["id"])
        if not verified:
            raise HTTPException(404, "knowledge not found")

        audit_log(
            "admin_verified_knowledge",
            user=user,
            detail={"knowledge_id": kid, "db_engine": "postgres"},
            request=request,
        )

        return {"ok": True}
    conn = get_db()
    conn.execute(
        "UPDATE knowledge SET source = 'admin', approved_by = ? WHERE id = ?",
        (user["id"], kid),
    )
    conn.commit()

    audit_log(
        "admin_verified_knowledge",
        user=user,
        detail={"knowledge_id": kid},
        request=request,
    )

    return {"ok": True}


@app.post("/admin/knowledge")
@limiter.limit("30/minute")
def create_knowledge(request: Request, body: KnowledgeIn, user: dict = Depends(require_admin)):
    question = body.question.strip()
    answer = body.answer.strip()

    if not question or not answer:
        raise HTTPException(400, "question and answer are required")

    kid = add_knowledge(question, answer, user["id"])

    audit_log(
        "admin_created_knowledge",
        user=user,
        detail={"knowledge_id": kid, "question": question[:200]},
        request=request,
    )

    return {"id": kid}


@app.delete("/admin/knowledge/{kid}")
@limiter.limit("30/minute")
def delete_knowledge(request: Request, kid: int, user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import delete_knowledge_pg

        deleted = delete_knowledge_pg(kid)
        if not deleted:
            raise HTTPException(404, "knowledge not found")

        audit_log(
            "admin_deleted_knowledge",
            user=user,
            detail={"knowledge_id": kid, "db_engine": "postgres"},
            request=request,
        )

        return {"ok": True}
    conn = get_db()
    conn.execute("DELETE FROM knowledge WHERE id = ?", (kid,))
    conn.execute("DELETE FROM knowledge_vec WHERE knowledge_id = ?", (kid,))
    conn.commit()

    audit_log(
        "admin_deleted_knowledge",
        user=user,
        detail={"knowledge_id": kid},
        request=request,
    )

    return {"ok": True}


# ───────────────────────── User Management (admin only) ─────────────────────


@app.get("/admin/users")
def admin_list_users(user: dict = Depends(require_admin)):
    if use_postgres_auth():
        from admin_pg import admin_list_users_pg

        return admin_list_users_pg()

    rows = get_db().execute(
        """
        SELECT
            u.id, u.username, u.role,
            COALESCE(u.is_disabled, 0) AS is_disabled,
            u.created_at,
            (SELECT COUNT(*) FROM chat_sessions s WHERE s.user_id = u.id) AS chat_count,
            (SELECT MAX(s.updated_at) FROM chat_sessions s WHERE s.user_id = u.id) AS last_active
        FROM users u
        ORDER BY u.id ASC
        """
    ).fetchall()
    return [
        {
            "id": r["id"],
            "username": r["username"],
            "role": r["role"],
            "is_disabled": bool(r["is_disabled"]),
            "created_at": r["created_at"],
            "chat_count": r["chat_count"] or 0,
            "last_active": r["last_active"],
        }
        for r in rows
    ]


@app.patch("/admin/users/{user_id}/role")
def admin_set_user_role(
    user_id: int,
    body: UserRoleIn,
    request: Request,
    user: dict = Depends(require_admin),
):
    new_role = body.role.strip().lower()
    if new_role not in {"user", "admin"}:
        raise HTTPException(400, "role must be 'user' or 'admin'")

    if use_postgres_auth():
        from admin_pg import (
            admin_count_active_admins_pg,
            admin_get_user_pg,
            admin_set_user_role_pg,
        )

        target = admin_get_user_pg(user_id)
        if not target:
            raise HTTPException(404, "user not found")

        # Block demoting the last active admin (otherwise nobody can manage).
        if target["role"] == "admin" and new_role == "user":
            others = admin_count_active_admins_pg(exclude_user_id=user_id)
            if others == 0:
                raise HTTPException(
                    400, "ไม่สามารถลดสิทธิ์ admin คนสุดท้ายได้ — ต้องมี admin ที่ใช้งานอย่างน้อย 1 คน"
                )

        # Don't let an admin demote themselves into a corner.
        if user_id == user["id"] and new_role == "user":
            raise HTTPException(400, "ไม่สามารถลดสิทธิ์ตัวเองได้")

        admin_set_user_role_pg(user_id, new_role)

        audit_log(
            "admin_user_role_changed",
            user=user,
            detail={
                "target_user_id": user_id,
                "target_username": target["username"],
                "previous_role": target["role"],
                "new_role": new_role,
                "db_engine": "postgres",
            },
            request=request,
        )
        return {"ok": True, "role": new_role}

    raise HTTPException(501, "sqlite path not implemented")


@app.patch("/admin/users/{user_id}/status")
def admin_set_user_status(
    user_id: int,
    body: UserStatusIn,
    request: Request,
    user: dict = Depends(require_admin),
):
    if use_postgres_auth():
        from admin_pg import (
            admin_count_active_admins_pg,
            admin_get_user_pg,
            admin_set_user_status_pg,
        )

        target = admin_get_user_pg(user_id)
        if not target:
            raise HTTPException(404, "user not found")

        if user_id == user["id"]:
            raise HTTPException(400, "ไม่สามารถปิดบัญชีตัวเองได้")

        # Block disabling the last active admin.
        if body.is_disabled and target["role"] == "admin" and not target["is_disabled"]:
            others = admin_count_active_admins_pg(exclude_user_id=user_id)
            if others == 0:
                raise HTTPException(
                    400, "ไม่สามารถปิดบัญชี admin คนสุดท้ายได้"
                )

        admin_set_user_status_pg(user_id, body.is_disabled)

        audit_log(
            "admin_user_status_changed",
            user=user,
            detail={
                "target_user_id": user_id,
                "target_username": target["username"],
                "is_disabled": body.is_disabled,
                "db_engine": "postgres",
            },
            request=request,
        )
        return {"ok": True, "is_disabled": body.is_disabled}

    raise HTTPException(501, "sqlite path not implemented")


@app.delete("/admin/users/{user_id}/chats")
def admin_delete_user_chats(
    user_id: int,
    request: Request,
    user: dict = Depends(require_admin),
):
    """Bulk-delete every chat session owned by `user_id`."""
    if use_postgres_auth():
        from admin_pg import admin_delete_user_chats_pg, admin_get_user_pg

        target = admin_get_user_pg(user_id)
        if not target:
            raise HTTPException(404, "user not found")

        result = admin_delete_user_chats_pg(user_id)

        deleted_files = sum(
            1 for fp in result["file_paths"] if _safe_unlink_attachment(fp)
        )

        audit_log(
            "admin_user_chats_bulk_deleted",
            user=user,
            detail={
                "target_user_id": user_id,
                "target_username": target["username"],
                "sessions_deleted": result["sessions_deleted"],
                "deleted_files": deleted_files,
                "db_engine": "postgres",
            },
            request=request,
        )
        return {
            "ok": True,
            "sessions_deleted": result["sessions_deleted"],
            "deleted_files": deleted_files,
        }

    raise HTTPException(501, "sqlite path not implemented")

# ----------------------------------------------------------------------------
# Document translation (Phase 1) — upload a manual PDF, translate it in the
# background (translate_manual pipeline), download the Thai Word file.
# ----------------------------------------------------------------------------
from pathlib import Path as _Path


@app.post("/translate/start")
@limiter.limit("10/minute")
async def translate_start(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
):
    """Upload a PDF and queue a background translation. Returns the job."""
    is_pdf = att.is_pdf(file.content_type or "") or (file.filename or "").lower().endswith(".pdf")
    if not is_pdf:
        raise HTTPException(400, "รองรับเฉพาะไฟล์ PDF")
    filename, _ct, _size, file_path = await att.save_upload(file)
    job = doctr.start_job(file_path, filename, user["id"])
    audit_log(
        "translate_started",
        user=user,
        detail={"job_id": job["id"], "filename": filename, "pages": job["total"]},
        request=request,
    )
    return job


@app.get("/translate/status/{job_id}")
def translate_status(job_id: str, user: dict = Depends(current_user)):
    job = doctr.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "ไม่พบงานแปล")
    return job


@app.get("/translate/jobs")
def translate_jobs(user: dict = Depends(current_user)):
    return doctr.list_jobs(user["id"])


@app.delete("/translate/jobs/{job_id}")
def translate_delete(job_id: str, user: dict = Depends(current_user)):
    if not doctr.delete_job(job_id, user["id"]):
        raise HTTPException(404, "ไม่พบงานแปล")
    return {"ok": True}


@app.get("/translate/download/{job_id}")
def translate_download(job_id: str, fmt: str = "docx", user: dict = Depends(current_user)):
    job = doctr.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "ไม่พบงานแปล")
    if job["status"] != "done":
        raise HTTPException(409, "งานแปลยังไม่เสร็จ")
    path = job["pdf"] if (fmt == "pdf" and job.get("pdf")) else job["docx"]
    if not path or not _Path(path).exists():
        raise HTTPException(404, "ไม่พบไฟล์ผลลัพธ์")
    ext = _Path(path).suffix
    base = _Path(job["filename"]).stem or "document"
    media = (
        "application/pdf" if ext == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path=path, media_type=media, filename=f"{base}_แปลไทย{ext}")


@app.get("/translate/review/{job_id}")
def translate_review(job_id: str, user: dict = Depends(current_user)):
    job = doctr.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "ไม่พบงานแปล")
    review = doctr.get_review(job_id)
    if review is None:
        raise HTTPException(404, "ไม่พบรายงานตรวจทาน")
    return review
