# Project: xc-predictor / tests
# File:    test_bracket_engine.py
# Purpose: the bracket engine reads planted course difficulties back from
#          the same synthetic worlds the joint solve is tested on: the era
#          world's drifting course, and the track world's ovals; and its
#          held-out prediction covers and scores.
#
#   python -m pytest -q tests/test_bracket_engine.py
import io
import contextlib
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket_engine as be                                    # noqa: E402
import bracket_holdout as bh                                   # noqa: E402
from test_era_publish import _era_pack                         # noqa: E402
from test_track_diagnostics import _track_pack                 # noqa: E402


def test_the_engine_follows_a_course_that_changed():
    cols, keep, keys, sport_of_course = _era_pack()
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=60, top=1.0, era_years=2, verbose=True)
    D = f["D"]
    eras0 = sorted((int(k.rpartition("@e")[2]), i) for i, k in enumerate(f["cell_keys"])
                   if k.startswith(keys[0] + "@e"))
    d0 = np.array([D[i] for _, i in eras0])
    moved = float(d0[-1] - d0[0])
    print(f"  drifting course: {np.round(d0 - d0[0], 4)} (the rows moved about +0.031 "
          f"first era to last; the era pull keeps some back)")
    assert 0.02 < moved < 0.05, moved
    # without the pull toward the course's history the engine reads the
    # rows' own movement, the same number the joint solve found
    with contextlib.redirect_stdout(io.StringIO()):
        f0 = be.fit(cols, None, window=60, top=1.0, era_years=2, prior_rows=0.0)
    d00 = np.array([f0["D"][i] for _, i in eras0])
    assert 0.025 < float(d00[-1] - d00[0]) < 0.05, d00
    # a course that held still publishes about the same number from any era
    eras1 = [i for i, k in enumerate(f["cell_keys"]) if k.startswith(keys[1] + "@e")]
    assert np.ptp(D[eras1]) < 0.015, np.ptp(D[eras1])
    # every course with rows has votes
    assert (f["votes"] > 0).sum() == len(f["cell_keys"])


def test_the_engine_reads_the_ovals_and_the_meet_mix():
    cols, npz, level = _track_pack()
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, {"rating": npz["rating"]}, window=90, top=0.5, era_years=0,
                   tilt=False)
    D = f["D"]
    ovals = D[:6].mean() - D[6:].mean()
    print(f"  ovals minus outdoor tracks: {ovals:+.4f} (planted {level:+.4f})")
    assert abs(ovals - level) < 0.004, ovals
    # tracks that host championships read harder, stacked ones easier
    ordinary = D[6:16].mean(); champ = D[16:26].mean(); stacked = D[26:36].mean()
    assert champ > ordinary + 0.004 and stacked < ordinary - 0.001, (ordinary, champ, stacked)


def test_the_holdout_split_is_the_runners_and_the_engine_predicts_it():
    cols, keep, keys, sport_of_course = _era_pack()
    train, test = bh.sampleAndSplit(cols, pct=100, seed=11)
    assert train.sum() + test.sum() == keep.sum() and 0.05 < test.mean() < 0.2
    # a held-out race is held out whole
    import run_joint as rj
    race, _ = rj.raceCodes(cols["course"], cols["days"])
    assert not set(race[train]) & set(race[test])
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, train=train, window=60, top=1.0, era_years=2)
    pred, cov = be.predict(f)
    y = np.log(cols["norm"])
    m = test & cov
    assert cov[test].mean() > 0.9
    err = y[m] - pred[m]
    print(f"  held-out error sd {err.std():.4f} on {int(m.sum()):,} rows")
    assert err.std() < 0.045
    # and the training rows' own residuals are smaller than the raw spread
    mt = train & cov
    assert (y[mt] - pred[mt]).std() < 0.5 * y[mt].std()
