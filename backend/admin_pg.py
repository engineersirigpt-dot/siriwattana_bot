from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from db_pg import get_pg_conn

# Flat-rate per-question fallback for legacy rows that were inserted before
# we started persisting `chat_history.cost_usd` (Phase 1-4 of cost tracking,
# 2026-06-18). New rows carry the real token-derived cost and bypass this.
COST_USD_PER_QUESTION = float(os.getenv("DASHBOARD_USD_PER_Q", "0.0036"))
USD_TO_THB = float(os.getenv("DASHBOARD_USD_TO_THB", "36"))


def _query_cost_in_range(cur, since: datetime, until: datetime) -> tuple[float, int, int]:
    """Hybrid cost calc for a chat_history time window.

    Returns (real_cost_usd, real_rows, legacy_rows):
      - real_cost_usd: SUM(cost_usd) for rows that have it (post-Phase-4).
      - real_rows: count of those rows (drives the "by_model" chart).
      - legacy_rows: count of pre-Phase-4 rows without cost data — caller
        multiplies by COST_USD_PER_QUESTION for the flat-rate fallback.
    """
    cur.execute(
        """
        SELECT
            COALESCE(SUM(cost_usd), 0)::float,
            COUNT(*) FILTER (WHERE cost_usd IS NOT NULL),
            COUNT(*) FILTER (
                WHERE cost_usd IS NULL
                  AND source <> 'export_offer'
                  AND source <> 'blocked'
            )
        FROM chat_history
        WHERE asked_at >= %s AND asked_at < %s
        """,
        (since, until),
    )
    real_cost, real_rows, legacy_rows = cur.fetchone()
    return float(real_cost or 0.0), int(real_rows or 0), int(legacy_rows or 0)


def _cost_breakdown_by_model(cur, since: datetime, until: datetime) -> list[dict]:
    """Per-model cost rollup for the dashboard's "by model" donut chart.

    Only includes rows with real cost data (cost_usd IS NOT NULL). Legacy
    rows are aggregated separately under "legacy_estimate" so the user can
    see how much of the total is still flat-rate guesswork.
    """
    cur.execute(
        """
        SELECT
            COALESCE(model_used, 'unknown') AS model,
            SUM(cost_usd)::float AS cost,
            COUNT(*) AS rows
        FROM chat_history
        WHERE asked_at >= %s AND asked_at < %s
          AND cost_usd IS NOT NULL
        GROUP BY model_used
        ORDER BY cost DESC
        """,
        (since, until),
    )
    return [
        {"model": r[0], "cost_usd": float(r[1] or 0), "rows": int(r[2])}
        for r in cur.fetchall()
    ]


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_range(label: str) -> tuple[datetime, datetime, datetime, datetime, str]:
    """Pick (since, until, prev_since, prev_until, human_label) for a preset.

    "today"  → from local day-start to now, compared with yesterday's same window
    "7d"     → last 7 days vs previous 7
    "30d"    → last 30 days vs previous 30
    """
    now = datetime.now(timezone.utc)
    label = (label or "7d").strip().lower()

    if label == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        prev_since = since - timedelta(days=1)
        prev_until = since
        return since, now, prev_since, prev_until, "วันนี้"

    if label == "30d":
        since = now - timedelta(days=30)
        prev_since = since - timedelta(days=30)
        prev_until = since
        return since, now, prev_since, prev_until, "30 วันที่ผ่านมา"

    # default: 7d
    since = now - timedelta(days=7)
    prev_since = since - timedelta(days=7)
    prev_until = since
    return since, now, prev_since, prev_until, "7 วันที่ผ่านมา"


