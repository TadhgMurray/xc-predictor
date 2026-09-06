"""forecast.py: the venue normal, the Open-Meteo forecast, the words."""
import io
import json
import os
import re
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "racecast"))
import forecast as fc                                            # noqa: E402

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_race_hours_mirror_the_model():
    src = open(os.path.join(_ROOT, "model", "feature_extraction.py"), encoding="utf-8").read()
    xc = int(re.search(r"^XC_DEFAULT_HOUR\s*=\s*(\d+)", src, re.M).group(1))
    tf = int(re.search(r"^TF_DEFAULT_HOUR\s*=\s*(\d+)", src, re.M).group(1))
    assert fc.RACE_HOUR == {"XC": xc, "TF": tf}
    assert fc.raceHour("xc") == xc and fc.raceHour(None) == xc


def _canned(day, hour, temp=24.0):
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    n = 24
    return {"timezone": "America/Los_Angeles", "hourly": {
        "time": times,
        "temperature_2m": [temp] * n, "dew_point_2m": [12.0] * n,
        "relative_humidity_2m": [50.0] * n, "apparent_temperature": [27.0] * n,
        "precipitation": [0.0] * (hour) + [2.5] + [0.0] * (n - hour - 1),
        "surface_pressure": [1012.0] * n, "cloud_cover": [90.0] * n,
        "wind_speed_10m": [14.0] * n, "wind_direction_10m": [200.0] * n}}


def test_forecast_reads_the_race_hour_and_caches(monkeypatch):
    today = date(2026, 9, 6)
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    calls = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        calls.append(req.full_url)
        return _Resp(json.dumps(_canned("2026-09-12", 9)).encode())
    monkeypatch.setattr(fc.urllib.request, "urlopen", fake_open)
    fc._mem.clear()
    row = fc.forecastAt(47.04, -122.9, "2026-09-12", 9, now=now)
    assert row["temp_c"] == 24.0 and row["precipitation_mm"] == 2.5
    assert row["wind_speed_kmh"] == 14.0 and row["hour_local"] == 9
    assert row["kind"] == "forecast" and row["source"] == "open-meteo"
    assert "start_date=2026-09-12" in calls[0] and "timezone=auto" in calls[0]
    # a second ask is the cache, not a call
    fc.forecastAt(47.04, -122.9, "2026-09-12", 9, now=now)
    assert len(calls) == 1
    # out of reach: the past and beyond the horizon
    assert fc.forecastAt(47.04, -122.9, "2026-09-05", 9, now=now) is None
    assert fc.forecastAt(47.04, -122.9, "2026-10-01", 9, now=now) is None
    assert len(calls) == 1
    # a venue with no coordinates
    assert fc.forecastAt(None, None, "2026-09-12", 9, now=now) is None


def test_network_failure_is_none_not_an_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no route")
    monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
    fc._mem.clear()
    assert fc.forecastAt(47.0, -122.9, "2026-09-12", 9,
                         now=datetime(2026, 9, 6, tzinfo=timezone.utc)) is None


def test_words():
    row = {"temp_c": 24.0, "apparent_temp_c": 27.0, "wind_speed_kmh": 14.0,
           "precipitation_mm": 2.5, "cloud_cover": 90.0}
    assert fc.describe(row) == "24°C, feels 27, wind 14 km/h, 2.5 mm rain, overcast"
    assert fc.describe({"temp_c": 12.0, "cloud_cover": 10.0}) == "12°C, sunny"
    assert fc.describe(None) is None


def test_normal_is_the_cell_at_the_hour_across_years():
    class Cur:
        def __init__(self):
            self.sql = None
            self.connection = type("C", (), {"rollback": lambda self: None})()

        def execute(self, sql, params):
            self.sql, self.params = sql, params

        def fetchone(self):
            return (14.0, 8.0, 70.0, 13.0, 0.1, 1010.0, 60.0, 9.0, 120)
    cur = Cur()
    fc._mem.clear()
    row = fc.normalAt(cur, 47.04, -122.9, "2026-10-31", 9)
    assert row["temp_c"] == 14.0 and row["n_hours"] == 120 and row["kind"] == "normal"
    assert cur.params[:3] == (47.0, 237.0, 9)          # the cell, wrapped, and the local hour
    assert "extract(doy FROM date)" in cur.sql
