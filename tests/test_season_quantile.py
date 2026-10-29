# The season rating (season_floor.seasonQuantileSql): Hyndman-Fan type 8 at
# 0.8. Owner, 2026-10-28: Meshack Kipruto beat Rocky Hansen at Nuttycombe,
# but two races to Hansen's one ranked him below -- percentile_cont pulled a
# second, slower race into the number.
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "racecast"))
os.environ.setdefault("XCP_DB_PASSWORD", "x")

from season_floor import seasonQuantileSql                     # noqa: E402


@pytest.fixture
def cur():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres to run the SQL")
    import psycopg2
    cx = psycopg2.connect(dsn)
    c = cx.cursor()
    c.execute("CREATE TEMP TABLE q (p int, x real)")
    yield c
    cx.rollback()
    cx.close()


def _q(cur, rows):
    from psycopg2.extras import execute_values
    cur.execute("TRUNCATE q")
    execute_values(cur, "INSERT INTO q VALUES %s", rows)
    cur.execute(f"SELECT p, {seasonQuantileSql('x', 0.8)}::real FROM q GROUP BY p ORDER BY p")
    return {p: (round(v, 2) if v is not None else None) for p, v in cur.fetchall()}


def test_a_second_slower_race_does_not_lower_the_season(cur):
    got = _q(cur, [(1, 151.7), (2, 150.4), (2, 148.0)])
    assert got == {1: 151.7, 2: 150.4}        # Kipruto's best counts, ahead of Hansen's one


def test_it_moves_smoothly_with_more_races(cur):
    four = [(3, v) for v in (140, 138, 136, 130)]
    five = four + [(3, 135)]
    a = _q(cur, four)[3]
    b = _q(cur, [(4, v) for _p, v in five])[4]
    assert a == pytest.approx(139.6) and b == pytest.approx(139.2)
    assert a - b < 0.5                         # tenths, not a drop to the 2nd best
    three = _q(cur, [(5, 140), (5, 120), (5, 110)])[5]
    assert three == 140                        # one to three races: the best


def test_no_rated_race_is_null(cur):
    assert _q(cur, [(6, None)]) == {6: None}
    assert _q(cur, [(7, None), (7, 120)]) == {7: 120}
