"""
V4.1 Smart Model Routing — the rules, the model capability table, and the
fallback to the legacy model when the API rejects a routed one. Offline.
"""

from types import SimpleNamespace as NS

import openai
import pytest

import llm
import model_router as mr


@pytest.fixture(autouse=True)
def tiers(monkeypatch):
    monkeypatch.setattr(mr, "TIERS", {
        "light": ("gpt-5-mini", "minimal"), "standard": ("gpt-5", "low"),
        "calc": ("gpt-5-mini", "medium"), "deep": ("gpt-5", "medium"),
    })
    monkeypatch.setattr(llm, "LLM_MODEL", "gpt-5")
    monkeypatch.setattr(llm, "LLM_REASONING_EFFORT", "low")


@pytest.mark.parametrize("question,tier", [
    ("สวัสดีครับ", "light"), ("ขอบคุณมากครับ", "light"), ("thank you", "light"), ("โอเคค่ะ", "light"),
    ("สวัสดีครับ ช่วยบอกขั้นตอนเบิกค่าเดินทางหน่อย", "standard"),
    ("วิธีลาพักร้อนต้องทำยังไง", "standard"), ("CMYK กับ RGB ต่างกันยังไง", "standard"),
    ("นัดประชุม 28/10/2569 เวลา 10:00 ได้ไหม", "standard"),          # a date is not a calculation
    ("โทร 081-234-5678 ได้ทั้งวันไหม", "standard"),
    ("ฝากเงิน 100,000 บาท ดอกเบี้ย 5% ทบต้น 10 ปี ได้เท่าไหร่", "calc"),
    ("ราคา 1,250 บาท ลด 15% เหลือเท่าไหร่", "calc"), ("12 * 37 + 5", "calc"),
    ("ช่วยวิเคราะห์ข้อดีข้อเสียของการย้ายฐานผลิตไปต่างจังหวัด", "deep"),
    ("เปรียบเทียบเครื่องพิมพ์ออฟเซ็ตกับดิจิทัล", "deep"), ("เขียนโค้ด python อ่านไฟล์ csv", "deep"),
    ("วิเคราะห์ต้นทุน 3 สินค้า: A 120 บาท B 90 บาท C 75 บาท", "deep"),   # deep wins over calc
    ("ก" * 520, "deep"), ("ข้อ 1 คืออะไร? ข้อ 2 ล่ะ? แล้วข้อ 3?", "deep"),
])
def test_rules_pick_the_tier(question, tier):
    r = mr.pick(question)
    assert r.tier == tier, r
    assert (r.model, r.effort) == mr.TIERS[tier]
    assert r.reason


def test_company_mode_never_uses_the_light_tier():
    assert mr.pick("สวัสดีครับ", company=True).tier == "standard"


def test_legacy_source_labels_are_kept():
    assert mr.pick("คำนวณ 15% ของ 2,400").category == "calc"
    assert mr.pick("วิเคราะห์ยอดขาย").category == "general"


def test_knowledge_hit_lifts_a_light_answer():
    r = mr.for_context(mr.pick("ขอบคุณครับ"))
    assert r.tier == "standard" and "knowledge hit" in r.reason
    deep = mr.pick("วิเคราะห์นโยบายลา")
    assert mr.for_context(deep) is deep


def test_routing_never_raises(monkeypatch):
    monkeypatch.setattr(mr, "_GREETING", None)                    # a bug in the rules
    r = mr.pick("สวัสดี")
    assert (r.model, r.effort) == ("gpt-5", "low") and r.reason.startswith("router error")


def test_a_tier_whose_model_cannot_stream_chat_falls_back(monkeypatch):
    monkeypatch.setitem(mr.TIERS, "deep", ("text-embedding-3-large", "high"))
    r = mr.pick("วิเคราะห์ตลาด")
    assert (r.model, r.effort) == ("gpt-5", "low") and "unsupported" in r.reason


@pytest.mark.parametrize("model,needs,effort,ok", [
    ("gpt-5", {"chat", "stream"}, "medium", True),
    ("gpt-5-mini", {"chat", "stream", "vision"}, "minimal", True),
    ("gpt-5", {"web_search"}, "minimal", False),                       # web search needs reasoning
    ("gpt-5.6-terra", {"web_search", "function_tools"}, "low", True),
    ("gpt-4o-mini", {"code_interpreter"}, None, False),
    ("whisper-1", {"chat"}, None, False),
])
def test_capabilities(model, needs, effort, ok):
    assert mr.supports(model, needs, effort) is ok


