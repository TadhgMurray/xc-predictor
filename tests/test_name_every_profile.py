# Project: xc-predictor / tests
# File:    test_name_every_profile.py
# Purpose: list pages name a result from ANY profile of its person, as the
#          athlete page does -- not only from the profile whose id is the
#          person id.
#
# ⚠ OWNER, 2026-10-07: "there are unknown ppl with athlete names that are
#   not unknown". The shared lookup matched athlete_id = COALESCE(person_id,
#   athlete_id), one row; a blank one read Unknown while another profile of
#   the same person carried the name the athlete page showed.
#
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lateral():
    src = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    body = src[src.index("def _athlete_lateral("):src.index("def _name_sql(")]
    ns = {}
    exec(body, ns)
    name = re.search(r'def _name_sql\(r="r"\):\s*return (f".*?")\n', src, re.S).group(1)
    exec(f'def _name_sql(r="r"):\n    return {name}\n', ns)
    return ns["_athlete_lateral"], ns["_name_sql"]


def test_every_profile_is_a_candidate():
    lat, _ = _lateral()
    sql = lat("r")
    assert "WHERE  ap.person_id = r.person_id" in sql
    assert "ai.athlete_id IN (r.person_id, r.athlete_id)" in sql


def test_names_on_a_scratch_database():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    lat, name = _lateral()
    cx = psycopg2.connect(dsn)
    cur = cx.cursor()
    cur.execute("""
        CREATE TEMP TABLE athletes (athlete_id bigint PRIMARY KEY, first_name text,
                                    last_name text, gender text, school text, person_id bigint);
        CREATE TEMP TABLE results (result_id int, person_id bigint, athlete_id bigint,
                                   athlete_name text);
        INSERT INTO athletes VALUES
          (10, '',     '',      NULL, NULL, 10),   -- person 10's id profile: blank
          (11, 'Ana',  'Ruiz',  'F',  'X',  10),   -- ... another profile: named
          (20, NULL,   NULL,    NULL, NULL, NULL), -- unlinked, blank
          (30, 'Bo',   'Lee',   'M',  'Y',  30),
          (41, 'Cy',   'Diaz',  'M',  'Z',  NULL); -- the row's own athlete, no person link
        INSERT INTO results VALUES
          (1, 10, 10, NULL),    -- was Unknown: the id profile is blank
          (2, NULL, 20, NULL),  -- truly nameless
          (3, NULL, 20, 'Di Fox'),
          (4, 30, 30, NULL),
          (5, 40, 41, NULL);    -- person 40 has no profile; its own athlete row does
    """)
    cur.execute(f"SELECT r.result_id, {name('r')} FROM results r {lat('r')} ORDER BY 1")
    got = dict(cur.fetchall())
    cx.rollback()
    assert got == {1: "Ana Ruiz", 2: "Unknown", 3: "Di Fox", 4: "Bo Lee", 5: "Cy Diaz"}, got


def test_the_boards_copy_reads_every_profile_too():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    src = open(os.path.join(ROOT, "racecast", "rankings.py"), encoding="utf-8").read()
    lat = re.search(r'_NAME_LATERAL = """(.*?)"""', src, re.S).group(1)
    assert "a.person_id = {alias}.person_id" in lat
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    cx = psycopg2.connect(dsn)
    cur = cx.cursor()
    cur.execute("""
        CREATE TEMP TABLE athletes (athlete_id bigint PRIMARY KEY, first_name text,
                                    last_name text, gender text, school text, person_id bigint);
        CREATE TEMP TABLE results (person_id bigint, athlete_name text);
        CREATE TEMP TABLE results_tf (person_id bigint, athlete_name text);
        CREATE TEMP TABLE rr (person_id bigint);
        INSERT INTO athletes VALUES (10, '', '', NULL, NULL, 10), (11, 'Ana', 'Ruiz', 'F', 'X', 10);
        INSERT INTO rr VALUES (10);
    """)
    cur.execute("SELECT a.name FROM rr " + lat.format(alias="rr"))
    assert cur.fetchone()[0] == "Ana Ruiz"
    cx.rollback()
