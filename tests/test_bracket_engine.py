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


def test_an_island_of_two_cells_settles_instead_of_swapping():
    """★ THE CORPUS RUN OF 2026-09-12: max change 0.161 from pass 4 to pass
    30. Two cells whose runners' only other races are at each other are an
    island: a full step swaps their difficulties for ever. A half step
    settles them, and the island's sum stays at its prior (zero)."""
    rng = np.random.default_rng(7)
    n_ath = 300
    rows = []
    for i in range(n_ath):
        # every athlete races cell 0 on day 10 and cell 1 on day 20, nothing else
        rows.append((i, 0, 10.0)); rows.append((i, 1, 20.0))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    days = np.array([r[2] for r in rows])
    a = rng.normal(0, 0.1, n_ath)
    y = a[ath] + np.where(course == 1, 0.08, 0.0) + rng.normal(0, 0.01, ath.size)
    cols = {"athlete": ath, "year": np.full(ath.size, 2025), "course": course,
            "days": days, "sport": np.zeros(ath.size, dtype=np.int64), "norm": np.exp(y),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": ["XC:100:d5000", "XC:101:d5000"]}
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        f1 = be.fit(cols, None, window=30, top=1.0, damping=1.0, n_iter=40, verbose=True,
                    prior_group=0.0)
    lines1 = [ln for ln in log.getvalue().splitlines() if "iteration" in ln]
    assert len(lines1) == 40, "a full step never converges on an island"
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=30, top=1.0, n_iter=60, verbose=True, prior_group=0.0)
    D = f["D"]
    assert abs(D[1] - D[0] - 0.08) < 0.01, D
    assert abs(D[0] + D[1]) < 1e-6, D          # the island's sum stays at its prior
    # and the default damping converged well inside the passes allowed
    with contextlib.redirect_stdout(log2 := io.StringIO()):
        be.fit(cols, None, window=30, top=1.0, n_iter=60, verbose=True, prior_group=0.0)
    assert len([ln for ln in log2.getvalue().splitlines() if "iteration" in ln]) < 40


def test_a_course_seen_once_keeps_half_of_what_the_day_showed():
    """★ OWNER, 2026-09-13: "a 10 result venue should not have +17%". A race
    is one reading of a course; a course with one race keeps about half of
    it, one with thirty races keeps nearly all. Planted: 40 ordinary
    courses, one course raced once by 10 people on a day that ran +17%
    slower (the course itself is ordinary), one course raced 30 times at a
    real +8%."""
    rng = np.random.default_rng(21)
    n_ath = 3000
    n_ord = 40
    a = rng.normal(0, 0.12, n_ath)
    rows = []                                          # (ath, course, day, effect)
    for i in range(n_ath):
        for k in range(8):                             # eight ordinary races a season
            rows.append((i, rng.integers(0, n_ord), 10 + 7 * k + rng.integers(0, 3), 0.0))
    # the well-raced hard course: 30 races, 25 runners each, +8%
    for r in range(30):
        for i in rng.choice(n_ath, 25, replace=False):
            rows.append((i, n_ord, 12 + 2 * r, 0.08))
    # the once-raced course: 10 runners, one day, +17% that day
    for i in rng.choice(n_ath, 10, replace=False):
        rows.append((i, n_ord + 1, 40, 0.17))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    days = np.array([r[2] for r in rows], dtype=np.float64); eff = np.array([r[3] for r in rows])
    y = a[ath] + eff + rng.normal(0, 0.03, ath.size)
    cols = {"athlete": ath, "year": np.full(ath.size, 2025), "course": course, "days": days,
            "sport": np.zeros(ath.size, dtype=np.int64), "norm": np.exp(y),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": [f"XC:{100 + c}:d5000" for c in range(n_ord + 2)]}
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=21, top=1.0, prior_group=1.0)   # the stated prior
    D = f["D"]
    assert f["races_per_base"][n_ord + 1] == 1 and f["races_per_base"][n_ord] == 30
    # the once-raced course: about half of +17% (the prior is one race)
    assert 0.06 < D[n_ord + 1] < 0.12, D[n_ord + 1]
    # the well-raced course keeps its +8% within a percent
    assert abs(D[n_ord] - 0.08) < 0.012, D[n_ord]
    # and without the group prior the one-race course takes the whole day
    with contextlib.redirect_stdout(io.StringIO()):
        f0 = be.fit(cols, None, window=21, top=1.0, prior_group=0.0)
    assert f0["D"][n_ord + 1] > 0.14, f0["D"][n_ord + 1]


