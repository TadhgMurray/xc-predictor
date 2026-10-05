"""The head-to-head page's record (/compare), audited against the live site
2026-10-05: track meeting links 404ed (two path parts for a three-part
route), a prelim and a final crossed into four "meetings", anet and tfrrs
rows met on a colliding meet_id with no source in the key, tfrrs XC meets
were named "Meet 25571", a merged-away id gave a blank page, and the card's
rating was a two-race season the athlete's own page does not quote.

    python -m pytest -q tests/test_compare_meetings.py

The pure rules run anywhere. The SQL and the route run on a scratch
Postgres when one is offered (a database of your own; it is rebuilt):

    XCP_H2H_TEST_DSN="host=/tmp/pgtest port=54329 user=postgres dbname=<yours>"
"""
import contextlib
import datetime
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                  # noqa: E402

import compare as C                                            # noqa: E402

D = datetime.date


# ---- pure rules ---------------------------------------------------------- #

def _season(sport, year, n, last, rating):
    return {"sport": sport, "year": year, "n_races": n, "last_race": last,
            "mean_rating": rating, "pool": "hs_m"}


def test_the_card_rates_the_athlete_pages_header_season():
    # Trey Caldwell, 2026-10-05: two college races in 2026 TF, eleven high
    # school races in 2025 TF -- his page heads with the eleven
    deep = _season("TF", 2024, 11, D(2025, 5, 31), 135.5)
    thin = _season("TF", 2025, 2, D(2026, 2, 13), 111.6)
    assert C.headerSeason([thin, deep]) is deep
    # with no season three deep, the latest stands
    thin_old = _season("XC", 2024, 1, D(2024, 11, 1), 120.0)
    assert C.headerSeason([thin_old, thin]) is thin
    assert C.headerSeason([]) is None


def test_track_links_have_three_parts_and_pin_the_feed():
    tf = {"meet_id": 471051, "event_id": 52, "div_id": 2, "rid_a": 174342819}
    assert C.raceHref("TF", tf) == "/race/tf/471051/52/2?r=174342819"
    xc = {"meet_id": 25571, "div_id": 5, "rid_a": -301}
    assert C.raceHref("XC", xc) == "/race/xc/25571/5?r=-301"
    # a tfrrs track row with no event or division lives on its meet page
    bare = {"meet_id": 9, "event_id": None, "div_id": None, "rid_a": 7}
    assert C.raceHref("TF", bare) == "/meet/tf/9?r=7"


def test_the_event_carries_the_round():
    row = {"event_short": "1600m", "round": "Prelims"}
    assert C.eventLabel("TF", row) == "1600m Prelims"
    assert C.eventLabel("XC", {"distance": 4828.0}) == "4828m"
    assert C.eventLabel("XC", {"distance": None}) is None


def test_the_join_keys_on_source_and_round():
    for sport in ("XC", "TF"):
        sql = C.meetingsSql(sport)
        assert "b.source    = a.source" in sql
        assert "result_twin x" in sql and "b.result_id" in sql
        assert "ranking_results" not in sql
        assert "%" not in sql.replace("%(a)s", "").replace("%(b)s", "")
    tf = C.meetingsSql("TF")
    assert "IS NOT DISTINCT FROM NULLIF(btrim(a.round)" in tf
    assert "COALESCE(rb.event_id, 0)" not in tf
    assert "m.source = p.source" in tf, "a track meet is named by its own feed"
    assert "result_twin" not in C.meetingsSql("XC", twins=False)


def test_the_template_draws_the_routes_link():
    src = open(os.path.join(_ROOT, "racecast", "templates",
                            "compare.html")).read()
    assert 'href="{{ m.href }}"' in src
    assert "/race/{{ 'xc' if m.sport" not in src


# ---- on a scratch Postgres ---------------------------------------------- #

