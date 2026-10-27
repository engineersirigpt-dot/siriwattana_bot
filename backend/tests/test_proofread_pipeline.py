"""
Tests for the V1.1 proofreading pipeline (proofread.py).

OCR and the LLM are faked so these run anywhere; the one real-Tesseract test
is skipped when tesseract isn't installed.
"""

import base64
import io
import shutil

import pytest
from PIL import Image, ImageDraw

import proofread as pr
from xlsx_export import export_tables_to_xlsx, parse_markdown_tables

WAT_ARUN = "วัดอรุณราชวรารามราชวรมหาวิหาร"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def glyph_line(no, text, conf=95.0, y=0):
    """An OCR line whose glyphs are its non-space characters, 10px apart."""
    chars = [c for c in text if not c.isspace()]
    glyphs = [pr._Glyph(c, conf, (i * 10, y, i * 10 + 9, y + 20)) for i, c in enumerate(chars)]
    return pr._Line(no, text, glyphs)


def image_source(*texts, conf=95.0, poor=False, english=()):
    lines = [glyph_line(i, t, conf, y=i * 30) for i, t in enumerate(texts, 1)]
    src = pr._Source("รูปที่ 1", lines, image=Image.new("RGB", (400, 30 * len(texts) + 30), "white"), poor=poor)
    src.english = [pr._Glyph(w, 95.0, (0, 0, 50, 20)) for w in english]
    return src


def item(found, suggest, kind="spelling", source="รูปที่ 1"):
    return {"source": source, "found": found, "suggest": suggest, "kind": kind, "reason": "r"}


@pytest.fixture
def run(monkeypatch):
    """run(sources, items, rereads={found: [readings]}) → markdown answer."""
    def _run(sources, items, rereads=None, docs=()):
        rereads = rereads or {}
        it = iter(sources)
        monkeypatch.setattr(pr, "_read_image", lambda url, label: next(it))
        monkeypatch.setattr(pr, "_llm_candidates", lambda q, srcs: (items, {"cost_usd": 0.01}))

        def fake_reread(src, line, langs, isolate=True):
            for found, readings in rereads.items():
                if pr._norm(found) in pr._norm(line.text):
                    return readings
            return [pr._norm(line.text)] * 3

        monkeypatch.setattr(pr, "_reread", fake_reread)
        answer, usage = pr.proofread("ตรวจคำผิด", ["img"] * len(sources), list(docs))
        assert usage == {"cost_usd": 0.01}
        return answer

    return _run


def section(answer, title):
    """Table rows (excluding header) under the heading containing `title`."""
    rows, inside = [], False
    for line in answer.splitlines():
        if line.startswith("###"):
            inside = title in line
        elif inside and line.startswith("| ") and not line.startswith("| #"):
            rows.append(line)
    return rows


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "found,suggest,kind",
    [
        ("บิดา มารดา", "บิดา-มารดา", "spacing"),
        ("Pantome", "Pantone", "english"),
        ("สัญชาต", "สัญชาติ", "mark"),        # missing vowel
        ("อนุญาติ", "อนุญาต", "mark"),         # extra vowel
        ("ล๊อบบี้", "ล็อบบี้", "confusable"),   # swapped marks — OCR mix-up
        ("เลืองลือ", "เลื่องลือ", "confusable"),  # second-tier tone mark: OCR drops these
        ("ตักแตน", "ตั๊กแตน", "confusable"),
        ("มหาซน", "มหาชน", "confusable"),       # look-alike consonants
        ("ฌาปนสมรส", "มงคลสมรส", "rewrite"),
        ("สามรถ", "สามารถ", "other"),
        ("ผู้สนไจ", "ผู้สนใจ", "other"),         # ใ/ไ is a real spelling error, not an OCR mix-up
        ("บุคล", "บุคคล", "other"),
    ],
)
def test_diff_kind(found, suggest, kind):
    assert pr._diff_kind(found, suggest) == kind


