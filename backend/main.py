import csv
import io
import os

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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import attachments as att
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
    answer_freely,
    answer_from_context,
    answer_with_files,
    classify_query,
)
from mi_auth import mi_enabled, verify_mi_credentials
from rag import add_knowledge, embed, log_pending_question, resolve_pending, search_knowledge
from sensitive import BLOCKED_RESPONSE, is_sensitive


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
    audit_log("app_startup", detail={"status": "ok"})


class ChatResponse(BaseModel):
    answer: str
    source: str
    similarity: float | None = None
    session_id: int
    session_title: str
    attachments: list[dict] = []


class KnowledgeIn(BaseModel):
    question: str
    answer: str


class AnswerPendingIn(BaseModel):
    answer: str


class SessionRenameIn(BaseModel):
    title: str


class SessionSaveIn(BaseModel):
    is_saved: bool


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
            "SELECT id, username, password_hash, role FROM users WHERE username = ?",
            (form.username,),
        ).fetchone()

    if row and verify_password(form.password, row["password_hash"]):
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
            }

        return create_user_pg(username, sentinel_hash, role="user")

    conn = get_db()
    existing = conn.execute(
        "SELECT id, username, role FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if existing:
        return {
            "id": existing["id"],
            "username": existing["username"],
            "role": existing["role"],
        }

    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, sentinel_hash, "user"),
    )
    conn.commit()
    new_id = cur.lastrowid
    return {"id": new_id, "username": username, "role": "user"}


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


def _title_from_question(question: str, max_len: int = 40) -> str:
    one_line = " ".join(question.split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 1] + "…"


HISTORY_TURNS = 6


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
        "INSERT INTO chat_sessions (user_id, title) VALUES (?, ?)",
        (user_id, title),
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
    question = message.strip()
    company_only = mode == "company"

    if not question and not files:
        raise HTTPException(400, "empty message")

    # Pre-filter: block sensitive questions before any upload / RAG / LLM call.
    # Saves OpenAI tokens and prevents the chatbot from ever attempting to answer.
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

    saved_files: list[dict] = []

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
            )

            history = get_session_history_pg(
                session_id=session_id,
                user_id=user["id"],
                limit=HISTORY_TURNS,
            )

            if saved_files:
                image_urls: list[str] = []
                text_attachments: list[tuple[str, str]] = []

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

                    if att.is_pdf(sf["content_type"]) and att.is_text_sparse(extracted):
                        rendered = att.render_pdf_pages_as_images(sf["file_path"])
                        image_urls.extend(rendered)

                        if extracted.strip():
                            text_attachments.append((sf["filename"], extracted))
                    elif extracted.strip():
                        text_attachments.append((sf["filename"], extracted))

                prompt_question = question or "ช่วยอธิบายเนื้อหาในไฟล์ที่แนบ และให้คำแนะนำที่เกี่ยวข้อง"
                answer = answer_with_files(
                    prompt_question,
                    image_urls,
                    text_attachments,
                    history=history,
                )
                source = "files"
                knowledge_id = None
                similarity = None

            else:
                category = classify_query(question)
                chosen_model = LLM_MODEL_CALC if category == "calc" else LLM_MODEL

                vec = embed(question)
                hit = search_knowledge(vec)

                if hit:
                    answer = answer_from_context(
                        question,
                        hit["question"],
                        hit["answer"],
                        model=chosen_model,
                        history=history,
                    )
                    source = "rag" if category == "general" else "rag-calc"
                    knowledge_id = hit["id"]
                    similarity = hit["similarity"]
                else:
                    log_pending_question(question, vec)

                    audit_log(
                        "pending_question_created",
                        user=user,
                        detail={"question": question[:300], "db_engine": "postgres"},
                        request=request,
                    )

                    answer = answer_freely(question, model=chosen_model, history=history, company_only=company_only)
                    source = "llm" if category == "general" else "llm-calc"
                    knowledge_id = None
                    similarity = None

            message_id = save_chat_message_pg(
                user_id=user["id"],
                session_id=sid,
                question=question or "[ไฟล์แนบ]",
                answer=answer,
                source=source,
                knowledge_id=knowledge_id,
            )

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
            )
        conn = get_db()
        sid, stitle = _ensure_session(
            conn, user["id"], session_id, question or saved_files[0]["filename"]
        )

        history = _get_session_history(conn, session_id, user["id"])

        if saved_files:
            image_urls: list[str] = []
            text_attachments: list[tuple[str, str]] = []

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

                if att.is_pdf(sf["content_type"]) and att.is_text_sparse(extracted):
                    rendered = att.render_pdf_pages_as_images(sf["file_path"])
                    image_urls.extend(rendered)

                    if extracted.strip():
                        text_attachments.append((sf["filename"], extracted))
                elif extracted.strip():
                    text_attachments.append((sf["filename"], extracted))

            prompt_question = question or "ช่วยอธิบายเนื้อหาในไฟล์ที่แนบ และให้คำแนะนำที่เกี่ยวข้อง"
            answer = answer_with_files(prompt_question, image_urls, text_attachments, history=history)
            source = "files"
            knowledge_id = None
            similarity = None

        else:
            category = classify_query(question)
            chosen_model = LLM_MODEL_CALC if category == "calc" else LLM_MODEL

            vec = embed(question)
            hit = search_knowledge(vec)

            if hit:
                answer = answer_from_context(
                    question,
                    hit["question"],
                    hit["answer"],
                    model=chosen_model,
                    history=history,
                )
                source = "rag" if category == "general" else "rag-calc"
                knowledge_id = hit["id"]
                similarity = hit["similarity"]

            else:
                log_pending_question(question, vec)

                audit_log(
                    "pending_question_created",
                    user=user,
                    detail={"question": question[:300]},
                    request=request,
                )

                answer = answer_freely(question, model=chosen_model, history=history)
                source = "llm" if category == "general" else "llm-calc"
                knowledge_id = None
                similarity = None

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

        conn.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?", (sid,))
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


def _messages_with_attachments(conn, session_id: int) -> list[dict]:
    msgs = conn.execute(
        "SELECT id, question, answer, source, asked_at FROM chat_history "
        "WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()

    result: list[dict] = []

    for m in msgs:
        atts = conn.execute(
            "SELECT id, filename, content_type, size_bytes FROM attachments WHERE message_id = ?",
            (m["id"],),
        ).fetchall()
        result.append({**dict(m), "attachments": [dict(a) for a in atts]})

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
        "SELECT id, title, created_at FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()

    if not s:
        raise HTTPException(404, "session not found")

    return {**dict(s), "messages": _messages_with_attachments(conn, session_id)}


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