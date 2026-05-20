"""Attachment helpers: save uploaded files and extract text from many document types."""

import base64
import io
import os
import uuid
from pathlib import Path

import fitz  # PyMuPDF
from fastapi import HTTPException, UploadFile

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./data/uploads"))
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10MB
SPARSE_TEXT_THRESHOLD = 100  # if PDF extracts < this many chars, treat as scanned
MAX_PDF_PAGES_AS_IMAGES = 5
PDF_RENDER_DPI = 150
MAX_EXTRACT_CHARS = 60_000

IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}
PDF_TYPES = {"application/pdf"}
DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
XLSX_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
PPTX_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Text/code files come with many different MIME types from browsers (text/plain,
# application/octet-stream, or empty). Allow them by extension instead of MIME.
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log",
    ".ini", ".env", ".cfg", ".conf", ".toml",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx",
    ".html", ".htm", ".css", ".scss", ".sass",
    ".sql", ".yaml", ".yml", ".xml",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs",
    ".rb", ".php", ".kt", ".swift", ".dart", ".lua", ".r", ".scala",
    ".vue", ".svelte", ".gradle", ".pl", ".pm",
}

MIME_ALLOWED = IMAGE_TYPES | PDF_TYPES | DOCX_TYPES | XLSX_TYPES | PPTX_TYPES


def _ensure_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _is_allowed(content_type: str, filename: str) -> bool:
    if content_type in MIME_ALLOWED:
        return True
    ext = Path(filename or "").suffix.lower()
    return ext in TEXT_EXTENSIONS


def _default_ext_for(content_type: str) -> str:
    if content_type in PDF_TYPES:
        return ".pdf"
    if content_type in DOCX_TYPES:
        return ".docx"
    if content_type in XLSX_TYPES:
        return ".xlsx"
    if content_type in PPTX_TYPES:
        return ".pptx"
    if content_type in IMAGE_TYPES:
        return ".png"
    return ".bin"


