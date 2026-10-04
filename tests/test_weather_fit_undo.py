"""fit_weather_correction reads normalized_time AFTER the last correction was
divided in; it must divide it back out, and key athletes per season
(2026-09-28: refits alternated between the effect and nothing)."""
import os
import sys

import numpy as np

os.environ.setdefault("XCP_DB_PASSWORD", "unused-by-this-test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fit_weather_correction as F                            # noqa: E402
import normalize_distance as nd                               # noqa: E402

ART = {"reference": {"apparent_temp": 13.0, "wind": 3.0, "precip": 0.0,
                     "soil": 0.2, "snow": 0.0},
       "betas": {"wind": 0.0, "precip": 0.004, "snow": 0.0},
       "linear_features": ["wind", "precip", "snow"],
       "splines": {}, "dist_betas": {}}


def _cols(raw_nt, precip):
    n = raw_nt.size
    return {"ath": np.array([f"p{i % 5}" for i in range(n)], dtype=object),
            "course": np.array(["Glendoveer"] * n, dtype=object),
            "event": np.array(["Glendoveer|f24"] * n, dtype=object),
            "date": np.array([f"20{15 + i % 8}-12-0{1 + i % 5}" for i in range(n)], dtype=object),
            "nt": raw_nt.copy(), "dist": np.full(n, 5000.0),
            "apparent_temp": np.full(n, 13.0), "wind": np.full(n, 3.0),
            "precip": precip, "soil": np.full(n, 0.2), "snow": np.zeros(n)}


def test_undo_recovers_the_times_before_the_correction():
    raw = np.array([960.0, 975.0, 990.0, 1001.0])
    precip = np.array([0.0, 5.0, 12.0, 20.0])
    cols = _cols(raw, precip)
    saved = nd._WEATHER.get("XC")
    nd._WEATHER["XC"] = ART
    try:                                  # what the backfill wrote
        cols["nt"] = np.array([nd._applyWeather(t, {"apparent_temp": 13.0, "wind": 3.0,
                                                    "precip": p, "soil": 0.2, "snow": 0.0,
                                                    "doy": 340}, "Glendoveer", "XC", 5000.0)
                               for t, p in zip(raw, precip)])
    finally:
        nd._WEATHER["XC"] = saved
    assert cols["nt"][3] < raw[3], "a wet race was credited: its stored time is faster"
    out = F.undoAppliedWeather(cols, "XC", ART, "test")
    assert np.allclose(out["nt"], raw), out["nt"]
    assert nd._WEATHER.get("XC") is saved, "the module's own artifact is restored"


def test_nothing_to_undo_without_an_artifact():
    cols = _cols(np.array([900.0, 910.0]), np.array([0.0, 3.0]))
    assert F.undoAppliedWeather(cols, "XC", None, "none")["nt"].tolist() == [900.0, 910.0]


def test_athlete_is_keyed_per_season():
    cols = _cols(np.array([900.0, 910.0]), np.array([0.0, 3.0]))
    cols["ath"] = np.array(["7", "7"], dtype=object)
    cols["date"] = np.array(["2023-12-02", "2024-12-07"], dtype=object)
    a = F.athleteSeason(cols)
    assert a[0] != a[1], "one runner, two seasons: two athlete effects"
    src = open(os.path.join(_ROOT, "engine", "fit_weather_correction.py")).read()
    assert src.count('_denseCode(cols["ath"])') == 0
    assert "undoAppliedWeather(cols, args.sport" in src


def test_backfill_records_the_applied_artifact():
    src = open(os.path.join(_ROOT, "backfill", "backfill_normalize.py")).read()
    assert "recordAppliedWeather(sport)" in src
    assert "if args.apply and not args.limit and not args.only_changed and not args.new_only:" in src
