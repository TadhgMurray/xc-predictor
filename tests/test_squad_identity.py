# Project: xc-predictor / tests
# File:    test_squad_identity.py
# Purpose: a school NAME is not a school, and many squads in one request are
#          the same squads as one at a time (owner, 2026-10-05).
#
#   "We really need to separate schools by pool because adding the entire
#   roster of Amherst should not add this guy" -- a grade-5 runner from
#   Amherst (WI), on an Amherst College / NESCAC prediction.
#
#   "speed up the predictions and the loading of the squads. They're pretty
#   slow" -- Squads: Everyone sent one request per team.
#
# No database: the squad queries are replaced by fixtures that honour the
# same gender/level arguments the SQL does, and the identity tables by a
# fake cursor (assignedStates) or a stub (clustersMany).
#
#   python -m pytest -q tests/test_squad_identity.py
import copy
import os
import sys

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "racecast"), os.path.join(_ROOT, "engine"),
           os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "model")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest                                                # noqa: E402

import predict as P                                          # noqa: E402
import school_identity as SI                                 # noqa: E402

NOW = 2026

# athlete_season this year: (school, person_id, pool, grade, name, rating)
SEASON = [
    # Amherst College (MA): one runner whose HOME state is his high
    # school's (CA), one tfrrs freshman nobody has placed yet
    ("Amherst", 1, "college_m", "SR-4", "College Senior", 115.0),
    ("Amherst", 2, "college_m", "FR-1", "College Frosh", 110.0),
    # Amherst Regional (MA) and Amherst (WI): both high schools, both hs_m
    ("Amherst", 3, "hs_m", "11", "Regional Junior", 112.0),
    ("Amherst", 4, "hs_m", "10", "Wisconsin Soph", 109.0),
    ("Tufts", 10, "college_m", "JR-3", "Tufts Junior", 113.0),
    ("Tufts", 50, "college_m", "SO-2", "Moved Runner", 108.0),
    ("Bates", 20, "college_m", "SR-4", "Bates Senior", 111.0),
    # a one-state name: nobody is ever narrowed off it
    ("Plainfield", 30, "hs_m", "12", "Plainfield Senior", 101.0),
]

# results this season that athlete_season has not caught up with
FRESH = [
    # ★ THE RUNNER THE OWNER FOUND: grade 5, elementary pool, Wisconsin
    ("Amherst", 5, "elem_m", "5", "Unknown", 118.4),
    ("Bates", 50, "college_m", "SO-2", "Moved Runner", 108.0),
    ("Plainfield", 31, "hs_m", "9", "Plainfield Frosh", 99.0),
]

# where the identity puts each athlete (school_athlete_state, else home)
ASSIGNED = {("Amherst", 1): "CA", ("Amherst", 3): "MA",
            ("Amherst", 4): "WI", ("Amherst", 5): "WI",
            ("Plainfield", 30): "IN", ("Plainfield", 31): "NJ"}

CLUSTERS = {
    "Amherst": [{"state": "WI", "n": 643, "share": 0.55, "is_primary": True},
                {"state": "MA", "n": 520, "share": 0.45, "is_primary": False}],
    "Plainfield": [{"state": "IN", "n": 900, "share": 0.97, "is_primary": True},
                   {"state": "NJ", "n": 2, "share": 0.03, "is_primary": False}],
}


def _lvl(pool):
    return pool.split("_")[0]


def _entry(row):
    sch, pid, pool, grade, name, rating = row
    return {"person_id": pid, "school": sch, "pool": pool, "grade": grade,
            "name": name, "rating": rating, "n_races": 2}


