"""One meet per prediction, picked the way the meet page picks it (owner,
2026-10-05: "NCAA Division III Cross Country Championships 2025" read "ran
2009-10-13 · Cross Country · Warinanco Park", and its race list mixed the D3
races with "Varsity · Boys · 5000m (22)" and "Varsity · Girls · 5000m (8)" --
a 2009 New Jersey high-school meet that shares its meet_id).

The anet and tfrrs id spaces collide. The meet page resolves ?alt=N to one
source (app.meet_sources / pick_source); the predictions page read every meet
by meet_id alone. These pin the source filter on every lookup the page's
requests reach, then run the whole thing against a scratch Postgres where one
meet_id holds both meets.

    python -m pytest tests/test_predict_meet_source.py
    XCP_TWIN_TEST_DSN=postgresql://postgres@/<db>?host=/tmp/pgtest&port=54329 \
        python -m pytest tests/test_predict_meet_source.py
"""
import os
import sys

import _env  # noqa: F401  -- sets XCP_DB_PASSWORD, must precede config

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("racecast", "engine", "scripts", "model"):
    sys.path.insert(0, os.path.join(_ROOT, sub))

import pytest  # noqa: E402

P = pytest.importorskip("predict")


class _Cur:
    """Records every statement; answers fetchone/fetchall from a script."""

    def __init__(self, one=None, many=None):
        self.calls, self.one, self.many = [], one, many or []
        self.connection = self

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.one(self.calls[-1]) if callable(self.one) else self.one

    def fetchall(self):
        return self.many(self.calls[-1]) if callable(self.many) else self.many

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------------ #
#  THE SQL CARRIES THE SOURCE
# ------------------------------------------------------------------ #

def test_the_target_spec_reads_the_anet_meet_of_the_id():
    cur = _Cur(one={"course_name": "Warinanco Park", "distance_meters": 5000.0,
                    "date": "2009-10-13"})
    spec = P._targetSpec(cur, {"mode": "rerun_exact", "meet_id": 94686,
                               "div_id": "22", "sport": "XC",
                               "source": "anet"})
    sql, params = cur.calls[0]
    assert "FROM meets m" in sql
    assert "m.source = %(src)s" in sql and params["src"] == "anet"
    # the date subquery reads that meet's results, not the id's
    assert "r.source = m.source" in sql
    assert spec["source"] == "anet" and spec["date"] == "2009-10-13"


def test_the_target_spec_reads_a_tfrrs_meet_from_its_own_row():
    """`meets` is anet's table: the row there under this id IS the other
    meet. A tfrrs target is read from meets_tfrrs."""
    cur = _Cur(one={"course_name": "LaVern Gibson", "distance_meters": 8000.0,
                    "date": "2025-11-22"})
    spec = P._targetSpec(cur, {"mode": "rerun_exact", "meet_id": 94686,
                               "div_id": "291", "sport": "XC",
                               "source": "tfrrs"})
    sql, params = cur.calls[0]
    assert "FROM meets_tfrrs mt" in sql and "FROM meets m" not in sql
    assert "r.source = 'tfrrs'" in sql
    assert params == {"meet": 94686, "divtext": "291"}
    assert spec["distance_meters"] == 8000.0 and spec["date"] == "2025-11-22"


def test_the_target_spec_narrows_a_track_meet_too():
    cur = _Cur(one={"distance_meters": 1600.0, "date": "2025-04-01"})
    P._targetSpec(cur, {"mode": "meet", "meet_id": 5, "sport": "TF",
                        "source": "tfrrs"})
    sql, params = cur.calls[0]
    assert "FROM meets_tf m" in sql
    assert "m.source = %(src)s" in sql and params["src"] == "tfrrs"
    assert "r.source = m.source" in sql


def test_a_missing_date_comes_from_the_same_source():
    """No meet row (a tfrrs track meet never landed in meets_tf): the date is
    that source's own, never the colliding meet's."""
    seen = []

    def one(call):
        seen.append(call)
        return None if len(seen) == 1 else {"d": "2025-04-01"}
    cur = _Cur(one=one)
    spec = P._targetSpec(cur, {"mode": "meet", "meet_id": 5, "sport": "TF",
                               "source": "tfrrs"})
    sql, params = cur.calls[1]
    assert "results_tf" in sql and "source = %(src)s" in sql
    assert params["src"] == "tfrrs"
    assert spec["date"] == "2025-04-01"


