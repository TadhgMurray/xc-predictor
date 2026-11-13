"""racecast/panels.combineFacts (2026-11-12): the home page's results and
years come from BOTH sports' stored spans, whichever sport's process wrote
last. Needs a scratch Postgres: XCP_TEST_PG_DSN (skipped without one)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, d) for d in ("racecast", "scripts", "engine")]
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
DSN = os.environ.get("XCP_TEST_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="XCP_TEST_PG_DSN not set")


def test_both_sports_spans_make_the_hero_numbers():
    import psycopg2
    import panels
    conn = psycopg2.connect(DSN)
    try:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS homepage_meta")
        cur.execute("CREATE TABLE homepage_meta (key text PRIMARY KEY, value text)")
        cur.execute("INSERT INTO homepage_meta VALUES ('span_XC','34000000,1990,2026'),"
                    "('span_TF','168000000,1985,2025'),('fact_results','34M')")
        conn.commit()
        assert panels.combineFacts(conn) == {"fact_results": panels._formatCount(202000000),
                                             "fact_years": "42"}
        cur.execute("SELECT value FROM homepage_meta WHERE key = 'fact_results'")
        assert cur.fetchone()[0] == panels._formatCount(202000000)
    finally:
        conn.rollback(); conn.close()