def _priorWorld(tau, sig, n_c=80, r=6, n_v=20, seed=1, keys=None):
    """n_c courses whose true difficulties have sd `tau`, each raced r
    times with a race-day effect of sd `sig`, n_v runners a race, individual
    noise 3%."""
    rng = np.random.default_rng(seed)
    n_ath = 4000
    a = rng.normal(0, 0.12, n_ath)
    d = rng.normal(0, tau, n_c)
    rows = []
    day = 0
    for c in range(n_c):
        for _k in range(r):
            u = rng.normal(0, sig)
            day += 1
            for i in rng.choice(n_ath, n_v, replace=False):
                rows.append((i, c, day % 60 + 7 * (day // 60), d[c] + u))
    rows = np.array(rows, dtype=float)
    ath = rows[:, 0].astype(int); course = rows[:, 1].astype(int)
    days = rows[:, 2]; eff = rows[:, 3]
    y = a[ath] + eff + rng.normal(0, 0.03, ath.size)
    keys = keys or [f"XC:{100 + c}:d5000" for c in range(n_c)]
    sport = 1 if keys[0].startswith("TF:") else 0
    cols = {"athlete": ath, "year": np.full(ath.size, 2025), "course": course, "days": days,
            "sport": np.full(ath.size, sport), "norm": np.exp(y),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)], "course_keys": keys}
    return cols, d


def test_the_prior_is_fitted_per_group_from_the_two_variances():
    """★ OWNER, 2026-09-13: "shrink variance for outdoor courses and let
    indoor keep its difficulty". The prior in races is the race-day
    variance over the course variance, read from the courses with 2+
    races. Grass at the corpus' numbers (course sd 3.5%, day sd 3%) fits
    about one race, as stated; an outdoor track world (course sd 0.9%,
    day sd 1.45%) fits several, and its board is shrunk harder; a world
    with no day effect fits under one."""
    cols, d = _priorWorld(0.035, 0.03)
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=21, top=1.0)
    assert f["prior_group_fitted"]
    k_xc = f["prior_group"][0]
    assert 0.6 < k_xc < 1.6, f["prior_lines"]
    assert np.corrcoef(f["D"], d)[0, 1] > 0.9
    cols, d = _priorWorld(0.009, 0.0145, keys=[f"TF:loc:{c}:out" for c in range(80)])
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=21, top=1.0)
        f1 = be.fit(cols, None, window=21, top=1.0, prior_group=1.0)
    k_out = f["prior_group"][1]
    assert k_out > 2.5, f["prior_lines"]
    assert f["prior_group"][0] == be.PRIOR_GROUP_BY["XC"]       # no XC courses: stated
    # the fitted prior shrinks the track board harder than one race does,
    # and lands nearer the truth
    assert np.std(f["D"]) < np.std(f1["D"])
    assert np.abs(f["D"] - d).mean() < np.abs(f1["D"] - d).mean()
    cols, d = _priorWorld(0.03, 0.0)
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=21, top=1.0)
    assert f["prior_group"][0] < 0.6, f["prior_lines"]


def test_a_thin_oval_sits_at_the_indoor_level_not_the_outdoor_one():
    """Each group is pulled toward ITS OWN average: an indoor oval raced
    once is shrunk toward the indoor level, not toward the outdoor zero."""
    rng = np.random.default_rng(5)
    n_ath = 3000
    a = rng.normal(0, 0.12, n_ath)
    rows = []
    # 20 outdoor tracks at 0, 6 races each; 5 ovals at +1.5%, 6 races each;
    # one oval at +1.5% raced once, in the same window as the others
    for c in range(20):
        for k in range(6):
            for i in rng.choice(n_ath, 25, replace=False):
                rows.append((i, c, 100 + 3 * k + c % 3, 0.0))
    for c in range(20, 25):
        for k in range(6):
            for i in rng.choice(n_ath, 25, replace=False):
                rows.append((i, c, 85 + 3 * k + c % 3, 0.015))
    for i in rng.choice(n_ath, 25, replace=False):
        rows.append((i, 25, 95, 0.015))
    # the ovals' days end where the tracks' begin, so the indoor level is
    # read through athletes with both inside one window (the March seam)
    rows = np.array(rows, dtype=float)
    ath = rows[:, 0].astype(int); course = rows[:, 1].astype(int)
    days = rows[:, 2]; eff = rows[:, 3]
    y = a[ath] + eff + rng.normal(0, 0.02, ath.size)
    keys = [f"TF:loc:{c}:out" for c in range(20)] + [f"TF:loc:{c}:in" for c in range(20, 26)]
    cols = {"athlete": ath, "year": np.full(ath.size, 2025), "course": course, "days": days,
            "sport": np.ones(ath.size, dtype=np.int64), "norm": np.exp(y),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)], "course_keys": keys}
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=21, top=1.0, prior_group={"TF:in": 1.0, "TF:out": 2.5})
    D = f["D"]
    lvl = D[20:25].mean() - D[:20].mean()
    assert lvl > 0.010, lvl                        # the ovals' level is read
    # the once-raced oval sits with the other ovals, not halfway to outdoor
    assert D[25] - D[:20].mean() > 0.7 * lvl, (D[25], lvl)


def test_the_prior_spec_parses():
    assert be.parsePrior(None) == be.PRIOR_FIT
    assert be.parsePrior("fit") == be.PRIOR_FIT
    assert be.parsePrior("2.5") == 2.5
    got = be.parsePrior("TF:out=3, XC=0.8")
    assert got == {"XC": 0.8, "TF:out": 3.0, "TF:in": be.PRIOR_GROUP_BY["TF:in"]}
    stated, fitted = be._statedPriors("XC=2")
    assert list(stated) == [2.0, be.PRIOR_GROUP_BY["TF:out"], be.PRIOR_GROUP_BY["TF:in"]] and not fitted
    stated, fitted = be._statedPriors(0.0)
    assert list(stated) == [0.0, 0.0, 0.0] and not fitted
    try:
        be.parsePrior("TF=1")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown group must be refused")