def _safety_counts_from_audit(since: datetime, until: datetime) -> dict:
    """Parse data/audit.log for safety + login events in [since, until).

    The audit log is JSON-lines on disk — small enough at PoC scale that
    streaming-read + filtering is fine. If the file is missing or the cap is
    reached we silently fall back to zeros so the dashboard still renders.
    """
    from audit import AUDIT_LOG_PATH
    path = str(AUDIT_LOG_PATH)
    if not os.path.exists(path):
        return {
            "blocked_total": 0,
            "blocked_by_category": {},
            "failed_logins": 0,
            "login_blocked_disabled": 0,
        }

    blocked = 0
    by_category: dict[str, int] = {}
    failed = 0
    blocked_disabled = 0

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ts_str = e.get("timestamp") or ""
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                ts = _utc(ts)
                if ts < since or ts >= until:
                    continue
                action = e.get("action") or ""
                if action == "sensitive_blocked":
                    blocked += 1
                    kw = (e.get("detail") or {}).get("matched_keyword") or "OTHER"
                    by_category[kw] = by_category.get(kw, 0) + 1
                elif action == "sensitive_blocked_classifier":
                    blocked += 1
                    cat = (e.get("detail") or {}).get("category") or "OTHER"
                    by_category[cat] = by_category.get(cat, 0) + 1
                elif action == "login_failed":
                    failed += 1
                elif action == "login_blocked_disabled":
                    blocked_disabled += 1
    except OSError:
        pass

    return {
        "blocked_total": blocked,
        "blocked_by_category": by_category,
        "failed_logins": failed,
        "login_blocked_disabled": blocked_disabled,
    }


def _delta_pct(curr: float, prev: float) -> float | None:
    """% change vs previous period. None when prev=0 (can't divide)."""
    if prev <= 0:
        return None
    return round(((curr - prev) / prev) * 100, 1)