@pytest.fixture
def fixtures(monkeypatch):
    import roster
    import season_year
    monkeypatch.setattr(season_year, "academicYear", lambda d: NOW)
    monkeypatch.setattr(roster, "carryingSchools", lambda *a, **k: set())
    monkeypatch.setattr(P, "_fillNames", lambda cur, rows: None)
    monkeypatch.setattr(P, "_currentSeason", lambda cur, sport: NOW)
    monkeypatch.setattr(P, "_stateOf", lambda s: None)
    monkeypatch.setattr(P, "_stampCrests", lambda rows, *a, **k: rows)

    def squadsForYear(cur, schools, sport, year, exclude_terminal=False,
                      active_year=None, gender=None, levels=None):
        out = {}
        for row in SEASON:
            e = _entry(row)
            if e["school"] not in schools:
                continue
            if gender and e["pool"][-1].upper() != gender:
                continue
            if levels and _lvl(e["pool"]) not in levels:
                continue
            out.setdefault(e["school"], []).append(e)
        return {s: sorted(r, key=lambda e: -e["rating"]) for s, r in out.items()}

    def raceEntrants(cur, schools, sport, year, gender=None, levels=None):
        out = {}
        for row in FRESH:
            e = _entry(row)
            e["from_results"] = True
            if e["school"] not in schools:
                continue
            if gender and e["pool"][-1].upper() != gender:
                continue
            if levels and _lvl(e["pool"]) not in levels:
                continue
            out.setdefault(e["school"], []).append(e)
        return copy.deepcopy(out)

    monkeypatch.setattr(P, "_squadsForYear", squadsForYear)
    monkeypatch.setattr(P, "_raceEntrants", raceEntrants)
    monkeypatch.setattr(SI, "clustersMany",
                        lambda cur, schools: {s: CLUSTERS[s] for s in schools
                                              if s in CLUSTERS})
    monkeypatch.setattr(SI, "assignedStates",
                        lambda cur, pairs: {p: ASSIGNED[p] for p in pairs
                                            if p in ASSIGNED})


def _ids(squad):
    return sorted(r["person_id"] for r in squad["runners"])


# ------------------------------------------------------------------ #
#  ONE NAME, SEVERAL SCHOOLS
# ------------------------------------------------------------------ #

def test_a_college_meet_takes_the_college_and_not_the_fifth_grader(fixtures):
    """The owner's case: Amherst's whole roster on a NESCAC race. The level
    takes the college; the Wisconsin elementary runner is not in it."""
    got = P.schoolSquad(None, "Amherst", "XC", gender="M",
                        levels={"college"}, state="MA")
    assert _ids(got) == [1, 2]


def test_the_state_splits_two_high_schools_of_one_name(fixtures):
    """Amherst Regional (MA) and Amherst (WI) are both hs_m: the level cannot
    tell them apart, the identity's state does."""
    ma = P.schoolSquad(None, "Amherst", "XC", levels={"hs"}, state="MA")
    wi = P.schoolSquad(None, "Amherst", "XC", levels={"hs"}, state="WI")
    assert _ids(ma) == [3]
    assert _ids(wi) == [4]


def test_with_no_level_the_state_still_drops_the_other_namesake(fixtures):
    """! A college runner is never judged by home state (his is his high
    school's, CA here) and an unplaced one stays: only evidence of another
    namesake drops a runner."""
    got = P.schoolSquad(None, "Amherst", "XC", state="MA")
    assert _ids(got) == [1, 2, 3]
    wi = P.schoolSquad(None, "Amherst", "XC", state="WI")
    assert _ids(wi) == [1, 2, 4, 5]


def test_no_state_and_no_level_is_the_name_as_before(fixtures):
    got = P.schoolSquad(None, "Amherst", "XC")
    assert _ids(got) == [1, 2, 3, 4, 5]


def test_a_one_state_school_is_never_narrowed(fixtures):
    """Plainfield's NJ sliver is below the chip bars, so asking for IN keeps
    its runner whose home state is NJ -- he runs for it."""
    got = P.schoolSquad(None, "Plainfield", "XC", state="IN")
    assert _ids(got) == [30, 31]


def test_chips_from_clusters_is_the_school_page_rule():
    chips, primary = SI.chipsFrom(CLUSTERS["Amherst"], "MA")
    assert primary == "WI" and {c["state"] for c in chips} == {"WI", "MA"}
    chips, primary = SI.chipsFrom(CLUSTERS["Plainfield"], "IN")
    assert primary == "IN" and chips == []


def test_assigned_states_prefers_the_schools_own_assignment(monkeypatch):
    class Cur:
        def execute(self, sql, params=None):
            self.sql, self.params = sql, params

        def fetchone(self):
            return {"to_regclass": "x"}

        def fetchall(self):
            if "school_athlete_state" in self.sql:
                return [{"school": "Amherst", "person_id": 1, "state": "MA"},
                        {"school": "Other", "person_id": 2, "state": "TX"}]
            return [{"person_id": 1, "state": "CA"},
                    {"person_id": 2, "state": "WI"}]
    monkeypatch.setitem(SI._LABELS, "athlete_state", True)
    got = SI.assignedStates(Cur(), [("Amherst", 1), ("Amherst", 2),
                                    ("Amherst", 3)])
    # 1: the school's own assignment beats his home state; 2: the TX row is
    # another school's, so his home state answers; 3: nobody knows -> absent
    assert got == {("Amherst", 1): "MA", ("Amherst", 2): "WI"}