@pytest.mark.parametrize(
    "kind,readings,min_conf,poor,expected",
    [
        ("other", ["xสามรถx"] * 3, 95, False, "confirmed"),
        ("other", ["xสามารถx"] * 3, 95, False, "misread"),       # zoom reads the corrected word
        ("other", ["xสามรถx", "", ""], 95, False, "unclear"),     # not reproducible
        ("other", ["xสามรถx"] * 3, 60, False, "unclear"),         # low glyph confidence
        ("other", ["xสามรถx"] * 3, 95, True, "unclear"),          # blurry image
        ("confusable", ["xสามรถx"] * 3, 95, False, "unclear"),
    ],
)
def test_decide(kind, readings, min_conf, poor, expected):
    assert pr._decide(kind, readings, "สามรถ", "สามารถ", min_conf, poor) == expected


def test_decide_never_confirms_when_the_missing_mark_is_inked():
    assert pr._decide("mark", ["xรบx"] * 3, "รบ", "รับ", 95, False, unread_mark=True) == "unclear"


def test_decide_handles_typo_that_is_prefix_of_correction():
    # "สัญชาต" is inside "สัญชาติ" — a reading of the correct word must not count for the typo.
    assert pr._decide("mark", ["สัญชาติไทย"] * 3, "สัญชาต", "สัญชาติ", 95, False) == "misread"
    assert pr._decide("mark", ["สัญชาตไทย"] * 3, "สัญชาต", "สัญชาติ", 95, False) == "confirmed"


def test_locate_maps_text_to_glyph_span():
    src = image_source("1. สามรถทำงาน")
    line, span = pr._locate(src, "สามรถ")
    assert line.no == 1 and "".join(g.text for g in span) == "สามรถ"
    assert pr._locate(src, "ไม่มีคำนี้") is None


def test_glossary_protects_only_exact_proper_names():
    glossary = pr._glossary()
    assert pr._protected("ราชวรมหาวิหาร", f"สถานที่: {WAT_ARUN}", glossary)
    assert not pr._protected("ราชวรมหาวิหาน", "วัดอรุณราชวรารามราชวรมหาวิหาน", glossary)


# ---------------------------------------------------------------------------
# End-to-end (faked OCR + LLM)
# ---------------------------------------------------------------------------

def test_correct_proper_name_is_not_reported(run, monkeypatch):
    # The model "corrects" a correct royal temple name; the glossary stops it.
    sources, items = [image_source(f"สถานที่แรก: {WAT_ARUN}")], [item("ราชวรมหาวิหาร", "ราชวรวิหาร")]
    answer = run(sources, items)
    assert "**ไม่พบคำสะกดผิด**" in answer and "ราชวรวิหาร" not in answer

    monkeypatch.setattr(pr, "_glossary", lambda: [])   # without it, the same item would be reported
    assert len(section(run([image_source(f"สถานที่แรก: {WAT_ARUN}")], items), "ยืนยัน")) == 1


def test_garbled_read_of_a_known_name_is_dropped(run):
    # Real calendar photo: a decorative font made OCR read the temple name as junk.
    garbled = "วัดอรธุณธาษวธาธามธาษวรธรมทห๊าวิหาร"
    answer = run([image_source(f"{garbled} เลื่องลือ")], [item(garbled, WAT_ARUN)])
    assert "**ไม่พบคำสะกดผิด**" in answer and garbled not in answer and "อ่านไม่ชัด" not in answer


def test_suggestion_wrapping_a_listed_typo_is_not_repeated(run):
    answer = run(
        [image_source("เลืองลือสถาปัตยกรรมไทย")],
        [item("เลืองลือสถาปัตยกรรมไทย", "เลื่องลือ สถาปัตยกรรมไทย", kind="suggestion"), item("เลืองลือ", "เลื่องลือ")],
    )
    assert len(section(answer, "อ่านไม่ชัด")) == 1 and "ข้อเสนอแนะ" not in answer


def test_ocr_misread_is_dropped_after_zoomed_reread(run):
    answer = run(
        [image_source("รวมตัวที่ล๊อบบี้อาคาร")],
        [item("ล๊อบบี้", "ล็อบบี้")],
        rereads={"ล๊อบบี้": ["รวมตัวที่ล็อบบี้อาคาร"] * 3},
    )
    assert "**ไม่พบคำสะกดผิด**" in answer and "ล๊อบบี้" not in answer