def test_the_season_is_the_chosen_meets():
    """"As it ran" reads the meet's season: the D3 championships are 2025,
    not the 2009 of the meet that shares the id."""
    cur = _Cur(one={"d": "2025-11-22"})
    assert P.meetSeason(cur, 94686, "XC", source="tfrrs") == 2025
    sql, params = cur.calls[0]
    assert "FROM results" in sql and "source = %(src)s" in sql
    assert params == {"m": 94686, "src": "tfrrs"}
    # anet's scheduled-date fallback is anet's table, tfrrs's is its own
    assert "FROM meets\n" in sql or "FROM meets " in sql
    assert "FROM meets_tfrrs" in sql
    # a caller naming no source reads the old, unnarrowed way
    cur = _Cur(one={"d": "2009-10-13"})
    assert P.meetSeason(cur, 94686, "XC") == 2009
    assert cur.calls[0][1]["src"] is None


def test_the_exact_field_is_one_meets_runners():
    cur = _Cur(many=[])
    P._exactField(cur, 94686, 22, "XC", source="anet")
    sql, params = cur.calls[0]
    assert "r.source = %(src)s" in sql and params["src"] == "anet"


def test_the_meets_state_is_the_chosen_meets():
    cur = _Cur(one={"state": "NJ"})
    assert P._meetState(cur, 94686, "XC", source="anet") == "NJ"
    sql, params = cur.calls[0]
    assert "FROM meets" in sql and "source = %s" in sql
    assert params == (94686, "anet", "anet")
    cur = _Cur(one={"state": "IN"})
    assert P._meetState(cur, 94686, "XC", source="tfrrs") == "IN"
    assert "meets_tfrrs" in cur.calls[0][0] and len(cur.calls) == 1


def test_the_rosters_read_the_targets_source(monkeypatch):
    """_teamRosters hands the target's source to every read of the meet."""
    got = []
    monkeypatch.setattr(P, "_exactField",
                        lambda cur, m, d, s, source=None, event_id=None:
                        got.append(("field", source)) or [])
    monkeypatch.setattr(P, "_meetState",
                        lambda cur, m, s, source=None:
                        got.append(("state", source)) or None)
    monkeypatch.setattr(P, "_teamStates", lambda cur, rows, st=None: {})
    P._teamRosters(None, [], {"mode": "rerun_exact", "meet_id": 94686,
                              "div_id": "291", "sport": "XC",
                              "source": "tfrrs"})
    assert got and all(src == "tfrrs" for _k, src in got), got


# ------------------------------------------------------------------ #
#  THE ROUTES RESOLVE IT ONCE, BY THE MEET PAGE'S RULE
# ------------------------------------------------------------------ #

@pytest.fixture
def A():
    pytest.importorskip("flask")
    import app
    return app


def _sourcesCur():
    """meet_sources' answer for the D3 id: tfrrs is the bigger meet."""
    return _Cur(many=[{"source": "tfrrs", "n": 291},
                      {"source": "anet", "n": 30}])


def test_alt_picks_the_meet_as_the_meet_page_does(A):
    assert A._predictSource(_sourcesCur(), 94686, "XC") == ("tfrrs", 0)
    assert A._predictSource(_sourcesCur(), 94686, "XC", "1") == ("anet", 1)
    # out of range clamps to a real index, junk is the default: pick_source
    assert A._predictSource(_sourcesCur(), 94686, "XC", "9") == ("anet", 1)
    assert A._predictSource(_sourcesCur(), 94686, "XC", "x") == ("tfrrs", 0)
    t, err = A._target({"mode": "rerun_exact", "meet_id": "94686",
                        "alt": "1"})
    assert not err and t["alt"] == "1"
    A._withSource(_sourcesCur(), t)
    assert t["source"] == "anet" and t["alt"] == 1


