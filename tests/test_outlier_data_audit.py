"""The data audit of 2026-11-10: the all-time boards' rows no rule caught.

    python -m pytest -q tests/test_outlier_data_audit.py
    XCP_TWIN_TEST_DSN="host=/var/tmp port=55432 user=postgres dbname=twin" \
        python -m pytest -q tests/test_outlier_data_audit.py      # and the SQL

Each class is pinned to the board row that showed it:

  race   Whitney Point Invitational 2018, Modified Boys/Girls at "3000 m":
         the whole field +46/+38 points over its own other races
  day    Kentucky state meet 1991: girls at 3000 m (run longer) made the
         venue-day "slow 8.3%", and the 5000 m boys carried it
  mixed  Coast-to-Coast Battle in Beantown 2022, "Combined 5k Women": men
         whose rows are pooled college_f by the division's label
  level  Scott Compton, grade "2010" (a graduation year) on 2008 rows
  close  Adrian Suarez, 10:05 for 3200 m against 13:59 and 12:54 within a
         month either side
"""
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")

import rating_outliers as RO  # noqa: E402

DSN = os.environ.get("XCP_TWIN_TEST_DSN")


# ---------------------------------------------------------------- pure

def test_a_graduation_year_that_means_high_school_is_level():
    # Scott Compton: class of 2010 in fall 2008 is an 11th grader
    assert RO.extraSide(159.0, 10, 157.0, 150.0, 80, "ms_m", 3800, 0,
                        True, 0, False, 0, 0, grad_grade=11) == "level"
    # Colette Heffernan: class of 2032 in fall 2026 is a 7th grader -- stays
    assert RO.extraSide(150.0, 5, 148.0, 145.0, 80, "ms_f", 3000, 0,
                        True, 0, False, 0, 0, grad_grade=7) is None
    # a year that reads as no grade at all (13+) proves nothing
    assert RO.extraSide(150.0, 5, 148.0, 145.0, 80, "ms_f", 3000, 0,
                        True, 0, False, 0, 0, grad_grade=13) is None
    # and a high school row is not this rule's
    assert RO.extraSide(150.0, 5, 148.0, 145.0, 80, "hs_m", 5000, 0,
                        True, 0, False, 0, 0, grad_grade=11) is None


def test_a_field_far_above_itself_is_race():
    # Whitney Point Modified Boys: 18 runners, median +0.36 log, all up
    assert RO.raceSide(18, 0.36, 1.0) == "race"
    # a championship field peaking: +3%, most up -- a real race
    assert RO.raceSide(60, 0.03, 0.70) is None
    # seven runners is one team, not a field
    assert RO.raceSide(7, 0.30, 1.0) is None
    # a high median carried by a few: half the field not up
    assert RO.raceSide(30, 0.09, 0.55) is None


def test_a_division_credited_a_slower_day_than_it_ran_is_day():
    # Kentucky 1991 boys: their own day ~0, the venue-day +8.3% slow
    assert RO.daySide(-0.083) == "day"
    # the broken division itself (ran slow against the day): rated low, safe
    assert RO.daySide(0.25) is None
    assert RO.daySide(-0.02) is None
    assert RO.daySide(None) is None


def test_the_new_sides_reach_the_page_and_the_report():
    tpl = open(os.path.join(_ROOT, "racecast", "templates", "athlete.html"),
               encoding="utf-8").read()
    assert "race.rank_outlier == 'race'" in tpl
    assert "race.rank_outlier == 'day'" in tpl
    src = open(os.path.join(_ROOT, "engine", "rating_outliers.py"), encoding="utf-8").read()
    for fn in ("closeRows(cur)", "raceRows(cur)", "dayRows(cur)"):
        assert f"extra += {fn}" in src, fn


# ---------------------------------------------------------------- SQL

def _conn(schema):
    import psycopg2
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema};"
                f" SET search_path = {schema};")
    return c, cur


def _drop(c, cur, schema):
    c.rollback()
    cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    c.commit()
    c.close()


