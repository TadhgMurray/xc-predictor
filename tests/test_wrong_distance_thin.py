"""wrong_distance's thin fields: a big field whose runners each have ONE
other race that season testifies (owner, 2026-11-04: seventh-grade girls at
9:19 for a "3000 m", rated 168, judged on nobody). Needs a scratch Postgres:

    XCP_TWIN_TEST_DSN="host=... port=... user=... dbname=..." python -m pytest tests/test_wrong_distance_thin.py
"""
import math
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, p))

DSN = os.environ.get("XCP_TWIN_TEST_DSN")


def e(t, d):
    return math.log(t) - 1.06 * math.log(d / 5000)


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_a_thin_fast_field_testifies_and_a_true_one_is_left_alone():
    import psycopg2
    import wrong_distance as W
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("CREATE TEMP TABLE wd_row (dk bigint, person_id bigint, yr smallint, e real)")
        cur.execute("CREATE TEMP TABLE wd_stat (dk bigint primary key, n_rows int, n_ref int, "
                    "res float8, q float8[], sh_fast float8, sh_slow float8)")
        rows = []
        for i in range(20):        # listed 3000, run ~2000; one other race each at 3218
            rows += [(1, i, 2026, e(570 + i * 10, 3000)), (100 + i, i, 2026, e(930 + i * 15, 3218))]
        for i in range(20):        # a thin field that ran true
            rows += [(2, 50 + i, 2026, e(1200 + i * 10, 5000)), (200 + i, 50 + i, 2026, e(1205 + i * 10, 5000))]
        for i in range(8):         # too few runners to testify
            rows += [(3, 90 + i, 2026, e(570, 3000)), (300 + i, 90 + i, 2026, e(930, 3218))]
        cur.executemany("INSERT INTO wd_row VALUES (%s,%s,%s,%s)", rows)
        cur.execute("INSERT INTO wd_stat VALUES (1,20,0,NULL,NULL,NULL,NULL),"
                    "(2,20,0,NULL,NULL,NULL,NULL),(3,8,0,NULL,NULL,NULL,NULL)")
        cur.execute(W._THIN_SQL, {"f": W.FIELD_MIN, "t": W.THIN_FIELD})
        cur.execute(W._THIN_APPLY, {"sh": W.THIN_SHARE})
        cur.execute("SELECT dk, n_ref, res FROM wd_stat ORDER BY dk")
        got = {dk: (n, r) for dk, n, r in cur.fetchall()}
        assert got[1][0] == 20 and 1900 < W.implied(3000, got[1][1]) < 2200
        assert got[2] == (0, None) and got[3] == (0, None)
    finally:
        c.rollback()
        c.close()