def test_the_races_route_lists_one_meet(A, monkeypatch):
    seen = {}

    def predictSource(cur, meet, sport, alt=None, div=None):
        seen["alt"] = alt
        return "anet", 1

    def divisions(cur, meet, source=None):
        seen["divisions"] = source
        return [{"div_id": 22, "division": "Varsity", "distance": 5000,
                 "gender": "M", "n_results": 22}]

    def meetName(cur, meet, sport, div_id=None, source=None):
        seen["name"] = source
        return {"meet_name": "Warinanco Invitational"}

    cur = _Cur(one=lambda call: ({"d": "2009-10-13"} if "min(date)" in call[0]
                                 else {"course_name": "Warinanco Park"}))

    class _Conn:
        def cursor(self, **k):
            return cur

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(A, "getConn", lambda: _Conn())
    monkeypatch.setattr(A, "_predictSource", predictSource)
    monkeypatch.setattr(A, "get_meet_divisions", divisions)
    monkeypatch.setattr(A, "predictMeetName", meetName)
    r = A.app.test_client().get("/api/predict/races?meet_id=94686&sport=XC"
                                "&alt=1")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert seen == {"alt": "1", "divisions": "anet", "name": "anet"}
    assert body["alt"] == 1 and body["date"] == "2009-10-13"
    date_sql, date_params = [c for c in cur.calls if "min(date)" in c[0]][0]
    assert "source = %(src)s" in date_sql and date_params["src"] == "anet"
    course_sql, course_params = [c for c in cur.calls
                                 if "course_name" in c[0]][0]
    assert "m.source = %(src)s" in course_sql
    assert course_params["src"] == "anet"


def test_squads_as_it_ran_read_the_chosen_meets_season(A, monkeypatch):
    """Squads: Everyone, as it ran = that meet's season's whole roster. The
    season is read off the meet the page chose."""
    import ttlcache
    ttlcache.clear()
    seen = {}
    monkeypatch.setattr(A, "_predictSource",
                        lambda cur, m, s, alt=None, div=None: ("tfrrs", 0))

    def season(cur, meet, sport, source=None):
        seen["season_source"] = source
        return 2025

    def squads(cur, wanted, sport, season_year, **k):
        seen["season"] = season_year
        seen["as_ran"] = k.get("as_ran")
        return [{"school": s, "runners": [{"person_id": 1}]} for s, _ in wanted]
    monkeypatch.setattr(P, "meetSeason", season)
    monkeypatch.setattr(P, "meetLevels",
                        lambda cur, m, d, s, source=None, event_id=None:
                        seen.setdefault("levels_source", source) and None)
    monkeypatch.setattr(P, "_meetState",
                        lambda cur, m, s, source=None:
                        seen.setdefault("state_source", source) and None)
    monkeypatch.setattr(P, "_currentSeason", lambda cur, s: 2026)
    monkeypatch.setattr(P, "schoolSquads", squads)
    p, err = A._squadParams({"sport": "XC", "meet_id": "94686",
                             "when": "asran", "alt": "0"})
    assert not err and p["alt"] == "0"
    A._squadsServed(None, [("Tufts", None)], p)
    assert seen["season_source"] == "tfrrs" and seen["season"] == 2025
    assert seen["as_ran"] is True
    assert seen["levels_source"] == "tfrrs"
    assert seen["state_source"] == "tfrrs"
    ttlcache.clear()


# ------------------------------------------------------------------ #
#  ONE meet_id, TWO MEETS, A REAL DATABASE
# ------------------------------------------------------------------ #

