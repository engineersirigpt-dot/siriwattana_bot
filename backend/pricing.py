"""
Per-model OpenAI pricing — single source of truth for cost calculations.

USD per 1,000,000 tokens (matches OpenAI's published pricing format).
When OpenAI changes prices or we adopt a new model, update the dict here —
every cost calculation across the codebase reads from this module.

Last verified: 2026-06-18 — https://openai.com/api/pricing/
"""

from __future__ import annotations


# ─────────────────────────────────────────────────────────────────────────────
# Pricing table
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: {"input": $/1M input tokens, "output": $/1M output tokens}.
# Embedding models have no output cost — set "output": 0.0.
MODEL_PRICING_USD_PER_1M_TOKENS: dict[str, dict[str, float]] = {
    # Chat / reasoning models
    "gpt-4.1":         {"input": 2.00,  "output": 8.00},
    "gpt-4.1-mini":    {"input": 0.40,  "output": 1.60},
    "gpt-4.1-nano":    {"input": 0.10,  "output": 0.40},
    "gpt-4o":          {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":     {"input": 0.15,  "output": 0.60},
    "gpt-5":           {"input": 1.25,  "output": 10.00},
    "gpt-5-mini":      {"input": 0.25,  "output": 2.00},
    "gpt-5-nano":      {"input": 0.05,  "output": 0.40},

    # Embedding models — used by RAG search; no output cost
    "text-embedding-3-large": {"input": 0.13, "output": 0.00},
    "text-embedding-3-small": {"input": 0.02, "output": 0.00},
}

# Conservative fallback when an unknown model id reaches us. We pick the most
# expensive model we currently use so we don't under-report cost on a typo.
DEFAULT_PRICING: dict[str, float] = {"input": 2.00, "output": 8.00}

# FX rate for the dashboard's "฿" column. Pin it here so every caller agrees
# on the same conversion. Update annually or pull from a live feed later.
USD_TO_THB: float = 36.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_pricing(model: str) -> dict[str, float]:
    """Return the pricing dict for `model`, falling back to DEFAULT_PRICING."""
    return MODEL_PRICING_USD_PER_1M_TOKENS.get(model, DEFAULT_PRICING)


def estimate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Cost in USD for a single OpenAI call.

    Args:
        model: OpenAI model id (e.g. "gpt-4.1"). Unknown ids fall back to
            DEFAULT_PRICING so we never crash on a typo.
        prompt_tokens: Input tokens from `response.usage.prompt_tokens`.
        completion_tokens: Output tokens from `response.usage.completion_tokens`.
            Pass 0 for embedding calls.

    Returns:
        Cost in USD as a float. Pass to round() before persisting.
    """
    p = get_pricing(model)
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000


def estimate_cost_thb(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Convenience wrapper — same as estimate_cost_usd() × USD_TO_THB."""
    return estimate_cost_usd(model, prompt_tokens, completion_tokens) * USD_TO_THB


def is_known_model(model: str) -> bool:
    """True if `model` has a real pricing entry (not the DEFAULT fallback)."""
    return model in MODEL_PRICING_USD_PER_1M_TOKENS
