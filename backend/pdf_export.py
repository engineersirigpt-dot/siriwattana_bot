"""
PDF export — renders markdown content as a styled A4 PDF.

Pipeline: markdown → HTML (with table/code extensions) → WeasyPrint → PDF bytes.

Fonts (installed via Dockerfile):
- Sarabun (fonts-thai-tlwg)   — Thai + Latin, brand-aligned baseline.
- Noto Color Emoji            — keeps 📘 ✅ ⚠️ etc. from rendering as boxes.
- DejaVu Sans (default)       — fallback for any glyph the others miss.

Output is A4 portrait, 18mm margins, with a purple brand header and a footer
that shows generation timestamp + page numbers.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from io import BytesIO

import markdown as _md
from weasyprint import HTML  # type: ignore[import-not-found]


# ── Markdown → HTML ──────────────────────────────────────────────────────────

_MD_EXTENSIONS = [
    "tables",            # GitHub-style pipe tables
    "fenced_code",       # ```python``` blocks
    "nl2br",             # single \n → <br> (matches chat UI behaviour)
    "sane_lists",        # don't merge adjacent lists with different markers
    "admonition",        # !!! note blocks
]


def _markdown_to_html(md_text: str) -> str:
    return _md.markdown(md_text or "", extensions=_MD_EXTENSIONS, output_format="html5")


# ── Styling ──────────────────────────────────────────────────────────────────

_CSS = """
@page {
    size: A4;
    margin: 18mm 16mm 22mm 16mm;

    @bottom-center {
        content: "หน้า " counter(page) " / " counter(pages);
        font-family: 'Sarabun', 'TH Sarabun New', 'DejaVu Sans', sans-serif;
        font-size: 9pt;
        color: #9ca3af;
    }
    @bottom-right {
        content: string(generated_at);
        font-family: 'Sarabun', 'TH Sarabun New', 'DejaVu Sans', sans-serif;
        font-size: 9pt;
        color: #9ca3af;
    }
}

body {
    font-family: 'Sarabun', 'TH Sarabun New', 'Noto Sans',
                 'DejaVu Sans', 'Noto Color Emoji', sans-serif;
    font-size: 11pt;
    line-height: 1.65;
    color: #1f2937;
}

.brand-header {
    border-bottom: 2px solid #7c3aed;
    padding-bottom: 8px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 12px;
}

.brand-header .brand-name {
    font-weight: 600;
    color: #6d28d9;
    font-size: 13pt;
}

.brand-header .brand-tag {
    color: #9ca3af;
    font-size: 9.5pt;
    margin-left: auto;
}

.doc-title {
    font-size: 17pt;
    color: #1f2937;
    margin: 4px 0 14px 0;
    font-weight: 600;
}

.meta-bar {
    /* Footer timestamp is rendered via @bottom-right + string(generated_at). */
    string-set: generated_at content();
    visibility: hidden;
    height: 0;
}

h1, h2, h3, h4 {
    color: #1f2937;
    font-weight: 600;
    margin-top: 14px;
    margin-bottom: 6px;
    line-height: 1.3;
}
h1 { font-size: 15pt; }
h2 { font-size: 13.5pt; color: #6d28d9; }
h3 { font-size: 12pt; }
h4 { font-size: 11pt; }

p { margin: 6px 0; }
ul, ol { margin: 6px 0; padding-left: 22px; }
li { margin: 2px 0; }
li::marker { color: #7c3aed; }

strong { color: #1f2937; font-weight: 600; }
em { color: #374151; }
a { color: #7c3aed; text-decoration: none; }

blockquote {
    border-left: 3px solid #c4b5fd;
    background: #f5f3ff;
    margin: 8px 0;
    padding: 6px 12px;
    color: #4b5563;
    border-radius: 0 6px 6px 0;
}

/* Inline code */
code {
    font-family: 'DejaVu Sans Mono', 'Consolas', 'Courier New', monospace;
    background: #f3f4f6;
    color: #6d28d9;
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.92em;
}

/* Code block */
pre {
    background: #1f2937;
    color: #f9fafb;
    padding: 10px 12px;
    border-radius: 6px;
    overflow-x: auto;
    font-family: 'DejaVu Sans Mono', 'Consolas', 'Courier New', monospace;
    font-size: 9.5pt;
    line-height: 1.45;
    margin: 8px 0;
    page-break-inside: avoid;
}
pre code {
    background: transparent;
    color: inherit;
    padding: 0;
    border-radius: 0;
}

/* Tables */
table {
    border-collapse: collapse;
    width: 100%;
    margin: 10px 0;
    font-size: 10pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #e5e7eb;
    padding: 6px 10px;
    text-align: left;
    vertical-align: top;
}
th {
    background: #f5f3ff;
    color: #6d28d9;
    font-weight: 600;
}
tbody tr:nth-child(even) {
    background: #fafafa;
}

hr {
    border: 0;
    border-top: 1px solid #e5e7eb;
    margin: 14px 0;
}

img { max-width: 100%; }
"""


# ── Public API ───────────────────────────────────────────────────────────────


def _safe(s: str) -> str:
    return _html.escape((s or "").strip())


def export_markdown_to_pdf(
    *,
    content_md: str,
    title: str | None = None,
    user_question: str | None = None,
    generated_by: str | None = None,
) -> bytes:
    """Render `content_md` (Markdown) as a styled A4 PDF.

    Args:
        content_md: The main content to render (markdown). Required.
        title: Document title shown under the header. If omitted, the user
            question is used; if that's also missing, falls back to a default.
        user_question: Optional — printed in a small italic line above the
            content so the export carries the original question.
        generated_by: Username shown in the timestamp footer, e.g.
            "Exported by 2690028".

    Returns:
        The complete PDF as a `bytes` object the caller can stream to the user.
    """
    body_html = _markdown_to_html(content_md or "")
    now_text = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    generated_line = (
        f"สร้างเมื่อ {now_text}"
        + (f" • โดย {_safe(generated_by)}" if generated_by else "")
    )
    doc_title = title or user_question or "บทสนทนา Sirivatana AI"

    question_block = (
        f'<p style="margin: 0 0 12px 0; color:#6b7280; font-size:10pt;">'
        f"<strong>คำถาม:</strong> {_safe(user_question)}</p>"
        if user_question
        else ""
    )

    full_html = f"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>{_safe(doc_title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="brand-header">
  <div class="brand-name">📘 ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน)</div>
  <div class="brand-tag">Sirivatana AI Chatbot</div>
</div>

<div class="meta-bar">{_safe(generated_line)}</div>

<h1 class="doc-title">{_safe(doc_title)}</h1>

{question_block}

{body_html}

</body>
</html>"""

    buf = BytesIO()
    HTML(string=full_html).write_pdf(target=buf)
    return buf.getvalue()
