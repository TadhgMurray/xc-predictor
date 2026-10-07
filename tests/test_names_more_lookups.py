# Project: xc-predictor / tests
# File:    test_names_more_lookups.py
# Purpose: the predictions roster and the panels' `ath` table name a person
#          from ANY profile, as app._athlete_lateral does.
#
# ⚠ OWNER, 2026-10-07: "still seeing unknowns that shouldn't be unknowns".
#   predict._NAME_LATERAL read one profile (athlete_id = person id); panels'
#   ath dropped every profile without an M/F gender before picking a name;
#   meet_compile's lookup did both.
#
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))

FIXTURE = """
    CREATE TEMP TABLE athletes (athlete_id bigint, first_name text, last_name text,
                                gender text, school text, person_id bigint);
    INSERT INTO athletes VALUES
      (10, '',    '',     'M',  NULL, 10),   -- the person-id profile: blank, gendered
      (11, 'Ana', 'Ruiz', NULL, 'X',  10),   -- another profile: named, no gender
      (20, 'Bo',  'Lee',  'M',  'Y',  20);
"""


def _cx():
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    cx = psycopg2.connect(dsn)
    cx.cursor().execute(FIXTURE)
    return cx


def test_the_predictions_roster_names_from_any_profile():
    import predict
    cx = _cx()
    cur = cx.cursor()
    cur.execute("CREATE TEMP TABLE s (person_id bigint); INSERT INTO s VALUES (10), (20);")
    cur.execute("SELECT s.person_id, COALESCE(a.first_name,'') || ' ' || COALESCE(a.last_name,'') "
                "FROM s " + predict._NAME_LATERAL.format(pid="s.person_id") + " ORDER BY 1")
    got = dict(cur.fetchall())
    cx.rollback()
    assert got == {10: "Ana Ruiz", 20: "Bo Lee"}


def test_the_panels_ath_keeps_the_name_and_the_gender():
    import panels
    cx = _cx()
    cur = cx.cursor()
    cur.execute(panels._ATH_TEMP_DDL)
    cur.execute("SELECT person_id, first_name, last_name, gender FROM ath ORDER BY 1")
    got = cur.fetchall()
    cx.rollback()
    assert got == [(10, "Ana", "Ruiz", "M"), (20, "Bo", "Lee", "M")]


def test_the_compiled_page_has_its_own_name_lookup():
    src = open(os.path.join(ROOT, "racecast", "meet_compile.py"), encoding="utf-8").read()
    assert ") an ON TRUE" in src and "COALESCE(an.first_name, '')" in src
    assert "WHERE  ai.athlete_id IN (r.person_id, r.athlete_id)) x" in src
