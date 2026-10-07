# Project: xc-predictor / tests
# File:    test_distance_holdout.py
# Purpose: the distance holdout (pair_validate.splitByDistance) holds out one
#          whole distance of multi-distance athlete-seasons only, and its
#          report (run_joint.distanceBreakdown, nearestTrainingGap,
#          seasonCurveBreakdown) reads the bias where it is.
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("engine", "scripts"):
    sys.path.insert(0, os.path.join(ROOT, p))
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
import pair_validate as pv                                      # noqa: E402


def test_one_whole_class_of_multi_distance_seasons_only():
    rng = np.random.default_rng(0)
    season = np.repeat(np.arange(4000), 6)
    # half the seasons race one distance, half race two
    cls = np.where(season % 2 == 0, 16, rng.choice([16, 32], season.size))
    held = pv.splitByDistance(season, cls, frac=0.10, seed=1)
    assert held.any()
    for s in np.unique(season[held]):
        m = season == s
        assert len(set(cls[m].tolist())) >= 2                   # a multi-distance season
        assert len(set(cls[m & held].tolist())) == 1            # one class held
        c = cls[m & held][0]
        assert (held[m] == (cls[m] == c)).all()                 # all of that class
    assert not held[season % 2 == 0].any()                      # single-distance: never
    # about 10% of the eligible seasons
    elig = len({s for s in np.unique(season) if len(set(cls[season == s].tolist())) > 1})
    assert 0.06 * elig < len(np.unique(season[held])) < 0.14 * elig


def test_split_for_dispatches_distance():
    m = pv.splitFor("distance", 6, season=np.array([0, 0, 0, 1, 1, 1]),
                    dist_class=np.array([16, 32, 32, 16, 16, 16]), frac=1.0, seed=3)
    assert m[3:].sum() == 0 and m[:3].sum() in (1, 2)
    assert "distance" in pv.HOLDOUT_KINDS


def _rj():
    import importlib
    return importlib.import_module("run_joint")


def test_gap_to_the_nearest_distance_raced():
    rj = _rj()
    gap = rj.nearestTrainingGap(np.array([1, 1, 2, 9]), np.log([3200.0, 1600.0, 800.0, 5000.0]),
                                np.array([1, 1, 2]), np.log([1600.0, 5000.0, 1600.0]))
    assert np.isclose(gap[0], np.log(3200 / 5000) * -1) or np.isclose(gap[0], np.log(2.0))
    assert np.isclose(gap[0], min(abs(np.log(3200 / 1600)), abs(np.log(3200 / 5000))))
    assert np.isclose(gap[1], 0.0)
    assert np.isclose(gap[2], np.log(2.0))
    assert np.isinf(gap[3])                                     # no training season


def test_the_breakdowns_find_the_bias_where_it_is():
    rj = _rj()
    rng = np.random.default_rng(1)
    n = 6000
    dist = rng.choice([1600.0, 3200.0, 5000.0], n)
    gap = np.where(dist == 1600.0, 0.69, 0.1)
    err = rng.normal(0, 0.02, n) + np.where(dist == 1600.0, 0.015, 0.0)
    lines = rj.distanceBreakdown(err, np.array(["hs_m"] * n), dist, gap)
    txt = "\n".join(lines)
    row1600 = next(l for l in lines if " 1600" in l)
    mean1600 = float(row1600.split()[3].rstrip("%"))
    assert abs(mean1600 - 1.5) < 0.3, row1600
    assert "0.50-0.75" in txt
    doy = np.where(rng.random(n) < 0.5, 260, 300)               # Sep / Oct
    sport = np.zeros(n, dtype=int)
    err2 = rng.normal(0, 0.02, n) + np.where(doy == 260, 0.01, 0.0)
    out = "\n".join(rj.seasonCurveBreakdown(err2, sport, doy))
    sep = float(out.split("Sep ")[1].split("%")[0])
    assert abs(sep - 1.0) < 0.3, out
