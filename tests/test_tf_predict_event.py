# Project: xc-predictor / tests
# File:    test_tf_predict_event.py
# Purpose: a track race is (meet, division, EVENT), end to end (owner,
#          2026-10-10: "predictions for tf races are majorly messed up").
#
#   * the races list is one row per distance event, keyed "<div>-<event>",
#     counted by its own results; sprints, field events and relays are not
#     races the page can predict
#   * the target spec reads THE EVENT's row, and its distance from the name
#     when the stored one is missing (tfrrs) or a placeholder (-1)
#   * the field is the event's runners, never the division's every athlete
#   * a track event scores by tf_points' event points, most points wins --
#     in the table and in the simulation
#   * a track squad is who runs the distance, best at it first; the form
#     behind a track time is weighted to the target distance
#
# No database: stub cursors record the SQL and answer from fixtures.
#
#   python -m pytest -q tests/test_tf_predict_event.py
import math
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


# ------------------------------------------------------------------ keys
def test_the_race_key_carries_the_event():
    assert P.raceKey(12, 345) == "12-345"
    assert P.raceKey(12) == "12"
    assert P.splitRaceKey("12-345") == (12, 345)
    assert P.splitRaceKey("12") == (12, None)        # an XC key, unchanged
    assert P.splitRaceKey(7) == (7, None)
    assert P.splitRaceKey(None) == (None, None)
    assert P.splitRaceKey("x-y") == (None, None)


def test_the_event_names_its_distance_and_gender():
    assert P.tfEventInfo("1600m", "Varsity Boys") == (1600, "M")
    assert P.tfEventInfo("3200m", "Girls JV") == (3200, "F")
    m, g = P.tfEventInfo("Women's 1500 Meters", None)
    assert (m, g) == (1500.0, "F")
    assert P.tfEventInfo("Men's Mile")[0] == pytest.approx(1609.344)
    for not_a_race in ("100m", "Shot Put", "4x400m", "110mh",
                       "3000m Steeplechase"):
        assert P.tfEventInfo(not_a_race, "Varsity Boys")[0] is None


def test_the_band_is_one_rung_of_the_ladder():
    # 1600: the 1500, the mile and 2000 are in; the 800 and 3200 are not
    for d in (1500, 1600, 1609.344, 2000):
        assert P.tfInBand(d, 1600)
    for d in (800, 3000, 3200):
        assert not P.tfInBand(d, 1600)
    # 3200 takes 3000 and the 2 mile
    assert P.tfInBand(3000, 3200) and P.tfInBand(3218.688, 3200)
    assert P.TF_BAND_LOG == pytest.approx(math.log(2) / 2)


def test_entries_cap_is_what_the_school_entered():
    assert P.tfEntryCap(4) == 4
    assert P.tfEntryCap(1) == 1
    assert P.tfEntryCap(0) == P.TF_DEFAULT_ENTRIES == 3
    assert P.tfEntryCap(4, per_team=2) == 2


# ------------------------------------------------------------ races list
DIVISION = [
    # (div, event, event_short, division, stored distance, n)
    (5, 101, "100m", "Varsity Boys", 100, 24),
    (5, 102, "1600m", "Varsity Boys", 1600, 18),
    (5, 103, "3200m", "Varsity Boys", -1, 12),        # failed-scrape -1
    (5, 104, "Shot Put", "Varsity Boys", None, 16),
    (5, 105, "4x400m", "Varsity Boys", 1600, 9),
    (6, 201, "1600m", "Varsity Girls", 1600, 15),
    (6, 202, "3200m", "Varsity Girls", 3200, 10),
    (0, 301, "Men's 1500 Meters", None, None, 22),    # a tfrrs event
]


def _raceRows():
    return [{"div_id": d, "event_id": e, "event_short": es, "division": dv,
             "distance_meters": st, "n_results": n}
            for d, e, es, dv, st, n in DIVISION]


