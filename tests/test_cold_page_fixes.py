"""The first 17a_page_timing work list (2026-10-13): the home page's
"Coming up" block and /search, the two pages over a second cold."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, _d))


def test_coming_up_counts_only_the_weeks_meets():
    import weekend
    sql = weekend._SQL["anet_TF"]
    assert "GROUP BY meet_id) d" not in sql            # the 14M-row derived table
    assert "LEFT JOIN LATERAL" in sql and "t.meet_id = m.meet_id" in sql


def test_calendar_date_indexes_wanted():
    import add_page_indexes as A
    have = {(t, c) for t, c, _n, _s in A.WANTED}
    for want in (("meets", "meet_date"), ("meets_tf_meta", "meet_date"), ("meets_tfrrs", "date")):
        assert want in have, want


def test_search_counts_one_pass_with_fallback():
    src = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    body = src[src.index("def _run_search"):src.index("PAGE_SIZE = 30")]
    assert "LIMIT  %(one)s" in body and "len(one_pass) < SEARCH_ONE_PASS" in body
    # the per-kind capped counts are still there for a common token or a year
    assert re.search(r"else:\n\s+count_where = list\(where_base\)", body)
    assert re.search(r"^SEARCH_ONE_PASS = \d+$", src, re.M)
