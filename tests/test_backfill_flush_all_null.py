"""A write page whose normalized times are all None (every row withheld)
must still write (2026-10-23, the nightly's 05_normalize_new failed:
'column normalized_time is of type real but expression is of type text').

    XCP_TWIN_TEST_DSN=postgresql://... python -m pytest -q tests/test_backfill_flush_all_null.py
"""
import os
import sys

import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("backfill", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, generated, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (  # as tests/test_backfill_new_only.py does
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c


@pytest.fixture
def pg():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    conn = psycopg2.connect(dsn)
    yield conn
    conn.rollback()
    conn.close()


def test_an_all_none_page_and_a_mixed_page_write(pg):
    import backfill_normalize as B
    with pg.cursor() as cur:
        cur.execute("CREATE TEMP TABLE r (result_id bigint, normalized_time real)")
        cur.execute("INSERT INTO r VALUES (1, 300), (2, 301), (3, 302)")
        assert B._flushPage(cur, "r", [(None, 1), (None, 2)]) == 2
        assert B._flushPage(cur, "r", [(299.5, 3), (None, 1)]) == 2
        cur.execute("SELECT result_id, normalized_time FROM r ORDER BY 1")
        assert cur.fetchall() == [(1, None), (2, None), (3, 299.5)]