def test_track_races_are_the_distance_events():
    races = P.trackRaces(_raceRows())
    keys = [r["div_id"] for r in races]
    assert keys == ["5-102", "5-103", "6-201", "6-202", "0-301"]
    by = {r["div_id"]: r for r in races}
    assert by["5-102"]["gender"] == "M" and by["6-201"]["gender"] == "F"
    # its own count, not the division's
    assert by["5-102"]["n_results"] == 18
    # a placeholder -1 is not a distance: the name's is
    assert by["5-103"]["distance"] == 3200.0
    # tfrrs: no division, the event says it all
    assert by["0-301"]["gender"] == "M" and by["0-301"]["distance"] == 1500.0
    assert by["5-102"]["event"] == "1600m" and by["5-102"]["label"] == "Varsity Boys"


# ------------------------------------------------------------ the spec
class _SpecCur:
    def __init__(self, row):
        self.row, self.sql, self.params = row, [], []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.params.append(params)
        if params is not None:
            sql % params

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


@pytest.fixture
def no_overrides(monkeypatch):
    monkeypatch.setattr(P, "_specOverride", lambda *a, **k: ("", ""))
    monkeypatch.delenv("XCP_PREDICT_VENUE_DIFFICULTY", raising=False)


def test_the_spec_reads_the_events_own_row(no_overrides):
    cur = _SpecCur({"distance_meters": 1600.0, "date": "2026-04-01",
                    "event_short": "1600m", "division": "Varsity Boys"})
    spec = P._targetSpec(cur, {"mode": "rerun_exact", "sport": "TF",
                               "meet_id": 9, "div_id": 5, "event_id": 102,
                               "source": "anet"})
    sql = cur.sql[0]
    assert "m.event_id = %(event)s" in sql and cur.params[0]["event"] == 102
    assert "ORDER BY" in sql and "LIMIT 1" in sql
    assert spec["distance_meters"] == 1600.0


@pytest.mark.parametrize("stored,name,want", [
    (-1.0, "3200m", 3200.0),                  # anet's failed-scrape placeholder
    (None, "Men's Mile", 1609.344),           # tfrrs: never stored
    (0.0, "Women's 1500 Meters", 1500.0),
])
def test_the_spec_distance_comes_from_the_name(no_overrides, stored, name, want):
    cur = _SpecCur({"distance_meters": stored, "date": "2026-04-01",
                    "event_short": name, "division": None})
    spec = P._targetSpec(cur, {"mode": "rerun_exact", "sport": "TF",
                               "meet_id": 9, "div_id": 0, "event_id": 7,
                               "source": "tfrrs"})
    assert spec["distance_meters"] == pytest.approx(want)


# ------------------------------------------------------------ the field
def test_the_field_is_the_events_runners(monkeypatch):
    import pool_view
    monkeypatch.setattr(pool_view, "stampBoardRows", lambda *a, **k: None)
    cur = _SpecCur(None)
    P._exactField(cur, 9, 5, "TF", source="anet", event_id=102)
    sql, params = cur.sql[0], cur.params[0]
    assert "COALESCE(r.is_field, 0) = 0" in sql
    assert "COALESCE(r.is_relay, 0) = 0" in sql
    assert "r.event_id = ANY(%(events)s)" in sql and params["events"] == [102]
    # XC is what it was: no track filters
    cur = _SpecCur(None)
    P._exactField(cur, 9, 5, "XC", source="anet")
    assert "is_field" not in cur.sql[0] and "events)s" not in cur.sql[0]


# ------------------------------------------------------------ event points
def _finisher(pid, school, secs, place):
    return {"person_id": pid, "name": f"R{pid}", "school": school,
            "school_state": "MA", "seconds": secs, "place": place,
            "score_place": place}


def test_event_points_are_tf_points_table():
    fin = [_finisher(1, "Alpha", 260.0, 1),
           _finisher(2, None, 261.0, 2),          # unattached: no points
           _finisher(3, "Beta", 262.0, 3),
           _finisher(4, "Alpha", 263.0, 4)]
    fin += [_finisher(10 + i, "Gamma", 270.0 + i, 5 + i) for i in range(8)]
    teams = P._eventPoints(fin)
    by = {t["team"]: t for t in teams}
    # 10 to Alpha's winner, the unattached runner's 8 skips to Beta, then 6
    assert by["Alpha"]["score"] == 10 + 6
    assert by["Beta"]["score"] == 8
    # Gamma's eight: 5+4+3+2+1, and the rest score nothing
    assert by["Gamma"]["score"] == 15
    assert [t["team"] for t in teams] == ["Alpha", "Gamma", "Beta"]
    assert fin[1]["points"] is None and fin[0]["points"] == "10"
    assert all(r["score_place"] is None for r in fin)
    assert teams[0]["scoring"] == "points"


