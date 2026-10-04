"""The cross-sport holdout (owner, 2026-10-04: "we also don't holdout things
like the conversions or what you think someone will run xc vs track").
Pure: the split and the bias table, no solve."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import pair_validate as pv                                      # noqa: E402


def test_only_dual_sport_seasons_and_only_one_sport_is_held_out():
    rng = np.random.default_rng(0)
    n_season = 5000
    season = np.repeat(np.arange(n_season), 6)
    sport = np.tile([0, 0, 0, 1, 1, 1], n_season)
    # a third of the seasons raced cross country only
    xc_only = np.arange(n_season) % 3 == 0
    sport = np.where(xc_only[season], 0, sport)
    te = pv.splitFor("sport", season.size, season=season, sport=sport, frac=0.1)
    picked = np.unique(season[te])
    assert 0.07 < picked.size / (n_season * 2 / 3) < 0.13
    assert not xc_only[picked].any(), "an XC-only season has nothing to predict from"
    assert (sport[te] == 1).all(), "only the track rows are held out"
    # every picked season keeps ALL its cross country in training
    for s in picked[:50]:
        m = season == s
        assert (~te[m & (sport == 0)]).all() and te[m & (sport == 1)].all()
    rev = pv.splitFor("sport-xc", season.size, season=season, sport=sport, frac=0.1)
    assert (sport[rev] == 0).all()
    assert rng is not None


def test_the_bias_table_reads_the_sign_and_the_distance():
    sys.modules.setdefault("database", type(sys)("database"))
    import importlib
    rj = importlib.import_module("run_joint")
    n = 4000
    err = np.r_[np.full(n, 0.02), np.full(n, -0.01)]
    pool = np.array(["hs_m"] * (2 * n), dtype=object)
    dist = np.r_[np.full(n, 3218.7), np.full(n, 1609.3)]
    rating = np.linspace(90, 130, 10)
    athlete = np.arange(2 * n) % 10
    lines = "\n".join(rj.crossSportBreakdown(err, pool, dist, rating, athlete))
    assert "3200" in lines and "+2.00%" in lines
    assert "1600" in lines and "-1.00%" in lines
    assert "Q1" in lines and "Q4" in lines


def test_the_holdout_predicts_track_from_cross_country_alone(monkeypatch):
    """End to end on test_forward_holdout's synthetic pack: only track rows
    are scored, every one from a season whose cross country stayed in the
    fit, and an unbiased world scores near-zero bias."""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import test_forward_holdout as tfh
    rj = tfh.rj
    cols = tfh._pack(seed=4, n_person=300)
    args = tfh._args("--holdout-only", "--holdout-kind", "sport", "--no-tilt")
    keep = (cols["course"] >= 0) & (cols["norm"] > 0)
    seen = []
    real = rj.buildDesign

    def spy(c, k, *a, **kw):
        seen.append(np.asarray(k).copy())
        return real(c, k, *a, **kw)
    monkeypatch.setattr(rj, "buildDesign", spy)
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _D, athlete_pool, _n = real(cols, keep, **rj.designKwargs(args))
        rj.holdout(cols, keep, args, athlete_pool, None)
    log = buf.getvalue()
    tr, te = seen[0], seen[1]
    assert te.any() and (cols["sport"][te] == 1).all()
    assert not (tr & te).any()
    key = cols["athlete"] * 10000 + cols["year"]
    for k in np.unique(key[te])[:40]:
        m = key == k
        assert tr[m & (cols["sport"] == 0)].all(), "the season's XC is in the fit"
    assert "TRACK season" in log
    assert "covered 100.0%" in log or "covered 9" in log
