# Project: xc-predictor / tests
# File:    test_curve_bakeoff.py
# Purpose: the bake-off scores a candidate curve by the same-athlete gap it
#          leaves: on a world whose true relation is a 1.10 exponent, the
#          matching curve scores near zero and Riegel's 1.06 does not; the
#          VDOT equivalence and the hybrid compose correctly.
#
#   python -m pytest -q tests/test_curve_bakeoff.py
import io
import contextlib
import math
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                           # noqa: E402
import curve_bakeoff as cb                                     # noqa: E402
import distance_tables as dt                                   # noqa: E402
import event_check as ec                                       # noqa: E402
import normalize_distance as nd                                # noqa: E402


def _artifact(k):
    """A potential with one exponent everywhere, hs_m target 5000."""
    knots = [math.log(700.0) + 0.25 * i for i in range(13)]
    vals = [k * (kn - knots[0]) for kn in knots]
    e = {"knots": knots, "values": vals}
    return {"kind": "distance_potential", "target": 5000.0,
            "pool_targets": {"hs_m": 5000.0}, "pools": {"hs_m|TF": e}, "global": e,
            "global_by_sport": {"TF": e}}


def _world(k_true=1.10, seed=5, n_ath=2000):
    """Rows at 800/1600/3200 whose raw times follow t = a d^k_true, then
    normalised to 5000 with the FITTED artifact's exponent (the pack)."""
    rng = np.random.default_rng(seed)
    a = np.exp(rng.normal(0, 0.12, n_ath))
    rows = []
    for i in range(n_ath):
        for j, d in enumerate((800, 1600, 3200, 800, 1600, 3200)):
            rows.append((i, d, 100 + 5 * j + rng.integers(0, 3)))
    ath = np.array([r[0] for r in rows]); dist = np.array([r[1] for r in rows], dtype=np.float64)
    days = np.array([r[2] for r in rows], dtype=np.float64)
    t = a[ath] * (dist / 5000.0) ** k_true * 1000.0 * np.exp(rng.normal(0, 0.02, ath.size))
    n = ath.size
    return ath, dist, days, t, n, n_ath


def _cols(ath, dist, days, norm, n, n_ath):
    return {"athlete": ath, "year": np.full(n, 2025), "course": np.zeros(n, dtype=np.int64),
            "days": days, "sport": np.ones(n, dtype=np.int64), "norm": norm,
            "doy": np.full(n, 120), "dist_m": dist, "meet_class": np.zeros(n, dtype=np.int64),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)], "course_keys": ["TF:loc:1:out"]}


def test_the_matching_curve_wins_and_riegel_loses(monkeypatch):
    monkeypatch.setattr(nd, "_SPLINES", _artifact(1.10))
    nd._normalizationFactorCached.cache_clear() if hasattr(nd._normalizationFactorCached, "cache_clear") else None
    ath, dist, days, t, n, n_ath = _world(1.10)
    # the pack's normalised time through the (matching) fitted curve
    norm = np.array([t[i] * math.exp(dt.curveLogFactorFor("hs_m")(dist[i])) for i in range(n)])
    cols = _cols(ath, dist, days, norm, n, n_ath)
    npz = {"delta": np.zeros(1), "course_keys": np.array(["TF:loc:1:out"]),
           "rating": 100.0 * np.ones(n_ath)}
    with contextlib.redirect_stdout(io.StringIO()):
        cols, codes = bk.packCodes(cols, npz, 0)
    base, pool_row, rating, bucket, season, ok = ec.adjustedLogTime(cols, npz, codes, use_offsets=False)
    scores = {}
    for name in ("fitted", "riegel", "wa", "hybrid", "vdot"):
        adj = cb.candidateAdjusted(name, base, cols, npz, codes, ok, pool_row, rating, bucket)
        rows = ec.pairs(adj, pool_row, rating, bucket, season, ok, cols["days"],
                        classes=(800, 1600, 3200), min_rows=100)
        scores[name] = cb.score(rows)[0]
    assert scores["fitted"] < 0.004, scores
    # Riegel's 1.06 against a 1.10 world: 0.04 x ln 2 = 2.8% per doubling
    assert scores["riegel"] > 0.02, scores
    assert scores["fitted"] < scores["wa"] < scores["riegel"]
    # the hybrid keeps the fitted curve inside 800-3200, where every row is
    assert scores["hybrid"] < 0.004, scores
    assert np.isfinite(scores["vdot"])


def test_vdot_equivalence_and_the_hybrid_compose():
    # a 4:00 1600 has one VDOT; the equivalent 3200 is slower than double
    t_eq = cb.vdotEquivalentTime(np.array([240.0]), np.array([1600.0]), np.array([3200.0]))
    assert 490 < t_eq[0] < 530, t_eq
    assert abs(cb.vdotOf(t_eq, np.array([3200.0]))[0] - cb.vdotOf(np.array([240.0]), np.array([1600.0]))[0]) < 1e-6
    assert abs(cb.lnRatioRiegel(800.0, 1600.0) - 1.06 * math.log(2)) < 1e-12
    assert abs(cb.lnRatioWA("M", 1, 1600.0, 800.0) + cb.lnRatioWA("M", 1, 800.0, 1600.0)) < 1e-12
