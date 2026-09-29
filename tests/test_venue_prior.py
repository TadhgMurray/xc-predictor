# Project: xc-predictor / tests
# File:    test_venue_prior.py
# Purpose: the bracket engine's venue prior (owner, 2026-09-29). Mt. SAC's
#          2024 layout (4828 m, key d4800) is its own course with its own
#          number, but with one meet of evidence it is shrunk toward the
#          VENUE's reading (its 4715 m course, d4700), not the average
#          course. A layout with a season of its own reads its own days,
#          a course alone is where it was, and the rich course does not move.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_venue_prior.py
import io
import contextlib
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bracket_engine as be                                    # noqa: E402


def test_venue_links_join_one_course_across_distances():
    keys = ["XC:500:d4700", "XC:500:d4800", "XC:500:d2300", "XC:600:d4800",
            "XC:name:Camp Dusk:d6000", "XC:name:Camp Dusk:d8000", "TF:loc:44:out"]
    place, n = be.placeClusters(keys, None, None)
    assert n == 2
    assert place[0] == place[1] == place[2] >= 0
    assert place[3] == -1                                      # another course
    assert place[4] == place[5] >= 0 and place[4] != place[0]
    assert place[6] == -1                                      # track untouched
    place0, n0 = be.placeClusters(keys, None, None, venue=False)
    assert n0 == 0 and (place0 == -1).all()


def test_venue_and_coordinate_links_are_one_place():
    """A course 100 m from Mt. SAC's d4700 at the same distance, and Mt. SAC's
    d4800: one component through d4700."""
    keys = ["XC:500:d4700", "XC:500:d4800", "XC:700:d4700"]
    lat = np.array([34.0480, 34.0480, 34.0489])
    lon = np.array([-117.845, -117.845, -117.845])
    place, n = be.placeClusters(keys, lat, lon, 400.0)
    assert n == 1 and len(set(place)) == 1 and place[0] >= 0


def _world(seed=5, new_eff=0.10, new_days=1):
    """Ordinary courses at 0; the venue's old layout at +10% raced 21 times;
    its new layout (another distance) at `new_eff` raced on `new_days` days;
    a control course alone raced once at `new_eff`."""
    rng = np.random.default_rng(seed)
    n_ath, n_ord = 2400, 40
    a = rng.normal(0, 0.12, n_ath)
    OLD, NEW, LONE = n_ord, n_ord + 1, n_ord + 2
    rows = []
    for i in range(n_ath):
        for k in range(8):
            rows.append((i, rng.integers(0, n_ord), 10 + 7 * k + rng.integers(0, 3), 0.0))
    for r in range(21):
        for i in rng.choice(n_ath, 25, replace=False):
            rows.append((i, OLD, 12 + 2 * r, 0.10))
    for r in range(new_days):
        for i in rng.choice(n_ath, 15, replace=False):
            rows.append((i, NEW, 40 + 2 * r, new_eff))
    for i in rng.choice(n_ath, 15, replace=False):
        rows.append((i, LONE, 41, new_eff))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    days = np.array([r[2] for r in rows], dtype=np.float64)
    eff = np.array([r[3] for r in rows])
    y = a[ath] + eff + rng.normal(0, 0.03, ath.size)
    keys = [f"XC:{100 + c}:d5000" for c in range(n_ord)]
    keys += ["XC:500:d4700", "XC:500:d4800", "XC:600:d4800"]
    cols = {"athlete": ath, "year": np.full(ath.size, 2024), "course": course,
            "days": days, "sport": np.zeros(ath.size, dtype=np.int64),
            "norm": np.exp(y), "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": keys}
    return cols, OLD, NEW, LONE


def _fit(cols, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return be.fit(cols, None, window=21, top=1.0, prior_group=1.0, **kw)


def test_one_meet_on_a_new_layout_rests_on_its_venue():
    cols, OLD, NEW, LONE = _world()
    f, f0 = _fit(cols), _fit(cols, place_venue=False)
    D, D0 = f["D"], f0["D"]
    print(f"  new layout {100 * D0[NEW]:+.2f}% -> {100 * D[NEW]:+.2f}%, "
          f"lone {100 * D0[LONE]:+.2f}% -> {100 * D[LONE]:+.2f}%")
    assert abs(D[OLD] - 0.10) < 0.015 and abs(D[OLD] - D0[OLD]) < 0.003
    assert D0[NEW] < 0.075                     # before: halfway to the average
    assert D[NEW] > 0.085 and D[NEW] - D0[NEW] > 0.02
    assert abs(D[LONE] - D0[LONE]) < 0.004     # a course alone is unchanged
    assert f["n_sibling"] == 0                 # its own cell, its own history


def test_a_new_layout_with_a_season_reads_its_own_days():
    """Different courses, different difficulties: the new layout at +4% over
    twelve race days lands near +4%, not the venue's +10%."""
    cols, OLD, NEW, LONE = _world(seed=9, new_eff=0.04, new_days=12)
    D = _fit(cols)["D"]
    print(f"  old {100 * D[OLD]:+.2f}%, new {100 * D[NEW]:+.2f}%")
    assert abs(D[NEW] - 0.04) < 0.015, D[NEW]
    assert abs(D[OLD] - 0.10) < 0.015, D[OLD]
