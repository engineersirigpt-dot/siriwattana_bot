"""
Helpers uploaded into the Code Interpreter sandbox (it lands there as
/mnt/data/file-<id>-sirigpt_tools.py, hence code_interpreter._TOOLS_LOADER).

Our own reviewed code, so summary-row handling is the same on every request
instead of being re-invented by the model (which double-counted or dropped
rows in ~1 of 5 runs). Runs only inside OpenAI's sandbox — never on our
server — and its pure-Python core is unit-tested in tests/test_sirigpt_tools.py.

Sandbox use:
    <code_interpreter._TOOLS_LOADER>
    from sirigpt_tools import drop_summary_rows, find_rows, closest_names
    clean, removed = drop_summary_rows(df, "ยอดขาย")
    rows = find_rows(clean, "ลูกค้า", "รวมมิตรการพิมพ์")
"""

from __future__ import annotations

import difflib
import math
import re

# A label counts as a summary label only when it IS a summary phrase — "รวม",
# "รวมกลุ่ม A", "ยอดรวม:", "Total" — not when the word is part of real data
# ("งานรวมเล่ม", "รวมมิตรการพิมพ์", "บริษัท รวมใจ"). Thai has no spaces, so the
# phrase must be followed by the end, a space, a digit or punctuation.
_SUMMARY_LABEL = re.compile(
    r"^\s*(?:ยอดรวม|รวมทั้งสิ้น|รวมทั้งหมด|รวมยอด|รวมกลุ่ม|รวม|grand\s*total|sub\s*-?\s*total|total|sum)"
    r"(?:$|[\s\d:：.\-–(\[/])",
    re.IGNORECASE,
)


def is_summary_label(label) -> bool:
    return bool(_SUMMARY_LABEL.match(str(label or "")))


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _blank(label) -> bool:
    if label is None:
        return True
    if isinstance(label, float) and math.isnan(label):
        return True
    return not str(label).strip()


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, 1e-6 * max(abs(a), abs(b)))


def summary_row_flags(values, labels=None, detail_filled=None) -> list[bool]:
    """True for each subtotal/total row.

    values         per-row amount (None/NaN = no amount: never a summary row)
    labels         per-row text label (e.g. the first text column), optional
    detail_filled  per-row bool — row has the other numbers item rows have
                   (qty, unit price); None when the table has no such columns

    A row is a summary row when its amount equals
      (a) the sum of >= 2 adjacent item rows directly above or below it
          (a group subtotal, or a total placed at the top/bottom), or
      (b) the sum of all other remaining item rows (a grand total, anywhere),
    AND it has evidence beyond the number: no qty/price where item rows have
    them, or a summary label, or — for (b) with >= 3 items — it is the first or
    last amount row AND has no label of its own. A row carrying a real name
    ("รวมมิตรการพิมพ์") or item-like qty/price is never cut on position, and a
    label alone never removes a row. Repeats until stable, so subtotals are
    removed before the totals that include them.
    """
    n = len(values)
    amt = [_num(v) for v in values]
    rows = [i for i in range(n) if amt[i] is not None]
    flags = [False] * n
    filled_share = (
        sum(1 for i in rows if detail_filled[i]) / len(rows)
        if detail_filled is not None and rows else 0.0
    )

    has_details = detail_filled is not None and filled_share >= 0.34

    def evidence(i: int, grand: bool, n_items: int) -> bool:
        if has_details and not detail_filled[i]:
            return True
        if labels is not None and is_summary_label(labels[i]):
            return True
        if has_details:
            return False  # has qty/price like an item row → it is an item
        unlabeled = labels is None or _blank(labels[i])
        return grand and n_items >= 3 and unlabeled and i in (rows[0], rows[-1])

    def adjacent_block(i: int) -> bool:
        for step in (-1, 1):
            total, k, j = 0.0, 0, i + step
            while 0 <= j < n:
                if amt[j] is not None:
                    if flags[j]:
                        break
                    total += amt[j]
                    k += 1
                    if k >= 2 and _same(total, amt[i]):
                        return True
                j += step
        return False

    changed = True
    while changed:
        changed = False
        items = [i for i in rows if not flags[i]]
        grand_sum = sum(amt[i] for i in items)
        for i in items:
            if amt[i] == 0:
                continue
            others = grand_sum - amt[i]
            grand = len(items) >= 2 and _same(others, amt[i])
            if (adjacent_block(i) and evidence(i, False, len(items))) or (grand and evidence(i, True, len(items))):
                flags[i] = True
                changed = True
                break
    return flags


