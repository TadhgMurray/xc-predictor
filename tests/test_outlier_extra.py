"""The rows the outlier neighbourhood cannot judge (owner, 2026-11-07, the
board audit): too few other races, too small a field, a college or open race
under a youth grade."""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import rating_outliers as RO  # noqa: E402


def test_the_audits_rows_get_their_side():
    # Navarrete: one other race at 71.9
    assert RO.extraSide(169.2, 1, 71.9, 71.9, 220, "hs_f", 5150, 0) == "thin"
    # Floris: 161.3 against one race at 114.2
    assert RO.extraSide(161.3, 1, 114.2, 114.2, 149, "hs_f", 4828, 0) == "thin"
    # a 9:12 3000 m in a field of two, far above nine other races
    assert RO.extraSide(176.0, 9, 95.0, 77.2, 2, "ms_f", 3000, 0) == "field"
    # a field of two and no other race to say
    assert RO.extraSide(167.1, 0, None, None, 2, "hs_f", 5000, 0) == "field"
    # an 8000 m championship under a middle school pool
    assert RO.extraSide(155.2, 12, 140.0, 130.7, 309, "ms_m", 8000, 250) == "level"
    # an open race most of whose field is college
    assert RO.extraSide(140.0, 0, None, None, 40, "ms_m", 5000, 30) == "level"
    # no grade, a club's name, in a race high school and college men ran too
    assert RO.extraSide(162.8, 1, 159.2, 159.2, 47, "ms_m", 5000, 3, False, 20) == "level"


def test_ordinary_rows_are_left_alone():
    # Kentucky 1991: 150.1 against one race at 135.8 -- 10%, not 35%
    assert RO.extraSide(150.1, 1, 135.8, 135.8, 7, "hs_m", 5000, 0) is None
    # a small field run at the athlete's own level
    assert RO.extraSide(120.0, 6, 125.0, 119.0, 2, "hs_f", 5000, 0) is None
    # a real middle school race
    assert RO.extraSide(120.0, 0, None, None, 80, "ms_m", 3200, 0) is None
    # no grade, but a middle school race (nobody older in it)
    assert RO.extraSide(120.0, 0, None, None, 80, "ms_m", 3200, 0, False, 0) is None
    # no grade, a middle school race with two ninth graders in it
    assert RO.extraSide(120.0, 0, None, None, 80, "ms_m", 3200, 0, False, 2) is None
    # graded 8 in an open race: the grade is evidence, left alone
    assert RO.extraSide(120.0, 3, 125.0, 118.0, 80, "ms_m", 5000, 3, True, 20) is None
    # a breakout among many races is the neighbourhood's to judge, not this
    assert RO.extraSide(150.0, 8, 130.0, 125.0, 200, "hs_m", 5000, 0) is None


DSN = os.environ.get("XCP_TWIN_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_sql_finds_them():
    import psycopg2
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    cur.execute("""
        DROP SCHEMA IF EXISTS ox CASCADE; CREATE SCHEMA ox; SET search_path = ox;
        CREATE TABLE results (result_id bigint, person_id bigint, source text, meet_id bigint,
                              div_id bigint, date text, speed_rating real, rating_pool text,
                              grade text DEFAULT '9');
        CREATE TABLE meets (div_id bigint, meet_id bigint, distance real);
        INSERT INTO meets VALUES (11, 1, 5000), (21, 2, 8000), (31, 3, 5000);
        -- a big race: person 1 far above their one other race (thin)
        INSERT INTO results SELECT 100 + g, 1000 + g, 'anet', 1, 11, '2026-09-05', 100, 'hs_f|XC'
                            FROM generate_series(1, 9) g;
        INSERT INTO results VALUES (1, 1, 'anet', 1, 11, '2026-09-05', 169, 'hs_f|XC'),
                                   (2, 1, 'anet', 3, 31, '2026-10-01', 72, 'hs_f|XC');
        -- an 8000 m race under a middle school pool (level)
        INSERT INTO results SELECT 200 + g, 2000 + g, 'anet', 2, 21, '2026-09-12', 150,
                                   CASE WHEN g = 1 THEN 'ms_m|XC' ELSE 'college_m|XC' END
                            FROM generate_series(1, 5) g;
    """)
    got = {r[0]: r[6] for r in RO.extraRows(cur)}
    cur.execute("DROP SCHEMA ox CASCADE")
    c.commit()
    c.close()
    assert got.get(1) == "thin"
    assert got.get(201) == "level"
    assert 2 not in got and 202 not in got