def admin_dashboard_overview_pg(range_label: str = "7d") -> dict:
    """Single payload that powers the KPI Dashboard tab.

    Returns counts for the chosen range AND the previous comparable range, so
    the UI can render trend arrows without doing math on the frontend.
    """
    since, until, prev_since, prev_until, human_label = _resolve_range(range_label)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            # ── KPIs (current period) ─────────────────────────────────────
            cur.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM chat_history
                WHERE asked_at >= %s AND asked_at < %s
                """,
                (since, until),
            )
            active_users = cur.fetchone()[0] or 0

            cur.execute(
                """
                SELECT COUNT(*) FROM chat_sessions
                WHERE created_at >= %s AND created_at < %s
                """,
                (since, until),
            )
            new_sessions = cur.fetchone()[0] or 0

            cur.execute(
                """
                SELECT COUNT(*) FROM chat_history
                WHERE asked_at >= %s AND asked_at < %s
                  AND source <> 'export_offer'
                  AND source <> 'blocked'
                """,
                (since, until),
            )
            total_messages = cur.fetchone()[0] or 0

            # ── KPIs (previous period — for trend arrows) ─────────────────
            cur.execute(
                """
                SELECT COUNT(DISTINCT user_id) FROM chat_history
                WHERE asked_at >= %s AND asked_at < %s
                """,
                (prev_since, prev_until),
            )
            prev_active_users = cur.fetchone()[0] or 0

            cur.execute(
                """
                SELECT COUNT(*) FROM chat_sessions
                WHERE created_at >= %s AND created_at < %s
                """,
                (prev_since, prev_until),
            )
            prev_new_sessions = cur.fetchone()[0] or 0

            cur.execute(
                """
                SELECT COUNT(*) FROM chat_history
                WHERE asked_at >= %s AND asked_at < %s
                  AND source <> 'export_offer'
                  AND source <> 'blocked'
                """,
                (prev_since, prev_until),
            )
            prev_total_messages = cur.fetchone()[0] or 0

            # ── Source distribution (current period) ──────────────────────
            cur.execute(
                """
                SELECT source, COUNT(*) FROM chat_history
                WHERE asked_at >= %s AND asked_at < %s
                  AND source <> 'export_offer'
                GROUP BY source ORDER BY 2 DESC
                """,
                (since, until),
            )
            raw_source = [{"source": r[0], "count": r[1]} for r in cur.fetchall()]

            # ── Daily volume timeseries (always 7 buckets for the chart) ──
            cur.execute(
                """
                SELECT to_char(date_trunc('day', asked_at), 'YYYY-MM-DD') AS day,
                       COUNT(DISTINCT session_id) AS sessions,
                       COUNT(*) FILTER (
                         WHERE source <> 'export_offer' AND source <> 'blocked'
                       ) AS messages
                FROM chat_history
                WHERE asked_at >= now() - interval '6 days'
                GROUP BY day ORDER BY day
                """
            )
            usage_trend = [
                {"day": r[0], "sessions": r[1], "messages": r[2]}
                for r in cur.fetchall()
            ]

            # ── Top questions in range ────────────────────────────────────
            cur.execute(
                """
                SELECT question, COUNT(*) c FROM chat_history
                WHERE asked_at >= %s AND asked_at < %s
                  AND source <> 'export_offer'
                  AND source <> 'blocked'
                GROUP BY question
                ORDER BY c DESC, MAX(asked_at) DESC
                LIMIT 5
                """,
                (since, until),
            )
            top_questions = [
                {"question": r[0], "count": r[1]} for r in cur.fetchall()
            ]

            # ── Top users in range ────────────────────────────────────────
            cur.execute(
                """
                SELECT u.username, COUNT(DISTINCT s.id) AS sessions,
                       COUNT(h.id) AS messages
                FROM chat_history h
                JOIN users u ON u.id = h.user_id
                JOIN chat_sessions s ON s.id = h.session_id
                WHERE h.asked_at >= %s AND h.asked_at < %s
                  AND h.source <> 'export_offer'
                GROUP BY u.username
                ORDER BY messages DESC LIMIT 5
                """,
                (since, until),
            )
            top_users = [
                {"username": r[0], "sessions": r[1], "messages": r[2]}
                for r in cur.fetchall()
            ]

            # ── Pending questions (RAG misses we should curate) ───────────
            cur.execute(
                "SELECT COUNT(*) FROM pending_questions WHERE status = 'pending'"
            )
            pending_count = cur.fetchone()[0] or 0

            # ── Feedback 👍 / 👎 in range ─────────────────────────────────
            # Filter by feedback created_at (not the message's asked_at) so
            # changing the range affects "satisfaction during this period",
            # which is what an admin actually wants on a dashboard.
            cur.execute(
                """
                SELECT vote, COUNT(*) FROM answer_feedback
                WHERE created_at >= %s AND created_at < %s
                GROUP BY vote
                """,
                (since, until),
            )
            votes = {row[0]: row[1] for row in cur.fetchall()}
            up = int(votes.get("up", 0) or 0)
            down = int(votes.get("down", 0) or 0)
            total_votes = up + down
            satisfaction_pct = round(up * 100 / total_votes, 1) if total_votes else None

            # ── Recent thumbs-down (audit list — what admin needs to fix) ──
            cur.execute(
                """
                SELECT h.id, h.question, af.reason, u.username, af.created_at
                FROM answer_feedback af
                JOIN chat_history h ON h.id = af.message_id
                JOIN users u ON u.id = af.user_id
                WHERE af.vote = 'down'
                ORDER BY af.created_at DESC
                LIMIT 5
                """,
            )
            recent_downvotes = [
                {
                    "message_id": r[0],
                    "question": r[1],
                    "reason": r[2],
                    "username": r[3],
                    "created_at": r[4].isoformat() if r[4] else None,
                }
                for r in cur.fetchall()
            ]

            # ── Real cost (Phase 5) — SUM(cost_usd) + flat-rate fallback for
            # rows persisted before per-message token tracking landed.
            curr_real, curr_real_rows, curr_legacy_rows = _query_cost_in_range(
                cur, since, until,
            )
            prev_real, _prev_real_rows, prev_legacy_rows = _query_cost_in_range(
                cur, prev_since, prev_until,
            )
            by_model = _cost_breakdown_by_model(cur, since, until)

    # ── Reshape source distribution into known buckets so the UI doesn't
    # have to guess. "brain" + "brain-calc" merge into one slice, etc.
    bucket_map = {
        "brain": "brain", "brain-calc": "brain",
        "rag": "rag", "rag-calc": "rag",
        "llm": "llm", "llm-calc": "llm",
        "files": "files",
        "blocked": "blocked",
    }
    distribution: dict[str, int] = {"brain": 0, "rag": 0, "llm": 0, "files": 0, "blocked": 0}
    for row in raw_source:
        key = bucket_map.get(row["source"], "llm")
        distribution[key] += row["count"]

    answered_total = sum(distribution[k] for k in ("brain", "rag", "llm", "files"))

    # Combine: per-row real cost + flat-rate for legacy NULL rows. As more
    # rows accrue real data, the "estimated_usd" share shrinks naturally.
    curr_estimated = curr_legacy_rows * COST_USD_PER_QUESTION
    prev_estimated = prev_legacy_rows * COST_USD_PER_QUESTION
    cost_usd = curr_real + curr_estimated
    prev_cost_usd = prev_real + prev_estimated

    safety = _safety_counts_from_audit(since, until)

    return {
        "range": {
            "label": range_label,
            "human": human_label,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "prev_since": prev_since.isoformat(),
            "prev_until": prev_until.isoformat(),
        },
        "kpis": {
            "active_users": {
                "value": active_users,
                "prev": prev_active_users,
                "delta_pct": _delta_pct(active_users, prev_active_users),
            },
            "new_sessions": {
                "value": new_sessions,
                "prev": prev_new_sessions,
                "delta_pct": _delta_pct(new_sessions, prev_new_sessions),
            },
            "messages": {
                "value": total_messages,
                "prev": prev_total_messages,
                "delta_pct": _delta_pct(total_messages, prev_total_messages),
            },
            "cost_thb": {
                "value": round(cost_usd * USD_TO_THB, 2),
                "prev": round(prev_cost_usd * USD_TO_THB, 2),
                "delta_pct": _delta_pct(cost_usd, prev_cost_usd),
            },
        },
        "source_distribution": distribution,
        "answered_total": answered_total,
        "usage_trend": usage_trend,
        "top_questions": top_questions,
        "top_users": top_users,
        "pending_count": pending_count,
        "feedback": {
            "up": up,
            "down": down,
            "total": total_votes,
            "satisfaction_pct": satisfaction_pct,
        },
        "recent_downvotes": recent_downvotes,
        # Detailed cost view: real (per-token) vs estimated (flat-rate fallback
        # for legacy NULL rows). UI shows the "X rows still estimated" badge so
        # admin knows when token tracking has fully replaced the estimate.
        "cost": {
            "real_usd": round(curr_real, 6),
            "estimated_usd": round(curr_estimated, 6),
            "total_usd": round(cost_usd, 6),
            "total_thb": round(cost_usd * USD_TO_THB, 2),
            "rows_with_real_cost": curr_real_rows,
            "rows_estimated": curr_legacy_rows,
            "by_model": [
                {
                    "model": m["model"],
                    "cost_usd": round(m["cost_usd"], 6),
                    "cost_thb": round(m["cost_usd"] * USD_TO_THB, 4),
                    "rows": m["rows"],
                }
                for m in by_model
            ],
        },
        "safety": safety,
    }


def dashboard_to_markdown(payload: dict) -> str:
    """Flatten the dashboard overview into a printable markdown report.

    Re-uses pdf_export.export_markdown_to_pdf so we don't maintain a second
    WeasyPrint template — the markdown picks up the same brand header, font
    stack, and table styling as the chat-answer PDF.
    """
    kpis = payload["kpis"]
    src = payload["source_distribution"]
    safety = payload["safety"]
    rng = payload["range"]

    def _arrow(d: float | None) -> str:
        if d is None:
            return "—"
        if d > 0:
            return f"▲ {d:+.1f}%"
        if d < 0:
            return f"▼ {d:.1f}%"
        return "—"

    parts: list[str] = []
    parts.append(f"**ช่วงเวลา:** {rng['human']} ({rng['since'][:10]} → {rng['until'][:10]})")
    parts.append("")
    parts.append("## สรุป KPI")
    parts.append("")
    parts.append("| ตัวชี้วัด | ค่าปัจจุบัน | ค่าก่อนหน้า | เปลี่ยนแปลง |")
    parts.append("|---|---:|---:|---:|")
    parts.append(
        f"| ผู้ใช้ Active | {kpis['active_users']['value']:,} "
        f"| {kpis['active_users']['prev']:,} | {_arrow(kpis['active_users']['delta_pct'])} |"
    )
    parts.append(
        f"| แชทใหม่ | {kpis['new_sessions']['value']:,} "
        f"| {kpis['new_sessions']['prev']:,} | {_arrow(kpis['new_sessions']['delta_pct'])} |"
    )
    parts.append(
        f"| ข้อความ | {kpis['messages']['value']:,} "
        f"| {kpis['messages']['prev']:,} | {_arrow(kpis['messages']['delta_pct'])} |"
    )
    parts.append(
        f"| ค่าใช้จ่าย (฿) | {kpis['cost_thb']['value']:,.2f} "
        f"| {kpis['cost_thb']['prev']:,.2f} | {_arrow(kpis['cost_thb']['delta_pct'])} |"
    )
    parts.append("")

    answered = payload.get("answered_total", 0) or 0
    parts.append("## คำตอบมาจากแหล่งใด")
    parts.append("")
    parts.append("| แหล่งข้อมูล | จำนวน | สัดส่วน |")
    parts.append("|---|---:|---:|")
    for label, key in [
        ("🧠 AI Brain", "brain"),
        ("📚 Knowledge Base", "rag"),
        ("🤖 LLM (general)", "llm"),
        ("📎 ไฟล์แนบ", "files"),
        ("🛑 ถูกบล็อก", "blocked"),
    ]:
        v = src.get(key, 0) or 0
        pct = (v / answered * 100) if answered else 0
        parts.append(f"| {label} | {v:,} | {pct:.1f}% |")
    parts.append("")

    if payload.get("top_questions"):
        parts.append("## คำถามยอดนิยม")
        parts.append("")
        parts.append("| อันดับ | คำถาม | จำนวนครั้ง |")
        parts.append("|---:|---|---:|")
        for i, q in enumerate(payload["top_questions"][:10], 1):
            text = (q["question"] or "").replace("|", "\\|").strip()
            if len(text) > 120:
                text = text[:117] + "..."
            parts.append(f"| {i} | {text} | {q['count']} |")
        parts.append("")

    if payload.get("top_users"):
        parts.append("## ผู้ใช้ที่ Active สูงสุด")
        parts.append("")
        parts.append("| อันดับ | Username | จำนวนแชท | จำนวนข้อความ |")
        parts.append("|---:|---|---:|---:|")
        for i, u in enumerate(payload["top_users"][:10], 1):
            parts.append(
                f"| {i} | {u['username']} | {u['sessions']} | {u['messages']} |"
            )
        parts.append("")

    cost = payload.get("cost") or {}
    if cost.get("by_model") or cost.get("rows_estimated"):
        parts.append("## ค่าใช้จ่ายจริง (per-token)")
        parts.append("")
        parts.append(
            f"- รวมทั้งช่วง: **฿{cost.get('total_thb', 0):.2f}** "
            f"(${cost.get('total_usd', 0):.4f})"
        )
        if cost.get("rows_with_real_cost"):
            parts.append(
                f"- ค่าจริงจาก tokens: {cost.get('rows_with_real_cost')} ครั้ง"
            )
        if cost.get("rows_estimated"):
            parts.append(
                f"- ค่าประมาณ (flat-rate, rows เก่า): "
                f"{cost.get('rows_estimated')} ครั้ง"
            )
        if cost.get("by_model"):
            parts.append("")
            parts.append("| โมเดล | จำนวนครั้ง | ค่าใช้จ่าย (฿) |")
            parts.append("|---|---:|---:|")
            for m in cost["by_model"]:
                parts.append(
                    f"| {m['model']} | {m['rows']} | {m['cost_thb']:.4f} |"
                )
        parts.append("")

    fb = payload.get("feedback") or {}
    if fb.get("total"):
        parts.append("## ความพึงพอใจ (👍 / 👎)")
        parts.append("")
        pct = fb.get("satisfaction_pct")
        pct_str = f"{pct}%" if pct is not None else "—"
        parts.append(
            f"- 👍 ถูกใจ: **{fb['up']}** ครั้ง | 👎 ไม่ถูกใจ: **{fb['down']}** ครั้ง "
            f"| satisfaction: **{pct_str}**"
        )
        parts.append("")

    recent_down = payload.get("recent_downvotes") or []
    if recent_down:
        parts.append("## คำตอบที่โดน 👎 ล่าสุด (admin ควรปรับ)")
        parts.append("")
        parts.append("| # | คำถาม | เหตุผล | โดย |")
        parts.append("|---:|---|---|---|")
        for i, r in enumerate(recent_down[:5], 1):
            q = (r.get("question") or "").replace("|", "\\|").strip()
            if len(q) > 80:
                q = q[:77] + "..."
            reason = (r.get("reason") or "—").replace("|", "\\|").strip()
            if len(reason) > 60:
                reason = reason[:57] + "..."
            parts.append(f"| {i} | {q} | {reason} | {r.get('username', '—')} |")
        parts.append("")

    parts.append("## Safety & Pending")
    parts.append("")
    parts.append(f"- ความพยายามเข้าถึงข้อมูลละเอียดอ่อนที่ถูกบล็อก: **{safety['blocked_total']}** ครั้ง")
    parts.append(f"- Login ล้มเหลว: **{safety['failed_logins']}** ครั้ง")
    parts.append(
        f"- ความพยายาม login ของ user ที่ถูกระงับ: **{safety['login_blocked_disabled']}** ครั้ง"
    )
    parts.append(
        f"- คำถามรอ admin ตอบ (Pending): **{payload.get('pending_count', 0)}** คำถาม"
    )
    if safety.get("blocked_by_category"):
        parts.append("")
        parts.append("### ประเภทที่ถูกบล็อกมากสุด")
        parts.append("")
        for cat, n in sorted(
            safety["blocked_by_category"].items(), key=lambda x: -x[1]
        )[:5]:
            parts.append(f"- {cat}: {n} ครั้ง")

    return "\n".join(parts)


def list_pending_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, question, ask_count, first_asked_at, last_asked_at
                FROM pending_questions
                WHERE status = 'pending'
                ORDER BY ask_count DESC, last_asked_at DESC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "question": row[1],
            "ask_count": row[2],
            "first_asked_at": row[3].isoformat() if row[3] else None,
            "last_asked_at": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


def ignore_pending_pg(pending_id: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE pending_questions
                SET status = 'ignored'
                WHERE id = %s
                """,
                (pending_id,),
            )
            updated = cur.rowcount

            cur.execute(
                """
                DELETE FROM pending_vec
                WHERE pending_id = %s
                """,
                (pending_id,),
            )

    return updated > 0


