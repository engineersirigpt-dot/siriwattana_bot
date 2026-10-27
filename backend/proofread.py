"""
Proofreading attachments (V1.1): read literally → check → verify → report.

Why separate stages: vision models silently "fix" Thai while reading (they see
'สามรถ' as 'สามารถ'), and OCR misreads glyphs (ล็→ล๊, มงคล→ฌาปน). V1 trusted
the OCR text outright and confirmed 43 non-existent typos over 16 test runs
(incl. 17-20 on one blurry image). Here a typo is only confirmed when the image
actually carries it.

1. Read   — Tesseract (tha+eng) returns each glyph cluster with a confidence
            and a box; an English-only pass recovers Latin words tha+eng
            garbles. Nothing is corrected. Junk lines (split-off tone marks,
            icons) are dropped.
2. Check  — the LLM sees only that text, never the image, and lists candidates:
            a spelling error, or a language suggestion (style/spacing/wording).
3. Verify — in code: a candidate must occur verbatim in the read text and not
            sit inside a protected proper name; its line is re-read at 2-4x
            zoom. Zoom reads the corrected word → OCR misread, dropped. A typo
            is confirmed only if it persists, its glyphs were read confidently,
            the image is clear, and the difference isn't one OCR often confuses
            (swapped marks, ช/ซ…); otherwise it's listed as unclear.
4. Report — built in code: confirmed / unclear / suggestion tables, or
            "ไม่พบคำสะกดผิด". Markdown tables keep the Excel export working.
"""

from __future__ import annotations

import base64
import csv
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

import llm

OCR_LANGS = os.getenv("OCR_LANGS", "tha+eng")
OCR_TIMEOUT_S = int(os.getenv("OCR_TIMEOUT_S", "90"))
MAX_DOC_CHARS = 60_000

# Tesseract confidences are 0-100. Thresholds were set on the V1.1 test set
# (clean renders, a stylised AI poster, a blurred low-res scan).
JUNK_LINE_MAX_GLYPHS = 10      # short + low-confidence "lines" = split-off marks / icons
JUNK_LINE_MAX_CONF = 70
UNREADABLE_LINE_CONF = 70      # longer lines below this are reported as unreadable
CONFIRM_MIN_GLYPH_CONF = 80    # every glyph of a confirmed typo must be read this well
POOR_IMAGE_LOW_RATIO = 0.08    # >8% of glyphs under 80 → too unclear to confirm anything
ENGLISH_MIN_CONF = 80
# Zoomed re-reads target these line heights (px): Tesseract returned nothing or
# garbage for lines blown up to ~160px, so scale by size rather than a fixed 2-4x.
REREAD_LINE_HEIGHTS = (55, 75, 95)
MAX_UNCLEAR_PER_POOR_IMAGE = 10

GLOSSARY_PATH = Path(__file__).parent / "proofread_glossary.txt"

_MARKS = set("ัิีึืฺุู็่้๊๋์ํ๎")
# Glyph pairs Tesseract swaps in Thai fonts. A "typo" differing only by these
# (or by swapped marks) can't be confirmed from OCR. Classic real misspellings
# (ใ/ไ, ศ/ส/ษ) are deliberately NOT here — OCR tells those apart reliably.
_LOOKALIKE = [set(p) for p in ("ชซ", "ดต", "บป", "ผฝ", "พฟ", "ขฃ", "คฅ", "ฎฏ", "ถภ", "าำ", "ฑฒ")]
_PUNCT = re.compile(r"[\s.,;:!?\"'“”‘’()\[\]{}\-–—/\\|…]")
_LATIN_WORD = re.compile(r"^[A-Za-z][A-Za-z'’\-]*[A-Za-z]$")
_THAI_LETTER = re.compile(r"[ก-๏]")  # Thai letters + marks, not digits ๐-๙

