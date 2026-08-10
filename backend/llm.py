import base64
import json
import os
import re
from typing import Any

from openai import OpenAI

from pricing import estimate_cost_usd

# Main answer model. gpt-5 (better instruction-following + reasoning) is the
# default; override with the LLM_MODEL env var (e.g. back to "gpt-4.1") without
# a rebuild. Cost is tracked per-answer in the dashboard so the swap is easy to
# evaluate/revert.
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-5")

# NOTE: LLM_MODEL_FILES (below) reads images + documents. gpt-5 is multimodal
# and reads them more accurately than gpt-4.1; run at LLM_REASONING_EFFORT (low)
# so files stay reasonably fast. Override with the env var if needed.

# How hard gpt-5 / o-series "think" before answering. The API default (medium)
# put a simple chat answer at 30-40s — unusable for an interactive assistant.
# "low" keeps gpt-5's language quality, knowledge and instruction-following
# (incl. the anti-fabrication rules) while cutting the deep multi-step
# deliberation that only pays off on hard problems. Tune without a rebuild:
# minimal | low | medium | high.
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")
LLM_MODEL_FILES = os.getenv("LLM_MODEL_FILES", "gpt-5")
LLM_MODEL_CALC = os.getenv("LLM_MODEL_CALC", "gpt-5-mini")
# Keep classifier on the cheapest model — it just routes "general" vs "calc",
# doesn't need strong Thai context reasoning like the main answer model.
LLM_MODEL_CLASSIFIER = os.getenv("LLM_MODEL_CLASSIFIER", "gpt-4o-mini")

# Image generation (gpt-image-1). Billed per image by size/quality; OpenAI's
# token-based billing varies, so we store a flat per-image cost estimate for
# the dashboard. high quality (default) ≈ $0.12-0.17/image (~4-6 ฿).
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")
# "auto" lets gpt-image-1 pick the aspect ratio that fits the content (a
# landscape scene comes out wide, a portrait subject tall, etc.). Posters are
# the one case we pin explicitly (see below) so headings never get clipped.
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "auto")
IMAGE_SIZE_PORTRAIT = os.getenv("IMAGE_SIZE_PORTRAIT", "1024x1536")  # posters
IMAGE_QUALITY = os.getenv("IMAGE_QUALITY", "high")           # crisper text
IMAGE_GEN_COST_USD = float(os.getenv("IMAGE_GEN_COST_USD", "0.12"))

# Prompt enhancement: rewrite the user's short (often Thai) request into a rich
# English image prompt before hitting gpt-image-1 — the same trick ChatGPT uses
# to get polished results. Set IMAGE_PROMPT_ENHANCE=0 to send prompts raw.
IMAGE_PROMPT_ENHANCE = os.getenv("IMAGE_PROMPT_ENHANCE", "1").strip().lower() not in {"0", "false", "no"}
IMAGE_PROMPT_MODEL = os.getenv("IMAGE_PROMPT_MODEL", "gpt-4.1")

# For posters/cards/flyers: generate a TEXT-FREE background, then draw the Thai
# headings ourselves with a real font (see image_compose) — perfect Thai text
# that the image model can't produce. Set IMAGE_TEXT_OVERLAY=0 to disable.
IMAGE_TEXT_OVERLAY = os.getenv("IMAGE_TEXT_OVERLAY", "1").strip().lower() not in {"0", "false", "no"}

# Poster/flyer/vertical requests → force a portrait canvas so headings +
# subtitles have vertical room and don't get clipped at the bottom edge.
# Everything else uses IMAGE_SIZE ("auto") so the model fits the ratio to the
# content.
_PORTRAIT_IMAGE_RE = re.compile(
    r"โปสเตอร์|โพสเตอร์|ใบปลิว|แผ่นพับ|ปฏิทิน|การ์ดเชิญ|ป้ายแนวตั้ง|แนวตั้ง|"
    r"poster|flyer|leaflet|portrait|vertical",
    re.IGNORECASE,
)

# Explicit size/format requests. gpt-image-1 supports 3 canvases; we map common
# formats to the nearest one. Checked before the generic poster→portrait rule so
# "โปสเตอร์แนวนอน" comes out landscape, etc.
_LANDSCAPE_RE = re.compile(
    r"แนวนอน|ผืนผ้า|ป้ายแนวนอน|แบนเนอร์แนวนอน|แบนเนอร์|ปกเฟซ|ปกเฟส|ปกเฟซบุ๊ก|"
    r"landscape|wide|banner|16[:x]9|fb ?cover|facebook cover|cover photo|ปกยูทูป|youtube",
    re.IGNORECASE,
)
_SQUARE_RE = re.compile(
    r"จตุรัส|สี่เหลี่ยมจัตุรัส|สี่เหลี่ยมจัสตุรัส|โพสต์ ?(?:ig|instagram|ไอจี|เฟซ|เฟส)|"
    r"square|1[:x]1|instagram post|ig post|โปรไฟล์|โปรไฟล|profile( ?(?:pic|picture|photo))?|avatar",
    re.IGNORECASE,
)
_A4_STORY_RE = re.compile(
    r"\ba4\b|เอ ?4|เอสี่|กระดาษ ?a4|story|สตอรี่|สตอรี|ไอจีสตอรี่|ig story|instagram story|9[:x]16",
    re.IGNORECASE,
)


def _pick_image_size(prompt: str) -> str:
    """Map the request to one of gpt-image-1's canvases.

    Explicit format keywords win (banner→landscape, IG post→square, A4/story→
    portrait); then poster-like requests default to portrait; else 'auto' so the
    model fits the ratio to the content.
    """
    p = prompt or ""
    if not p:
        return IMAGE_SIZE
    if _LANDSCAPE_RE.search(p):
        return "1536x1024"
    if _SQUARE_RE.search(p):
        return "1024x1024"
    if _A4_STORY_RE.search(p) or _PORTRAIT_IMAGE_RE.search(p):
        return IMAGE_SIZE_PORTRAIT
    return IMAGE_SIZE

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


# Speech-to-text. Powers voice input ("พูดถาม") and audio-file summaries.
# whisper-1 is the primary: it reliably transcribes the WHOLE clip. gpt-4o-
# transcribe is a touch sharper on Thai but tends to cut long-ish recordings off
# after the first sentence, so it's only the fallback. To pick the best result
# rather than just the first, we run BOTH and keep the longest transcript (a
# truncated one is shorter) — voice clips are short, so the extra call is cheap.
# TRANSCRIBE_LANGUAGE="" lets the model auto-detect; "th" forces Thai.
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "whisper-1")
TRANSCRIBE_FALLBACK_MODEL = os.getenv("TRANSCRIBE_FALLBACK_MODEL", "gpt-4o-transcribe")
# Default to Thai so short/ambiguous clips aren't mis-detected as another
# language (e.g. "ฮัลโหล" coming back as Chinese 哈喽). This is an internal Thai
# company; set TRANSCRIBE_LANGUAGE="" to re-enable auto-detect, or another
# ISO-639-1 code to force a different language.
TRANSCRIBE_LANGUAGE = os.getenv("TRANSCRIBE_LANGUAGE", "th").strip()
# A vocabulary/style hint biases the transcriber toward the right words —
# especially proper nouns it otherwise mangles (the company name; "พนักงาน"
# was coming back as "พันธมิตร"). Override/extend via TRANSCRIBE_PROMPT.
TRANSCRIBE_PROMPT = os.getenv(
    "TRANSCRIBE_PROMPT",
    "การถอดเสียงภาษาไทยของพนักงานบริษัทศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน) "
    "เกี่ยวกับงานพิมพ์ บรรจุภัณฑ์ ออฟเซ็ต ไดคัท และเรื่องภายในบริษัท",
).strip()