def test_real_typo_is_confirmed(run):
    answer = run([image_source("3. สามรถทำงานเป็นกะได้")], [item("สามรถ", "สามารถ")])
    rows = section(answer, "ยืนยัน")
    assert len(rows) == 1 and "สามรถ" in rows[0] and "สามารถ" in rows[0] and "บรรทัด 1" in rows[0]


def test_candidate_without_evidence_in_text_is_dropped(run):
    answer = run([image_source("ข้อความถูกต้องทั้งหมด")], [item("คำที่ไม่มีอยู่", "คำที่ไม่มีอยู่จริง")])
    assert "**ไม่พบคำสะกดผิด**" in answer and "คำที่ไม่มีอยู่" not in answer


def test_style_suggestions_are_separate_and_debris_is_dropped(run):
    answer = run(
        [image_source("ติดต่อ 038 sta 104 รายวัน 400 บาท", english=["sta"])],
        [
            item("รายวัน", "ค่าจ้างรายวัน", kind="suggestion"),
            item("sta", "ต่อ"),          # OCR debris "corrected" across scripts
            item("038", "๐๓๘"),           # no letters
        ],
    )
    assert "**ไม่พบคำสะกดผิด**" in answer
    assert len(section(answer, "ข้อเสนอแนะ")) == 1 and "sta" not in answer


def test_confusable_difference_is_unclear_not_confirmed(run):
    answer = run([image_source("บริษัท มหาซน จำกัด")], [item("มหาซน", "มหาชน")])
    assert "**ไม่พบคำสะกดผิด**" in answer
    assert len(section(answer, "อ่านไม่ชัด")) == 1


def test_blurry_image_confirms_nothing_and_caps_unclear_list(run):
    words = [f"คำผิด{n:02d}" for n in range(14)]   # zero-padded: no word contains another
    answer = run(
        [image_source(*words, poor=True)],
        [item(w, w + "ก") for w in words],
    )
    assert "**ไม่พบคำสะกดผิด**" in answer
    assert len(section(answer, "อ่านไม่ชัด")) == pr.MAX_UNCLEAR_PER_POOR_IMAGE
    assert "อีก 4 จุด" in answer


def test_digital_document_typos_are_confirmed_without_ocr(run):
    answer = run(
        [],
        [item("สามรถ", "สามารถ", source="ไฟล์ a.docx"), item("บิดา มารดา", "บิดา-มารดา", source="ไฟล์ a.docx")],
        docs=[("a.docx", "เราสามรถทำได้\nบิดา มารดา บุตร")],
    )
    assert len(section(answer, "ยืนยัน")) == 1 and "ไฟล์ a.docx บรรทัด 1" in answer
    assert len(section(answer, "ข้อเสนอแนะ")) == 1


def test_unreadable_lines_are_listed_as_unclear(run):
    src = image_source("บรรทัดปกติ")
    src.unreadable = [glyph_line(2, "ข้อความเลือนลาง", conf=50)]
    answer = run([src], [])
    assert "**ไม่พบคำสะกดผิด**" in answer
    assert any("อ่านไม่ชัด ตรวจไม่ได้" in r for r in section(answer, "อ่านไม่ชัด"))


def test_result_tables_still_export_to_excel(run):
    answer = run(
        [image_source("3. สามรถทำงาน", "บริษัท มหาซน จำกัด", "รายวัน 400 บาท")],
        [item("สามรถ", "สามารถ"), item("มหาซน", "มหาชน"), item("รายวัน", "ค่าจ้างรายวัน", kind="suggestion")],
    )
    tables = parse_markdown_tables(answer)
    assert len(tables) == 3 and tables[0][0][2] == "ที่เขียนไว้"
    assert export_tables_to_xlsx(answer)[:2] == b"PK"   # a real .xlsx (zip) file


def _tsv(lines):
    """Tesseract-style TSV for [(text_per_glyph, conf, y), ...] lines."""
    rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for n, (glyphs, conf, y) in enumerate(lines, 1):
        for i, g in enumerate(glyphs):
            rows.append(f"5\t1\t1\t1\t{n}\t{i + 1}\t{i * 12}\t{y}\t10\t20\t{conf}\t{g}")
    return "\n".join(rows)


