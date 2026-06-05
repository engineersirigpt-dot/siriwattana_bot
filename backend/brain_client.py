"""Client for the Company AI Brain — a central retrieval (RAG) service.

The Brain stores all company documents and exposes POST /search returning
context chunks filtered by department `role`. Our chatbot sends a query, gets
context back, and lets our own LLM generate the answer (the Brain does not
generate). See repo: company-ai-brain.

Config (env):
    AI_BRAIN_URL          base URL, e.g. http://192.168.5.32:8002
    AI_BRAIN_DEFAULT_ROLE department role used for RBAC (admin sees everything)
    AI_BRAIN_TIMEOUT      request timeout seconds
"""

import os

import httpx

AI_BRAIN_URL = os.getenv("AI_BRAIN_URL", "http://192.168.5.32:8002").rstrip("/")
AI_BRAIN_DEFAULT_ROLE = os.getenv("AI_BRAIN_DEFAULT_ROLE", "admin")
AI_BRAIN_TIMEOUT = float(os.getenv("AI_BRAIN_TIMEOUT", "20"))


def brain_enabled() -> bool:
    return bool(AI_BRAIN_URL)


def search_brain(query: str, role: str | None = None, top_k: int = 5) -> list[dict]:
    """Query the Brain. Returns the `results` list (possibly empty).

    Raises httpx errors on transport/HTTP failure — callers should catch and
    degrade gracefully (the chat flow falls back to a plain answer).
    """
    role = role or AI_BRAIN_DEFAULT_ROLE
    top_k = max(1, min(top_k, 10))
    resp = httpx.post(
        f"{AI_BRAIN_URL}/search",
        json={"query": query, "role": role, "top_k": top_k},
        timeout=AI_BRAIN_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def build_brain_context(results: list[dict]) -> str:
    """Turn Brain results into a context block for the LLM, with document
    labels so the answer can cite the source document."""
    parts: list[str] = []
    for r in results:
        src = (r.get("source") or "").strip()
        heading = (r.get("heading") or "").strip()
        preview = (r.get("preview") or "").strip()
        if not preview:
            continue
        label = f"[เอกสาร: {src}"
        if heading:
            label += f" — {heading}"
        label += "]"
        parts.append(f"{label}\n{preview}")
    return "\n\n".join(parts)