def transcribe_audio(file_path: str) -> str:
    """Transcribe an audio file to text. Runs the primary + fallback models and
    returns the LONGEST transcript (guards against a model truncating the clip);
    returns '' only if all fail."""
    models = [TRANSCRIBE_MODEL]
    if TRANSCRIBE_FALLBACK_MODEL and TRANSCRIBE_FALLBACK_MODEL != TRANSCRIBE_MODEL:
        models.append(TRANSCRIBE_FALLBACK_MODEL)

    best = ""
    for model in models:
        try:
            kwargs: dict = {"model": model}
            if TRANSCRIBE_LANGUAGE:
                kwargs["language"] = TRANSCRIBE_LANGUAGE
            if TRANSCRIBE_PROMPT:
                kwargs["prompt"] = TRANSCRIBE_PROMPT
            with open(file_path, "rb") as f:
                res = _get_client().audio.transcriptions.create(file=f, **kwargs)
            text = (getattr(res, "text", "") or "").strip()
            if len(text) > len(best):
                best = text
        except Exception:
            continue
    return best


_IMAGE_ENHANCE_SYSTEM = (
    "You rewrite a user's short image request (often in Thai) into ONE detailed "
    "English prompt for an image generator (gpt-image-1). Describe the subject, "
    "composition/layout, art style, colour palette, lighting, mood and background "
    "so the result looks polished and professional.\n"
    "Rules:\n"
    "- Stay faithful to what the user asked; don't invent unrelated elements, and "
    "keep any text minimal.\n"
    "- If the image should contain text (poster/flyer/card), specify each text "
    "element and the EXACT characters to render, wrapped in double quotes. Keep "
    "the user's script and numerals — Thai text stays Thai; if they asked for Thai "
    "numerals (เลขไทย) write the exact Thai digits.\n"
    "- For posters, lay out a clear title and, if given, a date/subtitle; leave "
    "margins so nothing is clipped.\n"
    "Output ONLY the final image prompt as one paragraph — no preamble, no quotes "
    "around the whole thing, no explanation."
)


