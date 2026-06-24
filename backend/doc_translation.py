"""
งานแปลเอกสารเบื้องหลัง (Phase 1) — ห่อ translate_manual.translate_pdf ด้วย job
registry ในหน่วยความจำ เพื่อให้แชทบอท:
  - เริ่มงานแปล (start_job) แล้วคืนทันที ไม่บล็อก request
  - ดึงสถานะ/ความคืบหน้า (get_job) สำหรับ polling
  - ดาวน์โหลดผลลัพธ์เมื่อเสร็จ

ข้อจำกัด Phase 1 (ตั้งใจให้เรียบง่าย เชื่อถือได้):
  - jobs เก็บในหน่วยความจำ — ถ้า backend restart ระหว่างแปล งานจะหาย
    แต่ checkpoint รายหน้า (_pages) ทำให้รันใหม่ราคาถูก (ไม่จ่ายซ้ำ)
  - แปลทีละ MAX_CONCURRENT งาน (ดีฟอลต์ 1) กัน CPU/โควตา OpenAI พุ่ง
  - จำกัด MAX_PAGES หน้า/งาน (ดีฟอลต์ 150) — เกินจากนั้นแปลแค่ N หน้าแรก
"""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz  # PyMuPDF

import translate_manual as tm

OUTPUT_ROOT = Path(os.getenv("TRANSLATION_DIR", "./data/translations"))
MAX_PAGES = int(os.getenv("TRANSLATE_MAX_PAGES", "150"))
MAX_CONCURRENT = int(os.getenv("TRANSLATE_CONCURRENCY", "1"))
# Phase 2 = ออก PDF ด้วย (LibreOffice อยู่ใน container แล้ว). ถ้าหา soffice ไม่เจอ
# docx_to_pdf จะคืน None และระบบยังได้ .docx ตามปกติ (graceful).
MAKE_PDF = os.getenv("TRANSLATE_MAKE_PDF", "true").lower() in ("1", "true", "yes")

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _update(job_id: str, **kw) -> None:
    with _lock:
        if job_id in _jobs:
            _jobs[job_id].update(kw)


# ---------------------------------------------------------------- DB persistence
# งานแปลถูกบันทึกลง postgres เพื่อให้ประวัติไม่หายตอน backend restart และโหลดซ้ำได้
# ถ้าไม่มี DATABASE_URL (dev/sqlite) จะทำงานในหน่วยความจำอย่างเดียว (graceful)

# map คีย์ใน job dict -> ชื่อคอลัมน์ใน DB
_COL = {
    "filename": "filename", "status": "status", "total": "total_pages",
    "translated_pages": "translated_pages", "done": "done",
    "exceeds_cap": "exceeds_cap", "max_pages": "max_pages",
    "docx": "docx_path", "pdf": "pdf_path", "review": "review_path",
    "review_flagged": "review_flagged", "cost_usd": "cost_usd", "error": "error",
}
_SELECT = ("SELECT id, user_id, filename, status, total_pages, translated_pages, done, "
           "exceeds_cap, max_pages, docx_path, pdf_path, review_path, review_flagged, "
           "cost_usd, error FROM translation_jobs ")


def _db_on() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def _row_to_job(r) -> dict:
    return {
        "id": r[0], "user_id": r[1], "filename": r[2], "status": r[3],
        "total": r[4], "translated_pages": r[5], "done": r[6],
        "exceeds_cap": r[7], "max_pages": r[8], "docx": r[9], "pdf": r[10],
        "review": r[11], "review_flagged": r[12],
        "cost_usd": float(r[13]) if r[13] is not None else 0.0, "error": r[14],
    }


def _db_insert(job: dict) -> None:
    if not _db_on():
        return
    try:
        import db_pg
        with db_pg.get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO translation_jobs (id, user_id, filename, status, total_pages, "
                "translated_pages, done, exceeds_cap, max_pages) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (job["id"], job["user_id"], job["filename"], job["status"], job["total"],
                 job["translated_pages"], job["done"], job["exceeds_cap"], job["max_pages"]),
            )
    except Exception:
        pass


def _db_update(job_id: str, **fields) -> None:
    if not _db_on() or not fields:
        return
    sets, vals = [], []
    for k, v in fields.items():
        col = _COL.get(k)
        if col:
            sets.append(f"{col} = %s")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at = now()")
    try:
        import db_pg
        with db_pg.get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE translation_jobs SET {', '.join(sets)} WHERE id = %s",
                (*vals, job_id),
            )
    except Exception:
        pass


def _db_get(job_id: str) -> dict | None:
    if not _db_on():
        return None
    try:
        import db_pg
        with db_pg.get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute(_SELECT + "WHERE id = %s", (job_id,))
            r = cur.fetchone()
            return _row_to_job(r) if r else None
    except Exception:
        return None


