"""Dated snapshots and their downloads (owner, 2026-11-06: "build the
snapshots"; an All-County selection asked how to archive ratings and course
difficulties, and how much historical ratings change)."""
import os
import sys
from contextlib import contextmanager

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "scripts", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, p))
DSN = os.environ.get("XCP_TWIN_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="no scratch database (XCP_TWIN_TEST_DSN)")
def test_a_snapshot_is_a_frozen_copy_and_downloads(monkeypatch):
    import psycopg2
    import data_snapshots as D
    import pool_view
    monkeypatch.setattr(pool_view, "toHs", lambda r, pool, sport=None: float(r) + 1.0)
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    try:
        for t in ("data_snapshot", "data_snapshot_athlete", "data_snapshot_course", "school_unit",
                  "athlete_season", "athletes", "results", "results_tf", "course_difficulties",
                  "person_optout"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        cur.execute("CREATE TABLE athlete_season (person_id bigint, sport text, pool text, year int, "
                    "mean_rating real, best_rating real, n_races int, state text, school text, grade text)")
        cur.execute("CREATE TABLE athletes (athlete_id bigint, person_id bigint, first_name text, "
                    "last_name text, gender text)")
        cur.execute("CREATE TABLE results (person_id bigint, athlete_name text)")
        cur.execute("CREATE TABLE results_tf (person_id bigint, athlete_name text)")
        cur.execute("CREATE TABLE course_difficulties (course_name text, distance_m real, "
                    "difficulty real, n_results int)")
        cur.execute("CREATE TABLE school_unit (school text, state text, sport text, league text, "
                    "conference text, is_college boolean)")
        cur.execute("INSERT INTO school_unit VALUES ('Horlick','WI','XC','Southeast',NULL,false)")
        cur.execute("INSERT INTO athlete_season VALUES (1,'XC','hs_m',2026,140,145,6,'WI','Horlick','12'),"
                    "(2,'XC','hs_f',2026,120,125,5,'IL','X','11'),(3,'XC','hs_m',2026,150,150,1,'WI','Y','10')")
        cur.execute("INSERT INTO athletes VALUES (1,1,'Josh','Runner','M'),(2,2,'Ann','Lee','F')")
        cur.execute("INSERT INTO course_difficulties VALUES ('XC:Lake Park',5000,0.051,900)")
        c.commit()
        snap, n_a, n_c = D.take(cur, "2026 end of season", "XC", 2026)
        c.commit()
        assert (n_a, n_c) == (2, 1)                      # person 3 has no name: left out
        cur.execute("UPDATE athlete_season SET mean_rating = 99")   # the live table moves on
        c.commit()

        @contextmanager
        def getConn():
            k = psycopg2.connect(DSN)
            try:
                yield k
            finally:
                k.close()
        import database
        monkeypatch.setattr(database, "getConn", getConn)
        from flask import Flask
        app = Flask("t", template_folder=os.path.join(_ROOT, "racecast", "templates"))
        app.register_blueprint(D.bp)
        client = app.test_client()
        body = client.get(f"/data/{snap}/athletes.csv?state=WI&level=hs_m").get_data(as_text=True)
        lines = body.strip().splitlines()
        assert lines[0].startswith("level,name,school,state,grade,league,conference,rating_hs_equivalent")
        assert len(lines) == 2 and "Josh Runner" in lines[1] and ",141.0," in lines[1]
        courses = client.get(f"/data/{snap}/courses.csv").get_data(as_text=True)
        assert "Lake Park,5000.0,5.1,900" in courses
        # an athlete who opted out is left out of every download, old snapshots too
        import person_optout
        person_optout.setHidden({1})
        try:
            assert "Josh Runner" not in client.get(
                f"/data/{snap}/athletes.csv?state=WI").get_data(as_text=True)
        finally:
            person_optout.setHidden(set())
        # schools and a league (owner, 2026-11-06)
        by_school = client.get(f"/data/{snap}/athletes.csv?state=WI&school=horlick,Case").get_data(as_text=True)
        assert "Josh Runner" in by_school and ",Southeast," in by_school
        assert len(client.get(f"/data/{snap}/athletes.csv?state=WI&school=Case").get_data(
            as_text=True).strip().splitlines()) == 1
        by_league = client.get(f"/data/{snap}/athletes.csv?state=WI&league=southeast").get_data(as_text=True)
        assert "Josh Runner" in by_league
        assert client.get("/data/999/athletes.csv?state=WI").status_code == 404
        # one state at a time (owner, 2026-11-06): no whole-season download
        assert client.get(f"/data/{snap}/athletes.csv").status_code == 400
        assert client.get(f"/data/{snap}/athletes.csv?level=hs_m").status_code == 400
        # the weekly ones are pruned, a hand-labelled one stays
        for i in range(3):
            D.take(cur, f"{D.AUTO_PREFIX}Nov {i}", "XC", 2026)
        c.commit()
        assert D.prune(cur, 1) == 2
        c.commit()
        cur.execute("SELECT label FROM data_snapshot ORDER BY snap_id")
        assert [r[0] for r in cur.fetchall()] == ["2026 end of season", f"{D.AUTO_PREFIX}Nov 2"]
        # and never copied into a new one
        cur.execute("CREATE TABLE person_optout (person_id bigint PRIMARY KEY)")
        cur.execute("INSERT INTO person_optout VALUES (1)")
        c.commit()
        assert D.take(cur, "after an opt-out", "XC", 2026)[1] == 1
        c.commit()
    finally:
        c.rollback()
        for t in ("data_snapshot", "data_snapshot_athlete", "data_snapshot_course", "school_unit",
                  "athlete_season", "athletes", "results", "results_tf", "course_difficulties",
                  "person_optout"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        c.commit()
        c.close()


def test_the_data_page_renders(monkeypatch):
    import contextlib
    import datetime
    import database
    import app as A

    class Cur:
        def __init__(self, has):
            self.has = has

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, args=None):
            self.sql = sql

        def fetchone(self):
            return ("data_snapshot",) if self.has else (None,)

        def fetchall(self):
            return [(3, "2026 cross country, end of season", "XC", 2026,
                     datetime.datetime(2026, 11, 22), 412345, 9876)]

    for has, want in ((False, "No snapshot has been taken yet"),
                      (True, "/data/3/courses.csv")):
        class Conn:
            def cursor(self, *a, **k):
                return Cur(has)

            def rollback(self):
                pass

        @contextlib.contextmanager
        def conn():
            yield Conn()
        monkeypatch.setattr(database, "getConn", conn)
        r = A.app.test_client().get("/data")
        assert r.status_code == 200 and want in r.get_data(as_text=True)
