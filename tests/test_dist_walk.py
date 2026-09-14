# Project: xc-predictor / tests
# File:    test_dist_walk.py
# Purpose: the event offsets are a curve, not a list. A thin distance
#          class (a 1000 m raced by few) fitted alone against a 3% prior
#          wanders; tied to its neighbours by the random walk in
#          log-distance it lands where the planted curve says.
#
#   python -m pytest -q tests/test_dist_walk.py
import io
import contextlib
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import joint_solve as js                                       # noqa: E402
import run_joint as rj                                         # noqa: E402


def _world(seed=7, n_ath=2500, thin=6):
    """Track rows at 800, 1000, 1600 (the reference), 3200 on one track;
    a smooth planted offset curve (800 +2%, 1000 +1%, 3200 -1%) except
    that the 1000 is raced by `thin` athletes only, one slow day (+5%),
    so alone it reads far from +1%. The walk is gentle by design (0.02 per
    unit log-distance): a class with thousands of rows keeps its own
    number, a class with a handful rests on its neighbours."""
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.12, n_ath)
    off = {800: 0.02, 1000: 0.01, 1600: 0.0, 3200: -0.01}
    rows = []
    for i in range(n_ath):
        for k, d in enumerate((800, 1600, 3200, 1600, 800, 3200)):
            rows.append((i, d, 100 + 6 * k + rng.integers(0, 3), off[d], 0.02))
    for i in rng.choice(n_ath, thin, replace=False):
        rows.append((i, 1000, 115, off[1000] + 0.05, 0.06))     # a slow day, few rows
    ath = np.array([r[0] for r in rows]); dist = np.array([r[1] for r in rows], dtype=np.float64)
    days = np.array([r[2] for r in rows], dtype=np.float64)
    eff = np.array([r[3] for r in rows]); sd = np.array([r[4] for r in rows])
    y = a[ath] + eff + rng.normal(0, 1, ath.size) * sd
    n = ath.size
    cols = {"athlete": ath, "year": np.full(n, 2025), "course": np.zeros(n, dtype=np.int64),
            "days": days, "sport": np.ones(n, dtype=np.int64), "norm": np.exp(y),
            "doy": np.full(n, 120), "dist_m": dist, "meet_class": np.zeros(n, dtype=np.int64),
            "result_id": np.arange(n), "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": ["TF:loc:1:out"]}
    return cols, off


def _solve(cols, walk_sd):
    keep = np.ones(cols["norm"].size, dtype=bool)
    with contextlib.redirect_stdout(io.StringIO()):
        D, athlete_pool, pool_names = rj.buildDesign(
            cols, keep, sport_offset=False, curve=False, rust=False, dist=True,
            dist_bands=False, slope=False, link=False, altitude=False,
            importance="none", indoor=False, dist_table=False)
        y = np.log(cols["norm"][keep])
        out = js.solveJoint(y, design=D, athlete_pool=athlete_pool, n_outer=3,
                            tilt=False, n_probe=0, dist_walk_sd=walk_sd)
    labels = list(D.dist_labels)
    e = np.asarray(out["dist_offset"], dtype=np.float64)
    return {int(float(lab.split(":")[1])): float(e[i]) for i, lab in enumerate(labels)}, D


def test_the_walk_pairs_neighbours_and_ties_the_reference():
    pairs, w, zero = rj.distWalkPairs(["hs_m:800", "hs_m:1000", "hs_m:3200", "hs_f:800"],
                                     {"hs_m": 1600, "hs_f": 1600}, 1)
    # hs_m: 800-1000 paired; 1000 and 3200 tied to the 1600 reference; hs_f: 800 tied
    assert pairs.shape == (2, 1) and set(pairs[:, 0]) == {0, 1}
    assert abs(w[0] - 1 / abs(np.log(1000) - np.log(800))) < 1e-12
    assert zero[1] > 0 and zero[2] > 0 and zero[3] > 0 and zero[0] == 0
    p4, w4, z4 = rj.distWalkPairs(["hs_m:800", "hs_m:1000"], {"hs_m": 1600}, 4)
    assert p4.shape == (2, 4) and z4.size == 8 and (z4[4:] > 0).all()


def test_a_thin_class_rests_on_its_neighbours_under_the_walk():
    cols, off = _world()
    alone, D = _solve(cols, 0.0)
    walked, D2 = _solve(cols, js.DIST_WALK_SD)
    assert getattr(D2, "n_dist_pair", 0) >= 1
    # the thick classes are read either way
    for d in (800, 3200):
        assert abs(alone[d] - off[d]) < 0.006 and abs(walked[d] - off[d]) < 0.006, (d, alone[d], walked[d])
    # the thin 1000 wanders alone (its slow day is 5% off) and comes home tied
    assert abs(walked[1000] - off[1000]) < abs(alone[1000] - off[1000]) - 0.005, (alone[1000], walked[1000])
    assert abs(walked[1000] - off[1000]) < 0.025
