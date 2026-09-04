"""The race-day effect is tilted like the course (issue 156): with the
tilt on, a cell's difficulty is the mean of its days and the days'
effects average to about zero inside the cell, instead of the
(delta = +c, u = -c) pair the untilted model could buy for free.

    python -m pytest -q tests/test_race_day_tilted.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import joint_solve as js                                        # noqa: E402


def _world(seed=0, n_ath=800, n_cell=16, races_per_cell=5, per_athlete=8):
    rng = np.random.default_rng(seed)
    true_delta = rng.normal(0, 0.06, n_cell)
    true_a = rng.normal(0, 0.18, n_ath)             # wide: h varies 0.9..1.1
    race_cell = np.repeat(np.arange(n_cell), races_per_cell)
    race_u = rng.normal(0, 0.05, race_cell.size)
    ath, cel, rac, y = [], [], [], []
    for i in range(n_ath):
        for _ in range(per_athlete):
            j = rng.integers(0, race_cell.size)
            ath.append(i); cel.append(race_cell[j]); rac.append(j)
            y.append(true_a[i] + true_delta[race_cell[j]] + race_u[j]
                     + rng.normal(0, 0.02))
    return (np.array(ath), np.array(cel), np.array(rac), np.array(y),
            true_delta, race_u, race_cell)


def test_days_average_to_zero_inside_the_cell_with_the_tilt_on():
    ath, cel, rac, y, true_delta, race_u, race_cell = _world()
    out = js.solveJoint(y, ath, cel, rac, n_outer=5, tilt=True, n_probe=2)
    d = out["delta"] - out["delta"].mean()
    r = float(np.corrcoef(d, true_delta)[0, 1])
    assert r > 0.9, r          # 5 days per cell, day sd 0.05 vs course sd 0.06
    # the row-weighted mean of u inside each cell: the priors' split
    rows = np.bincount(rac, minlength=race_cell.size).astype(float)
    num = np.bincount(race_cell, weights=out["race_effect"] * rows,
                      minlength=true_delta.size)
    den = np.bincount(race_cell, weights=rows, minlength=true_delta.size)
    cell_mean_u = num / np.maximum(den, 1)
    assert np.abs(cell_mean_u).max() < 0.02, cell_mean_u
    ru = float(np.corrcoef(out["race_effect"], race_u)[0, 1])
    assert ru > 0.9, ru
    print(f"  tilted u: delta corr {r:.3f}, u corr {ru:.3f}, max |cell mean "
          f"u| {np.abs(cell_mean_u).max():.4f} ... OK")
