# Project: xc-predictor / tests
# File:    test_race_difficulty_cell.py
# Purpose: the race page and athlete page find a course's difficulty the
#          way the course page does (app._xc_cell_join).
#
# ⚠ OWNER, 2026-10-07: "difficulty doesn't show on some race pages even if a
#   course has a difficulty". The race header required the gps equal to five
#   decimals and the cell stored at exactly the rounded distance; the course
#   page matches the name and both spellings of the distance.
#
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))


def test_both_queries_use_the_shared_lookup():
    src = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert src.count("{_xc_cell_join('r')}") == 2
    assert "round(cc.gps_lat::numeric,  5)" not in src


def test_finds_the_cell_with_gps_a_little_off_and_an_unrounded_distance():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import app as A
    cx = psycopg2.connect(dsn)
    cur = cx.cursor()
    cur.execute("""
        CREATE TEMP TABLE course_canonical (canonical_id int, course_name text,
                                            gps_lat double precision, gps_long double precision);
        CREATE TEMP TABLE course_difficulties (canonical_id int, distance_m int,
                                               difficulty real, n_results int);
        CREATE TEMP TABLE r (meet_id int, div_id int);
        CREATE TEMP TABLE m (meet_id int, div_id int, course_name text, distance real,
                             gps_lat double precision, gps_long double precision);
        CREATE TEMP TABLE mt (meet_id int, venue_name text, division_distances jsonb,
                              gps_lat double precision, gps_long double precision);
        CREATE TEMP TABLE dov (meet_id int, div_id int, distance real);
        -- two parks with one name: the near one is this meet's
        INSERT INTO course_canonical VALUES (1, 'Central Park', 40.78000, -73.96600),
                                            (2, 'Central Park', 33.00000, -117.00000);
        INSERT INTO course_difficulties VALUES (1, 4828, 0.041, 900), (1, 5000, 0.050, 4000),
                                               (2, 4800, -0.020, 50);
        INSERT INTO r VALUES (7, 1), (7, 2), (8, 1);
        -- gps a few metres off the canonical's: the old exact match found nothing
        INSERT INTO m VALUES (7, 1, 'Central Park', 4828, 40.78004, -73.96603),
                             (7, 2, 'Central Park', 5000, 40.78004, -73.96603),
                             (8, 1, 'Nowhere Field', 5000, 1, 1);
    """)
    cur.execute(f"""
        SELECT r.meet_id, r.div_id, cd.canonical_id, cd.difficulty, cd.distance_m
        FROM   r
        LEFT JOIN m  ON m.meet_id = r.meet_id AND m.div_id = r.div_id
        LEFT JOIN mt ON mt.meet_id = r.meet_id
        LEFT JOIN dov ON dov.meet_id = r.meet_id AND dov.div_id = r.div_id
        {A._xc_cell_join('r')}
        ORDER  BY 1, 2
    """)
    got = cur.fetchall()
    cx.rollback()
    assert got[0][2:] == (1, pytest.approx(0.041), 4828)    # 3 miles, stored unrounded, near park
    assert got[1][2:] == (1, pytest.approx(0.050), 5000)
    assert got[2][2:] == (None, None, None)                 # no such course: no difficulty
