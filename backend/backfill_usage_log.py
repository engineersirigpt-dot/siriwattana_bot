"""
One-off: fill the AI usage log (ai_usage_log) with the usage recorded before
the log existed, so the usage report covers earlier months as well.

    docker exec siriwattana-backend-prod python backfill_usage_log.py          # dry run: shows what it would add
    docker exec siriwattana-backend-prod python backfill_usage_log.py --apply  # writes it (once)

Everything older than the first live log row is taken from (so nothing is
counted twice with live logging):
  chat_history       answers with a recorded cost — except image / web answers
  image_generations  every generated or edited image   ┐ these two keep rows whose
  web_searches       every web search                 ┘ chat was deleted since
  translation_jobs   finished translation jobs
Rows are marked backfilled=true. Running it again does nothing; --redo
rebuilds the backfilled rows.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict

from db_pg import get_pg_conn
from usage_log import feature_for, insert_usage

# Counted from their own tables, which also kept the rows of deleted chats.
_FROM_OWN_TABLE = {"image_gen", "image_edit", "web"}


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def collect(cur, cutoff) -> list[dict]:
    """Rows to add, all older than `cutoff` (None → everything)."""
    before = "AND {col} < %s" if cutoff else ""
    args = (cutoff,) if cutoff else ()
    rows: list[dict] = []

    cur.execute(
        "SELECT id, user_id, session_id, source, model_used, prompt_tokens, completion_tokens, "
        f"cost_usd, asked_at FROM chat_history WHERE cost_usd IS NOT NULL {before.format(col='asked_at')}",
        args,
    )
    for mid, uid, sid, source, model, p, c, cost, at in cur.fetchall():
        if source in _FROM_OWN_TABLE:
            continue
        rows.append(dict(user_id=uid, feature=feature_for(source), source=source, session_id=sid,
                         message_id=mid, created_at=at, usage=dict(
                             model_used=model, prompt_tokens=p or 0, completion_tokens=c or 0,
                             cost_usd=float(cost))))

    # A search made by an Agent V1 turn is already inside that turn's chat cost;
    # take it from web_searches only if the agent answer is gone (chat deleted).
    not_agent = (
        "AND NOT EXISTS (SELECT 1 FROM chat_history h WHERE h.user_id = t.user_id "
        "AND h.source = 'agent' AND h.asked_at BETWEEN t.created_at AND t.created_at + interval '5 minutes')"
    )
    for table, feature, source, model, extra in (
        ("image_generations", "image", "image_gen", os.getenv("IMAGE_MODEL", "gpt-image-2"), ""),
        ("web_searches", "web_search", "web", os.getenv("WEB_SEARCH_MODEL", "gpt-5.6-terra"), not_agent),
    ):
        if not _table_exists(cur, table):
            continue
        cur.execute(f"SELECT user_id, cost_usd, created_at FROM {table} t WHERE true {extra} "
                    f"{before.format(col='t.created_at')}", args)
        for uid, cost, at in cur.fetchall():
            rows.append(dict(user_id=uid, feature=feature, source=source, created_at=at,
                             usage=dict(model_used=model, cost_usd=float(cost or 0))))

    if _table_exists(cur, "translation_jobs"):
        cur.execute("SELECT id, user_id, cost_usd, created_at FROM translation_jobs "
                    f"WHERE status = 'done' {before.format(col='created_at')}", args)
        for jid, uid, cost, at in cur.fetchall():
            rows.append(dict(user_id=uid, feature="translation", source="translation", ref=jid,
                             created_at=at, usage=dict(model_used=os.getenv("LLM_MODEL", "gpt-4.1"),
                                                       cost_usd=float(cost or 0))))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the rows (default: dry run)")
    ap.add_argument("--redo", action="store_true", help="replace previously backfilled rows")
    args = ap.parse_args()

    with get_pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ai_usage_log WHERE backfilled")
        already = cur.fetchone()[0]
        if already and not args.redo:
            print(f"มีข้อมูลย้อนหลังอยู่แล้ว {already} แถว — ไม่ทำซ้ำ (ใช้ --redo ถ้าต้องการสร้างใหม่)")
            return
        if already and args.redo and args.apply:
            cur.execute("DELETE FROM ai_usage_log WHERE backfilled")

        cur.execute("SELECT min(created_at) FROM ai_usage_log WHERE NOT backfilled")
        cutoff = cur.fetchone()[0]
        rows = collect(cur, cutoff)

        by_feature: dict = defaultdict(lambda: [0, 0.0])
        for r in rows:
            by_feature[r["feature"]][0] += 1
            by_feature[r["feature"]][1] += r["usage"]["cost_usd"]
        print(f"ข้อมูลก่อน {cutoff or 'ปัจจุบัน'}: {len(rows)} แถว, "
              f"${sum(v[1] for v in by_feature.values()):.4f}")
        for feature, (n, usd) in sorted(by_feature.items(), key=lambda kv: -kv[1][1]):
            print(f"  {feature:<18} {n:>6} แถว  ${usd:.4f}")

        if not args.apply:
            print("dry run — ยังไม่ได้บันทึก (เพิ่ม --apply เพื่อบันทึกจริง)")
            conn.rollback()
            return
        for r in rows:
            insert_usage(cur, user_id=r["user_id"], feature=r["feature"], source=r["source"],
                         usage=r["usage"], session_id=r.get("session_id"),
                         message_id=r.get("message_id"), ref=r.get("ref"),
                         created_at=r["created_at"], backfilled=True)
        print(f"บันทึกแล้ว {len(rows)} แถว")


if __name__ == "__main__":
    main()