async def save_upload(upload: UploadFile) -> tuple[str, str, int, str]:
    if not _is_allowed(upload.content_type or "", upload.filename or ""):
        raise HTTPException(
            400,
            f"ไฟล์ประเภทนี้ไม่รองรับ ({upload.content_type or Path(upload.filename or '').suffix or 'unknown'})",
        )

    _ensure_dir()
    ext = Path(upload.filename or "").suffix.lower()
    if not ext:
        ext = _default_ext_for(upload.content_type or "")

    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / stored_name

    size = 0
    with file_path.open("wb") as f:
        while chunk := await upload.read(64 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                f.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(400, "ไฟล์ใหญ่เกิน 10MB")
            f.write(chunk)

    return upload.filename or stored_name, upload.content_type, size, str(file_path)


def is_image(content_type: str) -> bool:
    return content_type in IMAGE_TYPES


def is_pdf(content_type: str) -> bool:
    return content_type in PDF_TYPES


def is_docx(content_type: str, filename: str = "") -> bool:
    return content_type in DOCX_TYPES or filename.lower().endswith(".docx")


def is_xlsx(content_type: str, filename: str = "") -> bool:
    return content_type in XLSX_TYPES or filename.lower().endswith(".xlsx")


def is_pptx(content_type: str, filename: str = "") -> bool:
    return content_type in PPTX_TYPES or filename.lower().endswith(".pptx")


def is_text_file(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in TEXT_EXTENSIONS


def encode_image_data_url(file_path: str, content_type: str) -> str:
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[…ตัดเนื้อหาที่เหลือเพราะยาวเกิน…]"


def extract_pdf_text(file_path: str, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    try:
        doc = fitz.open(file_path)
        parts: list[str] = []
        total = 0
        for i, page in enumerate(doc):
            text = (page.get_text() or "").strip()
            if not text:
                continue
            chunk = f"\n[หน้า {i + 1}]\n{text}\n"
            if total + len(chunk) > max_chars:
                parts.append(chunk[: max_chars - total])
                parts.append("\n[…ตัดเนื้อหาที่เหลือเพราะยาวเกิน…]")
                break
            parts.append(chunk)
            total += len(chunk)
        doc.close()
        return "".join(parts).strip()
    except Exception as e:
        return f"(อ่าน PDF ไม่สำเร็จ: {e})"


def extract_docx_text(file_path: str, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    try:
        from docx import Document

        doc = Document(file_path)
        parts: list[str] = []
        total = 0

        def add(s: str) -> bool:
            nonlocal total
            if total + len(s) > max_chars:
                parts.append(s[: max_chars - total])
                parts.append("\n[…ตัดเนื้อหา…]")
                return False
            parts.append(s)
            total += len(s)
            return True

        for para in doc.paragraphs:
            t = para.text.strip()
            if not t:
                continue
            if not add(t + "\n"):
                return "".join(parts).strip()

        for t_idx, table in enumerate(doc.tables):
            if not add(f"\n[ตาราง {t_idx + 1}]\n"):
                return "".join(parts).strip()
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells) + "\n"
                if not add(row_text):
                    return "".join(parts).strip()

        return "".join(parts).strip()
    except Exception as e:
        return f"(อ่าน Word ไม่สำเร็จ: {e})"


def extract_xlsx_text(file_path: str, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    try:
        from openpyxl import load_workbook

        wb = load_workbook(file_path, read_only=True, data_only=True)
        parts: list[str] = []
        total = 0
        for sheet_name in wb.sheetnames:
            if total >= max_chars:
                break
            header = f"\n=== Sheet: {sheet_name} ===\n"
            parts.append(header)
            total += len(header)
            ws = wb[sheet_name]
            for row in ws.iter_rows(values_only=True):
                if all(v is None for v in row):
                    continue
                row_text = " | ".join("" if v is None else str(v) for v in row) + "\n"
                if total + len(row_text) > max_chars:
                    parts.append("[…ตัดเนื้อหา…]")
                    wb.close()
                    return "".join(parts).strip()
                parts.append(row_text)
                total += len(row_text)
        wb.close()
        return "".join(parts).strip()
    except Exception as e:
        return f"(อ่าน Excel ไม่สำเร็จ: {e})"


def extract_pptx_text(file_path: str, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    try:
        from pptx import Presentation

        prs = Presentation(file_path)
        parts: list[str] = []
        total = 0
        for i, slide in enumerate(prs.slides):
            if total >= max_chars:
                break
            header = f"\n=== Slide {i + 1} ===\n"
            parts.append(header)
            total += len(header)
            for shape in slide.shapes:
                if not getattr(shape, "has_text_frame", False):
                    continue
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if not t:
                        continue
                    chunk = t + "\n"
                    if total + len(chunk) > max_chars:
                        parts.append("[…ตัดเนื้อหา…]")
                        return "".join(parts).strip()
                    parts.append(chunk)
                    total += len(chunk)
        return "".join(parts).strip()
    except Exception as e:
        return f"(อ่าน PowerPoint ไม่สำเร็จ: {e})"


def extract_text_file(file_path: str, max_chars: int = MAX_EXTRACT_CHARS) -> str:
    """Read a plain text/code file. Tries UTF-8 first, falls back to common Thai encodings."""
    for enc in ("utf-8", "utf-8-sig", "cp874", "tis-620", "latin-1"):
        try:
            with open(file_path, "r", encoding=enc) as f:
                text = f.read(max_chars + 1)
            return _truncate(text, max_chars)
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return f"(อ่านไฟล์ไม่สำเร็จ: {e})"
    return "(อ่านไฟล์ไม่สำเร็จ: encoding ไม่รองรับ)"


def extract_any_text(file_path: str, content_type: str, filename: str) -> str:
    """Dispatch text extraction by file type. Returns '' for images (use vision instead)."""
    if is_pdf(content_type):
        return extract_pdf_text(file_path)
    if is_docx(content_type, filename):
        return extract_docx_text(file_path)
    if is_xlsx(content_type, filename):
        return extract_xlsx_text(file_path)
    if is_pptx(content_type, filename):
        return extract_pptx_text(file_path)
    if is_text_file(filename):
        return extract_text_file(file_path)
    return ""


def render_pdf_pages_as_images(
    file_path: str,
    max_pages: int = MAX_PDF_PAGES_AS_IMAGES,
    dpi: int = PDF_RENDER_DPI,
) -> list[str]:
    urls: list[str] = []
    try:
        doc = fitz.open(file_path)
        zoom = dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            buf = io.BytesIO(pix.tobytes("png"))
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            urls.append(f"data:image/png;base64,{b64}")
        doc.close()
    except Exception:
        pass
    return urls


def is_text_sparse(extracted: str) -> bool:
    return len(extracted.strip()) < SPARSE_TEXT_THRESHOLD
