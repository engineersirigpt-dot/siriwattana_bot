"""Overlay real Thai text onto a generated background image.

Image models (gpt-image-1 included) can't render long Thai text/numerals
reliably, so for posters/cards we generate a *text-free* background and draw the
headings here with a real font (Sarabun) — giving pixel-perfect, correct Thai.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
FONT_REGULAR = FONT_DIR / "Sarabun-Regular.ttf"
FONT_BOLD = FONT_DIR / "Sarabun-Bold.ttf"

LOGO_PATH = Path(__file__).parent / "assets" / "logo.jpg"


def _add_brand_logo(
    img: "Image.Image", logo_path: Path, *, size_frac: float = 0.15,
    margin_frac: float = 0.045,
) -> "Image.Image":
    """Composite the company logo as a small rounded 'brand chip' in the
    bottom-right corner. The logo's white background blends into the white chip,
    so it reads as an intentional brand sign-off on any poster. No-op on error."""
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except Exception:
        return img

    W, H = img.size
    target = max(64, int(W * size_frac))
    logo.thumbnail((target, target), Image.LANCZOS)
    lw, lh = logo.size

    pad = max(8, int(target * 0.12))
    panel_w, panel_h = lw + pad * 2, lh + pad * 2
    margin = int(W * margin_frac)
    px, py = W - panel_w - margin, H - panel_h - margin

    base = img.convert("RGBA")
    # Soft shadow behind the chip for a bit of lift.
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [px + 3, py + 4, px + panel_w + 3, py + panel_h + 4],
        radius=int(pad * 1.4), fill=(0, 0, 0, 60),
    )
    base = Image.alpha_composite(base, shadow)

    # White rounded chip.
    panel = Image.new("RGBA", (panel_w, panel_h), (255, 255, 255, 240))
    mask = Image.new("L", (panel_w, panel_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, panel_w - 1, panel_h - 1], radius=int(pad * 1.4), fill=255
    )
    base.paste(panel, (px, py), mask)
    base.paste(logo, (px + pad, py + pad), logo)
    return base.convert("RGB")


def _hex_to_rgb(color: str, default=(26, 61, 124)) -> tuple[int, int, int]:
    c = (color or "").strip().lstrip("#")
    if len(c) == 6:
        try:
            return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
        except ValueError:
            pass
    return default


def _is_light(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150


def _fit_font(draw: ImageDraw.ImageDraw, text: str, font_path: Path, max_w: int,
              start: int, min_size: int = 18) -> ImageFont.FreeTypeFont:
    """Largest font size (<= start) whose text width fits max_w."""
    size = start
    while size > min_size:
        f = ImageFont.truetype(str(font_path), size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return ImageFont.truetype(str(font_path), min_size)


def compose_text_on_image(
    bg_bytes: bytes,
    *,
    title: str = "",
    subtitle: str = "",
    footer: str = "",
    text_area: str = "top",
    text_color: str = "#1a3d7c",
    logo_path: Path | None = None,
) -> bytes:
    """Draw title/subtitle/footer onto the background and return PNG bytes.

    Text is centered horizontally in the chosen band (top/center/bottom), sized
    to the image, with a contrasting outline so it stays readable on any
    background. If logo_path is given, the company logo is added as a small brand
    chip in the bottom-right corner. On any failure the original background is
    returned unchanged.
    """
    try:
        img = Image.open(BytesIO(bg_bytes)).convert("RGB")
    except Exception:
        return bg_bytes

    W, H = img.size
    draw = ImageDraw.Draw(img)
    max_w = int(W * 0.86)

    # Build the lines to draw. Title font is a touch bigger (W/8) for impact.
    lines: list[tuple[str, ImageFont.FreeTypeFont]] = []
    if (subtitle or "").strip() and text_area != "bottom":
        lines.append((subtitle.strip(), _fit_font(draw, subtitle.strip(), FONT_REGULAR, max_w, int(W / 19))))
    if (title or "").strip():
        lines.append((title.strip(), _fit_font(draw, title.strip(), FONT_BOLD, max_w, int(W / 8))))
    if (subtitle or "").strip() and text_area == "bottom":
        lines.append((subtitle.strip(), _fit_font(draw, subtitle.strip(), FONT_REGULAR, max_w, int(W / 19))))
    if (footer or "").strip():
        lines.append((footer.strip(), _fit_font(draw, footer.strip(), FONT_REGULAR, max_w, int(W / 26))))

    if not lines:
        if logo_path is not None:
            img = _add_brand_logo(img, logo_path)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    gap = int(H * 0.018)
    heights = []
    for text, font in lines:
        bbox = draw.textbbox((0, 0), text, font=font)
        heights.append(bbox[3] - bbox[1])
    block_h = sum(heights) + gap * (len(lines) - 1)

    # Vertical start of the text block within the chosen band.
    if text_area == "center":
        y = (H - block_h) // 2
    elif text_area == "bottom":
        y = int(H * 0.90) - block_h
    else:  # top
        y = int(H * 0.08)
    y = max(int(H * 0.04), min(y, int(H * 0.94) - block_h))

    # ── Readability ──────────────────────────────────────────────────────────
    # Sample the background behind the text, drop a soft rounded scrim, and pick
    # a text colour that actually contrasts (the planner sometimes returns a dark
    # colour on a dark background — the original bug that made text unreadable).
    band = img.crop((0, max(0, y - gap), W, min(H, y + block_h + gap))).convert("L")
    px = list(band.getdata())
    avg = (sum(px) / len(px)) if px else 128
    dark_bg = avg < 140

    longest = max(draw.textlength(t, font=f) for t, f in lines)
    pad_x, pad_y = int(W * 0.055), int(H * 0.022)
    panel_w = min(int(W * 0.92), int(longest) + pad_x * 2)
    px0 = (W - panel_w) // 2
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(scrim).rounded_rectangle(
        [px0, y - pad_y, px0 + panel_w, y + block_h + pad_y],
        radius=int(H * 0.022),
        fill=(0, 0, 0, 115) if dark_bg else (255, 255, 255, 150),
    )
    img = Image.alpha_composite(img.convert("RGBA"), scrim).convert("RGB")
    draw = ImageDraw.Draw(img)

    plan_fill = _hex_to_rgb(text_color)
    if dark_bg:
        fill = plan_fill if _is_light(plan_fill) else (255, 255, 255)
        stroke = (0, 0, 0)
    else:
        fill = plan_fill if not _is_light(plan_fill) else (26, 61, 124)
        stroke = (255, 255, 255)

    stroke_w = max(2, int(W / 320))
    for (text, font), h in zip(lines, heights):
        w = draw.textlength(text, font=font)
        x = (W - w) / 2
        draw.text(
            (x, y), text, font=font, fill=fill,
            stroke_width=stroke_w, stroke_fill=stroke,
        )
        y += h + gap

    if logo_path is not None:
        img = _add_brand_logo(img, logo_path)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
