"""/api/explain: one result taken apart into the engine's terms (owner,
2026-10-04: Hammerand's WashU 10k got +7.03% for afternoon heat on a night
race and nobody could see why). The chain must land on the stored rating,
say "not available" for a missing piece instead of failing, and aggregate
the grid the way the backfill did. No database: a fake cursor, the distance
pickles on disk, the conversions scale stubbed.

    XCP_DB_PASSWORD=x python -m pytest -q tests/test_explain_rating.py
"""
import math
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("racecast", "engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))

import explain_rating as ex                                    # noqa: E402


# ------------------------------------------------------------------ #
#  pure pieces
# ------------------------------------------------------------------ #

def test_clock_and_words():
    assert ex.clock(1760.48) == "29:20.48"
    assert ex.clock(862.1, 1) == "14:22.1"
    assert ex.clock(3723.4, 1) == "1:02:03.4"
    assert ex.clock(None) is None and ex.clock(0) is None
    assert ex.hourWords(15) == "3pm" and ex.hourWords(0) == "12am"
    assert ex.hourWords(12) == "12pm" and ex.hourWords(9) == "9am"
    assert ex.tempWords(33) == "33°C (91°F)"


def test_snap_matches_the_backfill():
    # backfill_normalize._snapCell: snap, THEN wrap into 0..360
    assert ex.snapCell(38.65, -90.31) == (38.75, 269.75)
    assert ex.snapCell(None, 1.0) is None


def _day(temps, wind=10.0):
    return [{"local_hour": h, "apparent_temperature": t, "wind_speed_10m": wind,
             "precipitation": 0.1 if h == 14 else 0.0, "soil_moisture": 0.3,
             "snow_depth": 0.0, "snowfall": 0.0} for h, t in temps]


def test_track_reads_the_peak_and_xc_the_mean():
    rows = _day([(h, 20.0 + (13.0 if h == 15 else 0.0)) for h in range(24)])
    wx, s = ex.aggregateWeather(rows, (9, 20), "max(apparent_temperature)")
    assert wx["apparent_temp"] == 33.0 and s["peak_hour"] == 15
    assert s["temp_agg"] == "max" and abs(s["temp_avg"] - (20 + 13 / 12)) < 0.05
    assert abs(wx["precip"] - 0.1) < 1e-12 and wx["wind"] == 10.0
    assert s["n_hours"] == 12 and len(s["hourly"]) == 24       # the whole day shown
    wx2, s2 = ex.aggregateWeather(rows, (8, 12), "avg(apparent_temperature)")
    assert wx2["apparent_temp"] == 20.0 and s2["temp_agg"] == "avg"
    # nothing in the window -> no weather, not a zero
    assert ex.aggregateWeather(_day([(2, 5.0)]), (9, 20)) == (None, None)


def test_day_modes_and_the_applied_term():
    assert ex.parseDayModes(["XC", "tf:fast", "", "road"]) == {"XC": "all", "TF": "fast"}
    assert ex.appliedDay(0.03, None) == 0.0                  # the sport is out
    assert ex.appliedDay(0.03, "all") == 0.03
    assert ex.appliedDay(0.25, "all") == ex.RACE_DAY_CAP     # capped
    assert ex.appliedDay(0.03, "fast") == 0.0                # a slow day, fast-only
    assert ex.appliedDay(-0.02, "fast") == -0.02


def _terms(**kw):
    t = dict(sport="TF", time=1760.48, distance_m=10000.0, ref_distance=5000.0,
             pool="college_m", rating=126.3, pm=1100.0, f_dist=0.49,
             era=0.004, geom=0.0, season=2026, w_stored=math.log(1.0703),
             w_live=math.log(1.0703),
             w_detail={"temp_used": 33.0, "temp_agg": "max", "hours": [9, 20],
                       "peak_hour": 15, "normal_temp": 18.0, "wind_kmh": 9.0},
             difficulty=-0.012, course_source="era", venue="This track",
             tilt=0.93, shift=-0.025, day_u=0.012, day_n=31, day_mode=None,
             offset=0.006, gain=-0.004)
    t.update(kw)
    return t


def test_the_steps_land_on_the_stored_rating():
    body = ex.buildChain(_terms())
    steps = {s["key"]: s for s in body["steps"]}
    run = body["start_rating"]
    for s in body["steps"]:
        if s.get("pts") is not None:
            run += s["pts"]
    assert abs(run - 126.3) < 0.02, run
    # the weather credit is the owner's +7.03%, in points too
    assert steps["weather"]["pct"] == 7.03
    assert steps["weather"]["pts"] > 7
    assert "peak" in steps["weather"]["text"] and "3pm" in steps["weather"]["text"]
    assert "evening" in steps["weather"]["text"]
    # the race-day term is shown but NOT applied when the sport is out
    assert steps["day"]["applied"] is False and steps["day"]["pct"] == 0.0
    assert "do not carry" in steps["day"]["text"]
    # a fast track lowers it; the tilt is said
    assert steps["course"]["pct"] < 0 and "0.93x" in steps["course"]["text"]
    assert steps["era"]["pct"] < 0
    assert steps["event"]["pct"] > 0 and steps["gain"]["pct"] < 0


def test_an_applied_day_moves_the_rating():
    body = ex.buildChain(_terms(day_mode="all"))
    day = next(s for s in body["steps"] if s["key"] == "day")
    assert day["applied"] is True
    assert abs(day["pct"] - 100 * math.expm1(0.93 * 0.012)) < 0.01
    assert "credited" in day["text"]


def test_missing_pieces_say_so_and_never_raise():
    body = ex.buildChain({"sport": "XC", "rating": 120.0})
    by = {s["key"]: s for s in body["steps"]}
    assert by["distance"]["available"] is False
    assert by["weather"]["available"] is False
    assert by["course"]["available"] is False
    assert by["day"]["available"] is False
    assert "geometry" not in by and "event" not in by           # track only
    assert body["start_rating"] is None
    # no rating at all: still a body
    assert "no rating" in " ".join(ex.buildChain({"sport": "TF"})["notes"])


def test_a_stale_weather_term_is_flagged():
    body = ex.buildChain(_terms(w_live=math.log(1.01)))
    w = next(s for s in body["steps"] if s["key"] == "weather")
    assert w["pct"] == 7.03                    # the row's own, which the rating used
    assert w["detail"]["model_pct"] == 1.0
    assert "latest weather refit" in w["text"]


def test_the_cache_expires():
    now = [0.0]
    c = ex.TtlCache(ttl=10, cap=2, clock=lambda: now[0])
    c.put("a", 1)
    assert c.get("a") == 1
    now[0] = 11
    assert c.get("a") is None
    c.put("a", 1); now[0] = 12; c.put("b", 2); now[0] = 13; c.put("c", 3)
    assert c.get("a") is None and c.get("c") == 3          # oldest out at the cap


# ------------------------------------------------------------------ #
#  end to end on a fake cursor
# ------------------------------------------------------------------ #

class Cur:
    """Answers by what the SQL reads. `fail` makes the full row query raise
    (a table this database lacks)."""

    def __init__(self, row, hours=None, day=None, fail=False):
        self.row, self.hours, self.day, self.fail = row, hours or [], day, fail
        self.rows, self.sql = [], []
        self.connection = types.SimpleNamespace(rollback=lambda: None)

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "weather_grid" in sql:
            self.rows = self.hours
        elif "race_day_effect" in sql:
            self.rows = [self.day] if self.day else []
        elif "FROM   results" in sql or "FROM results" in sql:
            if self.fail and "LATERAL" in sql or self.fail and "course_canonical" in sql:
                raise RuntimeError("relation does not exist")
            self.rows = [self.row] if self.row else []
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


FRAG = {"tfrrs_join": "", "dov_join": "", "champ_join": "", "xc_course": "m.course_name",
        "xc_distance": "m.distance", "blob": "NULL", "rating_pool": "r.rating_pool"}


class CvStub:
    """conversions, without a database."""
    def engineScale(self, pool, sport):
        return (1100.0, -0.03, -0.025)

    def pool_mean(self, pool, sport=None):
        return 1100.0

    def _tilt(self, rating, pool=None):
        return 1.0 + (-0.031) * (min(max(float(rating), 40), 200) - 100) / 10

    def distance_offset(self, pool, sport, d, rating=None):
        return 0.006 if sport == "TF" else 0.0

    def sport_gain(self, pool, sport, rating):
        return -0.004 if sport == "TF" else 0.0


class NdStub:
    """normalize_distance's surface, with a weather model that credits heat."""
    def factorForTime(self, t, d, pool, season=None, tl=None, tt=None, sport=None, ev=None):
        f = (5000.0 / d) ** 1.06
        if season:
            f *= math.exp(0.004)
        if tl and tl != 400:
            f *= 0.99
        return f

    def targetFor(self, pool, sport=None):
        return 5000.0

    def metersFromDistance(self, d):
        return float(d) if d else None

    def _weatherArtifactFor(self, sport):
        return {"race_local_hours": (9, 20)}

    def weatherTempAgg(self, art):
        return "max(apparent_temperature)"

    def isRaceWeatherPlausible(self, wx):
        return True

    def _weatherReference(self, art, course, doy):
        return {"apparent_temp": 18.0}

    def _applyWeather(self, x, wx, course, sport, distance_m=None):
        return x / 1.0703 if wx["apparent_temp"] > 30 else x


def _washu(**kw):
    t = 1760.48
    f_full = NdStub().factorForTime(t, 10000.0, "college_m", 2026)
    row = {"result_id": 277459367, "source": "tfrrs", "meet_id": 1, "div_id": None,
           "event_id": None, "date": "2026-03-26", "time_seconds": t,
           "normalized_time": round(t * f_full / 1.0703, 2), "speed_rating": 126.3,
           "rating_pool": "college_m|TF", "rr_pool": "college_m", "event_short": "10-km",
           "distance": None, "meet_name": "WashU Distance Carnival", "venue": None,
           "lat": 38.65, "lon": -90.31, "wx_course": "TF:loc:77:out",
           "location_id": 77, "is_indoor": 0, "track_type": None, "track_length": None,
           "cell_difficulty": -0.01, "champ_difficulty": None, "dist_corrected": False}
    row.update(kw)
    return row


def test_end_to_end_track():
    hours = _day([(h, 22.0 + (11.0 if h == 15 else 0.0)) for h in range(24)])
    cur = Cur(_washu(), hours, {"day_effect": 0.012, "n_rows": 31, "course_effect": -0.012})
    body = ex.explainResult(cur, "tf", 277459367, FRAG, ce_col="course_effect",
                            has_day_table=True, modes={}, nd=NdStub(), cv=CvStub())
    assert body["ok"] and body["sport"] == "TF"
    assert body["race"]["distance_m"] == 10000.0            # from the event name
    assert body["race"]["time"] == "29:20.48"
    by = {s["key"]: s for s in body["steps"]}
    assert abs(by["weather"]["pct"] - 7.03) < 0.02
    assert by["weather"]["detail"]["temp_max"] == 33.0
    assert by["weather"]["detail"]["peak_hour"] == 15
    assert by["course"]["detail"]["source"] == "era"         # the race's own era
    assert abs(by["course"]["detail"]["difficulty"] + 0.012) < 1e-9
    assert by["day"]["applied"] is False
    run = body["start_rating"] + sum(s["pts"] for s in body["steps"] if s.get("pts"))
    assert abs(run - 126.3) < 0.02


def test_end_to_end_indoor_and_no_gps():
    cur = Cur(_washu(is_indoor=1, normalized_time=None), [], None)
    body = ex.explainResult(cur, "TF", 1, FRAG, nd=NdStub(), cv=CvStub(), modes={})
    w = next(s for s in body["steps"] if s["key"] == "weather")
    assert w["available"] is False and "indoor" in w["text"]
    cur = Cur(_washu(lat=None, lon=None, normalized_time=None), [], None)
    body = ex.explainResult(cur, "TF", 1, FRAG, nd=NdStub(), cv=CvStub(), modes={})
    w = next(s for s in body["steps"] if s["key"] == "weather")
    assert "GPS" in w["text"]


def test_a_failing_join_falls_back_to_the_bare_row():
    cur = Cur(_washu(), [], None, fail=True)
    body = ex.explainResult(cur, "TF", 1, FRAG, nd=NdStub(), cv=CvStub(), modes={})
    assert body["ok"] and body["notes"]
    assert ex.explainResult(Cur(None), "XC", 2, FRAG, nd=NdStub(), cv=CvStub(),
                            modes={}) is None


def test_xc_hand_corrected_distance_carries_no_course():
    row = _washu(event_short=None, distance=5000, dist_corrected=True,
                 venue="Oxbow Park", rating_pool="hs_m|XC")
    cur = Cur(row, [], None)
    body = ex.explainResult(cur, "XC", 3, FRAG, nd=NdStub(), cv=CvStub(), modes={"XC": "all"})
    c = next(s for s in body["steps"] if s["key"] == "course")
    assert c["pct"] == 0.0 and "corrected" in c["text"]


def test_the_route_is_registered_and_signed():
    src = open(os.path.join(ROOT, "racecast", "app.py"), encoding="utf-8").read()
    assert '"/api/explain/<sport>/<int(signed=True):result_id>"' in src
    body = src[src.index("def api_explain"):]
    body = body[:body.index("\n@app.route")]
    assert "no-store" in body and "500" not in body


# ------------------------------------------------------------------ #
#  the route, through Flask, on the fake cursor
# ------------------------------------------------------------------ #

class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self, cursor_factory=None):
        cur = self.cur

        class _Ctx:
            def __enter__(self_inner):
                return cur

            def __exit__(self_inner, *a):
                return False
        return _Ctx()


