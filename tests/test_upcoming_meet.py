"""Predicting a meet that has not run yet (owner, 2026-10-06: "Make
predicting an UPCOMING meet work").

/meets?view=upcoming links each posted meet to /predictions, and that page
read everything -- races, date, field -- off the meet's RESULTS, which a meet
not yet run does not have. The races and date now come from the posted meet's
own rows, the field from its LAST EDITION (last_edition.py), and the page is
told so (`basis`). These pin the pure rules -- the name normaliser, which
earlier meet is the edition, which of its races an upcoming race is -- and
then run the routes against a scratch Postgres.

    python -m pytest tests/test_upcoming_meet.py
    XCP_TWIN_TEST_DSN=postgresql://postgres@localhost:54329/<db> \
        python -m pytest tests/test_upcoming_meet.py
"""
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", "model"):
    sys.path.insert(0, os.path.join(_ROOT, sub))

import pytest  # noqa: E402

L = pytest.importorskip("last_edition")


# ------------------------------------------------------------------ #
#  THE NAME OF A MEET, WITHOUT ITS RUNNING
# ------------------------------------------------------------------ #

def test_the_name_loses_years_ordinals_and_punctuation():
    n = L.editionName
    assert n("2026 38th Annual Nike Portland XC!") == "annual nike portland xc"
    assert n("Nike Portland XC 2025") == n("2024 Nike Portland XC")
    assert n("Twilight Invitational 2025-26") == "twilight invitational"
    assert n("St. Joe's 1st Invite") == "st joe s invite"
    # a number that is not a year or an ordinal is part of the name
    assert n("Region 3 Championships") == "region 3 championships"
    assert n("Mt. SAC Relays 5000") == "mt sac relays 5000"
    assert n(None) == "" and n("2026") == ""


# ------------------------------------------------------------------ #
#  WHICH EARLIER MEET IS THE LAST EDITION
# ------------------------------------------------------------------ #

_UP = {"meet_id": 500, "name": "2026 Lakeside Invitational",
       "date": "2026-10-10", "venue": "Lakeside Park", "state": "OR"}


def _c(mid, name, date, venue="Lakeside Park", state="OR"):
    return {"meet_id": mid, "name": name, "date": date, "venue": venue,
            "state": state}


def test_the_most_recent_earlier_running_with_results():
    got = L.pickEdition(_UP, [
        _c(300, "24th Lakeside Invitational", "2024-10-12"),
        _c(400, "2025 Lakeside Invitational", "2025-10-11"),
        _c(401, "Lakeside Invitational", None),            # no results
        _c(600, "Lakeside Invitational", "2026-10-20"),    # not earlier
        _c(500, "2026 Lakeside Invitational", "2026-10-10"),  # itself
        _c(410, "Lakeside Relays", "2025-10-30")])         # another meet
    assert got["meet_id"] == 400


def test_the_same_venue_beats_a_more_recent_namesake():
    """A generic name is a different meet in another state; its teams are
    a wrong field, not a newer one."""
    got = L.pickEdition(_UP, [
        _c(400, "Lakeside Invitational", "2025-10-11"),
        _c(450, "Lakeside Invitational 2025", "2025-11-01",
           venue="Lakeside HS", state="WA")])
    assert got["meet_id"] == 400
    # the venue moved: the same state still finds it over another state's
    got = L.pickEdition(_UP, [
        _c(450, "Lakeside Invitational", "2025-11-01",
           venue="Lakeside HS", state="WA"),
        _c(460, "Lakeside Invitational", "2025-10-11", venue="New Park")])
    assert got["meet_id"] == 460
    # and with nothing else to go on, the most recent
    got = L.pickEdition({**_UP, "venue": None, "state": None}, [
        _c(450, "Lakeside Invitational", "2025-11-01", state="WA"),
        _c(460, "Lakeside Invitational", "2024-10-11")])
    assert got["meet_id"] == 450


def test_no_edition_is_none():
    assert L.pickEdition(_UP, []) is None
    assert L.pickEdition(_UP, [_c(1, "Another Meet", "2025-10-11")]) is None
    assert L.pickEdition({**_UP, "name": "2026"},
                         [_c(1, "2025", "2025-10-11")]) is None


# ------------------------------------------------------------------ #
#  WHICH OF THE EDITION'S RACES AN UPCOMING RACE IS
# ------------------------------------------------------------------ #

def _r(div, label, dist=None, gender=None):
    return {"div_id": div, "label": label, "distance": dist, "gender": gender}