def test_points_simulation_most_points_wins():
    np = pytest.importorskip("numpy")              # noqa: F841
    import race_sim
    field, preds = [], []
    for i in range(4):          # Alpha: four fast milers
        field.append({"person_id": i, "school": "Alpha"})
        preds.append({"seconds": 255.0 + i, "sigma_pct": 1.0})
    for i in range(4):          # Beta: four slow ones
        field.append({"person_id": 10 + i, "school": "Beta"})
        preds.append({"seconds": 290.0 + i, "sigma_pct": 1.0})
    res = race_sim.simulate(field, preds, draws=400, scoring="points")
    a, b = res["teams"]["Alpha"], res["teams"]["Beta"]
    assert a["p_win"] > 0.95 and b["p_win"] < 0.05
    # every draw pays the whole table out between the two: 39 points
    assert a["score_mean"] + b["score_mean"] == pytest.approx(39.0)
    assert res["h2h"][("Alpha", "Beta")] > 0.95
    assert a["p_incomplete"] == 0.0


# ------------------------------------------------------------ form at distance
def test_form_weights_the_target_distance():
    # newest first: two 400-ish (unrated distance kept as metres) and a 1600
    rows = [("2026-05-03", 130.0, "hs_m", "TF", 800.0, 1),
            ("2026-05-02", 120.0, "hs_m", "TF", 1600.0, 2),
            ("2026-04-20", 120.0, "hs_m", "TF", 1600.0, 3)]
    plain = P._formRating(rows, "TF")[0]
    near = P._formRating(rows, "TF", distance=1600.0)[0]
    # the 800 counts half: the 1600 form is pulled less toward 130
    assert near < plain
    w = [1.0 * 0.5, 0.8, 0.64]
    want = (130 * w[0] + 120 * w[1] + 120 * w[2]) / sum(w)
    assert near == pytest.approx(want)


def test_a_prelim_and_its_final_count_once():
    rows = [("2026-05-03", 125.0, "hs_m", "TF", 1600.0, 7),     # final
            ("2026-05-02", 118.0, "hs_m", "TF", 1600.0, 7),     # prelim
            ("2026-04-20", 121.0, "hs_m", "TF", 1600.0, 3)]
    rating, _sig, _pool, n = P._formRating(rows, "TF", distance=1600.0)
    assert n == 2
    assert rating == pytest.approx((125 * 1.0 + 121 * 0.8) / 1.8)


def test_xc_form_is_unchanged():
    rows = [("2026-10-01", 110.0, "hs_m", "XC"),
            ("2026-09-20", 100.0, "hs_m", "XC")]
    assert P._formRating(rows, "XC")[0] == pytest.approx(
        (110 + 100 * 0.8) / 1.8)


# ------------------------------------------------------------ event squads
class _SquadCur:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


def test_a_track_squad_is_who_runs_the_distance():
    squads = {"Alpha": [{"person_id": 1, "rating": 140.0},      # a sprinter
                        {"person_id": 2, "rating": 120.0},
                        {"person_id": 3, "rating": 118.0}]}
    res = [
        {"pid": 1, "date": "2026-04-10", "speed_rating": 140.0,
         "pool": "hs_m", "meet_id": 1, "event_short": "400m"},
        {"pid": 2, "date": "2026-04-10", "speed_rating": 117.0,
         "pool": "hs_m", "meet_id": 1, "event_short": "1600m"},
        {"pid": 3, "date": "2026-04-10", "speed_rating": 121.0,
         "pool": "hs_m", "meet_id": 1, "event_short": "Boys Mile"},
        {"pid": 3, "date": "2026-04-03", "speed_rating": 110.0,
         "pool": "hs_m", "meet_id": 2, "event_short": "800m"},  # out of band
    ]
    cur = _SquadCur(res)
    got, rest = P._tfEventSquads(cur, squads, 1600.0, 2025)
    # the miler first, by his MILE form, though his season rating is lower
    assert [r["person_id"] for r in got["Alpha"]] == [3, 2]
    assert got["Alpha"][0]["event_form"] == 121.0
    # the sprinter waits under dropped, not deleted
    assert [r["person_id"] for r in rest["Alpha"]] == [1]
    assert "COALESCE(r.is_relay, 0) = 0" in cur.sql[0]


