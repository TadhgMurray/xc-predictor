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