def test_a_labelled_race_maps_by_label_and_gender():
    """tfrrs: "Men's 8k" says its gender; the blob's order can change."""
    up = [_r(0, "Men's 8k", 8000, "M"), _r(1, "Women's 6k", 6000, "F")]
    ed = [_r(0, "Women's 6k", 6000, "F"), _r(1, "Men's 8k", 8000, "M")]
    assert L.mapRace(up[0], up, ed) == ([1], "race")
    assert L.mapRace(up[1], up, ed) == ([0], "race")
    # renamed: the same gender at the same distance (snapped to 100 m)
    up2 = [_r(0, "Men's 8000 Meters", 8000.4, "M")]
    assert L.mapRace(up2[0], up2, ed) == ([1], "race")


def test_anets_two_varsity_races_pair_in_order():
    """anet's divisions say "Varsity" with no gender word: the boys' and the
    girls' race match both of last year's, so they pair in div_id order."""
    up = [_r(5001, "Varsity", 5000), _r(5002, "Varsity", 5000),
          _r(5003, "JV", 5000)]
    ed = [_r(4001, "Varsity", 5000, "M"), _r(4002, "Varsity", 5000, "F"),
          _r(4003, "JV", 5000, "M")]
    assert L.mapRace(up[0], up, ed) == ([4001], "race")
    assert L.mapRace(up[1], up, ed) == ([4002], "race")
    assert L.mapRace(up[2], up, ed) == ([4003], "race")
    assert [L.raceGender(u, up, ed) for u in up] == ["M", "F", "M"]
    # three Varsity races this year against two: no pairing to trust
    up3 = up[:2] + [_r(5004, "Varsity", 5000)]
    assert L.mapRace(up3[0], up3, ed) == (None, "whole")
    assert L.raceGender(up3[0], up3, ed) is None


def test_an_unmatched_race_takes_the_whole_edition_by_gender():
    ed = [_r(1, "Varsity", 5000, "M"), _r(2, "Varsity", 5000, "F"),
          _r(3, "Open", 5000, "F")]
    girls = _r(9, "Girls Frosh", 3000, "F")
    assert L.mapRace(girls, [girls], ed) == ([2, 3], "gender")
    mystery = _r(9, "Novice", 3000)
    assert L.mapRace(mystery, [mystery], ed) == (None, "whole")
    # "All races" is the whole edition
    assert L.mapRace(None, [], ed) == (None, "whole")


# ------------------------------------------------------------------ #
#  A COMING-UP LINK'S FEED
# ------------------------------------------------------------------ #

class _Cur:
    def __init__(self, many, one=None):
        self.many, self.one, self.calls = many, one, []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.many(self.calls[-1]) if callable(self.many) else self.many

    def fetchone(self):
        return self.one(self.calls[-1]) if callable(self.one) else self.one


def test_a_posted_track_meet_is_read_from_anets_track_rows():
    """Track: meets_tf_meta is the meet, meets_tf its divisions -- weekend.py's
    tables -- and both are read under the one feed."""
    cur = _Cur(many=[{"div_id": 7, "division": "Varsity", "meet_name": "X",
                      "venue_name": None, "state": "CA"}],
               one={"meet_name": "2026 Arcadia Invitational",
                    "meet_date": "2026-04-11", "venue_name": "Arcadia HS",
                    "state": "CA"})
    up = L.upcomingMeet(cur, 9, "TF", None)
    assert up["source"] == "anet" and up["date"] == "2026-04-11"
    assert up["name"] == "2026 Arcadia Invitational"
    assert [(r["div_id"], r["label"]) for r in up["races"]] == [(7, "Varsity")]
    assert "FROM   meets_tf_meta" in cur.calls[0][0]
    assert all(p["src"] == "anet" for _s, p in cur.calls)


def test_the_candidates_are_one_feeds():
    cur = _Cur(many=[])
    L._candidates(cur, "XC", "anet", "2026 Lakeside Invitational")
    sql, params = cur.calls[0]
    assert "FROM   meets" in sql and "source = %(src)s" in sql
    assert params["src"] == "anet"
    assert params["w0"] == "%lakeside%" and params["w1"] == "%invitational%"
    cur = _Cur(many=[])
    L._candidates(cur, "XC", "tfrrs", "Big Ten Championships 2026")
    assert "FROM   meets_tfrrs" in cur.calls[0][0]
    assert cur.calls[0][1]["sp"] == "XC"


@pytest.fixture
def A():
    pytest.importorskip("flask")
    import app
    return app


