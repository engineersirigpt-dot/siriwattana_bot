import csv
import io
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

import attachments as att
from auth import create_token, current_user, hash_password, require_admin, verify_password
from db import get_db
from llm import (
    LLM_MODEL,
    LLM_MODEL_CALC,
    answer_freely,
    answer_from_context,
    answer_with_files,
    classify_query,
)
from rag import add_knowledge, embed, log_pending_question, resolve_pending, search_knowledge

app = FastAPI(title="Siriwattan Chatbot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("CORS_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    get_db()  # initialize schema


class ChatResponse(BaseModel):
    answer: str
    source: str  # "rag", "llm", or "files"
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
def register(form: OAuth2PasswordRequestForm = Depends()):
    conn = get_db()
    existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (form.username,)).fetchone()
    if existing:
        raise HTTPException(400, "username already taken")
    role = "admin" if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0 else "user"
    conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (form.username, hash_password(form.password), role),
    )
    conn.commit()
    return {"ok": True, "role": role}


@app.post("/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    row = get_db().execute(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (form.username,),
    ).fetchone()
    if not row or not verify_password(form.password, row["password_hash"]):
        raise HTTPException(401, "invalid credentials")
    token = create_token(row["id"], row["username"], row["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": row["username"],
        "role": row["role"],
    }


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


def _title_from_question(question: str, max_len: int = 40) -> str:
    one_line = " ".join(question.split())
    return one_line if len(one_line) <= max_len else one_line[: max_len - 1] + "…"


HISTORY_TURNS = 6  # how many previous user+bot pairs to send back as context


def _get_session_history(conn, session_id: int | None, user_id: int) -> list[dict]:
    """Return last HISTORY_TURNS turns as OpenAI-style messages, oldest first.

    For turns that had file attachments, prepends the cached PDF text and a list
    of attached filenames so the LLM can answer follow-up questions about the
    same documents without the user re-attaching them.
    """
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


def _ensure_session(conn, user_id: int, session_id: int | None, first_question: str) -> tuple[int, str]:
    """Return (session_id, title). Creates a new session if session_id is None or doesn't belong to user."""
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


@app.post("/chat", response_model=ChatResponse)
async def chat(
    message: str = Form(...),
    session_id: int | None = Form(None),
    files: list[UploadFile] = File([]),
    user: dict = Depends(current_user),
):
    question = message.strip()
    if not question and not files:
        raise HTTPException(400, "empty message")

    # Persist any uploaded files first (we will link them to the chat row after insert).
    saved_files: list[dict] = []
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

    conn = get_db()
    sid, stitle = _ensure_session(
        conn, user["id"], session_id, question or saved_files[0]["filename"]
    )

    # Pull previous turns from the same session for conversational memory.
    history = _get_session_history(conn, session_id, user["id"])

    if saved_files:
        # Files attached — route to vision/file-capable model.
        # Images go through vision; everything else gets text-extracted.
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
            # Stash extracted text so follow-up turns in this session can see it.
            sf["extracted_text"] = extracted if extracted.strip() else None

            if att.is_pdf(sf["content_type"]) and att.is_text_sparse(extracted):
                # Scanned/image PDF — render pages as images so vision can read.
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
        # Text-only path: classify first to pick the right model.
        category = classify_query(question)
        chosen_model = LLM_MODEL_CALC if category == "calc" else LLM_MODEL

        vec = embed(question)
        hit = search_knowledge(vec)
        if hit:
            answer = answer_from_context(
                question, hit["question"], hit["answer"], model=chosen_model, history=history
            )
            source = "rag" if category == "general" else "rag-calc"
            knowledge_id = hit["id"]
            similarity = hit["similarity"]
        else:
            log_pending_question(question, vec)
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

    return ChatResponse(
        answer=answer,
        source=source,
        similarity=similarity,
        session_id=sid,
        session_title=stitle,
        attachments=attachment_rows,
    )


@app.get("/attachments/{aid}")
def get_attachment(aid: int, user: dict = Depends(current_user)):
    row = get_db().execute(
        "SELECT user_id, filename, content_type, file_path FROM attachments WHERE id = ?",
        (aid,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "attachment not found")
    if row["user_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "forbidden")
    return FileResponse(
        path=row["file_path"],
        media_type=row["content_type"],
        filename=row["filename"],
    )


UNSAVED_LIMIT = 20  # max unsaved sessions to keep per user


def _purge_unsaved_sessions(conn, user_id: int) -> None:
    """Delete unsaved sessions beyond the 20 most recent (oldest first).
    Saved sessions are never touched.
    """
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
    for fp in file_paths:
        try:
            os.remove(fp)
        except OSError:
            pass


@app.get("/chat/sessions")
def list_sessions(user: dict = Depends(current_user)):
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
    session_id: int, body: SessionSaveIn, user: dict = Depends(current_user)
):
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
def delete_session(session_id: int, user: dict = Depends(current_user)):
    conn = get_db()
    # Verify ownership before doing any destructive work.
    owned = conn.execute(
        "SELECT 1 FROM chat_sessions WHERE id = ? AND user_id = ?",
        (session_id, user["id"]),
    ).fetchone()
    if not owned:
        raise HTTPException(404, "session not found")

    # Collect file paths of every attachment in this session, then delete the rows.
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

    # Unlink files on disk. Do this last — DB state is the source of truth.
    deleted_files = 0
    for fp in file_paths:
        try:
            os.remove(fp)
            deleted_files += 1
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return {"ok": True, "deleted_files": deleted_files}


@app.get("/chat/search")
def search_chat(q: str = Query(..., min_length=1), user: dict = Depends(current_user)):
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
def export_session(session_id: int, user: dict = Depends(current_user)):
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

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'},
    )


@app.get("/admin/chat-history")
def admin_chat_history(user: dict = Depends(require_admin)):
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
def admin_export_all(user: dict = Depends(require_admin)):
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
        writer.writerow([r["username"], r["session_title"], r["asked_at"], r["question"], r["answer"], r["source"]])

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="all-chat-history.csv"'},
    )


