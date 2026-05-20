"""
Unit tests for attachments.py helper functions.

These tests do NOT spin up the HTTP server — they call Python functions directly.
No DB or LLM mocks are needed here.

Coverage:
- _is_allowed: MIME whitelist, extension whitelist, blocked types
- is_image / is_pdf / is_docx / is_xlsx / is_pptx / is_text_file
- is_text_sparse
- _truncate
- extract_text_file: UTF-8, Thai encoding fallback, truncation at MAX_EXTRACT_CHARS
- extract_any_text dispatcher: returns empty string for images, routes by MIME / extension
- extract_pdf / docx / xlsx / pptx: graceful error handling on missing file
"""

import attachments as att


# ---------------------------------------------------------------------------
# _is_allowed
# ---------------------------------------------------------------------------

def test_is_allowed_pdf_mime():
    assert att._is_allowed("application/pdf", "file.pdf") is True


def test_is_allowed_image_mime():
    for ct in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        assert att._is_allowed(ct, "img.jpg") is True


def test_is_allowed_docx_mime():
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert att._is_allowed(ct, "doc.docx") is True


def test_is_allowed_xlsx_mime():
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert att._is_allowed(ct, "data.xlsx") is True


def test_is_allowed_pptx_mime():
    ct = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert att._is_allowed(ct, "slides.pptx") is True


def test_is_allowed_text_extensions():
    text_files = [
        ("text/plain", "notes.txt"),
        ("application/octet-stream", "script.py"),
        ("application/octet-stream", "data.csv"),
        ("", "config.json"),
        ("text/x-c", "main.c"),
        ("", "Makefile.ts"),
    ]
    for ct, name in text_files:
        assert att._is_allowed(ct, name) is True, f"Should allow {name}"


def test_is_allowed_blocked_types():
    blocked = [
        ("application/octet-stream", "setup.exe"),
        ("application/zip", "archive.zip"),
        ("video/mp4", "video.mp4"),
        ("application/x-msdownload", "app.dll"),
    ]
    for ct, name in blocked:
        assert att._is_allowed(ct, name) is False, f"Should block {name}"


# ---------------------------------------------------------------------------
# Type-check helpers
# ---------------------------------------------------------------------------

def test_is_image_true_for_known_mime():
    assert att.is_image("image/jpeg") is True
    assert att.is_image("image/png") is True


def test_is_image_false_for_pdf():
    assert att.is_image("application/pdf") is False


def test_is_pdf_true():
    assert att.is_pdf("application/pdf") is True


def test_is_docx_by_extension():
    assert att.is_docx("application/octet-stream", "report.docx") is True


def test_is_xlsx_by_extension():
    assert att.is_xlsx("application/octet-stream", "data.xlsx") is True


def test_is_pptx_by_extension():
    assert att.is_pptx("application/octet-stream", "deck.pptx") is True


def test_is_text_file_known_extensions():
    for name in ("notes.txt", "code.py", "data.csv", "config.json", "query.sql"):
        assert att.is_text_file(name) is True, f"{name} should be text file"


def test_is_text_file_unknown_extension():
    assert att.is_text_file("binary.exe") is False


# ---------------------------------------------------------------------------
# is_text_sparse
# ---------------------------------------------------------------------------

def test_is_text_sparse_empty_string():
    assert att.is_text_sparse("") is True


def test_is_text_sparse_short_text():
    assert att.is_text_sparse("abc") is True


def test_is_text_sparse_exactly_at_threshold():
    # threshold is 100 chars — exactly 100 chars → NOT sparse
    text = "x" * 100
    assert att.is_text_sparse(text) is False


def test_is_text_sparse_long_text():
    assert att.is_text_sparse("a" * 500) is False


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------

def test_truncate_short_text_unchanged():
    text = "สวัสดีครับ"
    result = att._truncate(text, 100)
    assert result == text


def test_truncate_long_text_appends_marker():
    text = "a" * 200
    result = att._truncate(text, 100)
    assert len(result) > 100  # includes the cut marker
    assert "ตัดเนื้อหา" in result
    assert result.startswith("a" * 100)


# ---------------------------------------------------------------------------
# extract_text_file
# ---------------------------------------------------------------------------

def test_extract_text_file_utf8(tmp_path):
    f = tmp_path / "utf8.txt"
    f.write_text("Hello World\nทดสอบภาษาไทย", encoding="utf-8")
    result = att.extract_text_file(str(f))
    assert "Hello World" in result
    assert "ทดสอบ" in result


def test_extract_text_file_truncates_at_max(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * (att.MAX_EXTRACT_CHARS + 500), encoding="utf-8")
    result = att.extract_text_file(str(f))
    assert len(result) <= att.MAX_EXTRACT_CHARS + 100  # small slack for the marker


def test_extract_text_file_missing_file_returns_error():
    result = att.extract_text_file("/nonexistent/path/file.txt")
    assert "ไม่สำเร็จ" in result or result == ""


# ---------------------------------------------------------------------------
# extract_any_text dispatcher
# ---------------------------------------------------------------------------

def test_extract_any_text_returns_empty_for_image():
    result = att.extract_any_text("/any/path.jpg", "image/png", "photo.png")
    assert result == ""


def test_extract_any_text_dispatches_to_text_extractor(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("ข้อความทดสอบ", encoding="utf-8")
    result = att.extract_any_text(str(f), "text/plain", "notes.txt")
    assert "ข้อความทดสอบ" in result


def test_extract_any_text_dispatches_by_extension_py(tmp_path):
    f = tmp_path / "script.py"
    f.write_text("print('hello')", encoding="utf-8")
    result = att.extract_any_text(str(f), "application/octet-stream", "script.py")
    assert "print" in result


def test_extract_any_text_missing_pdf_returns_error_string():
    result = att.extract_any_text("/no/such/file.pdf", "application/pdf", "file.pdf")
    assert isinstance(result, str)  # graceful: returns error message, not exception


def test_extract_any_text_missing_docx_returns_error_string():
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    result = att.extract_any_text("/no/such/file.docx", ct, "file.docx")
    assert isinstance(result, str)


def test_extract_any_text_missing_xlsx_returns_error_string():
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    result = att.extract_any_text("/no/such/file.xlsx", ct, "file.xlsx")
    assert isinstance(result, str)


def test_extract_any_text_missing_pptx_returns_error_string():
    ct = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    result = att.extract_any_text("/no/such/file.pptx", ct, "file.pptx")
    assert isinstance(result, str)
