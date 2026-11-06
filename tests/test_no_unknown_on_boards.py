"""No "Unknown" on a board (owner, 2026-11-06, /athlete/1009420043: #2 on
the college board as "Unknown"). A placeholder is no name in any of the
three name sources, and a row with no name is left off the boards."""
import os
import sys
import warnings

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, p))
warnings.simplefilter("ignore", SyntaxWarning)
import rankings as R                                            # noqa: E402

DSN = os.environ.get("XCP_TWIN_TEST_DSN")


def test_every_board_query_leaves_the_unnamed_out():
    src = open(os.path.join(_ROOT, "racecast", "rankings.py")).read()
    assert src.count("pageThenName(") >= 4           # the def and the three boards
    bo = open(os.path.join(_ROOT, "racecast", "build_breakouts.py")).read()
    assert bo.count("a.name IS NOT NULL") == 2
    lat = R.nameLateral("s")
    assert "'unknown'" in lat and lat.count("NOT IN ('', 'unknown'") == 2


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_a_placeholder_name_is_no_name():
    import psycopg2
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, "
                    "first_name text, last_name text, gender text)")
        cur.execute("CREATE TEMP TABLE results (person_id bigint, athlete_name text)")
        cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
        cur.execute("INSERT INTO athletes VALUES (1,1,'Unknown',NULL,'M'),(2,2,'Jane','Doe','F'),"
                    "(3,3,'Unknown',NULL,'F')")
        cur.execute("INSERT INTO results VALUES (1,'Unknown'),(1,'Matt Meli'),(3,'Unknown'),(4,'')")
        cur.execute(f"SELECT s.person_id, a.name FROM (VALUES (1),(2),(3),(4)) s(person_id) "
                    f"{R.nameLateral('s')} WHERE true{R.UNNAMED_OUT} ORDER BY 1")
        assert cur.fetchall() == [(1, "Matt Meli"), (2, "Jane Doe")]
    finally:
        c.rollback()
        c.close()


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_the_page_is_cut_before_it_is_named():
    """2026-11-06: 51.8 s for a board page -- the name was looked up for
    every row of the board. The page (plus slack) is cut first."""
    import psycopg2
    c = psycopg2.connect(DSN)
    try:
        cur = c.cursor()
        cur.execute("CREATE TEMP TABLE athletes (athlete_id bigint, person_id bigint, "
                    "first_name text, last_name text, gender text)")
        cur.execute("CREATE TEMP TABLE results (person_id bigint, athlete_name text)")
        cur.execute("CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text)")
        cur.execute("CREATE TEMP TABLE b (person_id bigint, r real)")
        cur.execute("INSERT INTO b SELECT g, 200 - g FROM generate_series(1, 60) g")
        cur.execute("INSERT INTO athletes SELECT g, g, 'N' || g, 'X', 'M' FROM generate_series(1, 60) g "
                    "WHERE g <> 2")
        tail = R.pageThenName("s", "b s", "true", "s.r DESC, s.person_id")
        cur.execute(f"SELECT s.person_id, a.name {tail}", {"offset": 0, "limit": 5})
        assert [r[0] for r in cur.fetchall()] == [1, 3, 4, 5, 6]     # 2 has no name
        cur.execute(f"SELECT s.person_id, a.name {tail}", {"offset": 5, "limit": 5})
        assert [r[0] for r in cur.fetchall()] == [6, 7, 8, 9, 10]
    finally:
        c.rollback()
        c.close()
