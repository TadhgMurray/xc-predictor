# Project: xc-predictor / tests
# File:    test_distance_siblings.py
# Purpose: one course listed at two near distances is one history in the
#          bracket engine (owner, 2026-09-28: "Mt. SAC 2024 was cannibalised
#          by a distance split"). 2021-2023 stored at 4715 m (key d4700,
#          +11.8%), 2024 at 4828 m (key d4800, +6.3%): the thin d4800 cell
#          must rest on d4700's history, not on the sport's average, and a
#          course without a near sibling must not move.
#
#   XCP_DB_PASSWORD=x python -m pytest -q tests/test_distance_siblings.py
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


def test_siblings_are_one_course_at_near_distances_only():
    keys = ["XC:500:d4700", "XC:500:d4800", "XC:500:d5000",    # Mt. SAC: 4715, 4828, West regional 5000
            "XC:600:d5000",                                    # alone
            "XC:name:Camp Dusk:d6000", "XC:name:Camp Dusk:d6100",
            "TF:loc:44:out", "TF:tfv:dover:out"]
    w = np.array([900, 12, 300, 50, 5, 40, 70, 70])
    h = be.nearDistanceSiblings(keys, w)
    # the thin 4800 shares the rich 4700's history; the 5000 (6% off) does not
    assert h[1] == 0 and h[0] == 0 and h[2] == 2
    assert h[3] == 3                                           # no sibling: itself
    assert h[4] == 5 and h[5] == 5                             # the richer one anchors
    # a 100 m key step is 3.1% at 3200 m: not a sibling at the default 3%
    assert list(be.nearDistanceSiblings(["XC:9:d3200", "XC:9:d3300"])) == [0, 1]
    assert h[6] == 6 and h[7] == 7                             # track keys untouched
    # off is off
    assert (be.nearDistanceSiblings(keys, w, tol=0) == np.arange(len(keys))).all()


def test_siblings_anchor_rather_than_chain():
    """4600-4700-4800-4900-5000 is 2% a step. Chained it would fold a 4600
    into a 5000; anchored on the rich 4800 it takes 4700 and 4900 (2.1%) and
    leaves 4600 and 5000 (4.3%) to anchor their own."""
    keys = [f"XC:7:d{d}" for d in (4600, 4700, 4800, 4900, 5000)]
    h = be.nearDistanceSiblings(keys, [10, 10, 100, 10, 10])
    assert list(h) == [0, 2, 2, 2, 4]
    # without weights, the shortest anchors and the answer is still stable
    h0 = be.nearDistanceSiblings(keys)
    assert list(h0) == [0, 0, 2, 2, 4]


def _mt_sac_world(seed=5):
    """Ordinary courses at 0; Mt. SAC at +10% raced 21 times over 2021-2023
    under d4700 (stored 4715 m); the same course raced ONCE in 2024 under
    d4800 (stored 4828 m); a control course, alone at d4800, raced once in
    2024 at the same +10%."""
    rng = np.random.default_rng(seed)
    n_ath, n_ord = 2400, 40
    a = rng.normal(0, 0.12, n_ath)
    yr_of = rng.integers(2021, 2025, n_ath)                    # one season each
    MTSAC, SIB, LONE = n_ord, n_ord + 1, n_ord + 2
    rows = []
    for i in range(n_ath):
        for k in range(8):
            rows.append((i, rng.integers(0, n_ord), 10 + 7 * k + rng.integers(0, 3), 0.0))
    for y in (2021, 2022, 2023):
        pool = np.flatnonzero(yr_of == y)
        for r in range(7):
            for i in rng.choice(pool, 25, replace=False):
                rows.append((i, MTSAC, 12 + 5 * r, 0.10))
    pool24 = np.flatnonzero(yr_of == 2024)
    for i in rng.choice(pool24, 15, replace=False):
        rows.append((i, SIB, 40, 0.10))
    for i in rng.choice(pool24, 15, replace=False):
        rows.append((i, LONE, 41, 0.10))
    ath = np.array([r[0] for r in rows]); course = np.array([r[1] for r in rows])
    day = np.array([r[2] for r in rows], dtype=np.float64)
    eff = np.array([r[3] for r in rows])
    year = yr_of[ath]
    days = (2025 - year) * 365.0 + day
    y = a[ath] + eff + rng.normal(0, 0.03, ath.size)
    keys = [f"XC:{100 + c}:d5000" for c in range(n_ord)]
    keys += ["XC:500:d4700", "XC:500:d4800", "XC:600:d4800"]
    cols = {"athlete": ath, "year": year, "course": course, "days": days,
            "sport": np.zeros(ath.size, dtype=np.int64), "norm": np.exp(y),
            "athlete_keys": [(i, "hs_m") for i in range(n_ath)],
            "course_keys": keys}
    return cols, MTSAC, SIB, LONE