# ------------------------------------------------------------------ #
#  MANY AT ONCE IS THE SAME AS ONE AT A TIME
# ------------------------------------------------------------------ #

def test_a_batch_is_each_school_asked_alone(fixtures):
    """! THE CROSS-SCHOOL CASE: runner 50 is on Tufts' season roster and has
    raced for Bates this season. Asked alone, Bates gets him from its
    results; the batch must not let Tufts' roster take him off Bates'."""
    wanted = [("Amherst", "MA"), ("Tufts", None), ("Amherst", "WI"),
              ("Bates", None), ("Plainfield", "IN"), ("Nowhere", None),
              ("Tufts", None)]
    for kw in ({}, {"gender": "M"}, {"levels": {"college"}},
               {"gender": "M", "levels": {"hs", "elem"}}):
        batch = P.schoolSquads(None, wanted, "XC", **kw)
        alone = [P.schoolSquads(None, [w], "XC", **kw)[0] for w in wanted]
        assert batch == alone, kw
    bates = P.schoolSquads(None, [("Tufts", None), ("Bates", None)], "XC")[1]
    assert 50 in _ids(bates)


def test_the_empty_side_is_still_reported(fixtures):
    """Carondelet's rule survives the batching: an empty gendered squad says
    how many the school has on the other side."""
    got = P.schoolSquads(None, [("Amherst", None), ("Tufts", None)], "XC",
                         gender="F")
    assert [g["runners"] for g in got] == [[], []]
    assert got[0]["other_gender"] == 5 and got[1]["other_gender"] == 2


# ------------------------------------------------------------------ #
#  THE ROUTES
# ------------------------------------------------------------------ #

class _Conn:
    def cursor(self, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def client(fixtures, monkeypatch):
    flask = pytest.importorskip("flask")                   # noqa: F841
    import app as A
    import ttlcache
    ttlcache.clear()
    monkeypatch.setattr(A, "getConn", lambda: _Conn())
    calls = {"levels": 0}

    def meetLevels(cur, meet_id, div_id, sport, source=None, event_id=None):
        calls["levels"] += 1
        return {"college"}
    monkeypatch.setattr(P, "meetLevels", meetLevels)
    monkeypatch.setattr(P, "_meetState",
                        lambda cur, meet_id, sport, source=None: "MA")
    # which meet of the id (2026-10-05): this fake connection has no tables
    monkeypatch.setattr(A, "_predictSource", lambda *a, **k: ("anet", 0))
    yield A.app.test_client(), calls
    ttlcache.clear()


def test_a_squad_with_no_level_takes_the_meets(client):
    """★ The page used to send no level when the focus was on another block
    -- and no level was no filter. Naming the meet is enough now."""
    c, calls = client
    r = c.get("/api/predict/squad?school=Amherst&sport=XC&gender=M"
              "&meet_id=270850&div_id=1080171")
    assert r.status_code == 200, r.data
    assert _ids(r.get_json()) == [1, 2]
    assert calls["levels"] == 1
    # an explicit empty list is the field saying "mixed": not re-derived,
    # and the meet's state (MA) still picks the namesake
    r = c.get("/api/predict/squad?school=Amherst&sport=XC&levels="
              "&meet_id=270850")
    assert _ids(r.get_json()) == [1, 2, 3]
    assert calls["levels"] == 1


def test_the_batch_route_answers_in_order_and_caches(client, monkeypatch):
    c, _calls = client
    teams = '[["Amherst","WI"],"Tufts",["Amherst","MA"]]'
    r = c.post("/api/predict/squads", data={
        "sport": "XC", "levels": "hs,elem,college", "teams": teams})
    assert r.status_code == 200, r.data
    got = r.get_json()["squads"]
    assert [g["school"] for g in got] == ["Amherst", "Tufts", "Amherst"]
    assert _ids(got[0]) == [1, 2, 4, 5] and _ids(got[2]) == [1, 2, 3]
    # ★ HELD: a second ask does not compute again
    monkeypatch.setattr(P, "_squadsForYear",
                        lambda *a, **k: pytest.fail("recomputed"))
    again = c.get("/api/predict/squad?school=Tufts&sport=XC"
                  "&levels=hs,elem,college").get_json()
    assert again == got[1]


def test_the_batch_route_validates(client):
    c, _calls = client
    assert c.get("/api/predict/squads?teams=nope").status_code == 400
    assert c.get('/api/predict/squads?teams=[[1,2]]').status_code == 400
    many = "[" + ",".join('"S%d"' % i for i in range(500)) + "]"
    assert c.post("/api/predict/squads",
                  data={"teams": many}).status_code == 400