SCHEMA = """
DROP SCHEMA public CASCADE; CREATE SCHEMA public;
CREATE TABLE results (result_id bigint, person_id bigint, meet_id bigint,
    div_id bigint, source text, canon_meet_id bigint, date text,
    time_seconds real);
CREATE TABLE results_tf (result_id bigint, person_id bigint, meet_id bigint,
    div_id bigint, event_id bigint, event_short text, round text,
    source text, canon_meet_id bigint, date text, time_seconds real,
    is_field int, is_relay int);
CREATE TABLE meets (meet_id bigint, div_id bigint, source text,
    meet_name text, course_name text, distance real);
CREATE TABLE meets_tf (meet_id bigint, div_id bigint, event_id bigint,
    source text, meet_name text);
CREATE TABLE meets_tfrrs (meet_id bigint, sport text, meet_name text,
    venue_name text, division_distances jsonb);
CREATE TABLE dist_override (meet_id bigint, div_id bigint, distance real);
CREATE TABLE result_twin (sport text, result_id bigint, reason text);
CREATE TABLE athletes (person_id bigint, first_name text, last_name text);
CREATE TABLE athlete_season (person_id bigint, sport text, year int,
    pool text, mean_rating real, best_rating real, n_races int,
    school text, grade text, state text, last_race date);
CREATE TABLE ranking_results (sport text, result_id bigint, person_id bigint,
    pool text, speed_rating real, race_date date, year int, school text,
    grade text, meet_id bigint, div_id bigint, time_seconds real,
    distance real, event_id bigint);
CREATE TABLE person_redirect (old_id bigint PRIMARY KEY, new_id bigint);

INSERT INTO athletes VALUES (1, 'Ann', 'Alpha'), (2, 'Bea', 'Bravo');
INSERT INTO person_redirect VALUES (13, 1);
INSERT INTO athlete_season VALUES
  (1, 'XC', 2025, 'hs_f', 120.0, 125.0, 6, 'Alpha High', '12', 'CA', '2025-11-01'),
  (2, 'XC', 2025, 'hs_f', 118.0, 121.0, 6, 'Bravo High', '12', 'CA', '2025-11-01');

-- XC: one real anet meeting, named by its own feed
INSERT INTO results VALUES (1, 1, 100, 7, 'anet', NULL, '2025-10-01', 1000),
                           (2, 2, 100, 7, 'anet', NULL, '2025-10-01', 1010);
INSERT INTO meets VALUES (100, 7, 'anet', 'Anet Invite', 'Anet Park', 5000),
                         (300, 2, 'anet', 'Colliding Anet Meet', 'X', 3000);
-- XC: the SAME ids in two feeds are two different races
INSERT INTO results VALUES (3, 1, 200, 5, 'anet',  NULL, '2025-09-20', 990),
                           (4, 2, 200, 5, 'tfrrs', NULL, '2025-09-20', 995);
-- XC: a tfrrs meet, named from meets_tfrrs (the anet row above collides)
INSERT INTO results VALUES (5, 1, 300, 2, 'tfrrs', NULL, '2025-09-26', 1500),
                           (6, 2, 300, 2, 'tfrrs', NULL, '2025-09-26', 1470);
INSERT INTO meets_tfrrs VALUES (300, 'XC', 'Cowboy Jamboree', 'Greiner Course',
                                '{"2": {"distance": 8000}}');
-- XC: one race in both feeds, tied by canon_meet_id: ONE meeting
INSERT INTO results VALUES (7, 1, 600, 1, 'anet',  600, '2025-08-30', 1100),
                           (8, 2, 600, 1, 'anet',  600, '2025-08-30', 1105),
                           (9, 1, 9600, 1, 'tfrrs', 600, '2025-08-30', 1100),
                           (10, 2, 9600, 1, 'tfrrs', 600, '2025-08-30', 1105);
INSERT INTO meets VALUES (600, 1, 'anet', 'Twin Classic', 'Twin Park', 5000);
-- XC: a flagged twin is not a race
INSERT INTO results VALUES (11, 1, 700, 1, 'anet', NULL, '2025-08-23', 1000),
                           (12, 2, 700, 1, 'anet', NULL, '2025-08-23', 1001);
INSERT INTO result_twin VALUES ('XC', 12, 'test');

-- TF: a prelim and a final in one event are TWO meetings, not four
INSERT INTO results_tf VALUES
  (21, 1, 400, 1, 52, '1600m', 'Prelims', 'anet', NULL, '2025-05-07', 290.4, 0, 0),
  (22, 1, 400, 1, 52, '1600m', 'Final',   'anet', NULL, '2025-05-07', 292.6, 0, 0),
  (23, 2, 400, 1, 52, '1600m', 'Prelims', 'anet', NULL, '2025-05-07', 288.1, 0, 0),
  (24, 2, 400, 1, 52, '1600m', 'Final',   'anet', NULL, '2025-05-07', 275.3, 0, 0);
INSERT INTO meets_tf VALUES (400, 1, 52, 'anet', 'League Finals');
-- TF tfrrs, no event ids: the 1500 and the 5000 are not one race; the 800 is
INSERT INTO results_tf VALUES
  (31, 1, 500, NULL, NULL, '1500m', NULL, 'tfrrs', NULL, '2025-04-12', 250, 0, 0),
  (32, 2, 500, NULL, NULL, '5000m', NULL, 'tfrrs', NULL, '2025-04-12', 960, 0, 0),
  (33, 1, 500, NULL, NULL, '800m',  NULL, 'tfrrs', NULL, '2025-04-12', 130, 0, 0),
  (34, 2, 500, NULL, NULL, '800m',  NULL, 'tfrrs', NULL, '2025-04-12', 129, 0, 0);
-- the anet meet sharing tfrrs 500's id must not name it
INSERT INTO meets_tf VALUES (500, 1, 1, 'anet', 'Wrong Anet Meet');
INSERT INTO meets_tfrrs VALUES (500, 'TF', 'Spring Open', NULL, NULL);
-- TF: a relay leg shared by teammates is not a head-to-head
INSERT INTO results_tf VALUES
  (41, 1, 401, 1, 9, '4x400', 'Final', 'anet', NULL, '2025-05-01', 230, 0, 1),
  (42, 2, 401, 1, 9, '4x400', 'Final', 'anet', NULL, '2025-05-01', 230, 0, 1);
"""