def test_wordless_short_lines_and_picture_texture_are_dropped(monkeypatch):
    lines = [
        (["5", "๐"], 96, 0),                              # a split-off ั read as "๐" — not a line
        (list("รับสมัครงาน"), 95, 30),
        (list("abcdefghijkl"), 40, 60),                    # illustration texture
        (list("ข้อความเลือนลางมาก"), 50, 90),               # real but unreadable text
    ]
    outputs = {
        pr.OCR_LANGS: {"txt": "5 ๐\nรับสมัครงาน\nabcdefghijkl\nข้อความเลือนลางมาก", "tsv": _tsv(lines)},
        "eng": {"tsv": _tsv([])},
    }
    monkeypatch.setattr(pr, "_tesseract", lambda img, langs, psm, outs: outputs[langs])
    buf = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buf, format="PNG")
    src = pr._read_image("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "รูปที่ 1")
    assert [line.text for line in src.lines] == ["รับสมัครงาน"]
    assert [line.text for line in src.unreadable] == ["ข้อความเลือนลางมาก"]


def _band_image(mark_above):
    """Three 'letters' (black blocks on a base band), optionally a mark over the 2nd."""
    img = Image.new("RGB", (160, 80), "white")
    d = ImageDraw.Draw(img)
    for x in (20, 60, 100):
        d.rectangle((x, 40, x + 19, 59), fill="black")
    if mark_above:
        d.rectangle((64, 28, 74, 33), fill="black")
    glyphs = [pr._Glyph(t, 95, (x, 40, x + 20, 60)) for t, x in (("ร", 20), ("ร", 60), ("ป", 100))]
    line = pr._Line(1, "รรป", glyphs)
    return pr._Source("รูปที่ 1", [line], image=img), line


def test_unread_mark_sees_ink_where_ocr_dropped_a_mark():
    src, line = _band_image(mark_above=True)
    assert pr._unread_mark(src, line, line.glyphs[1], upper=True) is True
    assert pr._unread_mark(src, line, line.glyphs[0], upper=True) is False


def test_unread_mark_finds_nothing_when_mark_is_truly_missing():
    src, line = _band_image(mark_above=False)
    assert pr._unread_mark(src, line, line.glyphs[1], upper=True) is False
    assert pr._unread_mark(src, line, line.glyphs[2], upper=True) is False  # ป: ascender, can't judge


def test_line_crop_paints_neighbours_in_background_colour():
    img = Image.new("RGB", (200, 100), (30, 80, 120))     # white-on-teal like the calendar photo
    d = ImageDraw.Draw(img)
    d.rectangle((10, 40, 60, 60), fill="white")
    d.rectangle((10, 25, 60, 33), fill="white")            # the line above, overlapping the margin
    target = pr._Line(2, "x", [pr._Glyph("x", 95, (10, 40, 60, 60))])
    above = pr._Line(1, "y", [pr._Glyph("y", 95, (10, 25, 60, 33))])
    crop, region = pr._line_crop(pr._Source("รูปที่ 1", [above, target], image=img), target)
    assert crop.getpixel((30 - region[0], 31 - region[1])) == (30, 80, 120)   # painted out, not white


def test_missing_tesseract_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: None)
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    src = pr._read_image("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), "รูปที่ 1")
    assert src.lines == [] and "อ่านข้อความในภาพไม่ได้" in src.notes[0]


# ---------------------------------------------------------------------------
# Real OCR (only where tesseract + Thai data are installed, e.g. the Docker image)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_ocr_reads_long_temple_name_literally():
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=420, height=80)
    css = "@font-face {font-family: s; src: url(Sarabun-Regular.ttf);} * {font-family: s; font-size: 14pt}"
    page.insert_htmlbox(fitz.Rect(10, 10, 410, 70), f"<p>สถานที่: {WAT_ARUN}</p>", css=css, archive=fitz.Archive("fonts"))
    png = page.get_pixmap(dpi=144).tobytes("png")
    src = pr._read_image("data:image/png;base64," + base64.b64encode(png).decode(), "รูปที่ 1")
    assert any(WAT_ARUN in pr._norm(line.text) for line in src.lines)
    assert not src.poor
