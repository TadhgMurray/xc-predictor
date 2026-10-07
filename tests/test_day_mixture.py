# Project: xc-predictor / tests
# File:    test_day_mixture.py
# Purpose: the heavy-tailed day prior (joint_solve.dayMixtureFit /
#          dayMixturePosterior) finds heavy tails when they are there, estimates
#          days better than the one-normal (ridge) shrinkage there -- most of
#          all on ORDINARY days, which is what the robust scatter measures --
#          and is no worse when days really are normal.
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
import joint_solve as js                                        # noqa: E402


def _days(seed, heavy, n=20000):
    rng = np.random.default_rng(seed)
    if heavy:
        big = rng.random(n) < 0.08                      # mud, heat, long courses
        u = np.where(big, rng.normal(0, 0.04, n), rng.normal(0, 0.004, n))
    else:
        u = rng.normal(0, 0.012, n)
    field = rng.integers(8, 300, n)                     # runners in the race
    v = (0.04 ** 2) / field                             # 4% per-runner noise
    m = u + rng.normal(0, np.sqrt(v))
    return u, m, v


def _ridge(m, v, t):
    return t / (t + v) * m


def test_heavy_tails_are_found_and_ordinary_days_are_estimated_better():
    u, m, v = _days(1, heavy=True)
    fit = js.dayMixtureFit(m, v)
    assert fit["ll_mix"] > fit["ll_one"] + 100          # far more likely
    assert fit["pi"] > 0.8 and fit["t2"] > 20 * fit["t1"]
    mix = js.dayMixturePosterior(m, v, fit)
    rid = _ridge(m, v, fit["t_one"])
    err_mix, err_rid = np.abs(mix - u), np.abs(rid - u)
    ordinary = np.abs(u) < 0.008
    assert np.median(err_mix[ordinary]) < 0.8 * np.median(err_rid[ordinary])
    assert np.mean((mix - u) ** 2) < np.mean((rid - u) ** 2)


def test_normal_days_cost_nothing():
    u, m, v = _days(2, heavy=False)
    fit = js.dayMixtureFit(m, v)
    mix = js.dayMixturePosterior(m, v, fit)
    rid = _ridge(m, v, fit["t_one"])
    assert np.mean((mix - u) ** 2) <= 1.02 * np.mean((rid - u) ** 2)
    assert fit["ll_mix"] - fit["ll_one"] < 10           # no tail to find


def test_no_estimate_is_no_day():
    out = js.dayMixturePosterior(np.array([0.01, 0.02]), np.array([0.0, np.nan]),
                                 {"pi": 0.9, "t1": 1e-5, "t2": 1e-3})
    assert (out == 0).all()
    assert js.dayMixtureFit(np.zeros(10), np.ones(10)) is None


def test_the_consistency_report_prints_the_heavy_tailed_line(capsys):
    import joint_golive as jg
    rng = np.random.default_rng(3)
    n = 3000
    D = types.SimpleNamespace(athlete=np.repeat(np.arange(600), 5))
    y = rng.normal(0, 0.03, n)
    out = {"h": np.ones(n), "race_effect_row_mix": rng.normal(0, 0.002, n)}
    jg.dayConsistencyReport(y, np.zeros(n), out, D, rng.normal(0, 0.01, n),
                            rng.normal(0, 0.01, n), np.ones(n, dtype=bool),
                            np.zeros(n, dtype=bool), np.zeros(n, dtype=int))
    text = capsys.readouterr().out
    assert "day in, heavy-tailed" in text and "day out" in text
