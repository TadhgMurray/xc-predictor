# Project: xc-predictor / tests
# File:    test_event_check.py
# Purpose: scripts/event_check.py reads a planted event bias back: a world
#          where every 800 is run 1.5% "better" than the same athlete's
#          1600 (an overrated 800) shows +1.5% for the 800->1600 pair and
#          nothing for 1600->3200.
#
#   python -m pytest -q tests/test_event_check.py
import io
import contextlib
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket as bk                                           # noqa: E402
import event_check as ec                                       # noqa: E402


def _world(seed=4, n_ath=2500, bias_800=-0.015):
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 0.12, n_ath)
    rows = []
    for i in range(n_ath):
        for k in range(6):
            d = [800, 1600, 3200][k % 3]
            rows.append((i, k % 4, 100 + 5 * k + rng.integers(0, 3), d))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    days = np.array([r[2] for r in rows], dtype=np.float64); dist = np.array([r[3] for r in rows], dtype=np.float64)
    y = a[ath] + np.where(dist == 800, bias_800, 0.0) + rng.normal(0, 0.02, ath.size)
    n = ath.size
    keys = [f"TF:loc:{c}:out" for c in range(4)]
    cols = {"athlete": ath, "year": np.full(n, 2025), "course": course, "days": days,
            "sport": np.ones(n, dtype=np.int64), "norm": np.exp(y), "doy": np.full(n, 120),
            "dist_m": dist, "meet_class": np.zeros(n, dtype=np.int64),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)], "course_keys": keys}
    npz = {"delta": np.zeros(4), "course_keys": np.array(keys),
           "rating": 100.0 * np.exp(a.mean()) / np.exp(a)}
    return cols, npz


def test_a_planted_overrated_800_is_read_back():
    cols, npz = _world()
    with contextlib.redirect_stdout(io.StringIO()):
        cols, codes = bk.packCodes(cols, npz, 0)
    adj, pool_row, rating, bucket, season, ok = ec.adjustedLogTime(cols, npz, codes)
    rows = ec.pairs(adj, pool_row, rating, bucket, season, ok, cols["days"], window=21,
                    classes=(800, 1600, 3200), min_rows=100)
    by = {(r["short"], r["long"]): r for r in rows}
    assert abs(by[(800, 1600)]["median"] - 0.015) < 0.004, by[(800, 1600)]
    assert abs(by[(800, 3200)]["median"] - 0.015) < 0.004
    assert abs(by[(1600, 3200)]["median"]) < 0.004
    lines = []
    ec.report(rows, out=lines.append)
    assert any("800->1600" in ln for ln in lines)


def test_the_offset_table_reads_the_solve_file_and_interpolates_by_band():
    npz = {"dist_offset": np.array([0.02, 0.01, 0.0, -0.01]),
           "dist_labels": np.array(["hs_m:800:b0", "hs_m:800:b1", "hs_m:800:b2", "hs_m:800:b3"])}
    table, n_band = ec.offsetTable(npz)
    assert n_band == 4 and table[("hs_m", 800)] == [0.02, 0.01, 0.0, -0.01]
    pools = np.array(["hs_m", "hs_m", "hs_m", "college_m"], dtype=object)
    bucket = np.array([800, 800, 1600, 800])
    rating = np.array([90.0, 101.0, 100.0, 100.0])
    got = ec.offsetRows(pools, bucket, rating, table, n_band)
    assert got[0] == 0.02                      # at the first anchor
    assert 0.01 < got[1] < 0.02                # between 90 and 112
    assert got[2] == 0.0 and got[3] == 0.0     # unkeyed distance / pool
    assert ec.offsetTable(None) == ({}, 0)