def test_the_route_answers_and_caches(monkeypatch):
    import pytest
    app = pytest.importorskip("app")
    calls = []
    hours = _day([(h, 22.0 + (11.0 if h == 15 else 0.0)) for h in range(24)])

    def fake_conn():
        calls.append(1)
        return _Conn(Cur(_washu(), hours, None))
    monkeypatch.setattr(app, "getConn", fake_conn)
    app._EXPLAIN_CACHE.clear()
    c = app.app.test_client()
    r = c.get("/api/explain/tf/-277459367")             # tfrrs ids are negative
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] and body["race"]["time"] == "29:20.48"
    assert {s["key"] for s in body["steps"]} >= {"distance", "weather", "course", "day"}
    n = len(calls)
    assert c.get("/api/explain/tf/-277459367").status_code == 200
    assert len(calls) == n                               # served from the cache
    assert c.get("/api/explain/road/1").status_code == 400


def test_the_route_never_500s(monkeypatch):
    import pytest
    app = pytest.importorskip("app")

    def broken():
        raise RuntimeError("database is down")
    monkeypatch.setattr(app, "getConn", broken)
    app._EXPLAIN_CACHE.clear()
    r = app.app.test_client().get("/api/explain/xc/5")
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert r.headers["Cache-Control"] == "no-store"
    monkeypatch.setattr(app, "getConn", lambda: _Conn(Cur(None)))
    r = app.app.test_client().get("/api/explain/xc/6")
    assert r.status_code == 404 and r.headers["Cache-Control"] == "no-store"
