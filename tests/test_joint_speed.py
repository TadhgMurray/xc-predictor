"""The 2026-09-02 solver speedups cannot change the answer.

  - rows sorted by athlete (run_joint.sortRowsByAthlete) give the same
    fit as the pack's arrival order, and every per-row column moves together;
  - the looser intermediate/probe tolerances still recover the world;
  - the live pipeline step no longer pays for a held-out solve.

    python -m pytest -q tests/test_joint_speed.py
"""
import io
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import joint_solve as js                                         # noqa: E402
import run_joint as rj                                           # noqa: E402
from test_joint_year import world, fit                           # noqa: E402


def test_row_order_does_not_change_the_fit():
    y, D, truth, cols = world(seed=3)
    out_a = fit(y, D, truth)

    rng = np.random.default_rng(7)
    perm = rng.permutation(y.size)
    D_p = js.Design(cols["ath"][perm], cols["cel"][perm], cols["rac"][perm],
                    group_of_cell=cols["sport_of_cell"], sc=cols["sc"][perm],
                    pool_row=cols["pool_row"][perm], day=cols["doy"][perm],
                    first=cols["first"][perm])
    out_b = fit(y[perm], D_p, truth)

    # the published blocks agree to well inside the noise (0.02 in log-time)
    assert np.abs(out_a["delta"] - out_b["delta"]).max() < 2e-4
    assert np.abs(out_a["ability"] - out_b["ability"]).max() < 2e-4
    # per-row diagnostics come back in each call's own row order
    assert np.abs(out_a["weights"][perm] - out_b["weights"]).max() < 1e-4


def test_sort_moves_every_per_row_column_together():
    n = 12
    rng = np.random.default_rng(1)
    cols = {
        "athlete": rng.integers(0, 4, n), "year": rng.integers(2024, 2027, n),
        "norm": rng.random(n) + 1.0, "result_id": np.arange(100, 100 + n),
        "course": rng.integers(0, 3, n), "days": rng.integers(0, 300, n),
        "course_keys": np.array(["a", "b", "c"]),
        "athlete_keys": [("p1", 1), ("p2", 1), ("p3", 1), ("p4", 1)],
        "note": "a string rides along",
    }
    s = rj.sortRowsByAthlete(cols)
    key = s["athlete"] * 10000 + s["year"]
    assert np.all(np.diff(key) >= 0), "sorted by athlete then year"
    # the same rows, matched on result_id, carry the same values
    back = {r: i for i, r in enumerate(cols["result_id"])}
    for k in ("athlete", "year", "norm", "course", "days"):
        assert np.array_equal(s[k], cols[k][[back[r] for r in s["result_id"]]])
    assert s["course_keys"] is cols["course_keys"]
    assert s["athlete_keys"] is cols["athlete_keys"]
    assert s["note"] == cols["note"]


def test_live_step_has_no_holdout():
    sh = io.open(os.path.join(ROOT, "deploy", "run_pipeline.sh"),
                 encoding="utf-8").read()
    live = [ln for ln in sh.splitlines()
            if "step 08_golive" in ln and "run_joint.py" in ln]
    assert live and all("--holdout" not in ln for ln in live)
    shadow = [ln for ln in sh.splitlines() if "08b_joint_shadow" in ln
              and "run_joint.py" in ln]
    assert shadow and all("--holdout" in ln for ln in shadow)
    assert js.CG_TOL_OUTER == js.CG_TOL, "every outer solves tight (the level swung otherwise)"
    assert js.CG_TOL_PROBE > js.CG_TOL
