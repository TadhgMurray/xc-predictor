"""The runner's own form trend (joint_solve.formTrendRidge, 2026-10-20).

A per-runner slope over the season, about the pool's curve, centred within
the runner's season and sport. These hold it to its purpose: it recovers
who improved, and when the improvers are the ones who race the late, hard
course (a championship), the course stops absorbing their improvement.

    python -m pytest -q tests/test_form_trend.py
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "engine"))
import joint_solve as js                                        # noqa: E402


def _world(seed=4, n_ath=1500):
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.06, n_ath)
    slope = rng.normal(0, 0.03, n_ath)            # log-time per 100 days
    n_cell, hard = 40, 39                          # cell 39: the late, hard course
    d = rng.normal(0, 0.03, n_cell)
    d[hard] = 0.08
    ath, cel, day = [], [], []
    for i in range(n_ath):
        m = 7
        days = np.sort(rng.uniform(0, 90, m))
        cells = rng.integers(0, n_cell - 1, m)
        # the improvers (the quarter with the most negative slope) race the
        # hard course at the end of the season, like a championship
        if slope[i] < np.quantile(slope, 0.25):
            days[-1], cells[-1] = 95.0, hard
        ath.append(np.full(m, i)); cel.append(cells); day.append(days)
    ath, cel, day = map(np.concatenate, (ath, cel, day))
    mean = np.bincount(ath, weights=day) / np.bincount(ath)
    tz = (day - mean[ath]) / 100.0
    race = cel * 200 + np.round(day).astype(int)
    _, race = np.unique(race, return_inverse=True)
    y = a[ath] + d[cel] + slope[ath] * tz + rng.normal(0, 0.01, ath.size)
    return y, ath, cel, race, tz, slope, d, hard


def _fit(y, ath, cel, race, tz, trend):
    D = js.Design(ath, cel, race, tz=tz if trend else None)
    return js.solveJoint(y, design=D, n_outer=3, tilt=False, robust=False,
                         n_probe=0, ridge_trend=0.1 if trend else 0.0), D


def test_recovers_who_improved():
    y, ath, cel, race, tz, slope, d, hard = _world()
    out, _D = _fit(y, ath, cel, race, tz, trend=True)
    q = np.asarray(out["form_trend"])
    assert np.corrcoef(q, slope)[0, 1] > 0.7


def test_course_stops_absorbing_improvement():
    y, ath, cel, race, tz, slope, d, hard = _world()
    off, _ = _fit(y, ath, cel, race, tz, trend=False)
    on, _ = _fit(y, ath, cel, race, tz, trend=True)

    def err(out):
        dh = np.asarray(out["d"])
        # difficulty is identified up to a level: the hard course against
        # the others' mean
        return abs((dh[hard] - np.delete(dh, hard).mean())
                   - (d[hard] - np.delete(d, hard).mean()))
    assert err(on) < 0.5 * err(off), (err(on), err(off))


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("XCP_FORM_TREND", raising=False)
    assert js.formTrendRidge() == 0.0
    monkeypatch.setenv("XCP_FORM_TREND", "4")
    assert js.formTrendRidge() == 4.0
    monkeypatch.setenv("XCP_FORM_TREND", "junk")
    assert js.formTrendRidge() == 0.0


def test_season_day_centred_within_season_and_sport():
    import run_joint as rj
    cols = {"days": np.array([300, 250, 200, 100, 60, 400, 380.0]),   # days AGO
            "sport": np.array([0, 0, 0, 1, 1, 0, 0]),
            "athlete": np.array([7, 7, 7, 7, 7, 7, 7]),
            "year": np.array([2025, 2025, 2025, 2025, 2025, 2024, 2024])}
    keep = np.ones(7, bool)
    tz = rj.seasonDayCentered(cols, keep, None, None)
    # XC 2025: mean 250 days ago -> the latest race (200 ago) is +0.5
    np.testing.assert_allclose(tz[:3], [-0.5, 0.0, 0.5])
    np.testing.assert_allclose(tz[3:5], [-0.2, 0.2])          # its own sport
    np.testing.assert_allclose(tz[5:], [-0.1, 0.1])           # its own season
    sub = rj.seasonDayCentered(cols, np.array([1, 0, 1, 0, 0, 0, 0], bool), None, None)
    np.testing.assert_allclose(sub, [-0.5, 0.5])   # centred on ALL the season's rows
