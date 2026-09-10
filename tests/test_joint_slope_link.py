"""The per-athlete endurance slope and the season link (issue 154):
the slope is recovered from athletes who race several distances and is
exactly zero for one who races one; a race rating does not carry it; the
season link steadies a one-race season without moving a full one; with
both off the design is the old one.

    python -m pytest -q tests/test_joint_slope_link.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402
import run_joint as rj                                          # noqa: E402


def _world(seed=0, n_ath=400):
    rng = np.random.default_rng(seed)
    n_cell = 24
    d_true = rng.normal(0, 0.05, n_cell)
    a_true = rng.normal(0, 0.15, n_ath)
    g_true = rng.normal(0, 0.05, n_ath)             # the endurance slope
    g_true[: n_ath // 4] = 0.0                      # a quarter race one distance
    rows = []
    for i in range(n_ath):
        dists = [5000] * 6 if i < n_ath // 4 else [800, 1600, 3200, 5000] * 2
        for dm in dists:
            rows.append((i, rng.integers(0, n_cell), dm))
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    dist = np.array([r[2] for r in rows], dtype=np.float64)
    rac = np.arange(len(rows)) // 5
    lz_raw = np.log(dist)
    mean = np.bincount(ath, weights=lz_raw) / np.bincount(ath)
    lz = lz_raw - mean[ath]
    y = a_true[ath] + d_true[cel] + g_true[ath] * lz + rng.normal(0, 0.02, len(rows))
    cols = {"dist_m": dist.astype(np.float32)}
    return y, ath, cel, rac, cols, a_true, g_true


def test_slope_recovered_and_zero_for_one_distance():
    y, ath, cel, rac, cols, a_true, g_true = _world()
    n_ath = ath.max() + 1
    keep = np.ones(len(y), dtype=bool)
    lz = rj.logDistCentered(cols, keep, ath, n_ath)
    D = js.Design(ath, cel, rac, lz=lz)
    out = js.solveJoint(y, design=D, n_outer=4, tilt=False, n_probe=2)
    g = out["slope"]
    multi = np.arange(n_ath) >= n_ath // 4
    r = float(np.corrcoef(g[multi], g_true[multi])[0, 1])
    assert r > 0.9, r
    assert (g[~multi] == 0).all(), "one distance raced: the slope is exactly 0"
    # the ability keeps its meaning: it tracks the truth as before
    ra = float(np.corrcoef(out["ability"], a_true)[0, 1])
    assert ra > 0.98, ra
    print(f"  slope: corr {r:.3f} on multi-distance athletes, exactly 0 on "
          f"{int((~multi).sum())} single-distance ones ... OK")


def test_link_steadies_a_thin_season_only():
    rng = np.random.default_rng(3)
    # one person, two seasons in one pool: 12 races then 1 race, same truth
    n_cell = 6
    d_true = rng.normal(0, 0.03, n_cell)
    rows = [(0, rng.integers(0, n_cell)) for _ in range(12)] + [(1, 0)]
    # a crowd so the cells are identified
    for i in range(2, 60):
        rows += [(i, rng.integers(0, n_cell)) for _ in range(6)]
    ath = np.array([r[0] for r in rows]); cel = np.array([r[1] for r in rows])
    rac = np.arange(len(rows)) // 4
    truth = np.r_[0.10, 0.10, rng.normal(0, 0.1, 58)]
    y = truth[ath] + d_true[cel] + rng.normal(0, 0.03, len(rows))
    y[12] += 0.15                                     # the one race: a bad day
    raw = np.r_[0, 0, np.arange(2, 60)]               # seasons 0,1 are one person
    year = np.r_[2023, 2024, np.full(58, 2024)]
    links = rj.seasonLinks(raw, year, np.arange(60), 60)
    assert links[0].tolist() == [0] and links[1].tolist() == [1]
    D_link = js.Design(ath, cel, rac, link=links)
    D_none = js.Design(ath, cel, rac)
    # ! sigma_u_floor=0 ON PURPOSE, and it is not a workaround. This world
    #   plants NO race-day effect -- y is truth + difficulty + iid noise --
    #   so the default floor (js.SIGMA_U_FLOOR, a claim that race days DO
    #   vary) would force the solver to attribute variance to a race term
    #   that is genuinely zero here, and abilities would move for a reason
    #   the test is not about. Measured on this world: ability rmse 0.00710
    #   at floor 0, 0.01341 at 0.03. The floor is tested where it belongs,
    #   in tests/test_thin_course_shrinkage.py, against a world that has a
    #   race-day effect to find.
    a_link = js.solveJoint(y, design=D_link, n_outer=3, tilt=False, n_probe=1,
                           robust=False, sigma_u_floor=0.0)["ability"]
    a_none = js.solveJoint(y, design=D_none, n_outer=3, tilt=False, n_probe=1,
                           robust=False, sigma_u_floor=0.0)["ability"]
    gap_link = abs(a_link[1] - a_link[0])
    gap_none = abs(a_none[1] - a_none[0])
    assert gap_link < gap_none, (gap_link, gap_none)
    assert abs(a_link[0] - a_none[0]) < 0.01, "the full season did not move"
    print(f"  link: one-race season {gap_none:.3f} from its neighbour "
          f"without, {gap_link:.3f} with; full season unmoved ... OK")


def test_off_is_the_old_design():
    y, ath, cel, rac, cols, _, _ = _world(n_ath=50)
    D0 = js.Design(ath, cel, rac)
    assert D0.n_g == 0 and not D0.has_link and D0.n_total == D0.o_g
    out = js.solveJoint(y, design=D0, n_outer=2, tilt=False, n_probe=1)
    assert out["slope"] is None