def test_the_feed_pin(A):
    # no hint, or the feed already chosen: nothing changes, nothing is read
    cur = _Cur([])
    assert A._feedPin(cur, 1, "XC", None, "anet", 1) == ("anet", 1)
    assert A._feedPin(cur, 1, "XC", "anet", "anet", 1) == ("anet", 1)
    assert A._feedPin(cur, 1, "XC", "bogus", None, 0) == (None, 0)
    assert cur.calls == []
    # a tfrrs meet posted under an id whose anet meet has run
    cur = _Cur([{"source": "anet", "n": 30}])
    assert A._feedPin(cur, 1, "XC", "tfrrs", "anet", 0) == ("tfrrs", 0)
    # a tfrrs meet with results: its own ?alt= index, the meet page's rule
    cur = _Cur([{"source": "anet", "n": 30}, {"source": "tfrrs", "n": 9}])
    assert A._feedPin(cur, 1, "XC", "tfrrs", "anet", 0) == ("tfrrs", 1)


# ------------------------------------------------------------------ #
#  A REAL DATABASE: AN UPCOMING MEET, ITS EDITIONS, A COLLIDING ID
# ------------------------------------------------------------------ #

_FIXTURE = """
CREATE TABLE results (result_id bigint, athlete_id bigint, person_id bigint,
                      meet_id bigint, div_id bigint, time_seconds real,
                      grade text, date text, school text, team_id bigint,
                      athlete_name text, source text);
CREATE TABLE meets (div_id bigint, meet_id bigint, meet_name text,
                    meet_date text, course_name text, distance real,
                    gps_lat real, gps_long real, altitude_meters real,
                    state text, division text, source text);
CREATE TABLE meets_tfrrs (meet_id bigint, sport text, meet_name text,
                          date text, venue_name text, city text, state text,
                          gps_lat real, gps_long real,
                          division_distances jsonb, source text);
CREATE TABLE dist_override (meet_id bigint, div_id bigint, distance real);
CREATE TABLE athletes (athlete_id bigint, first_name text, last_name text,
                       gender text, school text);
CREATE TABLE athlete_season (person_id bigint, sport text, year int,
                             mean_rating real, pool text, n_races int,
                             school text);
CREATE TABLE course_canonical (course_name text, gps_lat real,
                               gps_long real, canonical_id int);
CREATE TABLE course_difficulties (canonical_id int, distance_m int,
                                  difficulty real, course_name text);

-- the upcoming anet meet: posted, three races, no results
INSERT INTO meets VALUES
  (5001, 500, '2026 Lakeside Invitational', '2026-10-10', 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'Varsity', 'anet'),
  (5002, 500, '2026 Lakeside Invitational', '2026-10-10', 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'Varsity', 'anet'),
  (5003, 500, '2026 Lakeside Invitational', '2026-10-10', 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'JV', 'anet'),
-- its last edition, an older one, and a namesake in another state
  (4001, 400, '2025 Lakeside Invitational', NULL, 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'Varsity', 'anet'),
  (4002, 400, '2025 Lakeside Invitational', NULL, 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'Varsity', 'anet'),
  (4003, 400, '2025 Lakeside Invitational', NULL, 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'JV', 'anet'),
  (3001, 300, '24th Lakeside Invitational', NULL, 'Lakeside Park',
   5000, NULL, NULL, NULL, 'OR', 'Varsity', 'anet'),
  (4501, 450, 'Lakeside Invitational 2025', NULL, 'Lakeside HS',
   5000, NULL, NULL, NULL, 'WA', 'Varsity', 'anet'),
-- a meet with no earlier running
  (7001, 700, 'First Ever Twilight', '2026-10-09', 'New Field',
   5000, NULL, NULL, NULL, 'OR', 'Varsity', 'anet'),
-- the anet meet that shares the upcoming tfrrs meet's number, and ran
  (8001, 800, 'Old Valley Meet', '2009-09-12', 'Valley Park',
   5000, NULL, NULL, NULL, 'NJ', 'Varsity', 'anet');

-- tfrrs: the upcoming conference meet and last year's, races reordered
INSERT INTO meets_tfrrs VALUES
  (800, 'XC', 'Big Ten Championships 2026', '2026-10-11', NULL, 'Madison',
   'WI', NULL, NULL,
   '{"0": {"div_name": "Men''s 8k", "distance": 8000},
     "1": {"div_name": "Women''s 6k", "distance": 6000}}', 'tfrrs'),
  (790, 'XC', 'Big Ten Championships 2025', '2025-10-12', NULL, 'Madison',
   'WI', NULL, NULL,
   '{"0": {"div_name": "Women''s 6k", "distance": 6000},
     "1": {"div_name": "Men''s 8k", "distance": 8000}}', 'tfrrs');

INSERT INTO athletes VALUES
  (1,'A','One','M','Lake A'), (2,'B','Two','M','Lake A'),
  (3,'C','Three','M','Lake B'), (4,'D','Four','F','Lake A'),
  (5,'E','Five','F','Lake A'), (6,'F','Six','M','Lake C'),
  (20,'G','Wrong','M','Wrong School'), (30,'H','Old','M','Old School'),
  (40,'I','Valley','M','Valley HS'),
  (51,'J','Badger','M','Wisconsin'), (52,'K','Hawk','M','Iowa'),
  (53,'L','Badger','F','Wisconsin');
INSERT INTO results VALUES
  (1,1,1,400,4001,960,'12','2025-10-11','Lake A',NULL,NULL,'anet'),
  (2,2,2,400,4001,970,'11','2025-10-11','Lake A',NULL,NULL,'anet'),
  (3,3,3,400,4001,980,'10','2025-10-11','Lake B',NULL,NULL,'anet'),
  (4,4,4,400,4002,1100,'11','2025-10-11','Lake A',NULL,NULL,'anet'),
  (5,5,5,400,4002,1110,'10','2025-10-11','Lake A',NULL,NULL,'anet'),
  (6,6,6,400,4003,1050,'9','2025-10-11','Lake C',NULL,NULL,'anet'),
  (7,30,30,300,3001,990,'12','2024-10-12','Old School',NULL,NULL,'anet'),
  (8,20,20,450,4501,990,'12','2025-11-01','Wrong School',NULL,NULL,'anet'),
  (9,40,40,800,8001,1000,'12','2009-09-12','Valley HS',NULL,NULL,'anet'),
  (10,51,51,790,1,1500,'SR','2025-10-12','Wisconsin',NULL,NULL,'tfrrs'),
  (11,52,52,790,1,1510,'JR','2025-10-12','Iowa',NULL,NULL,'tfrrs'),
  (12,53,53,790,0,1300,'SO','2025-10-12','Wisconsin',NULL,NULL,'tfrrs');
"""

