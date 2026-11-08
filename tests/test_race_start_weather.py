"""Each race's own start time, for its own weather (owner, 2026-11-08: "when
each race starts so we can get better weather ... specific for each race in
a meet")."""
import datetime as dt
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("", "scripts", "engine", "backfill"):
    sys.path.insert(0, os.path.join(_ROOT, _p))

import types  # noqa: E402

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
os.environ.setdefault("XCP_DB_QUIET", "1")
if "corrections" not in sys.modules:            # 165 MB, not in git
    _c = types.ModuleType("corrections")
    _c.__getattr__ = lambda name: (
        {} if not name.startswith("__") else getattr(object(), name))
    sys.modules["corrections"] = _c
_stub = sys.modules.get("database")
if _stub is not None and not hasattr(_stub, "dbJobs"):
    del sys.modules["database"]
import backfill_normalize as BN  # noqa: E402
if _stub is not None:
    sys.modules["database"] = _stub
from sources import athleticlive as A  # noqa: E402

UTC = dt.timezone.utc


def test_the_feeds_start_is_read_as_utc():
    assert A.startUtc({"start": "2026-10-24T14:20:00Z"}) == dt.datetime(2026, 10, 24, 14, 20, tzinfo=UTC)
    assert A.startUtc({"start": "2026-10-24T09:20:00-05:00"}) == dt.datetime(2026, 10, 24, 14, 20, tzinfo=UTC)
    # a date alone, or nothing, is no start
    assert A.startUtc({"start": "2026-10-24"}) is None
    assert A.startUtc({"start": None}) is None
    assert A.startUtc({}) is None


def test_every_xc_race_of_a_meet_keeps_its_own_start():
    from sources import athleticlive_xc as X
    events = [{"id": 11, "kind": "ind", "xc": True, "name": "Varsity Boys 5K", "ab": "5K", "field": False, "round": None, "division": "", "gender": "M",
               "start": "2026-10-24T13:00:00Z"},
              {"id": 12, "kind": "ind", "xc": True, "name": "Varsity Girls 5K", "ab": "5K", "field": False, "round": None, "division": "", "gender": "M",
               "start": "2026-10-24T13:45:00Z"}]
    row = {"i": 1, "m": "16:00.0", "im": 960000, "p": 1,
           "a": {"ani": 5, "fn": "A", "l": "B", "y": 11, "g": "male", "t": {"n": "X"}}}
    other = dict(row, i=2, im=1080000, a=dict(row["a"], ani=6, fn="C"))
    blobs = {11: {"r": [row]}, 12: {"r": [other]}}
    p = X.plan(999, {"meet_date": "2026-10-24"}, events, blobs)
    starts = {d: t for d, _e, t in p["starts"]}
    assert starts[A.DIV_BASE + 11] == dt.datetime(2026, 10, 24, 13, 0, tzinfo=UTC)
    assert starts[A.DIV_BASE + 12] == dt.datetime(2026, 10, 24, 13, 45, tzinfo=UTC)
    assert all(e == 0 for _d, e, _t in p["starts"])


def _hourly():
    # a cell at lon -90 (local = UTC - 6): 9am local is 15 UTC
    return {("2026-10-24", h): (5.0 + h, 3.0, 0.2, 0.3, 0.0, 0.0) for h in range(24)}


def test_a_race_reads_its_start_hour_and_the_next():
    w = BN.raceWindowWeather(_hourly(), dt.datetime(2026, 10, 24, 15, 40, tzinfo=UTC), -90 % 360,
                             "avg(apparent_temperature)", 5)
    temp, wind, precip, soil, snow = w
    assert temp == pytest.approx((20.0 + 21.0) / 2)      # 15 and 16 UTC
    assert wind == pytest.approx(3.0)
    assert precip == pytest.approx(0.2 * 5)               # the hourly rate on the window's 5 hours
    # track's aggregate is the peak
    w = BN.raceWindowWeather(_hourly(), dt.datetime(2026, 10, 24, 15, 0, tzinfo=UTC), -90 % 360,
                             "max(apparent_temperature)", 12)
    assert w[0] == pytest.approx(21.0)


def test_a_midnight_utc_start_is_not_a_start():
    # 00:00 UTC is 18:00 local at lon -90: a real evening start
    assert BN.raceWindowWeather(_hourly(), dt.datetime(2026, 10, 24, 0, 0, tzinfo=UTC), -90 % 360,
                                "avg(apparent_temperature)", 5) is not None
    # 06:00 UTC is midnight local: not a race start
    assert BN.raceWindowWeather(_hourly(), dt.datetime(2026, 10, 24, 6, 0, tzinfo=UTC), -90 % 360,
                                "avg(apparent_temperature)", 5) is None


def test_the_race_wins_and_the_meet_window_is_the_fallback():
    cells = {7: (40.0, 270.0, "XC:Somewhere")}
    wx = {(40.0, 270.0, "2026-10-24"): (10.0, 2.0, 0.0, 0.3, 0.0)}
    race = {("anet", 7, 701, 0): (18.0, 4.0, 0.0, 0.3, 0.0)}
    idx = BN.WeatherIndex(cells, {}, wx, race)
    day = dt.date(2026, 10, 24)
    w, course = idx.lookup("anet", 7, day, 701, 0)
    assert w["apparent_temp"] == 18.0 and course == "XC:Somewhere"
    w, _ = idx.lookup("anet", 7, day, 702, 0)               # a race with no start
    assert w["apparent_temp"] == 10.0
    w, _ = idx.lookup("anet", 7, day)                       # a caller that names no race
    assert w["apparent_temp"] == 10.0


DSN = os.environ.get("XCP_TWIN_TEST_DSN")


@pytest.mark.skipif(not DSN, reason="needs XCP_TWIN_TEST_DSN")
def test_the_loader_reads_each_race_at_its_own_hours():
    import psycopg2
    c = psycopg2.connect(DSN)
    cur = c.cursor()
    cur.execute("""
        DROP SCHEMA IF EXISTS rsw CASCADE; CREATE SCHEMA rsw; SET search_path = rsw;
        CREATE TABLE weather_grid (cell_lat real, cell_lon real, date date, hour int,
            apparent_temperature real, wind_speed_10m real, precipitation real,
            soil_moisture real, snow_depth real, snowfall real);
        INSERT INTO weather_grid SELECT 40.0, 270.0, '2026-10-24', h, h, 2, 0, 0.3, 0, 0
                                 FROM generate_series(0, 23) h;
    """)
    A.saveStarts(cur, "XC", 7, [(701, 0, dt.datetime(2026, 10, 24, 14, 0, tzinfo=UTC)),
                                (702, 0, dt.datetime(2026, 10, 24, 18, 30, tzinfo=UTC))])
    got = BN._loadRaceWx(cur, "XC", {7: (40.0, 270.0, "XC:x")}, {}, "avg(apparent_temperature)", 5)
    cur.execute("DROP SCHEMA rsw CASCADE")
    c.commit()
    c.close()
    assert got[("anet", 7, 701, 0)][0] == pytest.approx(14.5)   # 14 and 15 UTC
    assert got[("anet", 7, 702, 0)][0] == pytest.approx(18.5)   # the later race, warmer