# Company-form words and separators ignored when matching names — users write
# "รวมมิตรการพิมพ์" for "บริษัท รวมมิตรการพิมพ์ จำกัด" (exact == found nothing).
_NAME_NOISE = re.compile(
    r"ห้างหุ้นส่วนจำกัด|ห้างหุ้นส่วน|บริษัท|บจก\.?|หจก\.?|จำกัด|\(?มหาชน\)?|ร้าน|"
    r"co\.?\s*,?\s*ltd\.?|ltd\.?|inc\.?|limited|company|[\s.,()\-_/]",
    re.IGNORECASE,
)


def normalize_name(name) -> str:
    return "" if _blank(name) else _NAME_NOISE.sub("", str(name)).lower()


def name_matches(value, query) -> bool:
    """True when `query` names `value`, ignoring spaces, case and company-form
    words: "รวมมิตรการพิมพ์ จำกัด" matches "บริษัท รวมมิตรการพิมพ์ จำกัด"."""
    q = normalize_name(query)
    return bool(q) and q in normalize_name(value)


def names_in_query(values, query) -> set[str]:
    """Fallback when no value contains `query`: the longest values that appear
    INSIDE it. Thai has no spaces, so "งานรวมเล่มทั้งไตรมาส" (item + "whole
    quarter") still finds "งานรวมเล่ม" — and "กล่องลูกฟูกทั้งปี" finds
    "กล่องลูกฟูก", not every generic "กล่อง" row too."""
    q = normalize_name(query)
    inside = {str(v) for v in values if len(normalize_name(v)) >= 3 and normalize_name(v) in q}
    if not inside:
        return set()
    longest = max(len(normalize_name(v)) for v in inside)
    return {v for v in inside if len(normalize_name(v)) == longest}


def find_rows(df, col, query):
    """Rows of `df` whose `col` names `query` (see name_matches); if none,
    rows whose value appears inside the query (see names_in_query)."""
    hits = df[df[col].map(lambda v: name_matches(v, query))]
    if hits.empty:
        inside = names_in_query(df[col].tolist(), query)
        hits = df[df[col].map(lambda v: str(v) in inside)]
    return hits.copy()


def closest_names(df, col, query, n: int = 3) -> list[str]:
    """Up to `n` distinct values of `col` closest to `query` — to show the user
    before saying a name is not in the file."""
    names = {str(v): normalize_name(v) for v in df[col].tolist() if not _blank(v)}
    q = normalize_name(query)
    ranked = sorted(names, key=lambda v: -difflib.SequenceMatcher(None, q, names[v]).ratio())
    return ranked[:n]


def drop_summary_rows(df, value_col, label_col=None):
    """pandas wrapper: (clean_df, removed_df) — `df` without its subtotal/total
    rows. Sum `value_col` from clean_df only; show removed_df to the user."""
    import pandas as pd

    values = pd.to_numeric(df[value_col], errors="coerce").tolist()
    if label_col is None:
        text_cols = [c for c in df.columns if c != value_col and df[c].dtype == object]
        label_col = text_cols[0] if text_cols else None
    labels = df[label_col].tolist() if label_col is not None else None

    has_amount = pd.to_numeric(df[value_col], errors="coerce").notna()
    detail_cols = []
    for c in df.columns:
        if c == value_col:
            continue
        numeric = pd.to_numeric(df[c], errors="coerce").notna()
        if numeric[has_amount].sum() >= 2:
            detail_cols.append(c)
    detail_filled = (
        df[detail_cols].apply(pd.to_numeric, errors="coerce").notna().all(axis=1).tolist()
        if detail_cols else None
    )

    mask = pd.Series(summary_row_flags(values, labels, detail_filled), index=df.index)
    return df[~mask].copy(), df[mask].copy()