_FIXTURE = """
CREATE TABLE results (result_id bigint, athlete_id bigint, person_id bigint,
                      meet_id bigint, div_id bigint, time_seconds real,
                      grade text, date text, school text, team_id bigint,
                      athlete_name text, source text, place int,
                      speed_rating real, rating_pool text);
CREATE TABLE meets (div_id bigint, meet_id bigint, meet_name text,
                    meet_date text, course_name text, distance real,
                    gps_lat real, gps_long real, altitude_meters real,
                    state text, division text, source text);
CREATE TABLE meets_tfrrs (meet_id bigint, sport text, meet_name text,
                          date text, venue_name text, state text,
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

-- anet: a 2009 New Jersey high-school meet, two Varsity races
INSERT INTO meets VALUES
  (22, 94686, 'Warinanco Invitational', '2009-10-13', 'Warinanco Park', 5000,
   40.65, -74.24, NULL, 'NJ', 'Varsity', 'anet'),
  (8,  94686, 'Warinanco Invitational', '2009-10-13', 'Warinanco Park', 5000,
   40.65, -74.24, NULL, 'NJ', 'Varsity', 'anet');
-- tfrrs: the 2025 NCAA Division III championships, the same number
INSERT INTO meets_tfrrs VALUES
  (94686, 'XC', 'NCAA Division III Cross Country Championships', '2025-11-22',
   'LaVern Gibson Championship Course', 'IN', 39.43, -87.41,
   '{"291": {"distance": 8000, "div_name": "Men''s Race - 8000 Meters"},
     "290": {"distance": 6000, "div_name": "Women''s Race - 6000 Meters"}}',
   'tfrrs');
INSERT INTO athletes VALUES
  (1,'Al','Tufts','M','Tufts'), (2,'Bo','Tufts','M','Tufts'),
  (3,'Cy','Tufts','M','Tufts'), (4,'Di','Tufts','F','Tufts'),
  (5,'Ed','Tufts','F','Tufts'), (6,'Fi','Amherst','M','Amherst'),
  (7,'Gu','Amherst','F','Amherst'),
  (11,'Ha','Union','M','Union Catholic'), (12,'Io','Union','M','Union Catholic'),
  (13,'Jo','Union','F','Union Catholic');
INSERT INTO results VALUES
  (101,1,1,94686,291,1500,'SR-4','2025-11-22','Tufts',NULL,NULL,'tfrrs',1,NULL,NULL),
  (102,2,2,94686,291,1510,'JR-3','2025-11-22','Tufts',NULL,NULL,'tfrrs',2,NULL,NULL),
  (103,3,3,94686,291,1520,'SO-2','2025-11-22','Tufts',NULL,NULL,'tfrrs',3,NULL,NULL),
  (106,6,6,94686,291,1530,'FR-1','2025-11-22','Amherst',NULL,NULL,'tfrrs',4,NULL,NULL),
  (104,4,4,94686,290,1300,'SR-4','2025-11-22','Tufts',NULL,NULL,'tfrrs',1,NULL,NULL),
  (105,5,5,94686,290,1310,'JR-3','2025-11-22','Tufts',NULL,NULL,'tfrrs',2,NULL,NULL),
  (107,7,7,94686,290,1320,'SO-2','2025-11-22','Amherst',NULL,NULL,'tfrrs',3,NULL,NULL),
  (201,11,11,94686,22,1000,'12','2009-10-13','Union Catholic',NULL,NULL,'anet',1,NULL,NULL),
  (202,12,12,94686,22,1010,'11','2009-10-13','Union Catholic',NULL,NULL,'anet',2,NULL,NULL),
  (203,13,13,94686,8,1200,'10','2009-10-13','Union Catholic',NULL,NULL,'anet',1,NULL,NULL);
"""

_SCHEMA = "predict_meet_source"


@pytest.fixture
def pg(monkeypatch):
    """A connection whose search_path is a scratch schema holding the
    fixture, committed -- predict.py rolls back on a soft failure, and that
    must not take the tables with it."""
    dsn = os.environ.get("XCP_TWIN_TEST_DSN")
    if not dsn:
        pytest.skip("set XCP_TWIN_TEST_DSN to a scratch Postgres")
    psycopg2 = pytest.importorskip("psycopg2")
    import psycopg2.extras
    admin = psycopg2.connect(dsn)
    with admin.cursor() as c:
        c.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {_SCHEMA}")
        c.execute(f"SET search_path = {_SCHEMA}")
        c.execute(_FIXTURE)
    admin.commit()
    conn = psycopg2.connect(dsn, options=f"-c search_path={_SCHEMA}")
    # the parts of the page that are not about the meet stay out of the way
    monkeypatch.setattr(P, "_stampCrests", lambda rows, *a, **k: rows)
    monkeypatch.setattr(P, "_currentSeason", lambda cur, sport: 2026)
    states = []
    monkeypatch.setattr(P, "_teamStates",
                        lambda cur, rows, meet_state=None:
                        states.append(meet_state) or {})
    yield conn, states
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


