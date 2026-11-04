"""A runner whose career is a decade old is not at school (owner, 2026-11-04:
Jacques Sallberg, an adult rated as a collegian). Needs a scratch Postgres
(XCP_TWIN_TEST_DSN, as test_twin_flag)."""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
for sub in ("engine", "scripts", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, sub))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")


def test_long_career_seasons():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import pro_flag as P
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute("""
        CREATE TEMP TABLE tmp_team_season (person_id bigint, yr int, n_school int,
            n_college int, n_club int, n_other int, n_graded int);
        CREATE TEMP TABLE tmp_ncaa_season (person_id bigint, yr int);
        CREATE TEMP TABLE tmp_school_season (person_id bigint, yr int);
        CREATE TEMP TABLE tmp_pro_season (person_id bigint, yr int, pro_races int, round int);
        -- 1: an adult from 2003, unattached in 2013 -> professional 2013
        INSERT INTO tmp_team_season VALUES (1, 2003, 0, 0, 2, 0, 0), (1, 2013, 0, 0, 0, 0, 0);
        -- 2: a club kid from age seven, graded, at season ten -> not
        INSERT INTO tmp_team_season VALUES (2, 2010, 0, 0, 3, 0, 3), (2, 2020, 0, 0, 4, 0, 4);
        -- 3: a decade on but racing for a college that season -> not
        INSERT INTO tmp_team_season VALUES (3, 2005, 1, 0, 0, 0, 1), (3, 2016, 0, 5, 0, 0, 0);
        -- 4: nine seasons on -> not yet
        INSERT INTO tmp_team_season VALUES (4, 2005, 0, 0, 1, 0, 0), (4, 2014, 0, 0, 1, 0, 0);
        -- 5: a decade on, but an NCAA season -> not
        INSERT INTO tmp_team_season VALUES (5, 2005, 0, 0, 1, 0, 0), (5, 2016, 0, 0, 1, 0, 0);
        INSERT INTO tmp_ncaa_season VALUES (5, 2016);
    """)
    P.buildLongCareer(cur)
    cur.execute("SELECT person_id, yr FROM tmp_long_career ORDER BY 1, 2")
    assert cur.fetchall() == [(1, 2013)]
    assert P.addLongCareer(cur) == 1
    conn.rollback()
    conn.close()
