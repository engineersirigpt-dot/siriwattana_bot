"""linsip / Work-Instruction lookup.

When a user asks about a job by its number (e.g. "งาน J12600091 ใช้กาวอะไร"),
build the linsip WI page URL for that job, fetch it, and hand the text to the
normal file-answering path so the bot can answer job-specific questions
(customer, quantity, glue, specs, revisions, due date).

The WI page (mi_wi.php) is plain HTML on an internal host, so we reuse the
existing SSRF-guarded url_reader — this module just registers the linsip host in
its allowlist so that one internal host can be read (nothing else on the LAN is
opened up).

Config (env):
    LINSIP_ENABLED   "1" to turn the job-number lookup on (default off)
    LINSIP_WI_URL    URL template with a {job} placeholder
    LINSIP_JOB_RE    override the job-number pattern if the format differs
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import url_reader

LINSIP_ENABLED = os.getenv("LINSIP_ENABLED", "0").strip().lower() not in {"0", "false", "no", ""}
LINSIP_WI_URL = os.getenv(
    "LINSIP_WI_URL",
    "http://192.168.5.40/planning/mi/mi_wi.php?jobid={job}",
)
# Job numbers look like J12600091 (J + 7-10 digits). Case-insensitive.
_JOB_RE = re.compile(os.getenv("LINSIP_JOB_RE", r"\bJ\d{7,10}\b"), re.IGNORECASE)

# Allow the linsip host through url_reader's SSRF guard (only this host).
if LINSIP_ENABLED:
    try:
        _host = urlparse(LINSIP_WI_URL).hostname
        if _host:
            url_reader.ALLOW_HOSTS.add(_host.lower())
    except Exception:
        pass


# A follow-up asking about "this job" without repeating the number — so the
# caller knows to reuse the job number from earlier in the conversation.
_JOB_FOLLOWUP_RE = re.compile(
    r"งานนี้|งานนี่|งานนั้น|จ๊อบนี้|job\s*นี้|"
    r"ลูกค้า|customer|\bae\b|sale|เซล|กาว|กำหนดส่ง|\bdue\b|ส่งงาน|วันส่ง|ex.?factory|"
    r"จำนวน|ยอดงาน|\bqty\b|ขนาด|\bsize\b|สถานะ|status|เข้าเล่ม|binding|"
    r"revision|รีวิชั่น|แก้ไข|สเปก|สเปค|ประเภทงาน|reprint|กระดาษ|paper|"
    r"เทคนิค|ไดคัท|die.?cut|เคลือบ|กี่สี|พิมพ์|ผลิตภัณฑ์|สรุปงาน",
    re.IGNORECASE,
)


def extract_job_no(text: str) -> str | None:
    """Return the first job number found (upper-cased), else None."""
    if not text:
        return None
    m = _JOB_RE.search(text)
    return m.group(0).upper() if m else None


def looks_like_job_followup(text: str) -> bool:
    """True when a question (with no job number) is a follow-up about the job
    being discussed — e.g. 'AE คือใครงานนี้', 'ใช้กาวอะไร'."""
    return bool(text) and _JOB_FOLLOWUP_RE.search(text) is not None


def build_wi_url(job_no: str) -> str:
    return LINSIP_WI_URL.format(job=job_no)


def fetch_job_wi(job_no: str) -> tuple[str, str] | None:
    """Fetch the WI page for `job_no`. Returns (label, text) or None.

    `label` heads the context block so the answer can cite the job/WI.
    """
    url = build_wi_url(job_no)
    got = url_reader.fetch_url_text(url)
    if not got:
        return None
    _title, text = got
    if not text.strip():
        return None
    return (f"Work Instruction งาน {job_no} ({url})", text)