_CHECK_PROMPT = (
    "คุณคือนักพิสูจน์อักษรภาษาไทยและอังกฤษ ตรวจข้อความที่ระบบอ่านมาให้ (คุณไม่เห็นภาพ — "
    "ข้อความคือสิ่งที่พิมพ์อยู่จริงทีละตัวอักษร ไม่ได้ถูกแก้) แล้วลิสต์จุดที่ควรแก้\n\n"
    "kind:\n"
    "- \"spelling\" = คำที่สะกดผิดจริงตามพจนานุกรมฉบับราชบัณฑิตยสภา/หลักการเขียนคำทับศัพท์ "
    "หรือคำอังกฤษที่สะกดผิด (เช่น สามรถ→สามารถ, อนุญาติ→อนุญาต, Pantome→Pantone)\n"
    "- \"suggestion\" = ไม่ใช่คำสะกดผิด แต่ควรปรับ: สำนวน ไวยากรณ์ การเว้นวรรค เครื่องหมายวรรคตอน "
    "ความสม่ำเสมอ ลำดับเลขข้อ คำที่ใช้ผิดความหมาย\n\n"
    "กฎ:\n"
    "1. found ต้องคัดลอกจากข้อความตรงทุกตัวอักษร เป็นช่วงสั้นที่สุดที่มีจุดผิด (ปกติ 1 คำ) "
    "ห้ามแก้ ห้ามใส่คำที่ไม่มีในข้อความ; suggest = ช่วงเดียวกันที่แก้แล้ว\n"
    "2. ชื่อเฉพาะถือว่าถูกต้อง: ชื่อคน สถานที่ วัด (เช่น วัดอรุณราชวรารามราชวรมหาวิหาร — "
    "ราชวราราม/ราชวรมหาวิหาร/วรวิหาร เป็นสร้อยนามพระอารามหลวง) บริษัท แบรนด์ รุ่นสินค้า มาตรฐาน "
    "ตัวย่อ — รายงานได้เฉพาะชื่อที่รู้จักแน่นอนและสะกดผิดชัดเจน\n"
    "3. ข้อความจาก OCR อาจมีเศษอักขระที่ไม่ใช่คำ (สัญลักษณ์ เส้นตาราง | ตัวอักษรไทยปนเลข/อังกฤษมั่ว ๆ) "
    "— ไม่ต้องรายงาน; คำภาษาอังกฤษในรูปให้ตรวจจากรายการ 'คำภาษาอังกฤษ' ที่แนบเท่านั้น\n"
    "4. การปรับคำที่ถูกอยู่แล้วให้ดีขึ้น ห้ามนับเป็น spelling\n"
    "5. ไม่แน่ใจ = ไม่ต้องรายงาน ถ้าไม่พบอะไรให้ส่ง items ว่าง ห้ามพยายามหาให้ได้\n"
    "source = ชื่อแหล่งตามหัวข้อ เช่น 'รูปที่ 1' หรือ 'ไฟล์ a.docx'; reason = เหตุผลสั้น ๆ ภาษาไทย"
)
_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "found": {"type": "string"},
                    "suggest": {"type": "string"},
                    "kind": {"type": "string", "enum": ["spelling", "suggestion"]},
                    "reason": {"type": "string"},
                },
                "required": ["source", "found", "suggest", "kind", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass
class _Glyph:
    text: str
    conf: float
    box: tuple[int, int, int, int]  # left, top, right, bottom


@dataclass
class _Line:
    no: int
    text: str                        # as printed by OCR (with spaces)
    glyphs: list[_Glyph] = field(default_factory=list)  # empty for digital documents

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (
            min(g.box[0] for g in self.glyphs), min(g.box[1] for g in self.glyphs),
            max(g.box[2] for g in self.glyphs), max(g.box[3] for g in self.glyphs),
        )


@dataclass
class _Source:
    label: str                       # "รูปที่ 1" / "ไฟล์ a.docx"
    lines: list[_Line] = field(default_factory=list)
    image: Image.Image | None = None
    english: list[_Glyph] = field(default_factory=list)
    unreadable: list[_Line] = field(default_factory=list)
    poor: bool = False
    notes: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    # Tesseract emits sara-am as nikhahit + sara-aa; compare without spaces.
    return re.sub(r"\s+", "", (s or "").replace("ํา", "ำ"))


def _tesseract(img: Image.Image, langs: str, psm: int, outputs: tuple[str, ...]) -> dict[str, str] | None:
    if not shutil.which("tesseract"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src, base = os.path.join(tmp, "in.png"), os.path.join(tmp, "out")
        img.save(src)
        try:
            # One OpenMP thread per process: parallel tesseract runs (concurrent
            # users, zoomed re-reads) otherwise spin-wait on each other — 16 at
            # once all hit the timeout instead of taking <1s each.
            subprocess.run(
                ["tesseract", src, base, "-l", langs, "--psm", str(psm), *outputs],
                capture_output=True, timeout=OCR_TIMEOUT_S, check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
        except Exception:
            return None
        return {o: Path(f"{base}.{o}").read_text(encoding="utf-8") for o in outputs if Path(f"{base}.{o}").exists()}


def _tsv_glyphs(tsv: str) -> list[list[_Glyph]]:
    """Level-5 TSV rows grouped by line, in reading order."""
    lines: dict[tuple[int, int, int], list[_Glyph]] = {}
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if r.get("level") != "5" or not (r.get("text") or "").strip():
            continue
        left, top = int(r["left"]), int(r["top"])
        box = (left, top, left + int(r["width"]), top + int(r["height"]))
        key = (int(r["block_num"]), int(r["par_num"]), int(r["line_num"]))
        lines.setdefault(key, []).append(_Glyph(r["text"], float(r["conf"]), box))
    return [lines[k] for k in sorted(lines)]


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _centred_in(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def _inline_english(line: _Line, english: list[_Glyph]) -> str:
    """The line's text with each run the Thai pass garbled replaced by the
    English pass's word at that spot ("พนักงาน 0ล๐ฮู๑เ๓0" → "พนักงาน Packageing"),
    so the checker sees English in context, not only in a side list."""
    words = [w for w in english if any(_centred_in(g.box, w.box) for g in line.glyphs)]
    if not words:
        return line.text
    out, pos, used = [], 0, set()
    text = line.text
    for g in line.glyphs:
        while pos < len(text) and text[pos].isspace():
            out.append(text[pos])
            pos += 1
        if text[pos:pos + len(g.text)] != g.text:
            return line.text  # text and glyphs don't align — leave it as read
        w = next((w for w in words if _centred_in(g.box, w.box)), None)
        if w is None:
            out.append(g.text)
        elif id(w) not in used:
            out.append(w.text)
            used.add(id(w))
        pos += len(g.text)
    return "".join(out) + text[pos:]


def _english_words(img: Image.Image, thai_glyphs: list[_Glyph]) -> list[_Glyph]:
    """Latin words from an English-only pass, minus Thai text it misread as English."""
    out = _tesseract(img, "eng", 6, ("tsv",))
    words: list[_Glyph] = []
    for line in _tsv_glyphs(out["tsv"]) if out else []:
        for w in line:
            text = w.text.strip(" ,.;:()[]\"'“”™®©")
            if w.conf < ENGLISH_MIN_CONF or not _LATIN_WORD.match(text):
                continue
            # Drop it if the Thai pass read real Thai there, at least as
            # confidently. English the Thai pass garbles comes out as a low-
            # confidence mix of Thai digits/letters ("Packageing" → "0ล๐ฮู๑เ๓0").
            under = [g for g in thai_glyphs if _overlaps(g.box, w.box)]
            chars = "".join(g.text for g in under)
            if under and len(_THAI_LETTER.findall(chars)) * 2 >= len(chars) \
                    and sum(g.conf for g in under) / len(under) >= w.conf - 5:
                continue
            words.append(_Glyph(text, w.conf, w.box))
    return words


def _read_image(data_url: str, label: str) -> _Source:
    img = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    img = ImageOps.exif_transpose(img).convert("RGB")
    src = _Source(label, image=img)
    out = _tesseract(img, OCR_LANGS, 6, ("txt", "tsv"))
    if not out:
        src.notes.append(f"{label}: อ่านข้อความในภาพไม่ได้ (ระบบ OCR ไม่พร้อม) จึงยังตรวจรูปนี้ไม่ได้")
        return src

    texts = [t for t in out.get("txt", "").splitlines() if t.strip()]
    n = 0
    for i, glyphs in enumerate(_tsv_glyphs(out.get("tsv", ""))):
        conf = sum(g.conf for g in glyphs) / len(glyphs)
        if len(glyphs) <= JUNK_LINE_MAX_GLYPHS and conf < JUNK_LINE_MAX_CONF:
            continue
        joined = "".join(g.text for g in glyphs)
        text = texts[i] if i < len(texts) and _norm(texts[i]) == _norm(joined) else joined
        n += 1
        line = _Line(n, text.strip(), glyphs)
        (src.unreadable if conf < UNREADABLE_LINE_CONF else src.lines).append(line)

    kept = [g for line in src.lines for g in line.glyphs]
    low = sum(g.conf < 80 for g in kept) / len(kept) if kept else 1.0
    src.poor = low > POOR_IMAGE_LOW_RATIO
    if src.poor:
        src.notes.append(
            f"{label}: ภาพไม่ชัดพอ ระบบจึงไม่ยืนยันคำผิดจากรูปนี้ (จุดที่น่าสงสัยอยู่ในหัวข้อ 'อ่านไม่ชัด') "
            "— ส่งภาพที่ชัดหรือความละเอียดสูงกว่านี้เพื่อผลที่แม่นยำ"
        )
    src.english = _english_words(img, kept)
    return src


def _read_document(name: str, text: str) -> _Source:
    src = _Source(f"ไฟล์ {name}")
    if len(text) > MAX_DOC_CHARS:
        src.notes.append(f"ไฟล์ {name}: ยาวเกินไป ตรวจเฉพาะ {MAX_DOC_CHARS:,} ตัวอักษรแรก")
        text = text[:MAX_DOC_CHARS]
    src.lines = [_Line(i, t.strip()) for i, t in enumerate(text.splitlines(), 1) if t.strip()]
    return src


def _glossary() -> list[str]:
    try:
        lines = GLOSSARY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [_norm(t) for t in lines if t.strip() and not t.lstrip().startswith("#")]


def _protected(found: str, line_text: str, glossary: list[str]) -> bool:
    """True when `found` is part of a correctly spelled proper name in this line."""
    f, line = _norm(found).lower(), _norm(line_text).lower()
    return any(f in term.lower() and term.lower() in line for term in glossary)


def _diff_kind(found: str, suggest: str) -> str:
    """spacing | english | mark | confusable | rewrite | other.

    mark = only missing/extra vowel or tone marks (สัญชาต, อนุญาติ) — zoomed
    re-reads catch OCR dropping marks, and OCR rarely invents them, so these
    are confirmable. Swapped marks (ล็→ล๊) or look-alike glyphs are not."""
    f, c = _norm(found), _norm(suggest)
    if _PUNCT.sub("", f) == _PUNCT.sub("", c):
        return "spacing"
    if f.isascii() and c.isascii():
        return "english"
    ops = [o for o in difflib.SequenceMatcher(None, f, c, autojunk=False).get_opcodes() if o[0] != "equal"]
    if sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops) > 3:
        return "rewrite"
    kinds = set()
    for op, i1, i2, j1, j2 in ops:
        a, b = f[i1:i2], c[j1:j2]
        if op in ("insert", "delete") and all(ch in _MARKS for ch in a + b):
            kinds.add("mark")
        elif all(ch in _MARKS for ch in a + b):
            kinds.add("confusable")
        elif op == "replace" and len(a) == len(b) == 1 and any({a, b} <= p for p in _LOOKALIKE):
            kinds.add("confusable")
        else:
            kinds.add("other")
    if "other" in kinds:
        return "other"
    return "mark" if kinds == {"mark"} else "confusable"


def _locate(src: _Source, found: str) -> tuple[_Line, list[_Glyph]] | None:
    """The line holding `found` verbatim, and the glyphs it spans (OCR lines)."""
    key = _norm(found)
    if not key:
        return None
    if key.isascii() and src.image is not None:
        for w in src.english:
            if w.text == found.strip():
                return _Line(0, w.text, [w]), [w]
    for line in src.lines:
        if not line.glyphs:
            if key in _norm(line.text):
                return line, []
            continue
        joined, starts = "", []
        for g in line.glyphs:
            starts.append(len(joined))
            joined += _norm(g.text)
        pos = joined.find(key)
        if pos >= 0:
            end = pos + len(key)
            span = [g for g, s in zip(line.glyphs, starts) if s < end and s + len(_norm(g.text)) > pos]
            return line, span
    return None


def _reread(src: _Source, line: _Line, langs: str, isolate: bool = True) -> list[str]:
    """Re-OCR one line at several zooms. The crop gets a small margin (so tone
    marks Tesseract split off stay in) and, with `isolate`, every glyph of the
    neighbouring lines is painted white — tight line spacing otherwise puts 2-3
    lines in the crop and single-line OCR returns nothing."""
    img = src.image
    left, top, right, bottom = line.box
    h = max(8, bottom - top)
    region = (
        max(0, left - h), max(0, top - int(0.35 * h)),
        min(img.width, right + h), min(img.height, bottom + int(0.25 * h)),
    )
    crop = img.crop(region)
    if isolate:
        draw = ImageDraw.Draw(crop)
        for other in src.lines + src.unreadable:
            if other is line:
                continue
            for g in other.glyphs:
                if _overlaps(g.box, region):
                    draw.rectangle(
                        (g.box[0] - region[0], g.box[1] - region[1], g.box[2] - region[0], g.box[3] - region[1]),
                        fill="white",
                    )
    readings = []
    for target in REREAD_LINE_HEIGHTS:
        s = target / h
        zoom = crop.resize((max(1, round(crop.width * s)), max(1, round(crop.height * s))), Image.LANCZOS)
        zoom = ImageOps.expand(zoom, border=24, fill="white")
        out = _tesseract(zoom, langs, 7, ("txt",))
        if out:
            readings.append(_norm(out["txt"]))
    return readings


def _present(word: str, other: str, text: str) -> bool:
    """`word` occurs in `text` outside occurrences of `other` (one may contain the other)."""
    if word in other:
        text = text.replace(other, "\x00")
    return word in text


def _decide(kind: str, readings: list[str], found: str, suggest: str, min_conf: float, poor: bool) -> str:
    """confirmed | unclear | misread, for an OCR-sourced spelling candidate."""
    f, c = _norm(found), _norm(suggest)
    f_hits = sum(_present(f, c, r) for r in readings)
    c_hits = sum(_present(c, f, r) for r in readings)
    if c_hits > f_hits:
        return "misread"  # zoomed in, the image reads the corrected word
    if poor or min_conf < CONFIRM_MIN_GLYPH_CONF or f_hits < 2 or c_hits:
        return "unclear"
    return "confirmed" if kind in ("other", "mark", "english") else "unclear"


_UNCLEAR_WHY = {
    "confusable": "ต่างกันแค่วรรณยุกต์/ตัวอักษรหน้าตาคล้ายกัน ซึ่ง OCR สับสนบ่อย",
    "rewrite": "ข้อความต่างจากที่ควรเป็นมาก อาจอ่านผิดหรือใช้คำผิด",
    "low": "อ่านจากภาพได้ไม่ชัด/ไม่สม่ำเสมอ",
}


def _llm_candidates(question: str, sources: list[_Source]) -> tuple[list[dict], dict]:
    blocks = []
    for s in sources:
        if not s.lines:
            continue
        body = "\n".join(f"L{line.no}: {_inline_english(line, s.english)}" for line in s.lines)
        if s.image is not None:
            blocks.append(f"=== {s.label} — ข้อความที่อ่านได้จากภาพ (ตามที่พิมพ์จริง ไม่ได้แก้คำ) ===\n{body}")
            if s.english:
                words = ", ".join(dict.fromkeys(w.text for w in s.english))
                blocks.append(f"--- คำภาษาอังกฤษใน{s.label} ---\n{words}")
        else:
            blocks.append(f"=== {s.label} — ข้อความดิจิทัลจากไฟล์ (ตรงตัวอักษร) ===\n{body}")
    if not blocks:
        return [], {}
    kwargs = llm._model_kwargs(llm.PROOFREAD_MODEL, 32000)
    if "reasoning_effort" in kwargs:
        kwargs["reasoning_effort"] = llm.PROOFREAD_REASONING_EFFORT
    res = llm._get_client().chat.completions.create(
        model=llm.PROOFREAD_MODEL,
        messages=[
            {"role": "system", "content": _CHECK_PROMPT},
            {"role": "user", "content": f"คำขอของผู้ใช้: {question}\n\n" + "\n\n".join(blocks)},
        ],
        response_format={"type": "json_schema", "json_schema": {"name": "proofread", "strict": True, "schema": _CHECK_SCHEMA}},
        **kwargs,
    )
    items = json.loads(res.choices[0].message.content or "{}").get("items", [])
    return items, llm._extract_usage(res, llm.PROOFREAD_MODEL)


def _cell(s: str) -> str:
    return (s or "-").replace("|", "/").replace("\n", " ").strip() or "-"


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(_cell(c) for c in [str(i)] + r) + " |" for i, r in enumerate(rows, 1)]
    return "\n".join(out)


def proofread(question: str, image_urls: list[str], doc_texts: list[tuple[str, str]]) -> tuple[str, dict]:
    """Proofread attached images/documents. Returns (markdown answer, usage)."""
    sources = [_read_image(u, f"รูปที่ {i}") for i, u in enumerate(image_urls, 1)]
    sources += [_read_document(name, text) for name, text in doc_texts]
    items, usage = _llm_candidates(question, sources)

    glossary = _glossary()
    confirmed: list[list[str]] = []
    suggestions: list[list[str]] = []
    unclear: list[tuple[_Source, float, list[str]]] = []  # (source, glyph confidence, row)
    pending: list[tuple] = []
    seen: set[tuple[str, str]] = set()

    for it in items:
        found, suggest = (it.get("found") or "").strip(), (it.get("suggest") or "").strip()
        # Look in the source the model named first, then everywhere else.
        named = [s for s in sources if s.label == it.get("source")]
        src, hit = None, None
        for s in named + [s for s in sources if s not in named]:
            hit = _locate(s, found)
            if hit:
                src = s
                break
        # No evidence in the text, a no-op, or a repeat → not reported.
        if not hit or not suggest or _norm(found) == _norm(suggest) or (src.label, _norm(found)) in seen:
            continue
        # A "correction" that changes script (sta→ต่อ, uaz→และ) is the model
        # recognising an OCR misread, not an error in the text; one with no
        # letters at all ("๐"→"•") is an icon or bullet read as a digit.
        if found.isascii() != suggest.isascii() or not re.search(r"[A-Za-zก-ฮ]", found):
            continue
        line, span = hit
        if _protected(found, line.text, glossary):
            continue
        # Latin text inside a Thai OCR line that the English pass didn't confirm
        # as a word is OCR debris (e.g. "ต่อ" read as "sia") — nothing to report.
        if src.image is not None and found.isascii() and not any(w.text == found for w in src.english):
            continue
        seen.add((src.label, _norm(found)))
        where = f"{src.label} บรรทัด {line.no}" if line.no else src.label
        kind = _diff_kind(found, suggest)
        row = [where, found, suggest, it.get("reason", "")]
        suggestion = it.get("kind") == "suggestion" or kind == "spacing"
        if src.image is None:  # digital text: what's written is exact
            (suggestions if suggestion or kind == "rewrite" else confirmed).append(row)
        elif kind == "spacing":
            continue  # OCR doesn't see spacing/punctuation reliably enough to advise on it
        else:
            pending.append((src, line, span, kind, row, suggestion))

    # Verify image candidates against the image, zoomed in. Suggestions too: a
    # "wrong word" in OCR text is often just a misread ("อาย" for "อายุ").
    def verify(p: tuple) -> tuple[str, str, float, tuple]:
        src, line, span, kind, row, suggestion = p
        english = kind == "english"
        readings = _reread(src, line, "eng" if english else OCR_LANGS, isolate=not english)
        min_conf = min((g.conf for g in span), default=0.0)
        verdict = _decide(kind, readings, row[1], row[2], min_conf, src.poor)
        if suggestion and verdict != "misread":
            verdict = "suggestion"
        legible = not src.poor and min_conf >= CONFIRM_MIN_GLYPH_CONF
        why = _UNCLEAR_WHY.get(kind, _UNCLEAR_WHY["low"]) if legible else _UNCLEAR_WHY["low"]
        return verdict, why, min_conf, p

    with ThreadPoolExecutor(4) as ex:
        for verdict, why, min_conf, (src, _, _, _, row, _) in ex.map(verify, pending):
            if verdict == "confirmed":
                confirmed.append(row)
            elif verdict == "suggestion":
                suggestions.append(row)
            elif verdict == "unclear":
                unclear.append((src, min_conf, row[:3] + [why]))

    for s in sources:
        for line in s.unreadable:
            unclear.append((s, 0.0, [f"{s.label} บรรทัด {line.no}", line.text[:60], "-", "บรรทัดนี้อ่านไม่ชัด ตรวจไม่ได้"]))

    # A blurred image yields dozens of doubtful spots; list the most legible
    # few per image rather than burying the answer.
    notes = [n for s in sources for n in s.notes]
    unclear_rows: list[list[str]] = []
    for s in sources:
        mine = [u for u in unclear if u[0] is s]
        if s.poor and len(mine) > MAX_UNCLEAR_PER_POOR_IMAGE:
            notes.append(
                f"{s.label}: ยังมีจุดที่อ่านไม่ชัดอีก {len(mine) - MAX_UNCLEAR_PER_POOR_IMAGE} จุด "
                f"(แสดงเฉพาะ {MAX_UNCLEAR_PER_POOR_IMAGE} จุดที่อ่านได้ชัดที่สุด)"
            )
            mine = sorted(mine, key=lambda u: -u[1])[:MAX_UNCLEAR_PER_POOR_IMAGE]
        unclear_rows += [u[2] for u in mine]

    parts = ["### คำสะกดผิดที่ยืนยันได้"]
    parts.append(
        _table(["#", "ตำแหน่ง", "ที่เขียนไว้", "ที่ถูกต้อง", "หมายเหตุ"], confirmed)
        if confirmed else "**ไม่พบคำสะกดผิด**"
    )
    if unclear_rows:
        parts.append("### ข้อความที่อ่านไม่ชัด — ควรตรวจด้วยตา")
        parts.append(_table(["#", "ตำแหน่ง", "อ่านได้ว่า", "อาจเป็น", "เหตุผล"], unclear_rows))
    if suggestions:
        parts.append("### ข้อเสนอแนะด้านภาษา (ไม่ใช่คำสะกดผิด)")
        parts.append(_table(["#", "ตำแหน่ง", "ข้อความเดิม", "ข้อเสนอแนะ", "เหตุผล"], suggestions))
    if notes:
        parts.append("หมายเหตุ:\n" + "\n".join(f"- {n}" for n in notes))
    return "\n\n".join(parts), usage