def list_knowledge_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, question, answer, hit_count, approved_at, source
                FROM knowledge
                ORDER BY id DESC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "question": row[1],
            "answer": row[2],
            "hit_count": row[3],
            "approved_at": row[4].isoformat() if row[4] else None,
            "source": row[5],
        }
        for row in rows
    ]


def verify_knowledge_pg(kid: int, approved_by: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE knowledge
                SET source = 'admin',
                    approved_by = %s,
                    approved_at = now()
                WHERE id = %s
                """,
                (approved_by, kid),
            )
            updated = cur.rowcount

    return updated > 0


def delete_knowledge_pg(kid: int) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM knowledge
                WHERE id = %s
                """,
                (kid,),
            )
            exists = cur.fetchone()

            if not exists:
                return False

            cur.execute(
                """
                DELETE FROM knowledge_vec
                WHERE knowledge_id = %s
                """,
                (kid,),
            )

            cur.execute(
                """
                DELETE FROM knowledge
                WHERE id = %s
                """,
                (kid,),
            )

    return True
import csv
import io


def admin_chat_history_pg() -> list[dict]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.user_id,
                    u.username,
                    s.created_at,
                    s.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM chat_history h
                        WHERE h.session_id = s.id
                    ) AS message_count
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                ORDER BY s.updated_at DESC
                LIMIT 500
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "user_id": row[2],
            "username": row[3],
            "created_at": row[4].isoformat() if row[4] else None,
            "updated_at": row[5].isoformat() if row[5] else None,
            "message_count": row[6],
        }
        for row in rows
    ]


