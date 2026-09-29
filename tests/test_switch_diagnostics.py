"""The 2026-09-29 scorecards: scripts/switch_scorecard.py (a switch against
the baseline, held-out and on the boards) and diag_sport_gap --by-ability
(is the grass-to-track gap ability or pool?). Pure parts, synthetic data.

    python -m pytest -q tests/test_switch_diagnostics.py
"""
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "engine"), os.path.join(_ROOT, "scripts"),
           os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import switch_scorecard as sc                                  # noqa: E402


def _dump(rng, n, noise, rows=None, thin_noise=None):
    rows = np.arange(n) if rows is None else rows
    y = rng.normal(0, 0.1, n)
    season = np.where(np.arange(n) % 5 == 0, 1, 8)
    e = rng.normal(0, noise, n)
    if thin_noise is not None:
        e = np.where(season == 1, rng.normal(0, thin_noise, n), e)
    return {"row": rows, "y": y, "pred": y - e, "covered": np.ones(n, bool),
            "kind": np.array(["race"]), "sample_pct": np.array([15.0]),
            "sample_seed": np.array([11]),
            "season_train_rows": season.astype(np.int32),
            "pool": np.where(np.arange(n) % 2, "hs_m", "college_m")}


def test_holdout_compares_the_same_rows_by_bucket():
    rng = np.random.default_rng(0)
    base = _dump(rng, 20000, 0.04, thin_noise=0.06)
    test = dict(base)
    # the switch fixes the thin seasons only
    e = base["y"] - base["pred"]
    thin = base["season_train_rows"] == 1
    test["pred"] = np.where(thin, base["y"] - 0.7 * e, base["pred"])
    lines = "\n".join(sc.compareHoldout(base, test))
    assert "20,000 held-out rows covered by both" in lines
    one = next(l for l in lines.split("\n") if l.strip().startswith("1 rows"))
    six = next(l for l in lines.split("\n") if l.strip().startswith("6+ rows"))
    assert "-30.00%" in one and "*" in one, one
    assert "+0.00%" in six, six


def test_holdout_warns_on_different_splits():
    rng = np.random.default_rng(1)
    base = _dump(rng, 500, 0.04)
    test = dict(base, sample_seed=np.array([12]))
    assert any("NOT the same held-out rows" in l for l in sc.compareHoldout(base, test))


def test_boards_report_overlap_and_movers():
    n = 400
    rng = np.random.default_rng(2)
    rating = rng.normal(120, 15, n)
    base = {"rating": rating, "n_races": np.full(n, 6),
            "athlete_pool": np.zeros(n, dtype=np.int16)}
    test = dict(base, rating=rating.copy())
    top = np.argsort(-rating)[:25]
    test["rating"][top[0]] -= 40            # the leader drops out of the top
    person = np.array([f"p{i}" for i in range(n)])
    year = np.full(n, 2026)
    lines = sc.compareBoards(base, test, person, year, ["hs_m"], top=25)
    row = next(l for l in lines if l.strip().startswith("hs_m 2026"))
    assert "24/25" in row and f"p{top[0]}" in row, row


def test_gap_is_ability_not_pool():
    """A world where the gap is ONE curve in HS-equivalent ability and the
    pools just sit at different abilities: the raw pool medians spread, the
    polished pool effects do not."""
    import diag_sport_gap as dg
    rng = np.random.default_rng(3)
    rows = []
    factor = {"ms_m": 0.87, "hs_m": 1.0, "college_m": 1.21}
    centre = {"ms_m": 105.0, "hs_m": 125.0, "college_m": 150.0}   # HS-equivalent
    for pool, f in factor.items():
        a_hs = rng.normal(centre[pool], 12, 4000)
        gap = -0.03 + 0.0012 * (a_hs - 100) + rng.normal(0, 0.01, a_hs.size)
        own = a_hs / f
        # ln(xc / tf) = gap with sqrt(xc * tf) = own
        xc, tf = own * np.exp(gap / 2), own * np.exp(-gap / 2)
        rows += list(zip([pool] * a_hs.size, xc, tf))
    pools, gaps, abil = dg.gapRows(rows, factor.get)
    lines = dg.byAbilityLines(pools, gaps, abil, min_n=50)
    text = "\n".join(lines)
    assert "ability carries most of it" in text, text
    _o, _r, col = dg.medianPolish({k: v[1] for k, v in
                                   dg.binTable(pools, gaps, abil).items()})
    assert max(col.values()) - min(col.values()) < 0.003, col
    slopes, _ref = dg.poolSlopes(pools, gaps, abil)
    for p in factor:
        assert abs(slopes[p][2] - 0.012) < 0.002, slopes[p]


def test_gap_is_pool_when_the_pools_differ_at_one_ability():
    import diag_sport_gap as dg
    rng = np.random.default_rng(4)
    rows = []
    factor = {"ms_m": 0.87, "hs_m": 1.0}
    for pool, f in factor.items():
        a_hs = rng.normal(115, 15, 4000)
        gap = (-0.024 if pool == "ms_m" else -0.015) + rng.normal(0, 0.01, a_hs.size)
        own = a_hs / f
        rows += list(zip([pool] * a_hs.size, own * np.exp(gap / 2),
                         own * np.exp(-gap / 2)))
    pools, gaps, abil = dg.gapRows(rows, factor.get)
    text = "\n".join(dg.byAbilityLines(pools, gaps, abil, min_n=50))
    assert "the pools differ at the same ability" in text, text


def test_the_track_shift_is_taken_back_out():
    import diag_sport_gap as dg
    rows = [("hs_m", 100.0, 100.0 * np.exp(0.01))]      # tf carries +1%
    _p, g_in, _a = dg.gapRows(rows, lambda p: 1.0)
    _p, g_out, _a = dg.gapRows(rows, lambda p: 1.0, lambda p, r: 0.01)
    assert abs(g_in[0] + 0.01) < 1e-12 and abs(g_out[0]) < 1e-12
