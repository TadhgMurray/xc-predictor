"""The forecast as the weather correction reads it (forecast.daySummary,
raceDayWeather, weatherEffect) and predict.forecastWeather, which moves a
field's predicted times by it (owner, 2026-11-02)."""
import io
import json
import os
import sys
from datetime import date, datetime, timezone

_ROOT = os.path.join(os.path.dirname(__file__), "..")
for _p in ("racecast", "engine"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import forecast as fc                                            # noqa: E402


def _rows(day, temp_by_hour):
    return [{"date": day, "hour": h, "apparent_temperature": t,
             "wind_speed_10m": 12.0, "precipitation": 0.5, "soil_moisture": 0.3,
             "snow_depth": 0.0, "snowfall": 0.0} for h, t in temp_by_hour.items()]


def test_day_summary_reads_the_local_window():
    # Boston, lon -71: local = UTC - 5, so local 8..12 is UTC 13..17
    temps = {h: (30.0 if 13 <= h <= 17 else 0.0) for h in range(24)}
    wx = fc.daySummary(_rows("2026-11-07", temps), -71.0, "2026-11-07", (8, 12))
    assert wx["apparent_temp"] == 30.0 and wx["hours"] == 5
    assert abs(wx["precip"] - 2.5) < 1e-9            # summed, as the fitter does
    assert wx["wind"] == 12.0 and wx["soil"] == 0.3 and wx["snow"] == 0.0
    assert wx["doy"] == date(2026, 11, 7).timetuple().tm_yday


def test_day_summary_needs_enough_of_the_window():
    assert fc.daySummary(_rows("2026-11-07", {13: 10.0, 14: 10.0}), -71.0,
                         "2026-11-07", (8, 12)) is None


def test_race_day_weather_horizon_and_fetch(monkeypatch):
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(fc, "_raceWindow", lambda sport: (8, 12))
    fc._mem.clear()
    assert fc.raceDayWeather(42.3, -71.0, "2026-11-01", now=now) is None   # past
    assert fc.raceDayWeather(42.3, -71.0, "2026-11-30", now=now) is None   # > 16 days
    assert fc.raceDayWeather(None, -71.0, "2026-11-07", now=now) is None

    calls = []
    day = "2026-11-07"
    payload = {"hourly": {"time": [f"{day}T{h:02d}:00" for h in range(24)],
                          "temperature_2m": [20.0] * 24, "relative_humidity_2m": [50.0] * 24,
                          "wind_speed_10m": [10.0] * 24, "shortwave_radiation": [0.0] * 24,
                          "precipitation": [0.0] * 24}}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        calls.append(req.full_url)
        return _Resp(json.dumps(payload).encode())
    monkeypatch.setattr(fc.urllib.request, "urlopen", fake_open)
    fc._mem.clear()
    wx = fc.raceDayWeather(42.36, -71.06, day, now=now)
    assert wx and wx["hours"] == 5 and wx["kind"] == "forecast_day"
    assert "timezone=GMT" in calls[0]
    fc.raceDayWeather(42.36, -71.06, day, now=now)
    assert len(calls) == 1                                       # cached


def test_network_failure_is_none(monkeypatch):
    monkeypatch.setattr(fc, "_raceWindow", lambda sport: (8, 12))

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
    fc._mem.clear()
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert fc.raceDayWeather(40.0, -80.0, "2026-11-05", now=now) is None


def test_effect_is_one_without_weather():
    assert fc.weatherEffect(None, "Course", "XC", 5000) == 1.0


def test_describe_day():
    assert fc.describeDay({"apparent_temp": 23.3, "wind": 16.1, "precip": 2.54}) \
        == "feels 74°F, wind 10 mph, 0.10 in rain"
    assert fc.describeDay(None) is None


def test_forecast_weather_moves_times_not_ratings(monkeypatch):
    import predict
    monkeypatch.setattr(predict, "forecastFactor",
                        lambda cur, spec: (1.02, {"apparent_temp": 28.0}))
    preds = [{"seconds": 1000.0, "lo": 980.0, "hi": 1020.0, "is_race_time": True,
              "time_rating": 120.0},
             {"seconds": None, "reason": "none"},
             {"seconds": 900.0, "is_race_time": False}]
    predict.forecastWeather(None, preds, {"is_xc": True})
    assert preds[0]["seconds"] == 1020.0 and preds[0]["seconds_typical"] == 1000.0
    assert preds[0]["lo"] == 999.6 and preds[0]["hi"] == 1040.4
    assert preds[0]["time_rating"] == 120.0 and preds[0]["weather_pct"] == 2.0
    assert preds[1]["seconds"] is None and "weather_pct" not in preds[1]
    assert preds[2]["seconds"] == 900.0                  # not on the race clock


def test_forecast_course_key():
    import predict
    assert predict.forecastCourse({"is_xc": True, "source": "tfrrs", "course_name": "X"}) is None
    assert predict.forecastCourse({"is_xc": True, "source": "anet", "course_name": "X"}) == "X"


def test_track_key_and_indoor():
    import predict
    assert predict.forecastCourse({"is_xc": False, "location_id": 812}) == "TF:loc:812:out"
    assert predict.forecastCourse({"is_xc": False, "location_id": 0}) is None
    assert predict.forecastFactor(None, {"is_xc": False, "is_indoor": 1,
                                         "distance_meters": 1600}) == (1.0, None)
    assert predict.forecastFactor(None, {"is_xc": False, "distance_meters": None}) == (1.0, None)


def test_track_times_to_the_hundredth(monkeypatch):
    import predict
    monkeypatch.setattr(predict, "forecastFactor", lambda cur, spec: (1.01, {"apparent_temp": 30.0}))
    preds = [{"seconds": 61.23, "is_race_time": True}]
    predict.forecastWeather(None, preds, {"is_xc": False})
    assert preds[0]["seconds"] == 61.84


def test_a_miss_is_not_refetched_at_once(monkeypatch):
    monkeypatch.setattr(fc, "_raceWindow", lambda sport: (8, 12))
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("down")
    monkeypatch.setattr(fc.urllib.request, "urlopen", boom)
    fc._mem.clear()
    now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert fc.raceDayWeather(41.0, -81.0, "2026-11-05", now=now) is None
    assert fc.raceDayWeather(41.0, -81.0, "2026-11-05", now=now) is None
    assert len(calls) == 1
