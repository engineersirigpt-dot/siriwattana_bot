"""
Tests for sirigpt_tools — the summary-row helper uploaded into the Code
Interpreter sandbox. It decides which Excel rows are subtotals/totals, so these
pin both failure directions seen in E2E runs: counting a total row twice
(top / middle / bottom) and dropping real rows that contain "รวม".
"""

import pytest

from sirigpt_tools import (
    closest_names,
    drop_summary_rows,
    find_rows,
    is_summary_label,
    name_matches,
    names_in_query,
    summary_row_flags,
)

F, T = False, True


def _sum_kept(values, flags):
    return sum(v for v, f in zip(values, flags) if v is not None and not f)


@pytest.mark.parametrize("label,expected", [
    ("รวม", T), ("ยอดรวม", T), ("รวมทั้งสิ้น", T), ("รวมกลุ่ม A", T), ("รวม:", T),
    ("รวม 3 เดือน", T), ("Total", T), ("Grand Total", T), ("Sub-total", T), ("  total ", T),
    # "รวม" as part of real data — never a summary label
    ("งานรวมเล่ม", F), ("รวมมิตรการพิมพ์", F), ("บริษัท รวมมิตรการพิมพ์ จำกัด", F),
    ("ร้านรวมใจ", F), ("รวมเล่มวิทยานิพนธ์", F), ("summer sale", F), (None, F), ("", F),
])
def test_summary_label(label, expected):
    assert is_summary_label(label) is expected


def test_total_row_at_the_top():
    values = [30400, 10000, 12000, 8400]
    labels = ["ยอดรวม", "งานรวมเล่ม", "กล่องลูกฟูก", "ฉลากสินค้า"]
    flags = summary_row_flags(values, labels, detail_filled=[F, T, T, T])
    assert flags == [T, F, F, F]
    assert _sum_kept(values, flags) == 30400


def test_subtotals_in_the_middle_and_grand_total_at_the_bottom():
    values = [6000, 4000, 10000, 6900, 9100, 16000, 26000]
    labels = ["กล่องลูกฟูก", "งานรวมเล่ม", "รวมกลุ่ม A", "แคตตาล็อก", "ฉลากสินค้า", "รวมกลุ่ม B", "รวมทั้งสิ้น"]
    flags = summary_row_flags(values, labels, detail_filled=[T, T, F, T, T, F, F])
    assert flags == [F, F, T, F, F, T, T]
    assert _sum_kept(values, flags) == 26000


def test_total_at_the_bottom_with_real_ruam_data_and_no_qty_columns():
    values = [7500, 3200, 11300, 22000]
    labels = ["บริษัท รวมมิตรการพิมพ์ จำกัด", "ร้านรวมใจ", "บริษัท สยามแพ็ค จำกัด", "Total"]
    flags = summary_row_flags(values, labels, detail_filled=None)
    assert flags == [F, F, F, T]
    assert _sum_kept(values, flags) == 22000


def test_original_sales_sheet_bottom_ruam_row():
    values = [12000, 11250, 18400, 41650]
    flags = summary_row_flags(values, ["กล่องลูกฟูก", "ฉลากสินค้า", "แคตตาล็อก", "รวม"], [T, T, T, F])
    assert flags == [F, F, F, T]


def test_real_ruam_row_kept_even_when_its_amount_coincides_with_a_sum():
    # "รวมมิตรการพิมพ์" = 100 + 200 by coincidence, but it is a customer with
    # qty/price like every other item row → kept.
    values = [300, 100, 200]
    labels = ["รวมมิตรการพิมพ์", "ร้าน ก", "ร้าน ข"]
    assert summary_row_flags(values, labels, detail_filled=[T, T, T]) == [F, F, F]
    # Same without qty columns: no summary label and only 3 rows → still kept
    assert summary_row_flags(values, labels, detail_filled=None) == [F, F, F]


def test_unlabeled_total_row_found_by_position():
    # no qty columns, no label on the total row: first row = sum of the rest
    assert summary_row_flags([600, 100, 200, 300], [None, "ก", "ข", "ค"], None) == [T, F, F, F]
    assert summary_row_flags([100, 200, 300, 600], ["ก", "ข", "ค", ""], None) == [F, F, F, T]


def test_no_summary_rows_means_nothing_removed():
    values = [1200, 450, 2300, 980]
    labels = ["งานรวมเล่ม", "กล่อง", "ฉลาก", "รวมเล่มปกแข็ง"]
    assert summary_row_flags(values, labels, [T, T, T, T]) == [F, F, F, F]


def test_label_alone_never_removes_a_row():
    # "รวม" label but the amount matches no sum → it is data, not a total
    assert summary_row_flags([500, 700, 999], ["ก", "ข", "รวม"], [T, T, F]) == [F, F, F]


def test_one_item_plus_its_total():
    assert summary_row_flags([500, 500], ["ค่าออกแบบ", "รวม"], [T, F]) == [F, T]


def test_blank_and_text_cells_are_ignored():
    values = [None, 1000, "n/a", 2500, float("nan"), 3500]
    labels = ["กลุ่ม A", "ก", "หมายเหตุ", "ข", "", "รวม"]
    flags = summary_row_flags(values, labels, [F, T, F, T, F, F])
    assert flags == [F, F, F, F, F, T]


def test_float_amounts_with_rounding():
    flags = summary_row_flags([0.1, 0.2, 0.30000000000000004], ["ก", "ข", "Total"], None)
    assert flags == [F, F, T]


def test_negative_lines_such_as_credit_notes():
    values = [5000, -800, 1200, 5400]
    assert summary_row_flags(values, ["ขาย", "ลดหนี้", "ขาย", "ยอดรวม"], [T, T, T, F]) == [F, F, F, T]


