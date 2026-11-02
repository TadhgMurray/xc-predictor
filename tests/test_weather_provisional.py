"""Provisional weather from Open-Meteo for the days ERA5 has not reached
(2026-11-02): the same cell snap, units and derivations as the ERA5 rows."""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backfill"))

import weather_provisional as W                                  # noqa: E402


def test_the_snap_is_the_era5_scripts():
    assert W.snap(42.4075, -71.1190) == (42.5, 289.0)
    assert W.snap(37.77, -122.49) == (37.75, 237.5)
    assert W.lon180(289.0) == -71.0


def test_apparent_temperature_is_the_era5_formula():
    # 20 C, 50%, 10 km/h, no sun: the Steadman shade-ish value
    t = W.apparent(20.0, 50.0, 10.0, 0.0)
    e = 0.5 * 6.105 * math.exp(17.27 * 20 / (237.7 + 20))
    assert abs(t - (20 + 0.348 * e - 0.70 * (10 / 3.6) - 4.25)) < 1e-9


def test_rows_carry_era5_units_and_the_flag():
    payload = {"hourly": {"time": ["2026-10-31T15:00"], "temperature_2m": [9.0],
                          "relative_humidity_2m": [80.0], "dew_point_2m": [5.7],
                          "wind_speed_10m": [12.0], "shortwave_radiation": [300.0],
                          "precipitation": [0.4], "surface_pressure": [1012.0],
                          "cloud_cover": [70.0], "wind_direction_10m": [200.0],
                          "wind_gusts_10m": [25.0], "soil_moisture_0_to_1cm": [0.30],
                          "soil_moisture_1_to_3cm": [0.32], "soil_moisture_3_to_9cm": [0.34],
                          "snow_depth": [0.0], "snowfall": [0.0]}}
    (r,) = W.rowsFrom((42.5, 289.0), payload)
    assert r["date"] == "2026-10-31" and r["hour"] == 15 and r["provisional"] is True
    assert abs(r["soil_moisture"] - (0.30 + 2 * 0.32 + 4 * 0.34) / 7) < 1e-9
    assert r["wind_speed_10m"] == 12.0 and r["solar_radiation"] == 300.0