@app.get("/admin/pending")
def list_pending(user: dict = Depends(require_admin)):
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
def answer_pending(pending_id: int, body: AnswerPendingIn, user: dict = Depends(require_admin)):
    try:
        kid = resolve_pending(pending_id, body.answer.strip(), user["id"])
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"knowledge_id": kid}


@app.post("/admin/pending/{pending_id}/ignore")
def ignore_pending(pending_id: int, user: dict = Depends(require_admin)):
    conn = get_db()
    conn.execute("UPDATE pending_questions SET status = 'ignored' WHERE id = ?", (pending_id,))
    conn.execute("DELETE FROM pending_vec WHERE pending_id = ?", (pending_id,))
    conn.commit()
    return {"ok": True}


@app.get("/admin/knowledge")
def list_knowledge(user: dict = Depends(require_admin)):
    rows = get_db().execute(
        "SELECT id, question, answer, hit_count, approved_at, source FROM knowledge ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/admin/knowledge/{kid}/verify")
def verify_knowledge(kid: int, user: dict = Depends(require_admin)):
    conn = get_db()
    conn.execute(
        "UPDATE knowledge SET source = 'admin', approved_by = ? WHERE id = ?",
        (user["id"], kid),
    )
    conn.commit()
    return {"ok": True}


@app.post("/admin/knowledge")
def create_knowledge(body: KnowledgeIn, user: dict = Depends(require_admin)):
    kid = add_knowledge(body.question.strip(), body.answer.strip(), user["id"])
    return {"id": kid}


@app.delete("/admin/knowledge/{kid}")
def delete_knowledge(kid: int, user: dict = Depends(require_admin)):
    conn = get_db()
    conn.execute("DELETE FROM knowledge WHERE id = ?", (kid,))
    conn.execute("DELETE FROM knowledge_vec WHERE knowledge_id = ?", (kid,))
    conn.commit()
    return {"ok": True}
