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


def get_job(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def list_jobs(user_id) -> list[dict]:
    with _lock:
        return [dict(j) for j in _jobs.values() if j.get("user_id") == user_id]


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
    _executor.submit(_run, job_id, pdf_path, str(out_dir), base)
    return dict(job)


def _run(job_id: str, pdf_path: str, out_dir: str, base: str) -> None:
    _update(job_id, status="running")
    try:
        job = get_job(job_id) or {}
        pages = f"1-{MAX_PAGES}" if job.get("total", 0) > MAX_PAGES else None

        def progress(done: int, total: int) -> None:
            _update(job_id, done=done, total=total)

        res = tm.translate_pdf(
            pdf_path, out_dir, base,
            pages=pages,
            make_pdf=MAKE_PDF,
            progress=progress,
            log=lambda _m: None,     # ไม่พิมพ์ลง stdout ของ backend
        )
        _update(
            job_id,
            status="done",
            docx=res["docx"],
            pdf=res["pdf"],
            review=res["review"],
            review_flagged=res["n_flagged"],
            cost_usd=res["cost_usd"],
            done=res["n_pages"],
            total=res["n_pages"],
        )
    except Exception as e:  # noqa: BLE001 — เก็บ error ไว้ให้ frontend แสดง ไม่ให้ worker ตาย
        _update(job_id, status="error", error=str(e)[:300])


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
