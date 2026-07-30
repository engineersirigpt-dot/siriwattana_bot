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
) -> bytes:
    """Draw title/subtitle/footer onto the background and return PNG bytes.

    Text is centered horizontally in the chosen band (top/center/bottom), sized
    to the image, with a contrasting outline so it stays readable on any
    background. On any failure the original background is returned unchanged.
    """
    try:
        img = Image.open(BytesIO(bg_bytes)).convert("RGB")
    except Exception:
        return bg_bytes

    W, H = img.size
    draw = ImageDraw.Draw(img)
    fill = _hex_to_rgb(text_color)
    stroke = (255, 255, 255) if not _is_light(fill) else (40, 40, 40)
    max_w = int(W * 0.86)

    # Build the lines to draw: (text, font, gap-after-in-px).
    lines: list[tuple[str, ImageFont.FreeTypeFont]] = []
    if (subtitle or "").strip() and text_area != "bottom":
        lines.append((subtitle.strip(), _fit_font(draw, subtitle.strip(), FONT_REGULAR, max_w, int(W / 20))))
    if (title or "").strip():
        lines.append((title.strip(), _fit_font(draw, title.strip(), FONT_BOLD, max_w, int(W / 9))))
    if (subtitle or "").strip() and text_area == "bottom":
        lines.append((subtitle.strip(), _fit_font(draw, subtitle.strip(), FONT_REGULAR, max_w, int(W / 20))))
    if (footer or "").strip():
        lines.append((footer.strip(), _fit_font(draw, footer.strip(), FONT_REGULAR, max_w, int(W / 26))))

    if not lines:
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
        y = int(H * 0.92) - block_h
    else:  # top
        y = int(H * 0.08)
    y = max(int(H * 0.04), min(y, int(H * 0.96) - block_h))

    stroke_w = max(2, int(W / 340))
    for (text, font), h in zip(lines, heights):
        w = draw.textlength(text, font=font)
        x = (W - w) / 2
        draw.text(
            (x, y), text, font=font, fill=fill,
            stroke_width=stroke_w, stroke_fill=stroke,
        )
        y += h + gap

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
