"""rating_outliers.write takes no table lock on a normal run (2026-11-12:
athlete pages waited their 5 s lock_timeout on rating_outlier while the
pipeline rewrote it). A reader with the site's lock_timeout reads the old
rows while a write is open, and the new ones after it commits; a first run
still creates the table and its columns.

    XCP_TWIN_TEST_DSN="host=... dbname=postgres" runs it
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _env  # noqa: E402,F401

import pytest  # noqa: E402

import rating_outliers as RO  # noqa: E402

CAL = {"k": 5, "floor": 0.1, "fast_cut": 3.0, "slow_cut": 3.0, "n_judged": 2, "detail": {}}


def _row(rid, side="fast"):
    return (rid, "XC", 10 + rid, 2026, 150.0, 120.0, 4.0, 6, 5.1, side, 30)


def test_a_rewrite_never_blocks_a_reader():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    w, r = psycopg2.connect(dsn), psycopg2.connect(dsn)
    wc, rc = w.cursor(), r.cursor()
    try:
        wc.execute("DROP SCHEMA IF EXISTS t_rolock CASCADE; CREATE SCHEMA t_rolock")
        w.commit()
        for c in (wc, rc):
            c.execute("SET search_path TO t_rolock")
        # a first run creates the table and its columns
        assert RO.ensureTables(wc) is True
        RO.write(wc, [_row(1)], CAL)
        w.commit()
        assert RO.ensureTables(wc) is False
        w.commit()
        # the next run, left open, while a page reads with a short lock_timeout
        RO.write(wc, [_row(2), _row(3, "slow")], CAL)
        rc.execute("SET lock_timeout = '500ms'")
        rc.execute("SELECT result_id, side FROM rating_outlier WHERE sport = %s "
                   "AND result_id = ANY(%s)", ("XC", [1, 2, 3]))
        assert rc.fetchall() == [(1, "fast")]          # the old rows, no wait
        r.commit()
        w.commit()
        rc.execute("SELECT result_id FROM rating_outlier ORDER BY 1")
        assert [x[0] for x in rc.fetchall()] == [2, 3]
        r.commit()
    finally:
        w.rollback()
        r.rollback()
        wc.execute("DROP SCHEMA IF EXISTS t_rolock CASCADE")
        w.commit()
        w.close()
        r.close()
