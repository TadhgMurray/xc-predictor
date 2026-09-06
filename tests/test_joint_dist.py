"""The track distance offset (issue 148): one coefficient per (pool, track
distance), pinned at the pool's reference event, recovered from a world
where the normalisation is wrong by event; the classes are built the way
run_joint builds them; a design without the block is byte-for-byte the
old one.

    python -m pytest -q tests/test_joint_dist.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402


def _world(seed=0, n_ath=500):
    """Two pools; every athlete races XC cells and three track events in one
    location cell each; the track events carry a per-(pool, event) error."""
    rng = np.random.default_rng(seed)
    n_xc, n_tf = 20, 12
    n_cell = n_xc + n_tf
    group = np.r_[np.zeros(n_xc), np.ones(n_tf)].astype(int)
    d_true = np.r_[rng.normal(0, 0.06, n_xc), rng.normal(0, 0.03, n_tf)]
    mu_true = np.array([0.0, -0.05])
    delta_true = mu_true[group] + d_true
    pool_of_ath = rng.integers(0, 2, n_ath)
    a_true = rng.normal(0, 0.15, n_ath)
    # the truth: by pool, 800 reads fast, 3200 reads slow, 1600 is the ref
    e_true = {(0, 800): -0.020, (0, 3200): +0.018,
              (1, 800): -0.012, (1, 3200): +0.025}
    rows = []
    for i in range(n_ath):
        for _ in range(5):
            rows.append((i, rng.integers(0, n_xc), 0))
        for ev in (800, 1600, 3200):
            for _ in range(2):
                rows.append((i, n_xc + rng.integers(0, n_tf), ev))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    ev = np.array([r[2] for r in rows])
    rac = np.arange(len(rows)) // 7                # several rows per "day"
    pool_row = pool_of_ath[ath]
    e_row = np.array([e_true.get((p, e), 0.0) for p, e in zip(pool_row, ev)])
    y = a_true[ath] + delta_true[cel] + e_row + rng.normal(0, 0.02, len(rows))
    cols = {"norm": np.exp(y), "sport": (ev > 0).astype(np.int8),
            "athlete": ath, "dist_m": ev.astype(np.float32)}
    return y, ath, cel, rac, group, cols, pool_of_ath, e_true


def test_classes_pin_the_reference_and_skip_xc():
    y, ath, cel, rac, group, cols, pool_of_ath, _ = _world()
    classes, labels, refs = rj.distClasses(cols, pool_of_ath, ["p0", "p1"])
    assert refs == {"p0": 1600, "p1": 1600}
    assert sorted(labels) == ["p0:3200", "p0:800", "p1:3200", "p1:800"]
    assert (classes[cols["sport"] == 0] == -1).all(), "XC rows carry none"
    ref_rows = (cols["dist_m"] == 1600)
    assert (classes[ref_rows] == -1).all(), "the reference event is pinned"
    assert (classes[(cols["sport"] == 1) & ~ref_rows] >= 0).all()


def test_recovers_the_per_event_offsets():
    y, ath, cel, rac, group, cols, pool_of_ath, e_true = _world()
    classes, labels, _ = rj.distClasses(cols, pool_of_ath, ["p0", "p1"])
    D = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                  n_e=len(labels))
    out = js.solveJoint(y, design=D, n_outer=4, tilt=False, n_probe=2)
    e = out["dist_offset"]
    for i, lab in enumerate(labels):
        p, d = lab.split(":")
        truth = e_true[(int(p[1]), int(d))]
        assert abs(e[i] - truth) < 0.006, (lab, e[i], truth)
    # and the level is still the surface level, not polluted by the events
    assert abs((out["mu"][1] - out["mu"][0]) - (-0.05)) < 0.02, out["mu"]
    print("  distance offsets: " + ", ".join(
        f"{lab} {e[i]:+.4f}" for i, lab in enumerate(labels)) + " ... OK")


def test_without_the_block_nothing_changes():
    y, ath, cel, rac, group, cols, pool_of_ath, _ = _world()
    D0 = js.Design(ath, cel, rac, group_of_cell=group)
    D1 = js.Design(ath, cel, rac, group_of_cell=group, dist=None)
    assert D0.n_e == 0 and D1.n_e == 0
    assert D0.n_total == D1.n_total == D0.o_r + D0.n_r
    a = js.solveJoint(y, design=D0, n_outer=2, tilt=False, n_probe=1)
    assert a["dist_offset"] is None


def test_banded_offsets_follow_the_rating():
    """Issue 167: three classes per (pool, event), the row's class moving
    with its athlete-season's rating band; the pinned event stays pinned
    in every band; unbanded is byte-for-byte the old design."""
    y, ath, cel, rac, group, cols, pool_of_ath, e_true = _world()
    classes, labels, _ = rj.distClasses(cols, pool_of_ath, ["p0", "p1"])
    D = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                  n_e=len(labels), dist_banded=True)
    assert D.n_e == len(labels) * js.DIST_N_BAND
    free = classes >= 0
    assert (D.e_idx[free] == classes[free] * js.DIST_N_BAND + 1).all()
    assert (D.e_w[~free] == 0).all()
    rating = np.where(np.arange(len(y)) % 3 == 0, 130.0, 100.0)
    D.rebandDist(rating)
    top = free & (rating >= 120)
    assert (D.e_idx[top] == classes[top] * js.DIST_N_BAND + 2).all()
    assert (D.e_w[~free] == 0).all(), "the pinned event is pinned in every band"
    assert rj.bandLabels(["p0:800"]) == ["p0:800:b0", "p0:800:b1", "p0:800:b2"]
    D0 = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                   n_e=len(labels))
    assert D0.n_e == len(labels) and not D0.dist_banded


def _pairs_world(seed=1, n_ath=600):
    """Issue 189's trap: the top band is populated by distance runners
    who jog half their 1600s (+5%); the season-best relation between the
    events is the same for everyone. The rows say one thing, the pairs
    another, and the calibration makes the pairs win."""
    rng = np.random.default_rng(seed)
    n_cell = 8
    group = np.ones(n_cell, dtype=int)
    d_true = rng.normal(0, 0.01, n_cell)
    a_true = np.r_[rng.normal(-0.25, 0.03, n_ath // 2),   # the top band
                   rng.normal(0.00, 0.03, n_ath - n_ath // 2)]
    e_3200 = 0.030                                          # the truth
    rows = []
    for i in range(n_ath):
        top = i < n_ath // 2
        for k in range(4):
            rows.append((i, rng.integers(0, n_cell), 1600,
                         0.05 if (top and k % 2) else 0.0))
        for k in range(4):
            rows.append((i, rng.integers(0, n_cell), 3200, 0.0))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    ev = np.array([r[2] for r in rows]); jog = np.array([r[3] for r in rows])
    rac = np.arange(len(rows)) // 5
    y = (a_true[ath] + d_true[cel] + np.where(ev == 3200, e_3200, 0.0) + jog
         + rng.normal(0, 0.01, len(rows)))
    cols = {"norm": np.exp(y), "sport": np.ones(len(rows), dtype=np.int8),
            "athlete": ath, "dist_m": ev.astype(np.float32)}
    pool_of_ath = np.zeros(n_ath, dtype=int)
    return y, ath, cel, rac, group, cols, pool_of_ath, a_true, e_3200


def test_pairs_calibration_sets_the_prior_mean_and_the_solve_follows():
    y, ath, cel, rac, group, cols, pool_of_ath, a_true, e_3200 = _pairs_world()
    classes, labels, refs = rj.distClasses(cols, pool_of_ath, ["p0"])
    assert labels == ["p0:3200"] and refs == {"p0": 1600}
    ref_rows = rj.distRefRows(cols, pool_of_ath, refs, ["p0"])
    assert (ref_rows == (cols["dist_m"] == 1600)).all()
    D = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                  n_e=len(labels), dist_banded=True, dist_ref=ref_rows)
    # the top half rates 130, the rest 110: two populated bands
    rating = np.where(ath < 300, 130.0, 110.0)
    D.rebandDist(rating)
    n_cal = D.calibrateDist(y, rating, min_pairs=50)
    assert n_cal == 2, n_cal
    top, mid = 2, 1                                    # band index
    assert D.e_cal_n[top] == 300 and D.e_cal_n[mid] == 300
    # the season-best pair says the same thing in both bands: the truth
    # (the top band's honest 1600s are only two of four, so its best is
    # the min of fewer real draws -- a residual under 0.01)
    assert abs(D.e_mean[top] - e_3200) < 0.010, D.e_mean
    assert abs(D.e_mean[mid] - e_3200) < 0.006, D.e_mean
    assert D.e_cal_n[0] == 0 and D.e_mean[0] == 0.0, "uncalibrated"

    # the rows alone: the top band's jogged 1600s make the 3200 look ~3%
    # FASTER relative to the 1600 -- 189's wrong answer
    D_rows = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                       n_e=len(labels), dist_banded=True, dist_ref=ref_rows)
    D_rows.rebandDist(rating)
    out0 = js.solveJoint(y, design=D_rows, n_outer=3, tilt=False, n_probe=1,
                         dist_cal=False)
    assert out0["dist_offset"][top] < e_3200 - 0.015, out0["dist_offset"]

    # with the calibration the solve lands on the pairs
    out1 = js.solveJoint(y, design=D, n_outer=3, tilt=False, n_probe=1)
    assert abs(out1["dist_offset"][top] - e_3200) < 0.012, out1["dist_offset"]
    assert abs(out1["dist_offset"][mid] - e_3200) < 0.006, out1["dist_offset"]
    assert (out1["dist_cal_n"][[mid, top]] == 300).all()
    print(f"  rows alone top-band 3200 {out0['dist_offset'][top]:+.4f}, "
          f"pairs {D.e_mean[top]:+.4f}, calibrated solve "
          f"{out1['dist_offset'][top]:+.4f} (truth {e_3200:+.4f}) ... OK")


def test_calibration_is_a_no_op_without_reference_rows_or_bands():
    y, ath, cel, rac, group, cols, pool_of_ath, _, _ = _pairs_world(n_ath=100)
    classes, labels, refs = rj.distClasses(cols, pool_of_ath, ["p0"])
    D = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                  n_e=len(labels), dist_banded=True)
    assert D.calibrateDist(y, np.full(len(y), 100.0)) == 0
    ref_rows = rj.distRefRows(cols, pool_of_ath, refs, ["p0"])
    D1 = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                   n_e=len(labels), dist_ref=ref_rows)          # unbanded
    assert D1.calibrateDist(y, np.full(len(y), 100.0)) == 0
    assert (D1.e_cal_n == 0).all() and (D1.e_mean == 0.0).all()


def test_count_matched_bests_do_not_favour_the_event_raced_more():
    """Six 1600s and two 3200s per athlete, no offset at all: a plain
    best-vs-best would read the 3200 ~1% slow; count-matched it reads 0."""
    rng = np.random.default_rng(3)
    n_ath, n_cell = 800, 6
    rows = []
    for i in range(n_ath):
        for _ in range(6):
            rows.append((i, rng.integers(0, n_cell), 1600))
        for _ in range(2):
            rows.append((i, rng.integers(0, n_cell), 3200))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    ev = np.array([r[2] for r in rows])
    y = rng.normal(0, 0.1, n_ath)[ath] + rng.normal(0, 0.03, len(rows))
    cols = {"norm": np.exp(y), "sport": np.ones(len(rows), dtype=np.int8),
            "athlete": ath, "dist_m": ev.astype(np.float32)}
    pool_of_ath = np.zeros(n_ath, dtype=int)
    classes, labels, refs = rj.distClasses(cols, pool_of_ath, ["p0"])
    ref_rows = rj.distRefRows(cols, pool_of_ath, refs, ["p0"])
    D = js.Design(ath, cel, np.arange(len(rows)) // 4,
                  group_of_cell=np.ones(n_cell, dtype=int), dist=classes,
                  n_e=len(labels), dist_banded=True, dist_ref=ref_rows)
    rating = np.full(len(y), 110.0)
    D.rebandDist(rating)
    assert D.calibrateDist(y, rating, min_pairs=50) == 1
    assert abs(D.e_mean[1]) < 0.004, D.e_mean
    # and the plain best-vs-best is what it would have been: biased
    b16 = np.full(n_ath, np.inf); np.minimum.at(b16, ath[ev == 1600], y[ev == 1600])
    b32 = np.full(n_ath, np.inf); np.minimum.at(b32, ath[ev == 3200], y[ev == 3200])
    assert np.median(b32 - b16) > 0.008


def test_chain_calibrates_an_event_with_no_reference_pairs():
    """Issue 195: 5000 runners never race the 1600 but race the 3200;
    the 5000's prior mean comes through the 3200's."""
    rng = np.random.default_rng(5)
    n_ath, n_cell = 600, 6
    e_true = {3200: 0.020, 5000: 0.045}
    rows = []
    for i in range(n_ath):
        if i < 300:                                    # milers: 1600 + 3200
            evs = [1600] * 4 + [3200] * 4
        else:                                          # distance: 3200 + 5000
            evs = [3200] * 4 + [5000] * 4
        for ev in evs:
            rows.append((i, rng.integers(0, n_cell), ev))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    ev = np.array([r[2] for r in rows])
    y = (rng.normal(0, 0.1, n_ath)[ath] + rng.normal(0, 0.01, n_cell)[cel]
         + np.array([e_true.get(e, 0.0) for e in ev]) + rng.normal(0, 0.015, len(rows)))
    cols = {"norm": np.exp(y), "sport": np.ones(len(rows), dtype=np.int8),
            "athlete": ath, "dist_m": ev.astype(np.float32)}
    pool_of_ath = np.zeros(n_ath, dtype=int)
    classes, labels, refs = rj.distClasses(cols, pool_of_ath, ["p0"])
    assert labels == ["p0:3200", "p0:5000"]
    ref_rows = rj.distRefRows(cols, pool_of_ath, refs, ["p0"])
    D = js.Design(ath, cel, np.arange(len(rows)) // 4,
                  group_of_cell=np.ones(n_cell, dtype=int), dist=classes,
                  n_e=len(labels), dist_banded=True, dist_ref=ref_rows,
                  pool_row=np.zeros(len(rows), dtype=int))
    rating = np.full(len(y), 110.0)
    D.rebandDist(rating)
    n_cal = D.calibrateDist(y, rating, min_pairs=50)
    mid = 1
    i32, i50 = 0 * js.DIST_N_BAND + mid, 1 * js.DIST_N_BAND + mid
    assert n_cal == 2, n_cal
    assert D.e_cal_via[i32] == -1 and D.e_cal_via[i50] == 0, D.e_cal_via
    assert abs(D.e_mean[i32] - 0.020) < 0.006, D.e_mean
    assert abs(D.e_mean[i50] - 0.045) < 0.008, D.e_mean
    assert D.e_cal_n[i50] == 300
    print(f"  chain: 3200 {D.e_mean[i32]:+.4f} (ref), 5000 {D.e_mean[i50]:+.4f} "
          f"via 3200 (truth +0.045) ... OK")


def test_the_rating_interpolates_the_offset_between_band_anchors():
    """Issue 219: no step at 105 or 120 -- the applied offset is the three
    band values interpolated at the athlete-season's rating between the
    anchors 90 / 112 / 130, flat beyond; unbanded designs are untouched."""
    y, ath, cel, rac, group, cols, pool_of_ath, _ = _world()
    classes, labels, _ = rj.distClasses(cols, pool_of_ath, ["p0", "p1"])
    D = js.Design(ath, cel, rac, group_of_cell=group, dist=classes,
                  n_e=len(labels), dist_banded=True)
    nb = len(labels)
    e = np.zeros(nb * js.DIST_N_BAND)
    c = labels.index("p0:3200")
    e[c * js.DIST_N_BAND:(c + 1) * js.DIST_N_BAND] = [0.018, 0.009, 0.000]
    rows = np.flatnonzero((classes == c))
    for r_in, want in ((80.0, 0.018), (90.0, 0.018), (101.0, 0.0135),
                       (112.0, 0.009), (119.9, 0.009 - 0.009 * 7.9 / 18),
                       (120.1, 0.009 - 0.009 * 8.1 / 18), (130.0, 0.0), (140.0, 0.0)):
        got = js.distOffsetRow(D, e, np.full(len(y), r_in))
        assert abs(float(got[rows[0]]) - want) < 1e-9, (r_in, got[rows[0]], want)
    # continuous across 120: a hair apart, not a point
    a = js.distOffsetRow(D, e, np.full(len(y), 119.99))[rows[0]]
    b = js.distOffsetRow(D, e, np.full(len(y), 120.01))[rows[0]]
    assert abs(a - b) < 2e-5
    # rows with no class carry nothing; an unbanded design gets its value
    assert (js.distOffsetRow(D, e, np.full(len(y), 110.0))[classes < 0] == 0).all()
    D0 = js.Design(ath, cel, rac, group_of_cell=group, dist=classes, n_e=nb)
    e0 = np.arange(nb, dtype=float) * 0.01
    assert np.allclose(js.distOffsetRow(D0, e0, np.full(len(y), 110.0)),
                       D0.e_w * e0[D0.e_idx])
