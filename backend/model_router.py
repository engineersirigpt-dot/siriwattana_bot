"""
V4.1 Smart Model Routing — pick the answer model and reasoning effort for the
normal chat pipeline (RAG / company knowledge / free answer) from cheap rules.

Flag: MODEL_ROUTING_MODE=off|admin|all (+ MODEL_ROUTING_USERS), default off.
Off → the legacy path: the gpt-4o-mini classifier picks LLM_MODEL_CALC
("calc") or LLM_MODEL, at LLM_REASONING_EFFORT.

On → no classifier call. Rules on the question pick a tier:
  light     greetings / thanks / acknowledgements          fast, cheap
  standard  everything else (the legacy general model)     = today's answers
  calc      numbers + a calculation word                   small model, more thinking
  deep      analysis / planning / comparison / code / long → more thinking
A tier's model must support what the call needs (chat + streaming here); an
unknown or unsuitable model, or a routing bug, falls back to the legacy model.
llm._chat_create also falls back to LLM_MODEL when the API rejects the routed
model or effort. The dedicated routes (Agent V1, Proofreading V1.1, Code
Interpreter V2, Document Engine V3, web search, files) keep their own models.

Every routed answer records route, reason, model, effort and latency (in
ai_usage_log), so the admin report can compare tiers before widening the flag.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import llm

MODEL_ROUTING_MODE = os.getenv("MODEL_ROUTING_MODE", "off").strip().lower()
MODEL_ROUTING_USERS = {
    u.strip() for u in os.getenv("MODEL_ROUTING_USERS", "").split(",") if u.strip()
}


def routing_enabled_for(user: dict) -> bool:
    if MODEL_ROUTING_MODE == "all":
        return True
    if MODEL_ROUTING_MODE == "admin":
        return user.get("role") == "admin" or user.get("username") in MODEL_ROUTING_USERS
    return False


# ── what each model can do ──────────────────────────────────────────────────
# Longest matching prefix wins. "verified" = used successfully in this codebase
# (gpt-5 / gpt-5-mini: answers, Code Interpreter, proofreading; gpt-5.6-terra:
# web search and the agent at effort low; gpt-4.1 / gpt-4o: older answers). The
# others come from OpenAI's model docs and are only reached if an admin points a
# tier at them, with the API fallback as the safety net. Efforts are the values
# the model accepts.
_EFFORTS_GPT5 = ("minimal", "low", "medium", "high")
_EFFORTS_GPT51 = ("none", "low", "medium", "high")
_CHAT_TOOLS = frozenset({"chat", "stream", "vision", "function_tools", "web_search", "code_interpreter"})
MODEL_CAPS: dict[str, dict] = {
    "gpt-5":         {"efforts": _EFFORTS_GPT5, "caps": _CHAT_TOOLS, "verified": True},
    "gpt-5-mini":    {"efforts": _EFFORTS_GPT5, "caps": _CHAT_TOOLS, "verified": True},
    "gpt-5-nano":    {"efforts": _EFFORTS_GPT5, "caps": _CHAT_TOOLS - {"code_interpreter"}, "verified": False},
    "gpt-5-pro":     {"efforts": ("high",), "caps": frozenset({"chat", "function_tools", "web_search"}), "verified": False},
    "gpt-5.1":       {"efforts": _EFFORTS_GPT51, "caps": _CHAT_TOOLS, "verified": False},
    "gpt-5.2":       {"efforts": _EFFORTS_GPT51, "caps": _CHAT_TOOLS, "verified": False},
    "gpt-5.4":       {"efforts": _EFFORTS_GPT51, "caps": _CHAT_TOOLS, "verified": False},
    "gpt-5.5":       {"efforts": _EFFORTS_GPT51, "caps": _CHAT_TOOLS, "verified": False},
    "gpt-5.6":       {"efforts": _EFFORTS_GPT51, "caps": _CHAT_TOOLS, "verified": False},
    "gpt-5.6-terra": {"efforts": _EFFORTS_GPT51, "caps": _CHAT_TOOLS, "verified": True},
    "gpt-4.1":       {"efforts": (), "caps": _CHAT_TOOLS, "verified": True},
    "gpt-4o":        {"efforts": (), "caps": _CHAT_TOOLS - {"code_interpreter"}, "verified": True},
    "o3":            {"efforts": ("low", "medium", "high"), "caps": _CHAT_TOOLS, "verified": False},
    "o4-mini":       {"efforts": ("low", "medium", "high"), "caps": _CHAT_TOOLS, "verified": False},
}
# web_search is rejected with minimal reasoning on the gpt-5 family.
_NEEDS_REASONING = {"web_search": ("minimal",)}


def model_caps(model: str) -> dict | None:
    m = (model or "").lower()
    best = max((k for k in MODEL_CAPS if m == k or m.startswith(k + "-") or m.startswith(k + ".")),
               key=len, default=None)
    if best is None and m.startswith("gpt-5"):     # an unlisted gpt-5.x: assume the newest family
        best = "gpt-5.6"
    caps = MODEL_CAPS.get(best) if best else None
    if caps and "-chat" in m:                      # gpt-5-chat-latest & co. don't reason
        caps = {**caps, "efforts": (), "verified": False}
    return caps


def supports(model: str, needs: set[str], effort: str | None = None) -> bool:
    caps = model_caps(model)
    if caps is None or not set(needs) <= caps["caps"]:
        return False
    return not any(effort in _NEEDS_REASONING.get(n, ()) for n in needs)


def effort_for(model: str, effort: str | None) -> str | None:
    """The effort to send for this model: None for non-reasoning models, the
    nearest accepted value otherwise (minimal ↔ none across generations)."""
    caps = model_caps(model)
    if not caps or not caps["efforts"] or not effort:
        return None
    allowed = caps["efforts"]
    if effort in allowed:
        return effort
    alias = {"minimal": "none", "none": "minimal"}.get(effort)
    if alias in allowed:
        return alias
    order = ("none", "minimal", "low", "medium", "high")
    want = order.index(effort) if effort in order else 2
    return min(allowed, key=lambda e: abs(order.index(e) - want) if e in order else 9)


# ── tiers ───────────────────────────────────────────────────────────────────

def _tier(name: str, model: str, effort: str) -> tuple[str, str]:
    return (os.getenv(f"ROUTE_{name.upper()}_MODEL", model).strip(),
            os.getenv(f"ROUTE_{name.upper()}_EFFORT", effort).strip().lower())


TIERS = {
    "light": _tier("light", "gpt-5-mini", "minimal"),
    "standard": _tier("standard", llm.LLM_MODEL, llm.LLM_REASONING_EFFORT),
    "calc": _tier("calc", llm.LLM_MODEL_CALC, "medium"),
    "deep": _tier("deep", llm.LLM_MODEL, "medium"),
}
_RANK = {"light": 0, "standard": 1, "calc": 1, "deep": 2}


@dataclass
class Route:
    tier: str
    model: str
    effort: str | None
    reason: str

    @property
    def category(self) -> str:
        """The legacy classifier's label, for the existing source names (llm-calc …)."""
        return "calc" if self.tier == "calc" else "general"

    def meta(self) -> dict:
        return {"route": self.tier, "route_reason": self.reason[:200]}


