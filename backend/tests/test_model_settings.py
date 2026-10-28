"""
Model settings that must survive a model change (V4 follow-ups):
- the short classifier calls (routing, web/image intent, safety) keep their
  exact legacy arguments on gpt-4o-mini, and send valid ones on a gpt-5 model
  (no temperature, room for reasoning);
- document translation has its own TRANSLATE_MODEL, so LLM_MODEL (chat) can
  move to gpt-5 without breaking it;
- the safety prompt lets questions about the user themself through but not
  questions about someone else.
Offline.
"""

from types import SimpleNamespace as NS

import pytest

import llm
import sensitivity_classifier as sc
import translate_manual as tm


@pytest.mark.parametrize("model,expected", [
    ("gpt-4o-mini", {"temperature": 0, "max_tokens": 80}),               # exactly as before
    ("gpt-4.1", {"temperature": 0, "max_tokens": 80}),
    ("gpt-5-mini", {"max_completion_tokens": 680, "reasoning_effort": "minimal"}),
    ("gpt-5.4-mini", {"max_completion_tokens": 680, "reasoning_effort": "none"}),
])
def test_classifier_kwargs(model, expected):
    assert llm._classifier_kwargs(model, 80) == expected


def _capture(monkeypatch, target, content):
    sent = []
    client = NS(chat=NS(completions=NS(create=lambda **kw: sent.append(kw) or NS(
        choices=[NS(message=NS(content=content))], usage=NS(prompt_tokens=10, completion_tokens=2)))))
    monkeypatch.setattr(target, "_get_client", lambda: client)
    return sent


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-5-mini"])
def test_routing_and_intent_classifiers_on_either_model(monkeypatch, model):
    monkeypatch.setattr(llm, "LLM_MODEL_CLASSIFIER", model)
    sent = _capture(monkeypatch, llm, "general")
    assert llm.classify_query("วิธีเบิกค่าเดินทาง")[0] == "general"
    llm.wants_web_search.cache_clear()
    llm.wants_web_search("ข่าววันนี้")
    for kw in sent:
        assert ("temperature" in kw) is (model == "gpt-4o-mini")
        assert ("max_completion_tokens" in kw) is (model == "gpt-5-mini")


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-5-mini"])
def test_safety_classifier_on_either_model(monkeypatch, model):
    monkeypatch.setattr(sc, "CLASSIFIER_MODEL", model)
    sent = _capture(monkeypatch, sc, '{"category": "PAYROLL", "reason": "เงินเดือนคนอื่น"}')
    sc._classify_cached.cache_clear()
    usages: list = []
    assert sc.classify_sensitivity("เงินเดือนพี่ดาเท่าไหร่", usages) == ("PAYROLL", "เงินเดือนคนอื่น")
    sc._classify_cached.cache_clear()
    kw = sent[0]
    assert kw["model"] == model and kw["response_format"] == {"type": "json_object"}
    assert ("temperature" in kw) is (model == "gpt-4o-mini")
    assert usages[0]["model"] == model


def test_safety_classifier_has_its_own_setting(monkeypatch):
    import importlib

    monkeypatch.setenv("LLM_MODEL_CLASSIFIER", "gpt-4o-mini")
    monkeypatch.setenv("SENSITIVITY_CLASSIFIER_MODEL", "gpt-5-mini")
    try:
        assert importlib.reload(sc).CLASSIFIER_MODEL == "gpt-5-mini"
        monkeypatch.delenv("SENSITIVITY_CLASSIFIER_MODEL")
        assert importlib.reload(sc).CLASSIFIER_MODEL == "gpt-4o-mini"       # falls back
    finally:
        monkeypatch.delenv("LLM_MODEL_CLASSIFIER", raising=False)
        importlib.reload(sc)


def test_safety_prompt_exempts_questions_about_oneself_only():
    # Labelled set (tests/eval_sensitivity.py): this rule took gpt-4o-mini from
    # 30/33 to 33/33 — "ฉันอยู่ฝ่ายไหน" passes, "ฉันอยากรู้เงินเดือนพี่ดา" stays blocked.
    p = sc.CLASSIFIER_PROMPT
    assert "ข้อมูลของผู้ถามเอง" in p and "'ฉันอยู่ฝ่ายไหน'" in p
    assert "'ฉันอยากรู้เงินเดือนพี่ดา'" in p and "ยังต้อง BLOCK" in p


def test_translation_has_its_own_model(monkeypatch):
    import importlib

    monkeypatch.setenv("LLM_MODEL", "gpt-5")                            # chat moved to gpt-5…
    monkeypatch.delenv("TRANSLATE_MODEL", raising=False)
    try:
        assert importlib.reload(tm).MODEL == "gpt-4.1"                    # …translation did not
        assert tm._gen_kwargs(0.2, 12000) == {"temperature": 0.2, "max_tokens": 12000}
        monkeypatch.setenv("TRANSLATE_MODEL", "gpt-5")
        importlib.reload(tm)
        assert tm._gen_kwargs(0.2, 12000) == {"max_completion_tokens": 16000, "reasoning_effort": "low"}
    finally:
        monkeypatch.delenv("TRANSLATE_MODEL", raising=False)
        importlib.reload(tm)