def admin_session_messages_pg(session_id: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    s.id,
                    s.title,
                    s.user_id,
                    u.username,
                    s.created_at
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.id = %s
                """,
                (session_id,),
            )
            session = cur.fetchone()

            if not session:
                return None

            cur.execute(
                """
                SELECT h.id, h.question, h.answer, h.source, h.asked_at,
                       a.id, a.filename, a.content_type, a.size_bytes
                FROM chat_history h
                LEFT JOIN attachments a ON a.message_id = h.id
                WHERE h.session_id = %s
                ORDER BY h.id ASC, a.id ASC
                """,
                (session_id,),
            )
            rows = cur.fetchall()

    messages: dict[int, dict] = {}
    for row in rows:
        msg_id = row[0]
        if msg_id not in messages:
            messages[msg_id] = {
                "id": msg_id,
                "question": row[1],
                "answer": row[2],
                "source": row[3],
                "asked_at": row[4].isoformat() if row[4] else None,
                "attachments": [],
            }
        if row[5] is not None:
            messages[msg_id]["attachments"].append({
                "id": row[5],
                "filename": row[6],
                "content_type": row[7],
                "size_bytes": row[8],
            })

    return {
        "id": session[0],
        "title": session[1],
        "user_id": session[2],
        "username": session[3],
        "created_at": session[4].isoformat() if session[4] else None,
        "messages": list(messages.values()),
    }


def admin_export_all_chat_history_pg() -> tuple[str, str]:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.username,
                    s.title AS session_title,
                    h.asked_at,
                    h.question,
                    h.answer,
                    h.source
                FROM chat_history h
                JOIN chat_sessions s ON s.id = h.session_id
                JOIN users u ON u.id = h.user_id
                ORDER BY h.id ASC
                """
            )
            rows = cur.fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["username", "session_title", "timestamp", "question", "answer", "source"])

    for row in rows:
        writer.writerow(
            [
                row[0],
                row[1],
                row[2].isoformat() if row[2] else "",
                row[3],
                row[4],
                row[5],
            ]
        )

    return "all-chat-history.csv", buf.getvalue()


