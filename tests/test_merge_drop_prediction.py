"""An asserted sport level (merge semantics) drops each sport's mean d and u
AFTER the pass's solve, so the abilities were fitted with them; a held-out
prediction must carry them back (owner's holdouts, 2026-10-13: every XC race
~3.5% faster than predicted, the training rows' own residuals too). A planted
world whose real XC/TF gap (2.4%) is not the asserted one (5.83%)."""
import contextlib
import io
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("tests", "engine", "scripts"):
    sys.path.insert(0, os.path.join(_ROOT, _p))
import _env  # noqa: E402,F401

import joint_solve as js  # noqa: E402
import run_joint as rj  # noqa: E402
import test_race_day_divisions as T  # noqa: E402


def _world():
    cols, _keep, _w, _m = T._pack(broken=False)
    n = len(cols["norm"])
    tf = np.random.default_rng(3).random(n) < 0.4
    for k in ("athlete", "year", "course", "days", "norm", "doy", "dist_m",
              "meet_id", "div_id", "result_id"):
        cols[k] = np.concatenate([cols[k], cols[k][tf]])
    m = int(tf.sum())
    cols["sport"] = np.concatenate([np.zeros(n, int), np.ones(m, int)])
    cols["course_keys"] = cols["course_keys"] + ["TF:9001:o"]
    cols["course"][n:] = len(cols["course_keys"]) - 1
    cols["norm"][n:] = cols["norm"][n:] * np.exp(-0.024)     # the REAL gap
    cols["doy"] = np.concatenate([np.full(n, 270), np.full(m, 120)])
    cols["result_id"] = np.arange(n + m) + 1
    return cols, np.ones(n + m, dtype=bool)


def test_in_sample_prediction_is_unbiased_under_an_asserted_level():
    cols, keep = _world()
    with contextlib.redirect_stdout(io.StringIO()):
        D, ap, _pn = rj.buildDesign(cols, keep, sport_level=0.0583, importance="none",
                                    indoor=False, dist_table=False, altitude=False,
                                    dist=False, slope=False, link=False, rust=False)
        y = np.log(cols["norm"])
        out = js.solveJoint(y, design=D, athlete_pool=ap, n_outer=4, n_probe=0)
    assert out["merge_drop_d"] is not None
    pred, _ = js.predictHeldOut(out, D, D, athlete_pool=ap)
    r = y - pred
    for code in (0, 1):
        m = cols["sport"] == code
        assert abs(np.median(r[m])) < 0.01, (code, np.median(r[m]))
