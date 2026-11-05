"""twin_flag.xc_split_copy and the splits meet name (owner, 2026-11-05,
/athlete/33511944: a 13:24.4 split filed as a 5000 beside the day's 21:30
race). Needs a scratch Postgres: XCP_TWIN_TEST_DSN."""
import os
import re
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, p))
DSN = os.environ.get("XCP_TWIN_TEST_DSN")


def test_the_name_rule():
    import twin_flag as T
    rx = re.compile(T.SPLITS_NAME_RX.replace(r"\m", r"\b").replace(r"\M", r"\b"), re.I)
    assert rx.search("Lou Regions Splits (official Results on Milesplit)")
    assert rx.search("Region 3 Split Times")
    assert not rx.search("Split Rock Invitational")
    assert "SPLITS_NAME_RX" not in T.xcPlaceholderSql("results", "XC")
    assert "splits" in T.xcPlaceholderSql("results", "XC")


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_the_faster_row_of_one_day_is_flagged():
    import psycopg2
    import twin_flag as T
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("CREATE TEMP TABLE results (result_id bigint, person_id bigint, source text, "
                    "meet_id bigint, div_id bigint, date text, time_seconds real)")
        cur.execute("CREATE TEMP TABLE meets (div_id bigint, meet_id bigint, source text, "
                    "distance real)")
        cur.executemany("INSERT INTO meets VALUES (%s,%s,%s,%s)",
                        [(11, 1, "anet", 5000), (21, 2, "anet", 5000), (31, 3, "anet", 5000),
                         (41, 4, "anet", 3000), (51, 5, "anet", 5000)])
        cur.executemany("INSERT INTO results VALUES (%s,%s,%s,%s,%s,%s,%s)", [
            (1, 7, "anet", 1, 11, "2026-10-24", 1290.0),     # the race, 21:30
            (2, 7, "anet", 2, 21, "2026-10-24", 804.4),      # the split, 13:24.4
            (3, 8, "anet", 1, 11, "2026-10-24", 1100.0),     # a feed copy, 3% apart: not a split
            (4, 8, "anet", 3, 31, "2026-10-24", 1070.0),
            (5, 9, "anet", 4, 41, "2026-10-24", 600.0),      # a 3000 and a 5000: two distances
            (6, 9, "anet", 5, 51, "2026-10-24", 1100.0),
            (7, 7, "anet", 5, 51, "2026-10-25", 700.0)])      # another day
        cur.execute(T.xcSplitCopySql("results", "XC"))
        assert sorted(r[0] for r in cur.fetchall()) == [2]
        assert "WHERE false" in T.xcSplitCopySql("results_tf", "TF")
    finally:
        c.rollback()
        c.close()