_SCHEMA = "upcoming_meet"


@pytest.fixture
def pg(monkeypatch):
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    psycopg2 = pytest.importorskip("psycopg2")
    P = pytest.importorskip("predict")
    admin = psycopg2.connect(dsn)
    with admin.cursor() as c:
        c.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {_SCHEMA}")
        c.execute(f"SET search_path = {_SCHEMA}")
        c.execute(_FIXTURE)
    admin.commit()
    conn = psycopg2.connect(dsn, options=f"-c search_path={_SCHEMA}")
    # the parts of the field that are not about WHICH runners stay out of
    # the way: this season's squad of a school is its runners in the fixture
    monkeypatch.setattr(P, "_stampCrests", lambda rows, *a, **k: rows)
    monkeypatch.setattr(P, "_currentSeason", lambda cur, sport: 2026)
    monkeypatch.setattr(P, "_teamStates", lambda cur, rows, st=None: {})
    asked = []

    def squads(cur, schools, sport, year, gender=None, **k):
        asked.append((sorted(schools), gender))
        cur.execute("SELECT DISTINCT person_id, school FROM results "
                    "WHERE school = ANY(%s)", (list(schools),))
        out = {}
        for r in cur.fetchall():
            out.setdefault(r["school"], []).append(
                {"person_id": r["person_id"], "school": r["school"],
                 "name": "x"})
        return out
    monkeypatch.setattr(P, "_currentSquads", squads)
    yield conn, asked
    conn.rollback()
    conn.close()
    with admin.cursor() as c:
        c.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
    admin.commit()
    admin.close()


def _client(A, monkeypatch, conn):
    class _Conn:
        def cursor(self, **k):
            return conn.cursor(**k)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            conn.rollback()
            return False
    monkeypatch.setattr(A, "getConn", lambda: _Conn())
    return A.app.test_client()


def _schools(body):
    return sorted(t["school"] for t in body["teams"])


