"""--stage prepare's shared stream tables (2026-11-19): the same rows as the
session temps each stream used to build, ANALYZEd, autovacuum off, read by a
stream only while they carry the current shadow's oid, and the stream query
then names them and no temp.

    XCP_TWIN_TEST_DSN="host=... dbname=..." pytest tests/test_rank_shared_prep.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("scripts", "engine", "racecast"):
    sys.path.insert(0, os.path.join(ROOT, d))

_FIXTURE = """
    DROP SCHEMA IF EXISTS rr_prep_test CASCADE;
    CREATE SCHEMA rr_prep_test;
    SET search_path = rr_prep_test, public;
    CREATE TABLE athletes (athlete_id bigint, gender text, school text);
    INSERT INTO athletes VALUES (1,'M','a'),(1,'F','b'),(2,'F','c'),(2,'F','d'),
                                (2,'M','e'),(3,'X','f');
    CREATE TABLE meets_tfrrs (meet_id bigint, source text, sport text,
                              division_distances jsonb);
    INSERT INTO meets_tfrrs VALUES
        (7,'tfrrs','XC','{"1": {"distance": 8000}, "2": {"distance": 6000}, "x": {}}'),
        (8,'tfrrs','TF','{"1": {"distance": 1500}}');
    CREATE TABLE meets_tf (meet_id bigint, div_id bigint, source text, state text);
    INSERT INTO meets_tf VALUES (1,1,'anet','CA'),(1,1,'anet','CA'),(1,2,'anet',NULL),
                                (2,1,'tfrrs','OR');
    CREATE TABLE results_tf (event_short text);
    INSERT INTO results_tf VALUES ('100m'),('1600m'),('110mH'),('400m'),(NULL);
    CREATE TABLE ranking_results_new (x int);
"""

_TABLES = {"tmp_person_gender": "person_id", "tmp_xc_tfrrs_dist": "meet_id, div_id",
           "tmp_tf_state": "meet_id, div_id", "tmp_sprint_events": "event_short"}


def test_shared_prep_matches_the_temps_and_is_stamped():
    import pytest
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import build_ranking_results as B
    conn = psycopg2.connect(dsn)
    os.environ["XCP_RANK_PREP_JOBS"] = "1"
    try:
        with conn.cursor() as cur:
            cur.execute(_FIXTURE)
        conn.commit()
        B.buildSharedPrep(conn)
        B.prepareGenderTemp(conn)
        B.prepareXcTfrrsDistTemp(conn)
        B.prepareTfStateTemp(conn)
        B.prepareSprintEvents(conn)
        cur = conn.cursor()
        for temp, order in _TABLES.items():
            shared = B._SHARED_PREP[temp]
            cur.execute(f"SELECT * FROM {temp} ORDER BY {order}")
            want = cur.fetchall()
            cur.execute(f"SELECT * FROM {shared} ORDER BY {order}")
            assert cur.fetchall() == want and want, temp
            cur.execute("SELECT c.relpersistence, c.reloptions, s.last_analyze IS NOT NULL "
                        "FROM pg_class c JOIN pg_stat_user_tables s ON s.relid = c.oid "
                        "WHERE c.oid = %s::regclass", (shared,))
            persistence, opts, analyzed = cur.fetchone()
            assert persistence == "p" and analyzed, shared
            assert "autovacuum_enabled=false" in opts, opts

        got = B.sharedPrepFor(conn, ("TF",))
        assert got == {t: B._SHARED_PREP[t] for t in B._SHARED_FOR["TF"]}
        sql = B._sourceSql(conn, "TF", tables=got)
        assert not re.search(r"JOIN\s+tmp_", sql) and "rank_prep_tf_state m" in sql
        assert B._sourceSql(conn, "TF") == B._sourceSql(conn, "TF", tables={})

        # a new shadow (the next prepare's) is not these tables' shadow
        cur.execute("DROP TABLE ranking_results_new; CREATE TABLE ranking_results_new (x int)")
        conn.commit()
        assert B.sharedPrepFor(conn, ("XC",)) == {}
        os.environ["XCP_RANK_SHARED_PREP"] = "0"
        B.buildSharedPrep(conn)                 # the knob drops them
        cur.execute("SELECT to_regclass('rank_prep_person_gender')")
        assert cur.fetchone()[0] is None
    finally:
        os.environ.pop("XCP_RANK_SHARED_PREP", None)
        os.environ.pop("XCP_RANK_PREP_JOBS", None)
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS rr_prep_test CASCADE")
        conn.commit()
        conn.close()