def admin_delete_session_pg(session_id: int) -> dict | None:
    """
    Delete any chat session (admin override — bypasses user_id check).

    Returns:
        {"username": ..., "title": ..., "user_id": ..., "file_paths": [...]}
            on success — caller uses file_paths to unlink attachments from disk.
        None if the session doesn't exist.

    Mirrors delete_session_pg but without the WHERE user_id = ... guard so
    admins can clean up any user's chat.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id, s.title, s.user_id, u.username
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.id = %s
                """,
                (session_id,),
            )
            session = cur.fetchone()

            if not session:
                return None

            cur.execute(
                """
                SELECT a.file_path
                FROM attachments a
                JOIN chat_history h ON h.id = a.message_id
                WHERE h.session_id = %s
                """,
                (session_id,),
            )
            file_paths = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                DELETE FROM attachments
                WHERE message_id IN (
                    SELECT id FROM chat_history WHERE session_id = %s
                )
                """,
                (session_id,),
            )
            cur.execute(
                "DELETE FROM chat_history WHERE session_id = %s",
                (session_id,),
            )
            cur.execute(
                "DELETE FROM chat_sessions WHERE id = %s",
                (session_id,),
            )

    return {
        "id": session[0],
        "title": session[1],
        "user_id": session[2],
        "username": session[3],
        "file_paths": file_paths,
    }


# ───────────────────────── User management (admin only) ─────────────────────


def admin_list_users_pg() -> list[dict]:
    """List all users with role, status, chat count, and last activity."""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.role,
                    COALESCE(u.is_disabled, false) AS is_disabled,
                    u.created_at,
                    (SELECT COUNT(*) FROM chat_sessions s WHERE s.user_id = u.id) AS chat_count,
                    (SELECT MAX(s.updated_at) FROM chat_sessions s WHERE s.user_id = u.id) AS last_active
                FROM users u
                ORDER BY u.id ASC
                """
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "username": row[1],
            "role": row[2],
            "is_disabled": bool(row[3]),
            "created_at": row[4].isoformat() if row[4] else None,
            "chat_count": row[5] or 0,
            "last_active": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]