@pytest.mark.parametrize("value,query,expected", [
    # the E2E miss: user dropped "บริษัท" → exact == found nothing
    ("บริษัท รวมมิตรการพิมพ์ จำกัด", "รวมมิตรการพิมพ์ จำกัด", T),
    ("บริษัท รวมมิตรการพิมพ์ จำกัด", "รวมมิตรการพิมพ์", T),
    ("บริษัท รวมมิตรการพิมพ์ จำกัด", "บริษัท รวมมิตรการพิมพ์ จำกัด", T),
    ("บริษัทรวมมิตรการพิมพ์จำกัด", "รวมมิตร การพิมพ์", T),            # spacing differs
    ("ร้านรวมใจ", "รวมใจ", T),
    ("หจก. สยามแพ็ค", "ห้างหุ้นส่วนจำกัด สยามแพ็ค", T),
    ("Siam Pack Co., Ltd.", "siam pack", T),
    ("บริษัท ไทยรุ่งเรือง จำกัด (มหาชน)", "ไทยรุ่งเรือง", T),
    ("งานรวมเล่ม", "งานรวมเล่ม", T),
    ("บริษัท สยามแพ็ค จำกัด", "รวมมิตรการพิมพ์", F),
    ("ร้านรวมใจ", "รวมมิตร", F),
    ("บริษัท รวมมิตรการพิมพ์ จำกัด", "บริษัท", F),                  # only company-form words
    (None, "รวมใจ", F),
])
def test_name_matches(value, query, expected):
    assert name_matches(value, query) is expected


ITEMS = ["งานรวมเล่ม", "กล่องลูกฟูก", "กล่อง", "ฉลากสินค้า", "งานรวมเล่ม", None]


@pytest.mark.parametrize("query,expected", [
    # E2E miss: Thai has no spaces, the model searched "item + ทั้งไตรมาส"
    ("งานรวมเล่มทั้งไตรมาส", {"งานรวมเล่ม"}),
    ("ยอดงานรวมเล่มเดือนนี้", {"งานรวมเล่ม"}),
    # longest only: not every generic "กล่อง" row as well
    ("กล่องลูกฟูกทั้งปี", {"กล่องลูกฟูก"}),
    ("กล่องทั้งหมด", {"กล่อง"}),
    ("แคตตาล็อกทั้งไตรมาส", set()),
])
def test_names_in_query(query, expected):
    assert names_in_query(ITEMS, query) == expected


def test_names_in_query_ignores_very_short_values():
    assert names_in_query(["ก", "ขข"], "กขขค") == set()


def test_find_rows_falls_back_to_names_inside_the_query():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"รายการ": ["งานรวมเล่ม", "กล่องลูกฟูก", "งานรวมเล่ม", "กล่อง"],
                       "ยอดขาย": [10000, 12000, 4000, 500]})
    assert find_rows(df, "รายการ", "งานรวมเล่มทั้งไตรมาส")["ยอดขาย"].sum() == 14000
    assert find_rows(df, "รายการ", "กล่องลูกฟูกทั้งปี")["ยอดขาย"].sum() == 12000
    # a direct match still wins: "กล่อง" is inside "กล่องลูกฟูก" → both are boxes
    assert find_rows(df, "รายการ", "กล่อง")["ยอดขาย"].sum() == 12500


def test_find_rows_and_closest_names():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        [["บริษัท รวมมิตรการพิมพ์ จำกัด", 7500], ["ร้านรวมใจ", 3200], ["บริษัท สยามแพ็ค จำกัด", 11300]],
        columns=["ลูกค้า", "ยอดขาย"],
    )
    assert find_rows(df, "ลูกค้า", "รวมมิตรการพิมพ์ จำกัด")["ยอดขาย"].sum() == 7500
    assert find_rows(df, "ลูกค้า", "ไม่มีชื่อนี้").empty
    assert closest_names(df, "ลูกค้า", "รวมมิตรการพิม")[0] == "บริษัท รวมมิตรการพิมพ์ จำกัด"


def test_pandas_wrapper_matches_the_core():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        [["กล่องลูกฟูก", 5, 1200, 6000], ["งานรวมเล่ม", 8, 500, 4000], ["รวมกลุ่ม A", None, None, 10000],
         ["แคตตาล็อก", 3, 2300, 6900], ["ฉลากสินค้า", 20, 455, 9100], ["รวมกลุ่ม B", None, None, 16000],
         ["รวมทั้งสิ้น", None, None, 26000]],
        columns=["รายการ", "จำนวน", "ราคาต่อหน่วย", "ยอดขาย"],
    )
    clean, removed = drop_summary_rows(df, "ยอดขาย")
    assert clean["ยอดขาย"].sum() == 26000
    assert removed["รายการ"].tolist() == ["รวมกลุ่ม A", "รวมกลุ่ม B", "รวมทั้งสิ้น"]
    assert "งานรวมเล่ม" in clean["รายการ"].tolist()

    mar = pd.DataFrame(
        [["บริษัท รวมมิตรการพิมพ์ จำกัด", "โบรชัวร์", 7500], ["ร้านรวมใจ", "งานรวมเล่ม", 3200],
         ["บริษัท สยามแพ็ค จำกัด", "กล่องลูกฟูก", 11300], ["Total", None, 22000]],
        columns=["ลูกค้า", "รายการ", "ยอดขาย"],
    )
    clean, removed = drop_summary_rows(mar, "ยอดขาย")
    assert clean["ยอดขาย"].sum() == 22000 and removed["ลูกค้า"].tolist() == ["Total"]