def _db_list(user_id, limit: int = 50) -> list[dict]:
    if not _db_on():
        return []
    try:
        import db_pg
        with db_pg.get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute(_SELECT + "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                        (user_id, limit))
            return [_row_to_job(r) for r in cur.fetchall()]
    except Exception:
        return []


def mark_orphans_interrupted() -> None:
    """งานที่ค้าง 'queued'/'running' ตอน backend ดับ -> ตั้งเป็น error (worker หายไปแล้ว)
    เรียกตอน startup"""
    if not _db_on():
        return
    try:
        import db_pg
        with db_pg.get_pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE translation_jobs SET status='error', "
                "error='งานถูกหยุดเพราะระบบรีสตาร์ท — กรุณาอัปโหลดแปลใหม่', updated_at=now() "
                "WHERE status IN ('queued','running')"
            )
    except Exception:
        pass


def get_job(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        if j:
            return dict(j)
    return _db_get(job_id)          # หลัง restart: อ่านจาก DB


def list_jobs(user_id) -> list[dict]:
    db_jobs = _db_list(user_id)
    with _lock:
        mem = {j["id"]: dict(j) for j in _jobs.values() if j.get("user_id") == user_id}
    if not db_jobs:
        return list(mem.values())
    db_ids = {j["id"] for j in db_jobs}
    # งานในหน่วยความจำมีความคืบหน้าล่าสุด -> ใช้ทับของ DB
    merged = [mem.get(j["id"], j) for j in db_jobs]
    for jid, j in mem.items():
        if jid not in db_ids:
            merged.insert(0, j)
    return merged


def pdf_page_count(pdf_path: str) -> int:
    try:
        d = fitz.open(str(pdf_path))
        n = len(d)
        d.close()
        return n
    except Exception:
        return 0


def start_job(pdf_path: str, filename: str, user_id) -> dict:
    """คิวงานแปล คืน job dict (status='queued') ทันที"""
    pdf_path = str(pdf_path)
    total = pdf_page_count(pdf_path)
    job_id = uuid.uuid4().hex[:12]
    base = (Path(filename).stem or "document").strip() or "document"
    out_dir = OUTPUT_ROOT / job_id

    job = {
        "id": job_id,
        "user_id": user_id,
        "filename": filename,
        "status": "queued",          # queued | running | done | error
        "done": 0,
        "total": total,
        "translated_pages": min(total, MAX_PAGES) if total else 0,
        "exceeds_cap": total > MAX_PAGES,
        "max_pages": MAX_PAGES,
        "docx": None,
        "pdf": None,
        "review": None,
        "review_flagged": 0,
        "cost_usd": 0.0,
        "error": None,
    }
    with _lock:
        _jobs[job_id] = job
    _db_insert(job)
    _executor.submit(_run, job_id, pdf_path, str(out_dir), base)
    return dict(job)


def _run(job_id: str, pdf_path: str, out_dir: str, base: str) -> None:
    _update(job_id, status="running")
    _db_update(job_id, status="running")
    try:
        job = get_job(job_id) or {}
        pages = f"1-{MAX_PAGES}" if job.get("total", 0) > MAX_PAGES else None

        def progress(done: int, total: int) -> None:
            _update(job_id, done=done, total=total)
            if done % 10 == 0:                  # อัปเดต DB เป็นช่วงๆ (ไม่ถี่เกิน)
                _db_update(job_id, done=done, total=total)

        res = tm.translate_pdf(
            pdf_path, out_dir, base,
            pages=pages,
            make_pdf=MAKE_PDF,
            progress=progress,
            log=lambda _m: None,     # ไม่พิมพ์ลง stdout ของ backend
        )
        done_fields = dict(
            status="done",
            docx=res["docx"],
            pdf=res["pdf"],
            review=res["review"],
            review_flagged=res["n_flagged"],
            cost_usd=res["cost_usd"],
            done=res["n_pages"],
            total=res["n_pages"],
        )
        _update(job_id, **done_fields)
        _db_update(job_id, **done_fields)
    except Exception as e:  # noqa: BLE001 — เก็บ error ไว้ให้ frontend แสดง ไม่ให้ worker ตาย
        _update(job_id, status="error", error=str(e)[:300])
        _db_update(job_id, status="error", error=str(e)[:300])


def get_review(job_id: str) -> dict | None:
    """อ่านรายงานตรวจทาน (_ตรวจทาน.md) ของงาน แล้ว parse เป็นรายหน้า
    คืน {"pages": [{"page": N, "issues": [...]}], "raw": str} หรือ None"""
    import re
    job = get_job(job_id)
    if not job:
        return None
    path = job.get("review")
    if not path or not Path(path).exists():
        return {"pages": [], "raw": ""}
    text = Path(path).read_text(encoding="utf-8")
    pages: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = re.match(r"^##\s*หน้า\s*(\d+)", line)
        if m:
            cur = {"page": int(m.group(1)), "issues": []}
            pages.append(cur)
        elif line.startswith("- ") and cur is not None:
            cur["issues"].append(line[2:].strip())
    return {"pages": pages, "raw": text}
