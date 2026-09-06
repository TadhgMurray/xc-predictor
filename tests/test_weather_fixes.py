"""The weather bundle of 2026-09-06: the sun term, the optimum clamp, the
per-sport temperature aggregate, the day credit default."""
import math
import os
import re
import sys

import numpy as np

_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROOT, "engine"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import normalize_distance as nd                       # noqa: E402
import fit_weather_correction as fw                   # noqa: E402


def _const(path, name):
    src = open(os.path.join(_ROOT, path), encoding="utf-8").read()
    m = re.search(rf"^{name}\s*=\s*([0-9.]+)", src, re.M)
    assert m, f"{name} not in {path}"
    return float(m.group(1))


def test_sun_fraction_is_one_number_in_both_places():
    a = _const("backfill/atmost_era5_zarr.py", "SUN_ABSORBED_FRACTION")
    b = _const("scripts/recompute_apparent_temp.py", "SUN_ABSORBED_FRACTION")
    assert a == b == 0.175


def test_full_sun_adds_single_digits_not_thirty():
    # the fetcher's arithmetic, restated: 25 C, 50% RH, 10 km/h, 900 W/m2
    frac = _const("backfill/atmost_era5_zarr.py", "SUN_ABSORBED_FRACTION")
    t, rh, ws, sun = 25.0, 50.0, 10.0 / 3.6, 900.0
    e = (rh / 100.0) * 6.105 * math.exp((17.27 * t) / (237.7 + t))
    shade = t + 0.348 * e - 0.70 * ws - 4.25
    old = shade + 0.70 * (sun / (ws + 10.0))
    new = shade + 0.70 * (frac * sun / (ws + 10.0))
    assert old - shade > 30, "the old sun term was the bug: tens of degrees"
    assert 3 < new - shade < 10


def test_track_reads_peak_heat_and_xc_the_mean():
    assert fw.WX_AGG_BY_SPORT["TF"]["apparent_temp"] == "max(apparent_temperature)"
    assert fw.WX_AGG_BY_SPORT["XC"]["apparent_temp"] == "avg(apparent_temperature)"
    for sport in ("XC", "TF"):
        assert set(fw.WX_AGG_BY_SPORT[sport]) == set(fw.WX_AGG)


def _art(optimum):
    # a straight line: effect rises 1% per degree F above 55, falls below it
    spline = {"knots": [30.0, 40.0, 55.0, 70.0, 90.0], "coef": [0.01, 0.0, 0.0, 0.0],
              "ref": 55.0}
    if optimum is not None:
        spline["optimum"] = optimum
    return {"reference": {"apparent_temp": 55.0}, "betas": {}, "splines":
            {"apparent_temp": spline}, "linear_features": []}


def test_nothing_colder_than_the_optimum_moves_a_rating(monkeypatch):
    art = _art(optimum=55.0)
    monkeypatch.setattr(nd, "_weatherArtifactFor", lambda sport: art)
    monkeypatch.setattr(nd, "isRaceWeatherPlausible", lambda w: True)
    cold = nd._applyWeather(1000.0, {"apparent_temp": 40.0}, None, "TF")
    mild = nd._applyWeather(1000.0, {"apparent_temp": 55.0}, None, "TF")
    hot = nd._applyWeather(1000.0, {"apparent_temp": 75.0}, None, "TF")
    assert cold == mild == 1000.0
    assert hot < 1000.0
    # and without the optimum the old dock is still there, for an old artifact
    monkeypatch.setattr(nd, "_weatherArtifactFor", lambda sport: _art(None))
    assert nd._applyWeather(1000.0, {"apparent_temp": 40.0}, None, "TF") > 1000.0


def test_spline_optimum_is_the_curve_minimum():
    spline = {"knots": [30.0, 40.0, 55.0, 70.0, 90.0], "coef": [0.01, 0.0, 0.0, 0.0],
              "coef_dist": [0.0] * 4, "ref": 55.0}
    assert abs(fw.splineOptimum(spline) - 30.0) < 0.2


def test_an_unknown_aggregate_is_refused():
    assert nd.weatherTempAgg({"wx_agg": {"apparent_temp": "max(apparent_temperature)"}}) \
        == "max(apparent_temperature)"
    assert nd.weatherTempAgg({}) == "avg(apparent_temperature)"
    try:
        nd.weatherTempAgg({"wx_agg": {"apparent_temp": "pg_sleep(9); --"}})
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown aggregate reached SQL")
    # the backfill reads the grid through it
    src = open(os.path.join(_ROOT, "backfill", "backfill_normalize.py"),
               encoding="utf-8").read()
    assert "weatherTempAgg(art)" in src and "{temp_agg}" in src


def test_day_credit_defaults_to_no_sport():
    src = open(os.path.join(_ROOT, "engine", "run_joint.py"), encoding="utf-8").read()
    assert re.search(r'"--race-effect-sports",\s*default=""', src)


def test_tfrrs_track_meets_reach_the_weather_lookup():
    # 33M college rows had no weather: the backfill sent every tfrrs track
    # row to an empty dict and the fitter read anet's meta table only
    q = fw.tfQuery()
    assert "meets_tfrrs" in q and "tfrrs_meet_geometry" in q and "mm.source = r.source" in q
    assert "mm.location_id IS NOT NULL" in q, "a meet with no venue key stays out of the fit"
    src = open(os.path.join(_ROOT, "backfill", "backfill_normalize.py"), encoding="utf-8").read()
    assert "tfrrs = _loadMeetCellsTfrrsTF(cur)" in src
    assert "if indoor == 1:" in src
