"""
Tests for "make a poster with my attached logo": routing, the pixel-exact logo
paste, and generate_image's logo path.
"""

from io import BytesIO

import pytest
from PIL import Image

import llm
import main
from image_compose import place_logo_top

BLUE, RED = (20, 40, 160), (220, 30, 30)


def _png(size, color) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def logo_file(tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(_png((300, 120), RED))
    return path


@pytest.mark.parametrize(
    "text",
    ["ทำโปรเตอร์ป้ายรับสมัครงาน 3 ตำแหน่ง ใส่โลโก้ตามไฟล์", "ทำโปสเตอร์ลดราคา ใส่ logo นี้", "make a flyer with this logo"],
)
def test_logo_plus_design_noun_is_logo_design(text):
    assert main._wants_logo_on_new_design(text) is True


@pytest.mark.parametrize("text", ["สร้างรถจากโลโก้นี้", "ทำโปสเตอร์ปีใหม่", "อธิบายรูปนี้", ""])
def test_other_requests_are_not_logo_design(text):
    assert main._wants_logo_on_new_design(text) is False


def test_place_logo_top_pastes_logo_in_band_only(logo_file):
    out = Image.open(BytesIO(place_logo_top(_png((600, 900), BLUE), logo_file, 0.15))).convert("RGB")

    assert out.size == (600, 900)
    assert out.getpixel((300, 67)) == RED          # centre of the 135px top band
    assert out.getpixel((300, 600)) == BLUE        # design below the band untouched
    assert out.getpixel((10, 10)) == BLUE          # band edges keep the background


def test_generate_image_with_logo_skips_overlay_and_reserves_band(monkeypatch, logo_file):
    prompts = []
    monkeypatch.setattr(llm, "_enhance_image_prompt", lambda p: "ART")
    monkeypatch.setattr(llm, "_render_image", lambda p, size: prompts.append(p) or _png((600, 900), BLUE))
    monkeypatch.setattr(llm, "plan_poster", lambda p: pytest.fail("overlay path used"))

    data, _ = llm.generate_image("ทำโปสเตอร์รับสมัครงาน ใส่โลโก้", logo_path=str(logo_file))

    assert prompts == ["ART" + llm._LOGO_BAND_SUFFIX]
    assert Image.open(BytesIO(data)).convert("RGB").getpixel((300, 67)) == RED