_GREETING = re.compile(
    r"^\s*(?:สวัสดี|หวัดดี|ดีจ้า|ดีครับ|ดีค่ะ|ขอบคุณ|ขอบใจ|thank|thanks|thx|hi|hello|hey|"
    r"ok|โอเค|โอเคร|ได้เลย|เยี่ยม|ดีมาก|เข้าใจแล้ว|รับทราบ|ครับ|ค่ะ|คะ|จ้า|555+|bye|ลาก่อน|"
    r"good\s*(?:morning|night)|อรุณสวัสดิ์|ราตรีสวัสดิ์)"
    r"[\s!.,~😊🙏👍]*(?:ครับ|ค่ะ|คะ|จ้า|นะ|มาก|มากๆ|มาก ๆ|ครับผม|น้า|ค่า|you|ทุกคน|a lot)*[\s!.,~😊🙏👍]*$",
    re.IGNORECASE,
)
_DEEP = re.compile(
    r"วิเคราะห์|เปรียบเทียบ|ข้อดี.{0,6}ข้อเสีย|ข้อดีข้อเสีย|วางแผน|แผนงาน|แผนกลยุทธ์|กลยุทธ์|ประเมิน|"
    r"ออกแบบ|สถาปัตยกรรม|อย่างละเอียด|ละเอียดๆ|ละเอียด ๆ|เชิงลึก|ทีละขั้น|step.by.step|"
    r"สาเหตุ(?:ที่แท้จริง|หลัก)|root cause|แก้ปัญหา|ตัดสินใจ|ทางเลือก|swot|"
    r"เขียนโค้ด|โค้ด|\bcode\b|\bdebug\b|\bsql\b|\bregex\b|algorithm|อัลกอริทึม|python|javascript|"
    r"พิสูจน์|ร่าง(?:สัญญา|นโยบาย|ข้อเสนอ|ระเบียบ|แผน)|proposal|strategy|analy[sz]e|compare|trade.?off",
    re.IGNORECASE,
)
_CALC_WORD = re.compile(
    r"คำนวณ|คิดเป็น|คิดเลข|เท่ากับเท่า|ดอกเบี้ย|ภาษี|vat|เปอร์เซ็นต์|ร้อยละ|ส่วนลด|ต้นทุน|กำไร|ขาดทุน|"
    r"เฉลี่ย|ผ่อน|งวด|ค่าโอที|\bot\b|หาร|คูณ|ยกกำลัง|รากที่|สมการ|ผลรวม|รวมเป็น|ทั้งหมดกี่|calculate|"
    r"interest|percent|average|\d\s*%|\d\s*[+*×÷^]\s*\d",   # not / - = : dates, phone numbers, links
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d")
DEEP_LENGTH = int(os.getenv("ROUTE_DEEP_MIN_CHARS", "500"))
_NEEDS = {"chat", "stream"}


def _legacy() -> Route:
    """What runs when routing can't be trusted: the legacy general model."""
    return Route("standard", llm.LLM_MODEL, effort_for(llm.LLM_MODEL, llm.LLM_REASONING_EFFORT), "fallback")


def _make(tier: str, reason: str) -> Route:
    model, effort = TIERS[tier]
    if not supports(model, _NEEDS, effort):
        legacy = _legacy()
        legacy.reason = f"{tier} model {model} unsupported → legacy"
        return legacy
    return Route(tier, model, effort_for(model, effort), reason)


def pick(question: str, *, company: bool = False) -> Route:
    """The route for one question of the normal pipeline. Never raises."""
    try:
        q = (question or "").strip()
        n_questions = q.count("?") + q.count("？") + len(re.findall(r"(?m)^\s*\d+[.)]\s", q))
        deep_hits = sorted({m.group(0).lower() for m in _DEEP.finditer(q)})
        if deep_hits or len(q) >= DEEP_LENGTH or n_questions >= 3:
            why = ", ".join(deep_hits[:4]) or (f"long ({len(q)} chars)" if len(q) >= DEEP_LENGTH
                                               else f"{n_questions} questions")
            return _make("deep", f"deep: {why}")
        if _NUMBER.search(q) and (m := _CALC_WORD.search(q)):
            return _make("calc", f"calc: {m.group(0)}")
        if _GREETING.match(q) and len(q) <= 60 and not company:
            return _make("light", "greeting/ack")
        return _make("standard", "default")
    except Exception as e:  # noqa: BLE001 — routing must never break a chat
        legacy = _legacy()
        legacy.reason = f"router error ({type(e).__name__}) → legacy"
        return legacy


def for_context(route: Route) -> Route:
    """An answer grounded in company knowledge (RAG / AI Brain hit) gets at
    least the standard model — a light tier is only for small talk."""
    if _RANK.get(route.tier, 1) >= 1:
        return route
    up = _make("standard", f"{route.reason} + knowledge hit")
    return up
