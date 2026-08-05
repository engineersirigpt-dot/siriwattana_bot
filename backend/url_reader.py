"""Fetch and extract readable text from URLs pasted into a chat question.

This lets the bot answer about a *specific* web page the user links to — which
is different from live web *search* (that finds pages on its own). The extracted
page text is fed to the same vision/file answering path as an attachment.

Security: SSRF-guarded. Only public http(s) hosts are fetched — loopback,
private, link-local and cloud-metadata IP ranges are refused (a user could
otherwise paste http://192.168.x.x:port or a metadata endpoint and make the
server request internal services). Redirects are followed manually so every hop
is re-validated. Set URL_READ_ALLOW_PRIVATE=1 to allow intranet URLs if needed.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html as lxml_html

# http/https URLs in free text. Stops at whitespace and common closing
# punctuation so a trailing ")" / "." / Thai ")" isn't swallowed into the URL.
_URL_RE = re.compile(r"https?://[^\s<>\"'“”）)\]}]+", re.IGNORECASE)

MAX_URLS = int(os.getenv("URL_READ_MAX", "3"))
FETCH_TIMEOUT = float(os.getenv("URL_READ_TIMEOUT", "10"))
MAX_BYTES = int(os.getenv("URL_READ_MAX_BYTES", str(2_500_000)))  # ~2.5 MB
MAX_TEXT_CHARS = int(os.getenv("URL_READ_MAX_CHARS", "12000"))
ALLOW_PRIVATE = os.getenv("URL_READ_ALLOW_PRIVATE", "0") == "1"

# A normal browser UA — some sites 403 an obviously-bot agent.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SiriGPT/1.0"
)
_READABLE_TYPES = ("text/html", "application/xhtml", "text/plain", "application/json")


def extract_urls(text: str) -> list[str]:
    """Return de-duplicated http(s) URLs found in `text` (capped at MAX_URLS)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;:!?")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:MAX_URLS]


def _is_public_host(host: str) -> bool:
    """Resolve `host` and refuse it if ANY resolved IP is non-public (SSRF)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            return False
    return True


def _host_allowed(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if ALLOW_PRIVATE:
        return True
    return _is_public_host(p.hostname)


def _html_to_text(raw: bytes, content_type: str) -> tuple[str, str]:
    """(title, cleaned_text) from a fetched body. Plain text passes through."""
    if "html" not in content_type and "xml" not in content_type:
        text = raw.decode("utf-8", "replace")
    else:
        try:
            doc = lxml_html.fromstring(raw)
        except Exception:
            return "", raw.decode("utf-8", "replace")[:MAX_TEXT_CHARS]

        title = ""
        t = doc.find(".//title")
        if t is not None and t.text:
            title = t.text.strip()

        # Strip non-content nodes so the model sees the article, not chrome.
        for bad in doc.xpath(
            "//script | //style | //noscript | //nav | //footer "
            "| //header | //form | //svg | //template | //iframe"
        ):
            parent = bad.getparent()
            if parent is not None:
                parent.remove(bad)

        raw_text = doc.text_content()
        lines = [ln.strip() for ln in raw_text.splitlines()]
        text = "\n".join(ln for ln in lines if ln)
        return title, text[:MAX_TEXT_CHARS]

    lines = [ln.strip() for ln in text.splitlines()]
    return "", "\n".join(ln for ln in lines if ln)[:MAX_TEXT_CHARS]


def fetch_url_text(url: str) -> tuple[str, str] | None:
    """Fetch a URL and return (label, extracted_text), or None if unreadable.

    `label` is the page <title> (falling back to the URL) — used to head the
    context block. Returns None on any error, non-text content, blocked host,
    or empty page so the caller can fall through / report politely.
    """
    if not _host_allowed(url):
        return None

    current = url
    final_url = url
    raw = b""
    content_type = ""
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": _UA, "Accept-Language": "th,en;q=0.8"},
        ) as client:
            for _ in range(4):  # initial request + up to 3 redirects
                if not _host_allowed(current):
                    return None
                with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            return None
                        current = urljoin(current, loc)
                        continue
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "").lower()
                    if content_type and not any(
                        t in content_type for t in _READABLE_TYPES
                    ):
                        return None  # binary (pdf/img/zip) — not readable as text
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        total += len(chunk)
                        if total >= MAX_BYTES:
                            break
                    raw = b"".join(chunks)
                    final_url = str(resp.url)
                    break
            else:
                return None
    except Exception:
        return None

    if not raw:
        return None

    title, text = _html_to_text(raw, content_type)
    if not text.strip():
        return None
    label = title or final_url
    return label, text
