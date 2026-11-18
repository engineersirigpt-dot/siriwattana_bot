"""Long tables are cut in the history the model sees (offline)."""
from chat_pg import trim_long_tables


def _answer(n: int) -> str:
    return ("ได้ค่ะ นี่คือรายการทั้งหมด\n\n**รายการทั้งหมด %d งาน**\n\n| เลขที่ | ชื่องาน |\n|---|---|\n" % n
            + "\n".join(f"| E{i:08d} | งาน {i} |" for i in range(n)) + "\n\nจบ")


def test_a_full_list_is_cut_to_15_rows_with_a_note():
    # a "show all" answer has up to 1,000 rows; every later question would pay for them again
    out = trim_long_tables(_answer(661))
    assert out.count("| E") == 15 and "| E00000014 |" in out and "| E00000015 |" not in out
    assert "| เลขที่ | ชื่องาน |\n|---|---|" in out
    assert "(ตารางนี้มีทั้งหมด 661 แถว — ในประวัติแชทเหลือ 15 แถวแรก)" in out
    assert out.startswith("ได้ค่ะ นี่คือรายการทั้งหมด") and out.endswith("จบ")


def test_short_tables_and_plain_answers_are_unchanged():
    for text in (_answer(10), _answer(15), "ไม่มีตาราง", ""):
        assert trim_long_tables(text) == text
    assert trim_long_tables(None) == ""