@pytest.mark.parametrize("model,asked,sent", [
    ("gpt-5", "medium", "medium"), ("gpt-5-mini", "minimal", "minimal"),
    ("gpt-5.4-mini", "minimal", "none"),                               # newer family: none, not minimal
    ("gpt-5.1", "none", "none"), ("gpt-5", "none", "minimal"),
    ("gpt-4.1", "medium", None), ("gpt-5-chat-latest", "low", None),   # no reasoning
    ("o3", "minimal", "low"),
])
def test_effort_is_one_the_model_accepts(model, asked, sent):
    assert mr.effort_for(model, asked) == sent


def test_flag(monkeypatch):
    admin, staff = {"role": "admin", "username": "a"}, {"role": "user", "username": "2690028"}
    monkeypatch.setattr(mr, "MODEL_ROUTING_MODE", "off")
    assert not mr.routing_enabled_for(admin)
    monkeypatch.setattr(mr, "MODEL_ROUTING_MODE", "admin")
    assert mr.routing_enabled_for(admin) and not mr.routing_enabled_for(staff)
    monkeypatch.setattr(mr, "MODEL_ROUTING_USERS", {"2690028"})
    assert mr.routing_enabled_for(staff)
    monkeypatch.setattr(mr, "MODEL_ROUTING_MODE", "all")
    assert mr.routing_enabled_for({"role": "user", "username": "x"})


# ── llm: the routed effort reaches the API; a rejected route falls back ──────

class FakeCompletions:
    def __init__(self, fail_models=()):
        self.calls, self.fail_models = [], set(fail_models)

    def create(self, **kw):
        self.calls.append(kw)
        if kw["model"] in self.fail_models:
            raise openai.BadRequestError("unsupported value: reasoning_effort",
                                         response=NS(status_code=400, request=None, headers={}), body=None)
        usage = NS(prompt_tokens=100, completion_tokens=20)
        if kw.get("stream"):
            return iter([NS(choices=[NS(delta=NS(content="ตอบ"))], usage=None),
                         NS(choices=[], usage=usage)])
        return NS(choices=[NS(message=NS(content="ตอบ"))], usage=usage)


def _client(monkeypatch, **kw):
    comp = FakeCompletions(**kw)
    monkeypatch.setattr(llm, "_get_client", lambda: NS(chat=NS(completions=comp)))
    return comp


def test_routed_effort_is_sent(monkeypatch):
    comp = _client(monkeypatch)
    text, usage = llm.answer_freely("q", model="gpt-5", effort="medium")
    assert comp.calls[0]["reasoning_effort"] == "medium" and text == "ตอบ"
    assert usage["model"] == "gpt-5" and usage["reasoning_effort"] == "medium" and "fallback_from" not in usage


def test_unrouted_calls_are_unchanged(monkeypatch):
    comp = _client(monkeypatch)
    _, usage = llm.answer_freely("q", model="gpt-5-mini")
    assert comp.calls[0]["reasoning_effort"] == "low"
    assert set(usage) == {"model", "prompt_tokens", "completion_tokens", "cost_usd"}   # legacy shape


def test_rejected_route_falls_back_to_the_legacy_model(monkeypatch):
    comp = _client(monkeypatch, fail_models={"gpt-5.4-mini"})
    text, usage = llm.answer_from_context("q", "cq", "ca", model="gpt-5.4-mini", effort="none")
    assert [c["model"] for c in comp.calls] == ["gpt-5.4-mini", "gpt-5"]
    assert comp.calls[1]["reasoning_effort"] == "low" and text == "ตอบ"
    assert usage["model"] == "gpt-5" and usage["fallback_from"] == "gpt-5.4-mini"


def test_rejected_stream_route_falls_back_before_any_text(monkeypatch):
    comp = _client(monkeypatch, fail_models={"gpt-5.5"})
    holder: dict = {}
    out = "".join(llm.stream_freely("q", model="gpt-5.5", effort="high", usage_holder=holder))
    assert out == "ตอบ" and [c["model"] for c in comp.calls] == ["gpt-5.5", "gpt-5"]
    assert holder["model"] == "gpt-5" and holder["fallback_from"] == "gpt-5.5"
    assert comp.calls[1]["stream_options"] == {"include_usage": True}


def test_unrouted_errors_are_not_retried(monkeypatch):
    comp = _client(monkeypatch, fail_models={"gpt-5"})
    with pytest.raises(openai.BadRequestError):
        llm.answer_freely("q", model="gpt-5")
    assert len(comp.calls) == 1
