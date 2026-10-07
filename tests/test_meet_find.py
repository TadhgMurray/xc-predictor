# Project: xc-predictor / tests
# File:    test_meet_find.py
# Purpose: the meet page's finder searches every finisher at the meet
#          (app.meet_xc_find), not only the races table.
#
# ⚠ OWNER, 2026-10-07: "the search bar on the meet page is kind of
#   disfunctional". It filtered the races table, so a runner was found only
#   if they had won a race.
#
#   XCP_TWIN_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=postgres"
import contextlib
import os
import sys

import pytest

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))


def test_the_meet_template_asks_the_server():
    src = open(os.path.join(ROOT, "racecast", "templates", "meet.html"), encoding="utf-8").read()
    assert 'data-api="/api/meet/xc/{{ header.meet_id }}/find' in src
    assert '<ul class="rc-findlist" hidden></ul>' in src


def test_finds_any_finisher_by_name_or_school(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import psycopg2.extras
    import app as A
    cx = psycopg2.connect(dsn)
    cur = cx.cursor()
    cur.execute("""
        CREATE TEMP TABLE athletes (athlete_id bigint PRIMARY KEY, first_name text, last_name text,
                                    gender text, school text, person_id bigint);
        CREATE TEMP TABLE results (result_id bigint, meet_id bigint, div_id bigint, school text,
                                   time_seconds real, person_id bigint, athlete_id bigint,
                                   athlete_name text, source text);
        INSERT INTO athletes VALUES (1, 'Evan', 'Noonan', 'M', 'Dana Hills (CA)', 1),
                                    (2, 'Clark', 'Gregory', 'M', 'Campolindo (CA)', 2),
                                    (3, 'Ava', 'Park', 'F', 'Campolindo (CA)', 3);
        INSERT INTO results VALUES (10, 7, 100, 'Dana Hills (CA)', 883.7, 1, 1, NULL, 'anet'),
                                   (11, 7, 100, 'Campolindo (CA)', 925.7, 2, 2, NULL, 'anet'),
                                   (12, 7, 200, 'Campolindo (CA)', 1050.2, 3, 3, NULL, 'anet'),
                                   (13, 8, 100, 'Campolindo (CA)', 900.0, 2, 2, NULL, 'anet');
    """)

    @contextlib.contextmanager
    def _conn():
        yield cx
    monkeypatch.setattr(A, "getConn", lambda *a, **k: _conn())
    monkeypatch.setattr(A, "_inMaintenance", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(A, "_xc_meet_sources", lambda cur, m, args, div_id=None: (None, 0, []))
    monkeypatch.setattr(A, "get_meet_divisions", lambda cur, m, source=None: [
        {"div_id": 100, "gender": "M", "division": "Division 3"},
        {"div_id": 200, "gender": "F", "division": "Division 3"}])
    c = A.app.test_client()
    got = c.get("/api/meet/xc/7/find?q=gregory").get_json()
    assert [g["name"] for g in got] == ["Clark Gregory"]           # not a winner, still found
    assert got[0]["href"] == "/race/xc/7/100#r11"
    assert got[0]["race"] == "Boys Division 3" and got[0]["time"]
    team = c.get("/api/meet/xc/7/find?q=campolindo").get_json()
    assert sorted(g["name"] for g in team) == ["Ava Park", "Clark Gregory"]   # meet 8 left out
    assert c.get("/api/meet/xc/7/find?q=a").get_json() == []           # too short to search
    cx.rollback()


def test_track_meet_finds_athletes(monkeypatch):
    """The track meet page's box searches athletes too (owner, 2026-10-07:
    "so you can search your result")."""
    src = open(os.path.join(ROOT, "racecast", "templates", "meet_tf.html"), encoding="utf-8").read()
    assert 'data-api="/api/meet/tf/{{ header.meet_id }}/find' in src
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    import psycopg2
    import app as A
    cx = psycopg2.connect(dsn)
    cur = cx.cursor()
    cur.execute("""
        CREATE TEMP TABLE athletes (athlete_id bigint PRIMARY KEY, first_name text, last_name text,
                                    gender text, school text, person_id bigint);
        CREATE TEMP TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint, division text,
                                    source text);
        CREATE TEMP TABLE results_tf (result_id bigint, meet_id bigint, div_id bigint, event_id bigint,
                                      school text, time_seconds real, mark text, event_short text,
                                      is_field int, person_id bigint, athlete_id bigint,
                                      athlete_name text, source text);
        INSERT INTO athletes VALUES (1, 'Evan', 'Noonan', 'M', 'Dana Hills', 1),
                                    (3, 'Ava', 'Park', 'F', 'Campolindo', 3);
        INSERT INTO meets_tf VALUES (7, 1, 5, 'Girls Varsity', 'anet');
        INSERT INTO results_tf VALUES
            (10, 7, 2, 6, 'Dana Hills', 250.5, NULL, 'Boys 1600', 0, 1, 1, NULL, 'anet'),
            (11, 7, 1, 5, 'Campolindo', NULL, '5-02', 'High Jump', 1, 3, 3, NULL, 'anet'),
            (12, 7, 1, NULL, 'Campolindo', 70.0, NULL, '400m', 0, 3, 3, NULL, 'anet');
    """)

    @contextlib.contextmanager
    def _conn():
        yield cx
    monkeypatch.setattr(A, "getConn", lambda *a, **k: _conn())
    monkeypatch.setattr(A, "_inMaintenance", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(A, "_tf_meet_sources", lambda cur, m, args: (None, 0, []))
    with A.app.test_client() as c:
        got = c.get("/api/meet/tf/7/find?q=noonan").get_json()
        assert got == [{"name": "Evan Noonan", "school": "Dana Hills", "race": "Boys 1600",
                        "time": "4:10.5", "href": "/race/tf/7/6/2?r=10"}]
        got = c.get("/api/meet/tf/7/find?q=campolindo").get_json()
        # the id-less row has no race page to link, so it is not listed
        assert [(g["race"], g["time"]) for g in got] == [("Girls High Jump", "5-02")]
    cx.close()