def _enhance_image_prompt(prompt: str) -> str:
    """Expand a short user request into a rich image-gen prompt (ChatGPT-style).

    Fails open: any error (or empty result) returns the original prompt so image
    generation still works.
    """
    if not IMAGE_PROMPT_ENHANCE or not prompt.strip():
        return prompt
    try:
        res = _get_client().chat.completions.create(
            model=IMAGE_PROMPT_MODEL,
            messages=[
                {"role": "system", "content": _IMAGE_ENHANCE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            **_model_kwargs(IMAGE_PROMPT_MODEL, 700),
        )
        enhanced = (res.choices[0].message.content or "").strip()
        return enhanced or prompt
    except Exception:
        return prompt


def _render_image(prompt: str, size: str) -> bytes:
    """Raw call to the image model → PNG bytes."""
    kwargs: dict = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "n": 1}
    if IMAGE_MODEL.startswith("gpt-image"):
        kwargs["quality"] = IMAGE_QUALITY  # gpt-image-1 always returns b64
    else:
        kwargs["response_format"] = "b64_json"  # dall-e-* default to a URL

    resp = _get_client().images.generate(**kwargs)
    item = resp.data[0]
    b64 = getattr(item, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    import urllib.request

    with urllib.request.urlopen(item.url) as r:  # noqa: S310 (trusted OpenAI URL)
        return r.read()


_POSTER_PLAN_SYSTEM = (
    "You plan a poster/card/flyer from a user's short request (often Thai). "
    "Return STRICT JSON with these keys:\n"
    "- background_prompt: a detailed ENGLISH prompt for a beautiful background / "
    "illustration with ABSOLUTELY NO text, letters, numbers or typography. Keep "
    "the area named by text_area soft and uncluttered so text can be added on top.\n"
    "- title: the main heading to overlay (keep the user's script & numerals; if "
    "they asked for Thai numerals use exact Thai digits ๐-๙). Short.\n"
    "- subtitle: a secondary line or date, else \"\".\n"
    "- footer: a small tagline, else \"\".\n"
    "- text_area: one of top | center | bottom — where the text block sits.\n"
    "- text_color: a hex colour (e.g. #1a3d7c) that will read well on the background.\n"
    "Stay faithful to the request; keep text short and correct. Output ONLY the JSON."
)


# Repeated forcefully onto a poster BACKGROUND render. gpt-image-1 ignores a
# single "no text" in the plan and loves stamping its own (garbled, non-Thai)
# words like "HAPPY" onto celebration scenes — we draw the real Thai ourselves,
# so the background must stay empty.
_NO_TEXT_SUFFIX = (
    ". IMPORTANT: absolutely NO text, NO letters, NO words, NO numbers, NO "
    "typography, NO captions, NO watermark, NO signage, NO logo anywhere in the "
    "image — a clean decorative background only, leaving calm uncluttered space "
    "where text will be added on top later."
)

# Strip the leading create-image verbs/nouns so a fallback title keeps just the
# subject ("สร้างรูปภาพโปสเตอร์งานเลี้ยงปีใหม่" -> "งานเลี้ยงปีใหม่").
_IMAGE_VERB_RE = re.compile(
    r"^(?:ช่วย)?\s*(?:สร้าง|วาด|ทำ|ออกแบบ|ขอ|generate|create|draw|make)\s*"
    r"(?:(?:รูปภาพ|รูป|ภาพ|โปสเตอร์|โพสเตอร์|poster|image|picture)\s*)*",
    re.IGNORECASE,
)


def _fallback_poster_plan(prompt: str) -> dict:
    """A minimal poster plan for when the LLM planner is unavailable — so a
    poster request STILL goes through the correct-Thai overlay path instead of
    letting the image model mangle the text."""
    title = _IMAGE_VERB_RE.sub("", prompt or "").strip() or (prompt or "").strip()
    return {
        "background_prompt": (
            "an elegant, festive decorative background suitable for a poster: "
            "harmonious colours, soft lighting, tasteful ornaments, bokeh and "
            "gentle gradients"
        ),
        "title": title,
        "subtitle": "",
        "footer": "",
        "text_area": "center",
        "text_color": "#1a3d7c",
    }


def plan_poster(prompt: str) -> dict | None:
    """Ask the LLM for {background_prompt, title, subtitle, footer, text_area,
    text_color}. Returns None on failure or if there's no title to render."""
    try:
        res = _get_client().chat.completions.create(
            model=IMAGE_PROMPT_MODEL,
            messages=[
                {"role": "system", "content": _POSTER_PLAN_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            **_model_kwargs(IMAGE_PROMPT_MODEL, 700),
        )
        data = json.loads(res.choices[0].message.content or "{}")
        if isinstance(data, dict) and (data.get("title") or "").strip():
            return data
    except Exception as e:
        import sys

        print(f"PLAN_POSTER_FAILED: {e!r}", file=sys.stderr)
    return None


def edit_image(image_path: str, instruction: str) -> tuple[bytes, dict]:
    """Edit an existing image per a text instruction (gpt-image-1 image edit).

    Keeps the image's orientation (portrait/landscape/square). Returns
    (png_bytes, usage_row). Raises on API error — caller surfaces a friendly msg.
    """
    # Preserve orientation so an edit doesn't turn a poster into a square.
    try:
        from PIL import Image

        with Image.open(image_path) as im:
            w, h = im.size
        size = "1536x1024" if w > h * 1.15 else "1024x1536" if h > w * 1.15 else "1024x1024"
    except Exception:
        size = "auto"

    prompt = (
        "Apply this change to the image and keep everything else the same: "
        + instruction.strip()
    )

    # NOTE: images.edit() does not accept `quality` (unlike images.generate) —
    # passing it raises TypeError on this SDK.
    with open(image_path, "rb") as f:
        resp = _get_client().images.edit(
            model=IMAGE_MODEL, image=f, prompt=prompt, size=size,
        )

    item = resp.data[0]
    b64 = getattr(item, "b64_json", None)
    if b64:
        data = base64.b64decode(b64)
    else:
        import urllib.request

        with urllib.request.urlopen(item.url) as r:  # noqa: S310
            data = r.read()
    usage = {
        "model_used": IMAGE_MODEL,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": IMAGE_GEN_COST_USD,
    }
    return data, usage


def generate_image(prompt: str) -> tuple[bytes, dict]:
    """Generate one image for a text prompt.

    Posters/cards (see _pick_image_size / _PORTRAIT_IMAGE_RE) go through a
    text-overlay path: a text-free background is generated, then the Thai
    headings are drawn with a real font for perfect text. Everything else is
    enhanced (ChatGPT-style) and generated directly.

    Returns (png_bytes, usage_row). Raises on API error.
    """
    size = _pick_image_size(prompt)
    usage = {
        "model_used": IMAGE_MODEL,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": IMAGE_GEN_COST_USD,
    }

    # Poster path: real-font Thai text on a generated background. We ALWAYS
    # overlay for a poster (falling back to a self-built plan if the LLM planner
    # is down) — never let the image model render the Thai itself, since it
    # mangles it. The background is rendered with a forceful no-text suffix so
    # gpt-image-1 doesn't stamp its own garbled words onto it.
    is_poster = bool(prompt) and _PORTRAIT_IMAGE_RE.search(prompt) is not None
    if IMAGE_TEXT_OVERLAY and is_poster:
        plan = plan_poster(prompt) or _fallback_poster_plan(prompt)
        try:
            bg_prompt = (plan.get("background_prompt") or "").strip() \
                or _fallback_poster_plan(prompt)["background_prompt"]
            bg = _render_image(bg_prompt + _NO_TEXT_SUFFIX, size)
            from image_compose import compose_text_on_image

            composed = compose_text_on_image(
                bg,
                title=plan.get("title", ""),
                subtitle=plan.get("subtitle", ""),
                footer=plan.get("footer", ""),
                text_area=plan.get("text_area", "center"),
                text_color=plan.get("text_color", "#1a3d7c"),
            )
            return composed, usage
        except Exception as e:
            import sys

            print(f"POSTER_OVERLAY_FAILED: {e!r}", file=sys.stderr)
            # fall through to the normal path as a last resort

    # Normal path: enhance the prompt, generate directly.
    data = _render_image(_enhance_image_prompt(prompt), size)
    return data, usage


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return (
        m.startswith("gpt-5")
        or m.startswith("o1")
        or m.startswith("o3")
        or m.startswith("o4")
    )


def _model_kwargs(model: str, n: int) -> dict:
    """Per-model call kwargs.

    Reasoning models (gpt-5-*, o-series) take max_completion_tokens (not
    max_tokens) and accept reasoning_effort — we pass LLM_REASONING_EFFORT so
    chat answers stay fast. Older models (gpt-4.1, gpt-4o-mini) take max_tokens
    and reject reasoning_effort, so they get neither.
    """
    if _is_reasoning_model(model):
        return {
            "max_completion_tokens": n,
            "reasoning_effort": LLM_REASONING_EFFORT,
        }
    return {"max_tokens": n}


# ─────────────────────────────────────────────────────────────────────────────
# Token usage tracking — returned from every LLM call so callers can persist
# the real per-question cost to chat_history (see backend/pricing.py).
# ─────────────────────────────────────────────────────────────────────────────


def _extract_usage(response: Any, model: str) -> dict:
    """Pull token counts + cost from a non-stream OpenAI response.

    Returns a dict shaped for `accumulate_usage` and DB persistence:
        {"model": str, "prompt_tokens": int, "completion_tokens": int,
         "cost_usd": float}

    Defensive: if the SDK returns a response without `.usage` (rare but
    possible on errors), we return zeros so callers never see KeyError.
    """
    usage_obj = getattr(response, "usage", None)
    p = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    c = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    return {
        "model": model,
        "prompt_tokens": p,
        "completion_tokens": c,
        "cost_usd": estimate_cost_usd(model, p, c),
    }


def _extract_usage_from_chunk(chunk_usage: Any, model: str) -> dict:
    """Like _extract_usage but for the `usage` field on the final stream chunk.

    OpenAI populates `chunk.usage` only on the LAST streaming chunk when
    `stream_options={"include_usage": True}` is passed. Callers should check
    `chunk.usage` is truthy before calling this.
    """
    p = int(getattr(chunk_usage, "prompt_tokens", 0) or 0)
    c = int(getattr(chunk_usage, "completion_tokens", 0) or 0)
    return {
        "model": model,
        "prompt_tokens": p,
        "completion_tokens": c,
        "cost_usd": estimate_cost_usd(model, p, c),
    }


def accumulate_usage(*usages: dict, primary_model: str | None = None) -> dict:
    """Combine multiple per-call usages into one row ready for chat_history.

    One question often triggers multiple OpenAI calls (classifier + answer,
    or RAG search embedding + answer). Each call has its own model and its
    own cost — we SUM tokens and SUM cost (each cost was already computed
    at the correct per-model rate), and stamp `model_used` with the primary
    answer-model so the dashboard can group by it.

    Args:
        *usages: usage dicts from _extract_usage / _extract_usage_from_chunk.
            Empty or None entries are skipped.
        primary_model: the model id to store as `model_used`. Defaults to the
            model of the last non-empty usage (typically the answer call).

    Returns:
        {"model_used": str, "prompt_tokens": int, "completion_tokens": int,
         "cost_usd": float}
    """
    valid = [u for u in usages if u]
    if not valid:
        return {
            "model_used": primary_model or LLM_MODEL,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }
    total_p = sum(u.get("prompt_tokens", 0) for u in valid)
    total_c = sum(u.get("completion_tokens", 0) for u in valid)
    total_cost = sum(u.get("cost_usd", 0.0) for u in valid)
    return {
        "model_used": primary_model or valid[-1].get("model") or LLM_MODEL,
        "prompt_tokens": total_p,
        "completion_tokens": total_c,
        "cost_usd": round(total_cost, 6),
    }


COMPANY_IDENTITY = (
    "คุณคือผู้ช่วย AI ของบริษัท ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน) "
    "ผู้ผลิตสิ่งพิมพ์และบรรจุภัณฑ์ครบวงจรในประเทศไทย "
    "มีประสบการณ์ในธุรกิจสิ่งพิมพ์มากกว่า 45 ปี จดทะเบียนเป็นบริษัทมหาชนเมื่อปี พ.ศ. 2538 "
    "มีพนักงานมากกว่า 3,000 คน "
    "สำนักงานใหญ่ตั้งอยู่ที่กรุงเทพมหานคร (เขตสาทร) และโรงงานอยู่ที่จังหวัดฉะเชิงเทรา "
    "ให้บริการผลิตหนังสือ งานพิมพ์เชิงพาณิชย์ บรรจุภัณฑ์กระดาษ หนังสือ POP-UP "
    "บริการออกแบบ งานเตรียมพิมพ์ ควบคุมสี และงานหลังพิมพ์ "
    "เว็บไซต์ https://www.sirivatana.co.th\n\n"
    "ข้อมูลติดต่อภายในที่คุณ 'รู้' แต่ **ห้ามใส่ในคำตอบ** เว้นแต่ผู้ใช้ถามตรง ๆ ว่าจะติดต่อใคร/ที่ไหน:\n"
    "- ฝ่ายขาย: +66(0)89-969-2859 / sirivatanaonline@gmail.com\n"
    "- HR: 038-532-000 / HR@SIRIVATANA.CO.TH\n\n"
    "**กฎการใส่ข้อมูลติดต่อ:**\n"
    "- ห้ามแนบเบอร์โทร/อีเมลท้ายคำตอบโดยอัตโนมัติ\n"
    "- ใส่ได้เฉพาะเมื่อผู้ใช้ถามตรง ๆ ว่า 'ติดต่อ X ได้ที่ไหน', 'เบอร์ฝ่ายขาย', 'อีเมล HR' เป็นต้น\n"
    "- ในกรณีที่ระบบไม่รู้คำตอบจริง ๆ (fallback) ให้แนะนำให้ติดต่อบริษัท — เฉพาะกรณีนั้นเท่านั้น\n\n"
    "ตอบด้วยน้ำเสียงสุภาพ ชัดเจน เป็นมืออาชีพ และเป็นกันเอง "
    "ตอบให้ครบถ้วนตามความซับซ้อนของคำถาม — ถ้าคำถามง่ายตอบสั้น ถ้าซับซ้อนให้อธิบายละเอียด "
    "ใช้หัวข้อย่อย ข้อย่อย หรือตารางเมื่อเหมาะสมเพื่อให้อ่านง่าย "
    "ตอบเป็นภาษาไทยเป็นหลัก ถ้าผู้ใช้ถามภาษาอังกฤษให้ตอบภาษาอังกฤษได้"
)

COMPANY_FALLBACK = (
    "ขออภัยค่ะ/ครับ ข้อมูลนี้ยังไม่มีในระบบ "
    "กรุณาติดต่อบริษัทโดยตรงที่ +66(0)89-969-2859 หรืออีเมล sirivatanaonline@gmail.com "
    "เพื่อให้เจ้าหน้าที่ตรวจสอบข้อมูลล่าสุดให้"
)

# THE MOST IMPORTANT BLOCK — placed at the TOP of every system prompt so the
# model sees it before the persona / identity stuff. Without this gpt-4o-mini
# answered short follow-ups like "ต้นเหตุ" as standalone vocabulary lookups
# even when the previous turn made the referent obvious. Re-ordered + made
# imperative + concrete example after the prompt-only fix didn't change
# behaviour in production.
CONTEXT_FIRST_RULES = (
    "🚨 กฎสำคัญที่สุดเรื่องบริบทสนทนา — อ่านก่อนตอบเสมอ 🚨\n\n"
    "1. **บังคับ:** ดูประวัติบทสนทนา (history) ทุกครั้งก่อนตอบ ห้ามข้าม\n"
    "2. ถ้าคำถามใหม่เป็นข้อความสั้น / สรรพนาม / fragment "
    "(เช่น 'ต้นเหตุ', 'แล้วล่ะ', 'อันนั้นคืออะไร', 'มาจากไหน', 'ทำไม', "
    "'อธิบายเพิ่ม', 'ยกตัวอย่าง', 'แปลว่าอะไร') "
    "**ต้องตีความเป็นคำถามต่อเนื่องจาก turn ก่อนหน้าเสมอ** — "
    "ห้ามตอบเป็น dictionary / vocabulary lookup โดยลำพังเด็ดขาด\n\n"
    "**ตัวอย่าง (สำคัญ — ให้ทำตามนี้):**\n"
    "  Turn 1 User: 'Where is the john? แปลว่าอะไร'\n"
    "  Turn 1 Bot : 'แปลว่า ห้องน้ำอยู่ที่ไหน — john เป็นสแลง...'\n"
    "  Turn 2 User: 'ต้นเหตุ เริ่มต้นมาจากไหน'\n"
    "  ❌ ผิด: 'ต้นเหตุ หมายถึง สาเหตุของเหตุการณ์...' (ตอบเป็น vocab)\n"
    "  ✅ ถูก: 'ต้นเหตุของคำสแลง where is the john มาจาก...' "
    "(เชื่อมกับ turn 1)\n\n"
    "3. ถ้า context ที่ระบบ retrieval ส่งมาดูไม่เกี่ยวข้องกับคำถาม "
    "(เช่น มีแต่ heading ไม่มีเนื้อหา หรือคนละแนวกับ history) "
    "→ **เพิกเฉย context นั้น** แล้วใช้ความเข้าใจจาก history ตอบแทน\n"
    "4. ถ้า history ก็ไม่ช่วย และไม่รู้คำตอบจริง ๆ → ตอบตรง ๆ ว่าไม่แน่ใจ "
    "หรือถามกลับเพื่อ clarify ห้ามเดาให้ครบ\n"
)

# Used by Normal mode when RAG misses. Strips the "I am Sirivatana" framing so
# the model answers as a general assistant — this prevents fabricated claims
# like "บริษัทศิริวัฒนาใช้กระดาษ Art card 80gsm" when the KB has no such fact.
NEUTRAL_ASSISTANT_IDENTITY = (
    "คุณคือผู้ช่วย AI ทั่วไปที่ติดตั้งไว้ให้พนักงานบริษัท "
    "ศิริวัฒนาอินเตอร์พริ้นท์ จำกัด (มหาชน) ใช้สอบถามคำถามทั่วไป\n\n"
    "**กฎสำคัญ:**\n"
    "- **ห้ามพูดถึงโหมดการทำงาน การเปิด/ปิดโหมด หรือข้อจำกัดของระบบใด ๆ** — "
    "ระบบไม่มีโหมดให้ผู้ใช้เลือกแล้ว ตอบคำถามไปตามปกติเลย\n"
    "- ห้ามอ้างถึง 'บริษัทศิริวัฒนา' หรือ 'บริษัทเรา' ในคำตอบ — ตอบในนาม AI ทั่วไป\n"
    "- ห้ามเดาหรือแต่งข้อมูลของบริษัท (เช่น 'บริษัทใช้กระดาษ X', 'บริษัทมีนโยบาย Y', "
    "'พนักงานบริษัทได้สวัสดิการ Z') ที่ไม่ได้ verify จากระบบ\n"
    "- ถ้าผู้ใช้ถามเรื่องที่บริษัทน่าจะมีข้อมูลเฉพาะ (กระดาษ/วัสดุ, เครื่องจักร, ขั้นตอน, "
    "ราคา, สเปก, สวัสดิการ, HR, ตำแหน่งงาน ฯลฯ) ให้ตอบในเชิงทั่วไปอย่างเป็นกลาง "
    "แบบธรรมชาติ — ไม่ต้องออกตัว ไม่ต้องเกริ่นว่าตอบได้แค่ความรู้ทั่วไป\n"
    "- ถ้าผู้ใช้ถามเบอร์ติดต่อบริษัทตรง ๆ ตอบได้: ฝ่ายขาย +66(0)89-969-2859 / "
    "sirivatanaonline@gmail.com, HR 038-532-000 / HR@SIRIVATANA.CO.TH\n\n"
    "ตอบด้วยน้ำเสียงสุภาพ ชัดเจน เป็นมืออาชีพ และเป็นกันเอง "
    "ตอบให้ครบถ้วนตามความซับซ้อนของคำถาม "
    "ใช้หัวข้อย่อย ข้อย่อย หรือตารางเมื่อเหมาะสม "
    "ตอบเป็นภาษาไทยเป็นหลัก ภาษาอังกฤษถ้าผู้ใช้ใช้"
)

# Appended to every answering prompt. The app turns answers into downloadable
# PDF/Word/Excel files via buttons, so the model must NOT claim it can't make
# files — it should just produce the requested content normally.
EXPORT_FILE_NOTE = (
    "หมายเหตุเรื่องไฟล์: ระบบมีปุ่มให้ผู้ใช้ดาวน์โหลดคำตอบเป็นไฟล์ PDF, Word หรือ "
    "Excel ได้เองอยู่แล้ว ดังนั้นถ้าผู้ใช้ขอให้ทำ/สรุป/แปลเป็นไฟล์ PDF/Word/Excel "
    "ให้ตอบเนื้อหาที่ต้องการตามปกติให้ครบถ้วน (ถ้าขอเป็น Excel/ตาราง ให้จัดเป็น "
    "Markdown table) — ห้ามบอกว่า 'ไม่สามารถสร้างไฟล์ได้' หรือให้ไปใช้ Word/Google Docs เอง"
)

# Anti-vagueness: be specific when the data is there, and say plainly when it
# isn't — instead of padding with generic "ตามนโยบายบริษัท" filler.
ANSWER_STYLE_NOTE = (
    "**สไตล์การตอบ — เจาะจง ละเอียดเมื่อรู้ ไม่อ๊อง:**\n"
    "- **ค่าเริ่มต้น = ตอบแบบ 'ละเอียด ครบ ลึก' เสมอ** (เว้นแต่ผู้ใช้ขอสั้น) — "
    "ห้ามตอบแบบลิสต์ค่าดิบสั้น ๆ เมื่อมีข้อมูลจริงให้ขยายความให้เต็มที่ ตามโครงนี้:\n"
    "  (1) **เปิดด้วยบทนำ/ภาพรวม** สั้น ๆ ว่าสิ่งนี้คืออะไร ใช้ทำอะไร สำคัญอย่างไร\n"
    "  (2) **รายละเอียดแยกหัวข้อ/บูลเล็ต พร้อมคำอธิบายของแต่ละจุด** ไม่ใช่แค่ค่า — "
    "เช่น แทนที่จะเขียนแค่ 'ความเร็ว 15,000 แผ่น/ชม.' ให้เสริมว่าความเร็วระดับนี้หมายความว่าอย่างไร "
    "ในทางปฏิบัติ เหมาะกับงานแบบไหน ส่งผลต่ออะไร\n"
    "  (3) **เพิ่มบริบท/การนำไปใช้จริง/ข้อดี-ข้อควรระวัง/การเปรียบเทียบกับทางเลือกอื่น** "
    "เท่าที่ข้อมูลจริง + ความรู้ทั่วไปที่ถูกต้องรองรับ\n"
    "  (4) **ปิดท้ายด้วยสรุปหรือคำแนะนำที่นำไปใช้ได้จริง**\n"
    "  เน้น 'ลึกและมีสาระ' ไม่ใช่ 'ยืดด้วยน้ำ' — และ **ห้ามแต่งตัวเลข/ข้อเท็จจริงเฉพาะที่ไม่มี "
    "ในข้อมูล** (ขยายความด้วยความรู้ทั่วไปได้ แต่ห้ามกุสเปก/ข้อมูลเฉพาะของบริษัท)\n"
    "- ตอบโดยใช้ตัวเลข ชื่อ หรือขั้นตอน 'จริง' จากข้อมูลที่ได้รับ ฟันธงเมื่อมีข้อมูล\n"
    "- ห้ามร่ายน้ำกว้าง ๆ ที่ไม่มีสาระ (เช่น 'ตามนโยบายของบริษัท', 'ตามที่กำหนด', "
    "'แล้วแต่กรณี', 'ตรวจสอบกับหัวหน้างาน') มากลบส่วนที่ไม่รู้\n"
    "- ถ้าข้อมูลในระบบครอบคลุมแค่บางส่วน → ตอบส่วนที่รู้แบบเจาะจง แล้วบอกตรง ๆ สั้น ๆ "
    "ว่าส่วนที่เหลือ 'ยังไม่ได้ระบุในระบบ' และให้ติดต่อ HR/หัวหน้างาน — ไม่ต้องเดา\n"
    "- ถ้าไม่มีข้อมูลเลย → บอกสั้น ๆ ตรง ๆ ว่ายังไม่มีในระบบ อย่าแต่งคำตอบยาว ๆ ให้ดูเหมือนรู้"
)

SYSTEM_PROMPT_RAG = (
    f"{CONTEXT_FIRST_RULES}\n\n"
    f"{COMPANY_IDENTITY}\n\n"
    "ตอบคำถามผู้ใช้โดยอ้างอิงจากข้อมูลที่ให้ไว้ **และจากประวัติการสนทนาก่อนหน้า** (รวมถึงไฟล์ที่ผู้ใช้แนบในเทิร์นก่อน) "
    "ตอบให้ครบถ้วน อธิบายให้เข้าใจ ขยายความหรือยกตัวอย่างได้เมื่อเหมาะสม\n"
    "ถ้าผู้ใช้ถามคำถามต่อเนื่อง (เช่น 'เขาคือใคร', 'อันนั้นราคาเท่าไหร่', 'แล้วอันนี้ล่ะ') "
    "ให้ใช้บริบทจากการสนทนาก่อนหน้าเพื่อตอบ ห้ามตอบว่า 'ไม่มีข้อมูล' ถ้าคำตอบอยู่ใน history แล้ว\n"
    "ห้ามแต่งราคา ระยะเวลาผลิต หรือเงื่อนไขชำระเงินเอง\n"
    "ถ้าถูกถามราคา ให้ขอรายละเอียดงาน (ประเภทงาน ขนาด จำนวน วัสดุ สี เทคนิคพิเศษ งานหลังพิมพ์ กำหนดส่ง) "
    "แล้วแนะนำให้ส่งให้ฝ่ายขายประเมิน\n"
    "ถ้าถูกถามตำแหน่งงานว่าง ให้บอกว่าตำแหน่งงานเปลี่ยนแปลงได้ และแนะนำให้ติดต่อ HR\n"
    f"ถ้าข้อมูลที่ให้มาและประวัติสนทนาไม่ครอบคลุมคำถาม ให้ตอบตรง ๆ ว่า: {COMPANY_FALLBACK}\n\n"
    + ANSWER_STYLE_NOTE + "\n\n"
    + EXPORT_FILE_NOTE
)

SYSTEM_PROMPT_FREE = (
    f"{CONTEXT_FIRST_RULES}\n\n"
    f"{NEUTRAL_ASSISTANT_IDENTITY}\n\n"
    "**สำหรับเทิร์นนี้ (ไม่พบคำตอบใน RAG):**\n"
    "1. ตรวจประวัติการสนทนาก่อน — ถ้าคำตอบอยู่ในประวัติแชทแล้ว ให้ใช้ตอบทันที "
    "(เช่น ผู้ใช้เคยแนบไฟล์ที่มีชื่อพนักงาน แล้วถามต่อว่า 'เขาชื่ออะไร' → ใช้ข้อมูลจากเทิร์นก่อน)\n"
    "2. ถ้าเป็นคำถามทั่วไปที่ไม่เกี่ยวกับบริษัทเลย (ความรู้ทั่วไป กีฬา บุคคล ข่าว เทคโนโลยี "
    "การใช้ซอฟต์แวร์ ภาษา คำขอช่วยเขียน code ฯลฯ) → ตอบในนาม AI ทั่วไปอย่างละเอียด "
    "ตามกฎใน identity ข้างต้น **ห้ามอ้างถึงบริษัทศิริวัฒนา** และ "
    "**ห้ามใส่ป้ายกำกับ/ข้อความปิดท้ายเรื่องบริษัท-HR-สเปกใด ๆ ทั้งสิ้น** "
    "(เช่น 'Lewis Hamilton คือใคร' → ตอบจบสวย ๆ ไม่ต้องมีหมายเหตุใด ๆ)\n"
    "3. ถ้าเป็นเรื่องที่บริษัทอาจมีข้อมูลเฉพาะ "
    "(กระดาษ/วัสดุที่ใช้, เครื่องจักร, ขั้นตอนการผลิต, ราคา, สเปกสินค้า, สวัสดิการ, HR, "
    "ตำแหน่งงาน, นโยบาย, SOP ฯลฯ) — ใช้แนวทาง 'สายกลาง':\n"
    "   ✅ **ให้ความรู้ทั่วไปได้เต็มที่และละเอียด** — อธิบายภาพรวม หลักการทำงาน ประเภท "
    "จุดเด่น ข้อดี-ข้อจำกัด การใช้งานทั่วไป ปัจจัยที่เกี่ยวข้อง ฯลฯ จัดเป็นหัวข้อ/บูลเล็ต "
    "ให้อ่านง่ายและได้ความรู้จริง (เช่น 'Komori Lithrone G series เป็นซีรีส์อะไร เด่นเรื่องอะไร', "
    "'เครื่องพิมพ์ออฟเซ็ตทำงานอย่างไร', 'อะไรมีผลต่อความเร็ว/คุณภาพงานพิมพ์')\n"
    "   ⛔ **ห้ามใส่ 'ตัวเลข/เงื่อนไขเฉพาะของบริษัท' ที่ไม่ได้มาจากระบบเด็ดขาด** — เช่น "
    "จำนวนวันลา/เงื่อนไขการลา, สวัสดิการ, นโยบาย HR, ระเบียบภายใน, ความเร็วเครื่อง (แผ่น/ชม.), "
    "ขนาด (mm), ราคา, รุ่นหัวพิมพ์ ฯลฯ เพราะค่าจริงของบริษัทอาจต่างจากค่าทั่วไป "
    "ถ้าไม่รู้จริงห้ามเดาเด็ดขาด แม้ใส่คำว่า 'ตัวอย่าง/ประมาณ/สมมติ' ก็ห้าม\n"
    "   ⛔ **ห้ามพูดหรือบอกเป็นนัยว่าข้อมูลที่เดา = นโยบาย/ข้อมูลจริงของบริษัทศิริวัฒนา** — "
    "ห้ามขึ้นต้นทำนองว่า 'โดยหลักของบริษัทศิริวัฒนา...', 'บริษัทกำหนดว่า...', 'บริษัทอนุญาต...' "
    "กับข้อมูลที่ไม่ได้ยืนยันจากระบบ เพราะทำให้ข้อมูลที่เดาดูเหมือนนโยบายทางการ (อันตราย)\n"
    "   💬 **ตอบให้เป็นธรรมชาติเหมือนผู้ช่วย AI ทั่วไป** — ห้ามใส่ป้ายกำกับ ข้อความ "
    "ออกตัว คำเตือน หรือหมายเหตุปิดท้ายใด ๆ ที่ชี้ไปหาบริษัท/HR/ฝ่ายที่เกี่ยวข้อง "
    "และห้ามเกริ่นว่าข้อมูลที่ตอบไม่ใช่ของบริษัท — กฎ ⛔ ข้างต้นกันการมั่วอยู่แล้ว "
    "แค่ตอบความรู้ทั่วไปให้ดีแล้วจบสวย ๆ ก็พอ\n"
    "**ความละเอียดของคำตอบ (ค่าเริ่มต้น = ครบ ลึก มีโครงสร้าง เว้นแต่ผู้ใช้ขอสั้น):**\n"
    "(1) เปิดด้วยบทนำ/ภาพรวมว่าสิ่งนี้คืออะไร สำคัญอย่างไร\n"
    "(2) รายละเอียดแยกหัวข้อ/บูลเล็ต **พร้อมคำอธิบายของแต่ละจุด** ไม่ใช่ลิสต์สั้น ๆ\n"
    "(3) เพิ่มบริบท/หลักการ/การนำไปใช้จริง/ข้อดี-ข้อควรระวัง/การเปรียบเทียบ\n"
    "(4) ปิดท้ายด้วยสรุปหรือคำแนะนำที่นำไปใช้ได้ — เน้นมีสาระ ไม่ยืดด้วยน้ำ\n\n"
    + EXPORT_FILE_NOTE
)

SYSTEM_PROMPT_COMPANY_FREE = (
    f"{CONTEXT_FIRST_RULES}\n\n"
    f"{COMPANY_IDENTITY}\n\n"
    "**ขณะนี้คุณอยู่ในโหมด 'คู่มือบริษัท' (Company-Only Mode)**\n"
    "คุณสามารถตอบได้เฉพาะคำถามที่เกี่ยวข้องกับบริษัทศิริวัฒนาอินเตอร์พริ้นท์เท่านั้น "
    "เช่น: ข้อมูลบริษัท, บริการ, สินค้า, ขั้นตอนการทำงาน, นโยบาย, HR, สวัสดิการ, "
    "การติดต่อ, เครื่องจักร, โรงงาน, สถานที่, เอกสารบริษัท ฯลฯ\n\n"
    "**ถ้าคำถามไม่เกี่ยวข้องกับบริษัท** "
    "(เช่น ความรู้ทั่วไป, ข่าวสาร, การเมือง, สุขภาพ, ความบันเทิง, ภาวะโลกร้อน, "
    "code/programming ที่ไม่เกี่ยวข้องกับการดำเนินงานของบริษัท, แนะนำที่เที่ยว, "
    "คณิตศาสตร์ทั่วไป ฯลฯ) ให้ตอบปฏิเสธอย่างสุภาพด้วยข้อความนี้ทุกครั้ง:\n\n"
    "'ขออภัยค่ะ ขณะนี้อยู่ในโหมด \"คู่มือบริษัท\" จึงตอบได้เฉพาะคำถามที่เกี่ยวกับ"
    "บริษัทศิริวัฒนาอินเตอร์พริ้นท์เท่านั้น หากต้องการถามคำถามทั่วไป "
    "กรุณาออกจากโหมดนี้แล้วเริ่มแชทใหม่ค่ะ'\n\n"
    "ถ้าเป็นคำถามเกี่ยวกับบริษัทแต่ไม่มีข้อมูลในระบบ ให้ตอบว่า: "
    f"{COMPANY_FALLBACK}\n"
    "ห้ามแต่งข้อมูลเฉพาะของบริษัทขึ้นเอง ห้ามตอบคำถามนอกขอบเขตของบริษัทไม่ว่ากรณีใด\n\n"
    + ANSWER_STYLE_NOTE + "\n\n"
    + EXPORT_FILE_NOTE
)


SYSTEM_PROMPT_FILES = (
    f"{COMPANY_IDENTITY}\n\n"
    "ผู้ใช้แนบไฟล์มา — วิเคราะห์เนื้อหาในไฟล์อย่างละเอียดเพื่อตอบคำถาม\n"
    "ไฟล์ที่รองรับ: รูปภาพ, PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), ไฟล์ข้อความ/โค้ด (.txt, .md, .csv, .json, .py, .js, ฯลฯ)\n"
    "- รูป design/แบบกล่อง: ดูองค์ประกอบ (สี, ขนาด, รูปทรง, โลโก้, เทคนิคพิเศษ) แล้วประเมินว่าผลิตได้ไหม\n"
    "- PDF/Word: สรุปประเด็นสำคัญและแนะนำการดำเนินงาน\n"
    "- Excel/CSV: วิเคราะห์ข้อมูลในตาราง สรุปตัวเลข แนวโน้ม หรือคำนวณตามที่ผู้ใช้ขอ\n"
    "- PowerPoint: สรุปเนื้อหาแต่ละ slide\n"
    "- ไฟล์โค้ด: อธิบาย/รีวิว/หา bug/แนะนำการปรับปรุง ตามที่ผู้ใช้ขอ\n"
    "ห้ามแต่งราคา ระยะเวลาผลิต หรือเงื่อนไขชำระเงิน — แนะนำให้ติดต่อฝ่ายขายเพื่อใบเสนอราคา\n"
    "ตอบเป็นภาษาไทย กระชับ ชัดเจน เป็นมืออาชีพ\n\n"
    "**สำคัญ — อ้างอิงที่มาในไฟล์ (citation):**\n"
    "- เวลาสรุปหรือตอบข้อมูลจากไฟล์ ให้ระบุที่มาในไฟล์กำกับไว้ทุกครั้งที่ทำได้ "
    "เพื่อให้ผู้ใช้ตรวจย้อนกลับได้\n"
    "- PDF: เนื้อหาที่ดึงมาจะมีป้าย '[หน้า N]' คั่นอยู่ — ให้ใช้เลขหน้านั้นอ้างอิง "
    "เช่น 'ยอดผลิตอยู่ที่ X ตัน (หน้า 3)' หรือ 'ดูขั้นตอนได้ที่ (หน้า 5–6)'\n"
    "- Word/PowerPoint: อ้างอิงเป็นชื่อหัวข้อ/section หรือเลข slide เช่น "
    "'(หัวข้อ 2. นโยบาย)' หรือ '(สไลด์ 4)'\n"
    "- Excel/CSV: อ้างอิงเป็นชื่อชีตหรือหัวคอลัมน์/แถว เช่น '(ชีต Q1, แถวที่ 5)'\n"
    "- รูปแบบ: ใส่อ้างอิงในวงเล็บท้ายประโยค/bullet ที่เกี่ยวข้อง หรือทำเป็นตาราง "
    "'ประเด็น | ที่มา' ก็ได้ ถ้าคำตอบมีหลายจุด\n"
    "- **ห้ามเดาเลขหน้า/section ที่ไม่มีอยู่จริงในเนื้อหาที่ได้รับ** — ถ้าไม่แน่ใจ "
    "ที่มาของจุดไหน ให้ละไว้ ไม่ต้องใส่อ้างอิงมั่ว\n\n"
    "**สำคัญสำหรับงานแปล/อ่าน/ถอดความเอกสาร (เช่น คู่มือเครื่องจักร, สเปก, SOP):**\n"
    "- เมื่อผู้ใช้ขอให้ 'แปล', 'อ่าน', 'ถอดความ', 'transcribe' เนื้อหาในไฟล์ — ห้ามย่อ ห้ามสรุป ห้ามข้ามส่วนใด ๆ\n"
    "- ต้อง transcribe ทุกข้อความ ทุก section ทุกหัวข้อย่อย ทุกตาราง ตามลำดับที่ปรากฏในไฟล์\n"
    "- ตารางต้องคงโครงสร้าง column/row ตามต้นฉบับ (ใช้ Markdown table) — ห้าม summarize เป็น bullet\n"
    "- หมายเลข section/figure/table (เช่น 1-3, Fig 2-1, Table 4-2) ต้องคงไว้ตรงตามไฟล์\n"
    "- ศัพท์เทคนิค/รุ่น/หน่วย/ค่าตัวเลข ต้องคงต้นฉบับ (ใส่อังกฤษในวงเล็บถ้าแปล)\n"
    "- ห้ามแปลงหน่วย (mm คง mm, °F คง °F) เว้นแต่ผู้ใช้สั่งชัดเจน\n"
    "- คำเตือนความปลอดภัย (DANGER/WARNING/CAUTION/NOTICE) ต้อง transcribe ครบถ้วน ใช้คำเตือนระดับเดียวกัน\n"
    "- ถ้าเนื้อหายาวจน output ใกล้เต็ม ให้แจ้งท้ายคำตอบว่า 'เนื้อหายังไม่จบ ขอให้พิมพ์ \"ต่อ\" เพื่ออ่านส่วนถัดไป'\n\n"
    + EXPORT_FILE_NOTE
)


CLASSIFY_PROMPT = (
    "จัดหมวดคำถามต่อไปนี้ ตอบเป็นคำเดียวเท่านั้น: \n"
    "- 'calc' ถ้าต้องการการคำนวณตัวเลข สูตร หรือ logic หลายขั้น (เช่น คิดต้นทุน, คำนวณภาษี, คิด OT, สมการ)\n"
    "- 'general' ถ้าเป็นคำถามทั่วไป สรุป สอบถามข้อมูล ขอคำแนะนำ\n"
    "ตอบกลับเฉพาะคำว่า calc หรือ general"
)


def classify_query(question: str) -> tuple[str, dict]:
    """Return ('calc' | 'general', usage). Falls back to ('general', zero-usage) on failure.

    Usage is the per-call dict from _extract_usage; caller should hand it to
    accumulate_usage() together with the answer call's usage.
    """
    try:
        res = _get_client().chat.completions.create(
            model=LLM_MODEL_CLASSIFIER,
            temperature=0,
            messages=[
                {"role": "system", "content": CLASSIFY_PROMPT},
                {"role": "user", "content": question},
            ],
            **_model_kwargs(LLM_MODEL_CLASSIFIER, 5),
        )
        text = (res.choices[0].message.content or "").strip().lower()
        category = "calc" if "calc" in text else "general"
        return category, _extract_usage(res, LLM_MODEL_CLASSIFIER)
    except Exception:
        return "general", {
            "model": LLM_MODEL_CLASSIFIER,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }


def answer_from_context(
    question: str,
    context_question: str,
    context_answer: str,
    model: str | None = None,
    history: list[dict] | None = None,
) -> tuple[str, dict]:
    """Returns (answer_text, usage). usage is the per-call dict from _extract_usage."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT_RAG}]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": (
                f"ข้อมูลในระบบ:\nคำถาม: {context_question}\nคำตอบ: {context_answer}\n\n"
                f"คำถามจากผู้ใช้: {question}"
            ),
        }
    )
    chosen = model or LLM_MODEL
    res = _get_client().chat.completions.create(
        model=chosen,
        messages=messages,
        **_model_kwargs(chosen, 8000),
    )
    text = (res.choices[0].message.content or "").strip()
    return text, _extract_usage(res, chosen)


def answer_freely(
    question: str,
    model: str | None = None,
    history: list[dict] | None = None,
    company_only: bool = False,
) -> tuple[str, dict]:
    """Answer without RAG context. Returns (answer_text, usage).

    When company_only=True, the system prompt forces the model to refuse any
    question that is not about the company. Used by the "ถามข้อมูลบริษัท" mode.
    """
    system_prompt = SYSTEM_PROMPT_COMPANY_FREE if company_only else SYSTEM_PROMPT_FREE
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    chosen = model or LLM_MODEL
    res = _get_client().chat.completions.create(
        model=chosen,
        messages=messages,
        **_model_kwargs(chosen, 8000),
    )
    text = (res.choices[0].message.content or "").strip()
    return text, _extract_usage(res, chosen)


# Web search — for questions needing fresh/live info (news, today's prices,
# latest regulations). Uses OpenAI's search-enabled model so we reuse the same
# API key; the model browses and returns url citations in message.annotations.
WEB_SEARCH_MODEL = os.getenv("WEB_SEARCH_MODEL", "gpt-4o-search-preview")
# Flat surcharge per search on top of token cost (OpenAI bills web-search tool
# calls separately). ~$0.03/search is a safe estimate for the dashboard.
WEB_SEARCH_SURCHARGE_USD = float(os.getenv("WEB_SEARCH_SURCHARGE_USD", "0.03"))

SYSTEM_PROMPT_WEB = (
    "คุณเป็นผู้ช่วยของบริษัทศิริวัฒนาอินเตอร์พริ้นท์ ที่ค้นข้อมูลสดจากเว็บมาตอบ\n"
    "- ตอบเป็นภาษาไทย กระชับ ตรงประเด็น เป็นข้อ ๆ ถ้าเหมาะสม\n"
    "- ใช้ข้อมูลล่าสุดจากผลการค้นหา และระบุตัวเลข/วันที่ให้ชัด\n"
    "- ถ้าข้อมูลไม่แน่ชัดหรือขัดแย้งกัน ให้บอกตามตรง อย่าเดา\n"
    "- ไม่ต้องใส่รายการแหล่งอ้างอิงเอง ระบบจะแนบลิงก์ให้อัตโนมัติ"
)


def answer_with_web_search(
    question: str,
    history: list[dict] | None = None,
) -> tuple[str, list[dict], dict]:
    """Answer a question using live web search.

    Returns (answer_text, citations, usage):
        citations: [{"title": str, "url": str}, ...] extracted from the model's
                   url_citation annotations.
        usage: per-call dict (like _extract_usage) with the web-search surcharge
               already folded into cost_usd.
    Raises on API error — the caller falls back to the normal pipeline.
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT_WEB}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    # Search-preview models reject temperature/some params — keep the call minimal.
    res = _get_client().chat.completions.create(
        model=WEB_SEARCH_MODEL,
        messages=messages,
        max_tokens=4000,
    )
    msg = res.choices[0].message
    text = (msg.content or "").strip()

    citations: list[dict] = []
    seen_urls: set[str] = set()
    for ann in (getattr(msg, "annotations", None) or []):
        if getattr(ann, "type", None) != "url_citation":
            continue
        uc = getattr(ann, "url_citation", None)
        url = getattr(uc, "url", None) if uc else None
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        citations.append({"title": getattr(uc, "title", "") or url, "url": url})

    usage = _extract_usage(res, WEB_SEARCH_MODEL)
    usage["cost_usd"] = usage.get("cost_usd", 0.0) + WEB_SEARCH_SURCHARGE_USD
    return text, citations, usage


def stream_from_context(
    question: str,
    context_question: str,
    context_answer: str,
    model: str | None = None,
    history: list[dict] | None = None,
    usage_holder: dict | None = None,
):
    """Streaming twin of answer_from_context — yields text deltas as the model
    produces them. Same prompt/messages so output matches the non-stream path.

    If `usage_holder` is provided, it gets populated (in-place) with the final
    token usage on the LAST chunk. Caller pattern:
        usage = {}
        for delta in stream_from_context(..., usage_holder=usage):
            send(delta)
        # after loop, usage = {"model": ..., "prompt_tokens": ..., ...}
    """
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT_RAG}]
    if history:
        messages.extend(history)
    messages.append(
        {
            "role": "user",
            "content": (
                f"ข้อมูลในระบบ:\nคำถาม: {context_question}\nคำตอบ: {context_answer}\n\n"
                f"คำถามจากผู้ใช้: {question}"
            ),
        }
    )
    chosen = model or LLM_MODEL
    stream_kwargs: dict = dict(
        model=chosen,
        messages=messages,
        stream=True,
        **_model_kwargs(chosen, 8000),
    )
    if usage_holder is not None:
        stream_kwargs["stream_options"] = {"include_usage": True}
    stream = _get_client().chat.completions.create(**stream_kwargs)
    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        # `usage` arrives on the final chunk only when include_usage=True.
        if usage_holder is not None and getattr(chunk, "usage", None):
            usage_holder.update(_extract_usage_from_chunk(chunk.usage, chosen))


def stream_freely(
    question: str,
    model: str | None = None,
    history: list[dict] | None = None,
    company_only: bool = False,
    usage_holder: dict | None = None,
):
    """Streaming twin of answer_freely — yields text deltas.

    See stream_from_context for the `usage_holder` pattern.
    """
    system_prompt = SYSTEM_PROMPT_COMPANY_FREE if company_only else SYSTEM_PROMPT_FREE
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    chosen = model or LLM_MODEL
    stream_kwargs: dict = dict(
        model=chosen,
        messages=messages,
        stream=True,
        **_model_kwargs(chosen, 8000),
    )
    if usage_holder is not None:
        stream_kwargs["stream_options"] = {"include_usage": True}
    stream = _get_client().chat.completions.create(**stream_kwargs)
    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
        if usage_holder is not None and getattr(chunk, "usage", None):
            usage_holder.update(_extract_usage_from_chunk(chunk.usage, chosen))


def answer_with_files(
    question: str,
    image_data_urls: list[str],
    pdf_texts: list[tuple[str, str]],
    history: list[dict] | None = None,
) -> tuple[str, dict]:
    """Answer when the user attached files. Returns (answer_text, usage).

    Uses vision-capable model (LLM_MODEL_FILES). Vision image tokens are
    already counted inside prompt_tokens by OpenAI, so we don't need to
    estimate them separately — `_extract_usage` picks them up automatically.
    """
    content: list[dict] = []

    if pdf_texts:
        joined = "\n\n".join(
            f"=== เนื้อหาจากไฟล์ {name} ===\n{text}"
            for name, text in pdf_texts
        )
        content.append(
            {
                "type": "text",
                "text": f"{joined}\n\n=== คำถามจากผู้ใช้ ===\n{question}",
            }
        )
    else:
        content.append({"type": "text", "text": question})

    for url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT_FILES}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": content})

    res = _get_client().chat.completions.create(
        model=LLM_MODEL_FILES,
        messages=messages,
        **_model_kwargs(LLM_MODEL_FILES, 8000),
    )
    text = (res.choices[0].message.content or "").strip()
    return text, _extract_usage(res, LLM_MODEL_FILES)