# ------------------------------------------------------------ the routes
class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, **k):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RouteCur:
    """Answers the races route's queries by what they ask for."""

    def __init__(self):
        self.sql = []
        self._next = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "GROUP  BY r.div_id, r.event_id" in sql:
            self._next = _raceRows()
        elif "min(date)" in sql:
            self._next = [{"d": "2026-04-01"}]
        else:
            self._next = [{"meet_name": "Spring Invite", "division": None}]

    def fetchall(self):
        return self._next

    def fetchone(self):
        return (self._next or [None])[0]


def test_the_races_route_lists_events(monkeypatch):
    pytest.importorskip("flask")
    import app as A
    cur = _RouteCur()
    monkeypatch.setattr(A, "getConn", lambda: _Conn(cur))
    monkeypatch.setattr(A, "_predictSource", lambda *a, **k: ("anet", 0))
    r = A.app.test_client().get("/api/predict/races?meet_id=9&sport=TF")
    assert r.status_code == 200, r.data
    races = r.get_json()["races"]
    assert [x["div_id"] for x in races] == ["5-102", "5-103", "6-201",
                                            "6-202", "0-301"]
    # the count is grouped by the event (no join fan-out)
    q = next(s for s in cur.sql if "GROUP  BY r.div_id, r.event_id" in s)
    assert "m.event_id = r.event_id" in q


def test_the_target_splits_the_race_key():
    pytest.importorskip("flask")
    import app as A
    from werkzeug.datastructures import MultiDict
    t, err = A._target(MultiDict({"mode": "rerun", "meet_id": "9",
                                  "sport": "TF", "div_id": "5-102",
                                  "per_team": "2"}))
    assert err is None
    assert (t["div_id"], t["event_id"]) == (5, 102)
    assert t["per_team"] == 2                  # entries: one up is real
    t, _ = A._target(MultiDict({"mode": "rerun", "meet_id": "9",
                                "sport": "XC", "div_id": "5",
                                "per_team": "2"}))
    assert t["div_id"] == "5" and "event_id" not in t
    assert t["per_team"] is None               # XC: seven and under is seven


# ------------------------------------------------------------ last edition
def test_last_editions_same_event_by_distance_and_gender():
    import last_edition as LE
    rows = [{"div_id": 5, "event_id": 900, "event_short": "1600m",
             "division": "Varsity Boys"},
            {"div_id": 6, "event_id": 901, "event_short": "1600m",
             "division": "Varsity Girls"},
            {"div_id": 5, "event_id": 902, "event_short": "1 Mile",
             "division": "Varsity Boys"},          # the mile is not the 1600
            {"div_id": 5, "event_id": 903, "event_short": "800m",
             "division": "Varsity Boys"}]
    cur = _SquadCur(rows)
    got = LE._editionEvents(cur, 77, "anet", [5, 6], 1600.0, "M")
    assert got == {5: [900]}
    assert "COALESCE(r.is_field, 0) = 0" in cur.sql[0]


# ------------------------------------------------------------ the pipeline
def test_tfrrs_event_distances_from_the_name():
    import land_tfrrs_meet_names as L
    got = L.eventDistances(["Men's 1500 Meters", "Women's Mile",
                            "Men's 4x400 Relay", "Women's Shot Put",
                            "Men's 100 Meters", "Men's 3000 Steeplechase"])
    assert got == {"Men's 1500 Meters": 1500.0,
                   "Women's Mile": pytest.approx(1609.344)}
    # only a missing or placeholder distance is ever written
    assert "distance_meters IS NULL OR m.distance_meters <= 0" in L._SET_DISTANCE