def _cellOf(f, base_key):
    return [i for i, k in enumerate(f["cell_keys"]) if k.partition("@e")[0] == base_key]


def test_a_one_race_sibling_rests_on_its_course_not_the_average():
    """★ THE OWNER'S CASE. Before, d4800 was its own course with one race:
    pulled toward the sport's average like any course seen once (about half
    of its +10%). Now it rests on d4700's 21 races, and the control course --
    identical evidence, no sibling -- is exactly where it was."""
    cols, MTSAC, SIB, LONE = _mt_sac_world()
    kw = dict(window=21, top=1.0, prior_group=1.0, era_years=2)
    with contextlib.redirect_stdout(log := io.StringIO()):
        f = be.fit(cols, None, verbose=True, **kw)
    with contextlib.redirect_stdout(io.StringIO()):
        f0 = be.fit(cols, None, sibling_tol=0, **kw)
    assert "distance siblings: 1 XC courses" in log.getvalue()
    assert f["n_sibling"] == 1 and f0["n_sibling"] == 0
    assert f["history_of_base"][SIB] == MTSAC
    D, D0 = f["D"], f0["D"]
    sib, = _cellOf(f, "XC:500:d4800")
    sib0, = _cellOf(f0, "XC:500:d4800")
    lone, = _cellOf(f, "XC:600:d4800")
    lone0, = _cellOf(f0, "XC:600:d4800")
    rich = _cellOf(f, "XC:500:d4700")
    print(f"  d4800 sibling {100 * D0[sib0]:+.2f}% -> {100 * D[sib]:+.2f}%, "
          f"lone {100 * D0[lone0]:+.2f}% -> {100 * D[lone]:+.2f}%, "
          f"d4700 eras {np.round(100 * D[rich], 2)}")
    # the rich course reads its planted +10% and did not move
    assert all(abs(D[c] - 0.10) < 0.015 for c in rich), D[rich]
    # before: a thin cell pulled toward the average (well short of +10%)
    assert D0[sib0] < 0.075, D0[sib0]
    # now: pulled toward the course's history instead
    assert D[sib] > 0.088, D[sib]
    assert D[sib] - D0[sib0] > 0.02
    # the same evidence with no sibling: unchanged, still about half
    assert abs(D[lone] - D0[lone0]) < 0.004, (D[lone], D0[lone0])
    assert D[lone] < D[sib] - 0.02
    # each distance keeps its own cell and its own races
    assert f["races_per_cell"][sib] == 1
    assert len(f["cell_keys"]) == len(f0["cell_keys"])


def test_the_trace_reconciles_with_the_shared_history():
    """scripts/course_bracket.engineCells rebuilds era = (raw x votes + 2 x
    history) / (votes + 2) from bracket_base per cell. With a shared history
    that must still be the engine's own pre-pin number, cell by cell."""
    cols, MTSAC, SIB, LONE = _mt_sac_world(seed=8)
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, window=21, top=1.0, prior_group=1.0, era_years=2)
    b_of = np.asarray(f["base_of_cell"])
    base = np.asarray(f["D_base"])[b_of]
    raw = np.nan_to_num(f["D_cell_raw"])
    votes = f["votes"]
    era = (raw * votes + f["prior_races"] * base) / (votes + f["prior_races"])
    rebuilt = era - f["pin"]
    ok = votes > 0
    assert np.allclose(rebuilt[ok], f["D_fit"][ok], atol=1e-9)
    # and the sibling's history IS the rich course's
    sib, = _cellOf(f, "XC:500:d4800")
    rich = _cellOf(f, "XC:500:d4700")
    assert all(base[sib] == base[c] for c in rich)
    assert all(f["own_base_of_cell"][sib] != b_of[c] for c in rich)


def test_worlds_without_siblings_are_unchanged():
    """No near-distance sibling anywhere: the switch changes nothing."""
    cols, MTSAC, SIB, LONE = _mt_sac_world(seed=3)
    keys = list(cols["course_keys"])
    keys[SIB] = "XC:500:d5200"                         # 10.6% off: not a sibling
    cols["course_keys"] = keys
    kw = dict(window=21, top=1.0, prior_group=1.0, era_years=2)
    with contextlib.redirect_stdout(io.StringIO()):
        f = be.fit(cols, None, **kw)
        f0 = be.fit(cols, None, sibling_tol=0, **kw)
    assert f["n_sibling"] == 0
    assert np.array_equal(f["D"], f0["D"])
