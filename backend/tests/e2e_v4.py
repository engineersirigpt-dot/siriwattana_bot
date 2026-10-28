"""
End-to-end check of V4 (model routing + memory/context) against a RUNNING
backend with real OpenAI — about ฿3–5 for everything, capped at --budget-thb
(default 15): the script stops once the usage log passes it. Not collected by
pytest.

Backend: MODEL_ROUTING_MODE=admin MEMORY_MODE=admin (other flags off), so
e2e_admin gets V4 and e2e_other the legacy pipeline — the same questions to
both give the before/after numbers. Users from tests/e2e_code_interpreter.py.

    python tests/e2e_v4.py --base http://host:8000 --db postgresql://… [--part routing|memory|long]
Prints one JSON line per case, then the before/after table and the spend.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import e2e_code_interpreter as e2e  # noqa: E402

USD_TO_THB = 36.0
QUESTIONS = [   # (tier expected for the routed user, question, check on the answer)
    ("light", "สวัสดีครับ", lambda a: len(a) > 0),
    ("standard", "CMYK กับ RGB ต่างกันอย่างไร อธิบายสั้นๆ 3 บรรทัด", lambda a: "CMYK" in a and "RGB" in a),
    ("calc", "ฝากเงิน 100,000 บาท ดอกเบี้ย 3% ต่อปี ทบต้นรายปี 5 ปี ได้เงินรวมเท่าไหร่ ตอบตัวเลขทศนิยม 2 ตำแหน่ง",
     lambda a: "115927.41" in e2e.digits(a)),
    ("deep", "ช่วยวิเคราะห์ข้อดีข้อเสียของการเพิ่มกะกลางคืนในโรงพิมพ์ ตอบสั้นๆ อย่างละ 3 ข้อ",
     lambda a: "ข้อดี" in a and "ข้อเสีย" in a),
]


class Budget:
    def __init__(self, db: str, cap_thb: float):
        self.db, self.cap = db, cap_thb
        self.start = int(self.q("SELECT COALESCE(max(id), 0) FROM ai_usage_log")[0][0])

    def q(self, sql, args=()):
        with psycopg.connect(self.db) as c, c.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def spent_thb(self) -> float:
        return float(self.q("SELECT COALESCE(sum(cost_usd), 0) FROM ai_usage_log WHERE id > %s",
                            (self.start,))[0][0]) * USD_TO_THB

    def check(self) -> None:
        spent = self.spent_thb()
        if spent > self.cap:
            raise SystemExit(f"BUDGET: spent ฿{spent:.2f} > cap ฿{self.cap:.2f} — stopping")

    def rows(self, message_id: int | None) -> list[tuple]:
        if not message_id:
            return []
        return self.q("SELECT route, model, reasoning_effort, latency_ms, first_token_ms, cost_usd::float "
                      "FROM ai_usage_log WHERE message_id = %s AND id > %s ORDER BY id", (message_id, self.start))


def routing(admin: str, other: str, budget: Budget) -> list[dict]:
    """Same question: legacy (e2e_other) vs routed (e2e_admin)."""
    compare = []
    for tier, q, ok in QUESTIONS:
        for who, t in (("legacy", other), ("routed", admin)):
            res = e2e.stream_chat(t, q)
            rows = budget.rows(res["message_id"])
            chat = next((r for r in rows if r[0]), None)
            cost = sum(r[5] for r in rows) * USD_TO_THB          # answer + safety classifier
            want = "legacy" if who == "legacy" else tier
            e2e.record(f"{who}_{tier}", bool(chat) and chat[0] == want and ok(res["answer"]), res,
                       route=chat and chat[0], model=chat and chat[1], effort=chat and chat[2],
                       cost_thb=round(cost, 3))
            compare.append({"tier": tier, "who": who, "model": chat and chat[1], "effort": chat and chat[2],
                            "first_s": res["first_s"], "total_s": res["total_s"], "cost_thb": round(cost, 3)})
            budget.check()
    return compare


def memory_cases(admin: str, other: str) -> None:
    """Save → used in a new chat → not seen by another user → clear → gone."""
    r = e2e.stream_chat(admin, "จำไว้ว่า ฉันอยู่ฝ่ายผลิต ทำงานกะเช้า ชอบคำตอบสั้นๆ")
    e2e.record("memory_save_command", r["source"] == "memory" and r["answer"].startswith("จำไว้แล้ว"), r)
    r = e2e.stream_chat(admin, "จำไว้ว่า รหัสผ่านอีเมลของฉันคือ abc12345")
    e2e.record("memory_refuses_password",                        # refused by memory, or blocked earlier
               (r["source"] == "memory" and r["answer"].startswith("ไม่บันทึก")) or r["source"] == "blocked", r)
    r = e2e.stream_chat(admin, "ฉันอยู่ฝ่ายไหน ทำงานกะอะไร")                                # a NEW chat
    e2e.record("memory_used_in_a_new_chat", "ฝ่ายผลิต" in r["answer"] and "กะเช้า" in r["answer"], r)
    r = e2e.stream_chat(admin, "ช่วยแนะนำวิธีจัดตารางพักเบรกให้เหมาะกับงานของฉัน")
    e2e.record("memory_shapes_an_ordinary_answer", r["source"] not in ("blocked", "memory") and bool(r["answer"]), r)
    r = e2e.stream_chat(other, "ฉันอยู่ฝ่ายไหน ทำงานกะอะไร")
    e2e.record("memory_not_seen_by_another_user", "ฝ่ายผลิต" not in r["answer"], r)
    r = e2e.stream_chat(admin, "ลบความจำทั้งหมด")
    e2e.record("memory_clear_command", r["source"] == "memory" and "ลบความจำทั้งหมดแล้ว" in r["answer"], r)
    r = e2e.stream_chat(admin, "ฉันอยู่ฝ่ายไหน ทำงานกะอะไร")
    e2e.record("memory_gone_after_clear", "ฝ่ายผลิต" not in r["answer"], r)


def long_chat(admin: str, db: str) -> None:
    """A fact from turn 1 is recalled after it has left the recent window."""
    r = e2e.stream_chat(admin, "ข้อมูลโปรเจกต์นี้: รหัสงานคือ PJ-4471 ชื่องานคือปฏิทินตั้งโต๊ะ 2570 กำหนดส่ง 15 พ.ย.")
    sid = r["session_id"]
    for filler in ("โอเค", "ขอบคุณครับ", "เยี่ยม", "รับทราบ", "โอเคครับ", "ได้เลย", "ขอบคุณมากครับ"):
        e2e.stream_chat(admin, filler, sid=sid)
    time.sleep(8)                                    # the summary runs in the background after each answer
    r = e2e.stream_chat(admin, "รหัสงานของโปรเจกต์นี้คืออะไร ชื่องานอะไร ส่งวันไหน", sid=sid)
    with psycopg.connect(db) as c, c.cursor() as cur:
        cur.execute("SELECT context_summary, summary_upto FROM chat_sessions WHERE id = %s", (sid,))
        summary, upto = cur.fetchone()
    a = r["answer"].replace(" ", "")
    e2e.record("long_chat_recalls_turn_1_from_the_summary",
               "PJ-4471" in a and "ปฏิทิน" in a and "15" in a and bool(summary), r,
               summary=(summary or "")[:300], summary_upto=upto)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--budget-thb", type=float, default=15.0)
    ap.add_argument("--part", choices=("all", "routing", "memory", "long"), default="all")
    args = ap.parse_args()
    e2e.BASE = args.base.rstrip("/")
    budget = Budget(args.db, args.budget_thb)
    admin, other = e2e.login("e2e_admin"), e2e.login("e2e_other")
    compare = routing(admin, other, budget) if args.part in ("all", "routing") else []
    if args.part in ("all", "memory"):
        memory_cases(admin, other)
        budget.check()
    if args.part in ("all", "long"):
        long_chat(admin, args.db)
        budget.check()

    print("\nBEFORE / AFTER (same question):")
    for row in compare:
        print(json.dumps(row, ensure_ascii=False))
    failed = [x["case"] for x in e2e.results if not x["ok"]]
    print(f"\nspent ฿{budget.spent_thb():.2f} (cap ฿{budget.cap:.2f})")
    print(f"{len(e2e.results) - len(failed)}/{len(e2e.results)} PASS" + (f" — FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