def admin_get_user_pg(user_id: int) -> dict | None:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, role, COALESCE(is_disabled, false)
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "role": row[2],
        "is_disabled": bool(row[3]),
    }


def admin_count_active_admins_pg(exclude_user_id: int | None = None) -> int:
    """Number of admins that aren't disabled (optionally excluding one)."""
    query = (
        "SELECT COUNT(*) FROM users "
        "WHERE role = 'admin' AND COALESCE(is_disabled, false) = false"
    )
    params: tuple = ()
    if exclude_user_id is not None:
        query += " AND id <> %s"
        params = (exclude_user_id,)

    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
    return int(row[0]) if row else 0


def admin_set_user_role_pg(user_id: int, role: str) -> bool:
    if role not in {"user", "admin"}:
        raise ValueError("role must be 'user' or 'admin'")
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = %s WHERE id = %s",
                (role, user_id),
            )
            updated = cur.rowcount
    return updated > 0


def admin_set_user_status_pg(user_id: int, is_disabled: bool) -> bool:
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_disabled = %s WHERE id = %s",
                (is_disabled, user_id),
            )
            updated = cur.rowcount
    return updated > 0


def admin_delete_user_chats_pg(user_id: int) -> dict:
    """
    Delete every chat session owned by `user_id`.

    Returns {"sessions_deleted": N, "file_paths": [...]} so the caller can
    unlink attachment files from disk.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM chat_sessions WHERE user_id = %s",
                (user_id,),
            )
            session_ids = [row[0] for row in cur.fetchall()]

            if not session_ids:
                return {"sessions_deleted": 0, "file_paths": []}

            cur.execute(
                """
                SELECT a.file_path
                FROM attachments a
                JOIN chat_history h ON h.id = a.message_id
                WHERE h.session_id = ANY(%s)
                """,
                (session_ids,),
            )
            file_paths = [row[0] for row in cur.fetchall()]

            cur.execute(
                """
                DELETE FROM attachments
                WHERE message_id IN (
                    SELECT id FROM chat_history WHERE session_id = ANY(%s)
                )
                """,
                (session_ids,),
            )
            cur.execute(
                "DELETE FROM chat_history WHERE session_id = ANY(%s)",
                (session_ids,),
            )
            cur.execute(
                "DELETE FROM chat_sessions WHERE id = ANY(%s)",
                (session_ids,),
            )

    return {"sessions_deleted": len(session_ids), "file_paths": file_paths}


def admin_analytics_pg() -> dict:
    """Aggregate usage stats for the admin dashboard (postgres).

    Read-only. Mirrors the sqlite branch in main._admin_analytics_sqlite —
    keep the two in sync when changing the shape.
    """
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_history")
            total_messages = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM chat_sessions")
            total_sessions = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM chat_history "
                "WHERE asked_at >= now() - interval '7 days'"
            )
            messages_7d = cur.fetchone()[0]

            cur.execute("SELECT vote, COUNT(*) FROM answer_feedback GROUP BY vote")
            votes = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                "SELECT source, COUNT(*) c FROM chat_history "
                "GROUP BY source ORDER BY c DESC"
            )
            source_breakdown = [
                {"source": r[0], "count": r[1]} for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT to_char(date_trunc('day', asked_at), 'YYYY-MM-DD') d,
                       COUNT(*) c
                FROM chat_history
                WHERE asked_at >= now() - interval '13 days'
                GROUP BY d ORDER BY d
                """
            )
            daily_volume = [{"day": r[0], "count": r[1]} for r in cur.fetchall()]

            cur.execute(
                "SELECT question, ask_count FROM pending_questions "
                "WHERE status = 'pending' "
                "ORDER BY ask_count DESC, last_asked_at DESC LIMIT 10"
            )
            top_unanswered = [
                {"question": r[0], "ask_count": r[1]} for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT h.question, af.reason, u.username, af.created_at
                FROM answer_feedback af
                JOIN chat_history h ON h.id = af.message_id
                JOIN users u ON u.id = af.user_id
                WHERE af.vote = 'down'
                ORDER BY af.created_at DESC LIMIT 10
                """
            )
            recent_downvotes = [
                {
                    "question": r[0],
                    "reason": r[1],
                    "username": r[2],
                    "created_at": r[3].isoformat() if r[3] else None,
                }
                for r in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT u.username, COUNT(*) c
                FROM chat_history h JOIN users u ON u.id = h.user_id
                GROUP BY u.username ORDER BY c DESC LIMIT 10
                """
            )
            top_users = [{"username": r[0], "count": r[1]} for r in cur.fetchall()]

    return {
        "totals": {
            "messages": total_messages,
            "sessions": total_sessions,
            "users": total_users,
            "messages_7d": messages_7d,
            "feedback_up": votes.get("up", 0),
            "feedback_down": votes.get("down", 0),
        },
        "source_breakdown": source_breakdown,
        "daily_volume": daily_volume,
        "top_unanswered": top_unanswered,
        "recent_downvotes": recent_downvotes,
        "top_users": top_users,
    }