def test_one_id_two_meets_on_a_real_database(A, pg, monkeypatch):
    conn, states = pg
    c = _client(A, monkeypatch, conn)

    # ---- the race list, date and course: ONE meet each
    d3 = c.get("/api/predict/races?meet_id=94686&sport=XC").get_json()
    assert sorted(r["div_id"] for r in d3["races"]) == [290, 291]
    assert d3["alt"] == 0 and d3["date"] == "2025-11-22"
    assert d3["course"] == "LaVern Gibson Championship Course"
    assert d3["meet_name"] == "NCAA Division III Cross Country Championships"
    assert {r["label"] for r in d3["races"]} == {
        "Men's Race - 8000 Meters", "Women's Race - 6000 Meters"}

    nj = c.get("/api/predict/races?meet_id=94686&sport=XC&alt=1").get_json()
    assert sorted(r["div_id"] for r in nj["races"]) == [8, 22]
    assert nj["alt"] == 1 and nj["date"] == "2009-10-13"
    assert nj["course"] == "Warinanco Park"

    # an old link with no ?alt= but a division only the smaller meet has
    # lands on that meet -- the race page's own rule
    old = c.get("/api/predict/races?meet_id=94686&sport=XC&div_id=22").get_json()
    assert old["alt"] == 1 and old["date"] == "2009-10-13"

    # ---- the field, as it ran: who raced THAT meet, in THAT meet's state
    f0 = c.get("/api/predict/field?meet_id=94686&sport=XC&when=asran").get_json()
    assert {t["school"] for t in f0["teams"]} == {"Tufts", "Amherst"}
    assert sum(len(t["runners"]) for t in f0["teams"]) == 7
    assert states[-1] == "IN"
    f1 = c.get("/api/predict/field?meet_id=94686&sport=XC&when=asran"
               "&alt=1").get_json()
    assert {t["school"] for t in f1["teams"]} == {"Union Catholic"}
    assert sum(len(t["runners"]) for t in f1["teams"]) == 3
    assert states[-1] == "NJ"

    # ---- the target the model is aimed at, and the season "as it ran" reads
    cur = conn.cursor(cursor_factory=__import__("psycopg2.extras").extras
                      .RealDictCursor)
    t0, _ = A._target({"mode": "rerun_exact", "meet_id": "94686",
                       "div_id": "291", "sport": "XC"})
    s0 = P._targetSpec(cur, A._withSource(cur, t0))
    assert t0["source"] == "tfrrs"
    assert s0["date"] == "2025-11-22" and s0["distance_meters"] == 8000
    assert s0["course_name"] == "LaVern Gibson Championship Course"
    assert P.meetSeason(cur, 94686, "XC", source="tfrrs") == 2025
    assert P._meetState(cur, 94686, "XC", source="tfrrs") == "IN"

    t1, _ = A._target({"mode": "rerun_exact", "meet_id": "94686",
                       "div_id": "22", "sport": "XC", "alt": "1"})
    s1 = P._targetSpec(cur, A._withSource(cur, t1))
    assert t1["source"] == "anet"
    assert s1["date"] == "2009-10-13" and s1["distance_meters"] == 5000
    assert s1["course_name"] == "Warinanco Park"
    assert P.meetSeason(cur, 94686, "XC", source="anet") == 2009

    # the whole meet, no division named: still one meet's distance
    tw, _ = A._target({"mode": "rerun_exact", "meet_id": "94686",
                       "sport": "XC"})
    sw = P._targetSpec(cur, A._withSource(cur, tw))
    assert sw["date"] == "2025-11-22" and sw["distance_meters"] in (6000, 8000)

    # the exact field the scorer reads, per meet
    ids0 = {r["person_id"] for r in P._exactField(cur, 94686, None, "XC",
                                                  source="tfrrs")}
    ids1 = {r["person_id"] for r in P._exactField(cur, 94686, None, "XC",
                                                  source="anet")}
    assert ids0 == {1, 2, 3, 4, 5, 6, 7} and ids1 == {11, 12, 13}
    assert P._divisionLabel(cur, 94686, 291, "XC", source="tfrrs") \
        == "Men's Race - 8000 Meters"
    assert P._divisionLabel(cur, 94686, 22, "XC", source="anet") == "Varsity"
