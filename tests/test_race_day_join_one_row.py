"""A race is listed once however many race-day rows match it (owner,
2026-10-23: every race of a college runner listed twice on the athlete page
after the course rebuild merged two spellings of a venue into one).

    XCP_TWIN_TEST_DSN=postgresql://... python -m pytest -q tests/test_race_day_join_one_row.py
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _d))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")


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


def _joins():
    import app
    return app._dayJoins()


def test_two_spellings_of_one_course_day_give_one_row(pg):
    xc, tf = _joins()
    with pg.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE race_day_effect (course_name text, canonical_id bigint,
                distance_m integer, race_date date, day_effect real, n_rows integer,
                course_effect real);
            INSERT INTO race_day_effect VALUES
              ('Goodman Campus XC Course', 77, 8000, '2026-10-02', 0.010, 40, 0.02),
              ('Lehigh University - Murray H. Goodman Campus', 77, 8000, '2026-10-02', 0.012, 300, 0.03),
              ('XC:Foot Locker Northeast', NULL, 5000, '2026-11-28', 0.005, 200, NULL),
              ('XC:Foot Locker Northeast', NULL, 5000, '2026-11-28', 0.006, 100, NULL);
            CREATE TEMP TABLE r AS SELECT '2026-10-02'::text AS date, 1 AS id
                                    UNION ALL SELECT '2026-11-28', 2;
        """)
        cur.execute(f"""
            SELECT r.id, rde.day_effect, rde.course_effect
            FROM r
            LEFT JOIN LATERAL (SELECT 77::bigint AS canonical_id, 8000 AS distance_m,
                                      NULL::text AS course_name) cd ON TRUE
            LEFT JOIN LATERAL (SELECT CASE WHEN r.id = 2 THEN 'XC:Foot Locker Northeast' END AS course_name,
                                      5000 AS distance_m) cdc ON TRUE
            {xc}
            ORDER BY r.id""")
        rows = cur.fetchall()
    # one row each; the day with the most results stands for it
    assert [(i, round(d, 3)) for i, d, _ in rows] == [(1, 0.012), (2, 0.005)]
    assert round(rows[0][2], 2) == 0.03


def test_the_track_join_is_one_row_too(pg):
    _, tf = _joins()
    with pg.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE race_day_effect (course_name text, canonical_id bigint,
                distance_m integer, race_date date, day_effect real, n_rows integer);
            INSERT INTO race_day_effect VALUES ('loc:1:out', NULL, 1600, '2026-05-02', 0.1, 5),
                                               ('loc:1:out', NULL, 3200, '2026-05-02', 0.2, 9);
        """)
        cur.execute(f"""
            SELECT count(*) FROM (SELECT '2026-05-02'::text AS date) r
            LEFT JOIN LATERAL (SELECT 'loc:1:out'::text AS course_name) cd ON TRUE
            {tf}""")
        assert cur.fetchone()[0] == 1