_RESULTS = """
    CREATE TABLE results (result_id bigint, person_id bigint, source text,
                          meet_id bigint, div_id bigint, date text,
                          speed_rating real, rating_pool text, grade text DEFAULT '8',
                          time_seconds real DEFAULT 600);
    CREATE TABLE meets (div_id bigint, meet_id bigint, distance real);
"""


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_whole_field_sql():
    c, cur = _conn("oa_race")
    try:
        cur.execute(_RESULTS + """
            -- Whitney Point: 12 runners at 150 here, 100 at their other race
            INSERT INTO results SELECT 100 + g, 1000 + g, 'anet', 1, 11, '2018-10-16', 150, 'ms_m|XC'
                                FROM generate_series(1, 12) g;
            INSERT INTO results SELECT 200 + g, 1000 + g, 'anet', 2, 21, '2018-09-29', 100, 'ms_m|XC'
                                FROM generate_series(1, 12) g;
            -- their ninth-grade race the next fall: another pool, no reference
            INSERT INTO results SELECT 300 + g, 1000 + g, 'anet', 3, 31, '2019-09-20', 150, 'hs_m|XC'
                                FROM generate_series(1, 12) g;
            -- a real field: 12 runners at their own level
            INSERT INTO results SELECT 400 + g, 2000 + g, 'anet', 4, 41, '2018-10-20', 121, 'ms_m|XC'
                                FROM generate_series(1, 12) g;
            INSERT INTO results SELECT 500 + g, 2000 + g, 'anet', 5, 51, '2018-09-15', 118, 'ms_m|XC'
                                FROM generate_series(1, 12) g;
        """)
        got = {r[0]: r[6] for r in RO.raceRows(cur)}
    finally:
        _drop(c, cur, "oa_race")
    assert all(got.get(100 + g) == "race" for g in range(1, 13))
    assert not any(k >= 200 for k in got), got


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_last_nights_flag_does_not_clear_itself():
    """A flagged division's rows are no one's reference but are still judged,
    so the next run flags it again on the same evidence."""
    c, cur = _conn("oa_again")
    try:
        cur.execute(_RESULTS + """
            CREATE TABLE rating_outlier (result_id bigint, sport text, side text);
            INSERT INTO results SELECT 100 + g, 1000 + g, 'anet', 1, 11, '2018-10-16', 150, 'ms_m|XC'
                                FROM generate_series(1, 10) g;
            INSERT INTO results SELECT 200 + g, 1000 + g, 'anet', 2, 21, '2018-09-29', 100, 'ms_m|XC'
                                FROM generate_series(1, 10) g;
            INSERT INTO rating_outlier SELECT 100 + g, 'XC', 'race' FROM generate_series(1, 10) g;
        """)
        got = {r[0]: r[6] for r in RO.raceRows(cur)}
    finally:
        _drop(c, cur, "oa_again")
    assert all(got.get(100 + g) == "race" for g in range(1, 11))


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_day_sql_reads_the_solves_guard():
    c, cur = _conn("oa_day")
    try:
        cur.execute(_RESULTS + """
            CREATE TABLE race_day_suspect_division (sport text, meet_id bigint, div_id bigint,
                sample_result_id bigint, excess real, log_excess real);
            -- KY 1991 Boys AAA: ran 8% faster than the day it carries
            INSERT INTO race_day_suspect_division VALUES ('XC', 7, 71, 701, -0.08, -0.083),
            -- the girls' race stored short: ran slow, rated low, left alone
                                                         ('XC', 7, 72, 711, 0.25, 0.26);
            INSERT INTO results VALUES (701, 1, 'anet', 7, 71, '1991-11-02', 150, 'hs_m|XC', '12'),
                                       (702, 2, 'anet', 7, 71, '1991-11-02', 148, 'hs_m|XC', '12'),
                                       (711, 3, 'anet', 7, 72, '1991-11-02', 90, 'hs_f|XC', '12');
        """)
        got = {r[0]: r[6] for r in RO.dayRows(cur)}
    finally:
        _drop(c, cur, "oa_day")
    assert got == {701: "day", 702: "day"}


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_day_rule_without_the_table_is_empty():
    c, cur = _conn("oa_noday")
    try:
        cur.execute(_RESULTS)
        assert RO.dayRows(cur) == []
    finally:
        _drop(c, cur, "oa_noday")


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_close_sql():
    c, cur = _conn("oa_close")
    try:
        cur.execute(_RESULTS + """
            -- Suarez: 159.6 between 117 and 125 within a month
            INSERT INTO results VALUES (1, 9, 'anet', 1, 11, '2019-12-03', 159.6, 'ms_m|XC', '8'),
                                       (2, 9, 'anet', 2, 21, '2019-11-12', 117.0, 'ms_m|XC', '8'),
                                       (3, 9, 'anet', 3, 31, '2019-12-10', 125.0, 'ms_m|XC', '8'),
            -- a breakthrough the next race confirms: left alone
                                       (4, 8, 'anet', 1, 11, '2019-10-03', 150.0, 'ms_m|XC', '8'),
                                       (5, 8, 'anet', 2, 21, '2019-09-12', 117.0, 'ms_m|XC', '8'),
                                       (6, 8, 'anet', 3, 31, '2019-10-20', 148.0, 'ms_m|XC', '8'),
            -- one other race only: the thin rule's, not this one
                                       (7, 7, 'anet', 1, 11, '2019-10-03', 160.0, 'ms_m|XC', '8'),
                                       (8, 7, 'anet', 2, 21, '2019-09-12', 117.0, 'ms_m|XC', '8');
        """)
        got = {r[0]: r[6] for r in RO.closeRows(cur)}
    finally:
        _drop(c, cur, "oa_close")
    assert got == {1: "fast"}


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_extra_sql_counts_people_and_reads_graduation_years():
    c, cur = _conn("oa_extra")
    try:
        cur.execute(_RESULTS + """
            CREATE TABLE person_gender (person_id bigint PRIMARY KEY, gender text, split boolean);
            INSERT INTO meets VALUES (41, 4, 5000), (51, 5, 3800);
            -- Beantown: every row pooled college_f by the label; 8 are men
            INSERT INTO results SELECT 400 + g, 4000 + g, 'anet', 4, 41, '2022-09-23', 130,
                                       'college_f|XC', 'SR-4'
                                FROM generate_series(1, 40) g;
            INSERT INTO person_gender SELECT 4000 + g, CASE WHEN g <= 8 THEN 'M' ELSE 'F' END, false
                                      FROM generate_series(1, 40) g;
            -- Scott Compton: "2010" on a 2008 row in a middle school pool
            INSERT INTO results SELECT 500 + g, 5000 + g, 'anet', 5, 51, '2008-09-06', 120,
                                       'ms_m|XC', '8'
                                FROM generate_series(1, 20) g;
            INSERT INTO results VALUES (599, 5999, 'anet', 5, 51, '2008-09-06', 159, 'ms_m|XC', '2010'),
            -- a seventh grader by graduation year: stays
                                       (598, 5998, 'anet', 5, 51, '2008-09-06', 121, 'ms_m|XC', '2014');
        """)
        got = {r[0]: r[6] for r in RO.extraRows(cur)}
    finally:
        _drop(c, cur, "oa_extra")
    assert all(got.get(400 + g) == "mixed" for g in range(1, 41)), got
    assert got.get(599) == "level"
    assert 598 not in got