def test_an_upcoming_meet_on_a_real_database(A, pg, monkeypatch):
    conn, asked = pg
    c = _client(A, monkeypatch, conn)

    # ---- the posted races and date, the genders last year's runners give
    races = c.get("/api/predict/races?meet_id=500&sport=XC").get_json()
    assert races["upcoming"] is True and races["date"] == "2026-10-10"
    assert races["meet_name"] == "2026 Lakeside Invitational"
    assert races["course"] == "Lakeside Park"
    assert [(r["div_id"], r["label"], r["gender"], r["n_results"])
            for r in races["races"]] == [
        (5001, "Varsity", "M", 0), (5002, "Varsity", "F", 0),
        (5003, "JV", "M", 0)]

    # ---- the field: last edition's teams, race by race, and it says so
    f = c.get("/api/predict/field?meet_id=500&sport=XC&div_id=5001").get_json()
    assert _schools(f) == ["Lake A", "Lake B"]
    assert f["basis"] == {"kind": "last_edition", "meet_id": 400,
                          "date": "2025-10-11",
                          "meet_name": "2025 Lakeside Invitational",
                          "matched": "race"}
    assert {t["school"]: t["entered"] for t in f["teams"]} == {
        "Lake A": 2, "Lake B": 1}
    girls = c.get("/api/predict/field?meet_id=500&sport=XC&div_id=5002"
                  ).get_json()
    assert _schools(girls) == ["Lake A"]
    assert girls["basis"]["meet_id"] == 400
    jv = c.get("/api/predict/field?meet_id=500&sport=XC&div_id=5003"
               ).get_json()
    assert _schools(jv) == ["Lake C"]
    whole = c.get("/api/predict/field?meet_id=500&sport=XC").get_json()
    assert _schools(whole) == ["Lake A", "Lake B", "Lake C"]
    assert "Wrong School" not in _schools(whole)

    # ---- the prediction's own roster agrees with the page's field
    import predict as P
    cur = conn.cursor(cursor_factory=__import__("psycopg2.extras").extras
                      .RealDictCursor)
    t, err = A._target({"mode": "rerun", "meet_id": "500", "div_id": "5001",
                        "sport": "XC"})
    assert not err
    entries = P._teamRosters(cur, [], A._withSource(cur, t))
    assert sorted({e["school"] for e in entries}) == ["Lake A", "Lake B"]
    spec = P._targetSpec(cur, t)
    assert spec["date"] == "2026-10-10" and spec["distance_meters"] == 5000

    # ---- nothing earlier to borrow from: an empty field that says so
    none = c.get("/api/predict/field?meet_id=700&sport=XC").get_json()
    assert none["teams"] == [] and none["basis"] == {"kind": "none"}

    # ---- a meet that ran is untouched: its own runners, no basis
    ran = c.get("/api/predict/field?meet_id=400&sport=XC&div_id=4001"
                ).get_json()
    assert _schools(ran) == ["Lake A", "Lake B"] and ran["basis"] is None
    # and an empty division of it is an empty race, not last year's teams
    empty = c.get("/api/predict/field?meet_id=400&sport=XC&div_id=4999"
                  ).get_json()
    assert empty["teams"] == [] and empty["basis"] is None


def test_a_tfrrs_meet_posted_under_an_id_anet_has_used(A, pg, monkeypatch):
    """The Coming-up link carries &src=tfrrs; without it the colliding anet
    meet -- the one with results -- is what the id resolves to."""
    conn, _asked = pg
    c = _client(A, monkeypatch, conn)
    old = c.get("/api/predict/races?meet_id=800&sport=XC").get_json()
    assert old["date"] == "2009-09-12" and not old["upcoming"]

    r = c.get("/api/predict/races?meet_id=800&sport=XC&src=tfrrs").get_json()
    assert r["upcoming"] is True and r["date"] == "2026-10-11"
    assert r["meet_name"] == "Big Ten Championships 2026"
    assert [(x["div_id"], x["gender"]) for x in r["races"]] == [
        (0, "M"), (1, "F")]

    men = c.get("/api/predict/field?meet_id=800&sport=XC&div_id=0&src=tfrrs"
                ).get_json()
    assert _schools(men) == ["Iowa", "Wisconsin"]
    assert {p["person_id"] for t in men["teams"] for p in t["runners"]} \
        >= {51, 52}
    assert men["basis"]["meet_id"] == 790
    assert men["basis"]["date"] == "2025-10-12"
    assert "Valley HS" not in _schools(men)

    # the prediction resolves the same feed from the same link
    t, _ = A._target({"mode": "rerun", "meet_id": "800", "div_id": "0",
                      "sport": "XC", "src": "tfrrs"})
    cur = conn.cursor(cursor_factory=__import__("psycopg2.extras").extras
                      .RealDictCursor)
    A._withSource(cur, t)
    assert t["source"] == "tfrrs"