@pytest.fixture(scope="module")
def pg():
    dsn = os.environ.get("XCP_H2H_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_H2H_TEST_DSN to a scratch Postgres to run the SQL")
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()
    yield conn
    conn.close()


def _cur(conn):
    import psycopg2.extras
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def test_the_meetings_on_postgres(pg):
    with _cur(pg) as cur:
        rows = C.meetings(cur, 1, 2)
    got = {(m["meet_name"], m["event"]): m for m in rows}
    assert set(got) == {
        ("Anet Invite", "5000m"),
        ("Cowboy Jamboree", "8000m"),
        ("Twin Classic", "5000m"),
        ("League Finals", "1600m Prelims"),
        ("League Finals", "1600m Final"),
        ("Spring Open", "800m"),
    }, sorted(got)
    assert len(rows) == 6, "the twin pair is one meeting, the colliding ids none"
    fin = got[("League Finals", "1600m Final")]
    assert fin["winner"] == "b" and fin["time_a"] == "4:52.6"
    assert fin["href"] == "/race/tf/400/52/1?r=22"
    assert got[("Spring Open", "800m")]["href"] == "/meet/tf/500?r=33"
    cj = got[("Cowboy Jamboree", "8000m")]
    assert cj["course"] == "Greiner Course" and cj["winner"] == "b"
    assert cj["href"] == "/race/xc/300/2?r=5"
    assert got[("Twin Classic", "5000m")]["href"].startswith("/race/xc/600/1?")
    wa, wb, ties, _avg = C.record(rows)
    assert (wa, wb, ties) == (2, 4, 0)


def test_without_result_twin_nothing_is_hidden(pg):
    with pg.cursor() as cur:
        cur.execute("ALTER TABLE result_twin RENAME TO result_twin_off")
    try:
        with _cur(pg) as cur:
            rows = C.meetings(cur, 1, 2)
        assert len(rows) == 7
    finally:
        with pg.cursor() as cur:
            cur.execute("ALTER TABLE result_twin_off RENAME TO result_twin")
        pg.commit()


@pytest.fixture
def client(pg, monkeypatch):
    import psycopg2
    import app as A
    dsn = os.environ["XCP_H2H_TEST_DSN"]

    @contextlib.contextmanager
    def conn():
        c = psycopg2.connect(dsn)
        try:
            yield c
        finally:
            c.close()

    monkeypatch.setattr(A, "getConn", conn)
    return A.app.test_client()


def test_the_page_renders_the_record(client):
    r = client.get("/compare?a=1&b=2")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'href="/race/tf/400/52/1?r=22"' in body
    assert "Cowboy Jamboree" in body and "Meet 300" not in body
    assert "Wrong Anet Meet" not in body and "Colliding Anet Meet" not in body
    assert "6 meetings" in body


def test_a_merged_id_follows_the_athlete(client):
    r = client.get("/compare?a=13&b=2")
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/compare?a=1&b=2")


def test_an_unknown_id_keeps_the_other_side(client):
    r = client.get("/compare?a=999&b=2")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "no athlete with id 999" in body
    assert 'data-pid="2"' in body and 'value="Bea Bravo"' in body
