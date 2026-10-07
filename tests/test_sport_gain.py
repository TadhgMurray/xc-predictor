"""The winter gain per ability (issue 194): the go-live's shift on track
rows makes the dual-sport page gap in each band the stated one, the
band shifts interpolate by rating, thin bands borrow, and the
conversions page applies the same shift.

    python -m pytest -q tests/test_sport_gain.py
"""
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "racecast"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.modules.setdefault("database", types.SimpleNamespace(getConn=None))
import joint_solve as js                                        # noqa: E402
import conversions as cv                                        # noqa: E402


def _world(seed=0, n_ath=900):
    """Athlete-seasons in three bands, two pools; every one races both
    sports; the track rows read 0.7% SLOWER than XC (the 2024+ reading),
    plus a per-athlete sport preference."""
    rng = np.random.default_rng(seed)
    rating = np.r_[rng.uniform(80, 100, n_ath // 3),
                   rng.uniform(106, 119, n_ath // 3),
                   rng.uniform(121, 140, n_ath - 2 * (n_ath // 3))]
    pool = rng.integers(0, 2, n_ath)
    rows = []
    for i in range(n_ath):
        pref = rng.normal(0, 0.01)
        for _ in range(5):
            rows.append((i, 0, rng.normal(0, 0.02)))
        for _ in range(4):
            rows.append((i, 1, 0.007 + pref + rng.normal(0, 0.02)))
    ath = np.array([r[0] for r in rows])
    sport = np.array([r[1] for r in rows])
    log_adj = np.array([r[2] for r in rows]) - 0.3 * (rating[ath] - 100) / 100
    return log_adj, sport, ath, rating, pool


def _gapByBand(log_adj, sport, ath, rating, pool, n_pool=2):
    _s, gap, n = js.sportGainShift(log_adj, sport, ath, rating, pool, n_pool,
                                   (0.0, 0.0, 0.0))
    return gap, n


def test_shift_makes_each_band_read_the_stated_gain():
    log_adj, sport, ath, rating, pool = _world()
    gains = (0.03, 0.02, 0.03)
    shift, gap, n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2, gains)
    assert (n >= 100).all(), n
    # what it read, on the mean the gap was first defined by
    _s, gap_mean, _n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2, gains,
                                         row_q=None, min_rows=1)
    assert np.allclose(gap_mean, 0.007, atol=0.004), gap_mean
    # ★ apply it as the go-live does (interpolated by rating): each band's
    #   MEDIAN gap now lands on -gain, as the boards read it (2026-10-03:
    #   the one-pass mean left hs 0.56% short of its stated level)
    row_shift = js.sportGainRow(rating[ath], pool[ath], shift)
    after = log_adj - np.where(sport == 1, row_shift, 0.0)
    gap2, _n = _gapByBand(after, sport, ath, rating, pool)
    for b, g in enumerate(gains):
        assert np.allclose(gap2[:, b], -g, atol=2e-4), (b, gap2[:, b])
    # and it is continuous: a 119.9 and a 120.1 get shifts a hair apart
    r = np.array([119.9, 120.1])
    s2 = js.sportGainRow(r, np.zeros(2, dtype=int), shift)
    assert abs(s2[0] - s2[1]) < 0.001
    # XC rows are untouched
    assert (after[sport == 0] == log_adj[sport == 0]).all()


def test_thin_band_borrows_and_no_gain_is_no_shift():
    log_adj, sport, ath, rating, pool = _world()
    # nobody above 120 in pool 1
    rating = rating.copy()
    rating[(pool == 1) & (rating > 120)] = 110.0
    shift, gap, n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2,
                                      (0.03, 0.02, 0.03))
    assert n[1, 2] == 0 and n[1, 1] > 0
    assert shift[1, 2] == shift[1, 1], "the top borrows the middle"
    assert shift[0, 2] != shift[0, 1]
    # stated gains of exactly the realised gap leave zero shift
    z, _g, _n = js.sportGainShift(log_adj, sport, ath, rating, pool, 2,
                                  tuple(-gap[0]))
    assert np.allclose(z[0], 0.0)


def test_conversions_apply_the_same_shift():
    cv._gain["map"] = {("hs_m", "TF"): [(90.0, 0.01), (112.0, 0.02), (130.0, 0.04)]}
    cv._gain["at"] = 1e18
    assert cv.sport_gain("hs_m", "TF", 80) == 0.01
    assert cv.sport_gain("hs_m", "TF", 140) == 0.04
    assert abs(cv.sport_gain("hs_m", "TF", 121) - 0.03) < 1e-9
    assert cv.sport_gain("hs_m", "XC", 121) == 0.0
    assert cv.sport_gain("hs_f", "TF", 121) == 0.0          # no row: none applied
    cv._scale["map"] = {("hs_m", "XC"): (1247.6, -0.025, -0.0253),
                        ("hs_m", "TF"): (1247.6, -0.055, -0.0253)}
    cv._scale["at"] = 1e18
    cv._offsets["map"] = {}
    cv._offsets["at"] = 1e18
    # a track time still converts to itself on the shifted scale
    ctx = {"distance": 3200.0, "pool": "hs_m", "sport": "TF"}
    norm = cv._norm_from_time(541.1, 3200.0, "hs_m", sport="TF", chosen=None)
    back = cv.normalized_to_time(norm, ctx)
    assert abs(back - 541.1) < 0.05, back
    # and rates HIGHER than with no shift, by the shift at its rating
    cv._gain["map"] = {}
    norm0 = cv._norm_from_time(541.1, 3200.0, "hs_m", sport="TF", chosen=None)
    assert norm < norm0


def test_conversions_interpolate_the_offset_between_bands():
    cv._offsets["map"] = {("hs_m", "TF", 3200, 0): 0.018, ("hs_m", "TF", 3200, 1): 0.009,
                          ("hs_m", "TF", 3200, 2): 0.0, ("hs_m", "TF", 800, 1): 0.004}
    cv._offsets["at"] = 1e18
    assert abs(cv.distance_offset("hs_m", "TF", 3200, rating=101) - 0.0135) < 1e-9
    assert abs(cv.distance_offset("hs_m", "TF", 3200, rating=130) - 0.0) < 1e-9
    assert abs(cv.distance_offset("hs_m", "TF", 3200, rating=80) - 0.018) < 1e-9
    a = cv.distance_offset("hs_m", "TF", 3200, rating=119.99)
    b = cv.distance_offset("hs_m", "TF", 3200, rating=120.01)
    assert abs(a - b) < 2e-5, "no step at the band edge"
    # a class with only the middle band still answers by band
    assert cv.distance_offset("hs_m", "TF", 800, rating=125) == 0.004


def test_a_skewed_gap_is_held_on_the_median():
    """The board reads the median athlete; a long tail of bad track days
    must not leave the median short of the stated gain."""
    rng = np.random.default_rng(3)
    n_ath = 3000
    rating = rng.uniform(90, 135, n_ath)
    pool = np.zeros(n_ath, dtype=int)
    rows = []
    for i in range(n_ath):
        tail = rng.exponential(0.03) if rng.random() < 0.3 else 0.0
        rows.append((i, 0, 0.0))
        rows.append((i, 1, 0.01 + 0.04 * (rating[i] - 90) / 45 + tail))
    ath = np.array([r[0] for r in rows])
    sport = np.array([r[1] for r in rows])
    log_adj = np.array([r[2] for r in rows])
    gains = (0.0092, 0.0092, 0.0092)
    # one row a sport: the quantile of one row is that row (min_rows=1 to
    # keep them; the boards' 3-race floor is tested below)
    shift, _gap, _n = js.sportGainShift(log_adj, sport, ath, rating, pool, 1, gains,
                                        min_rows=1)
    after = log_adj - np.where(sport == 1, js.sportGainRow(rating[ath], pool[ath], shift), 0.0)
    gap_ath = after[sport == 1] - after[sport == 0]
    band = np.digitize(rating, js.SPORT_GAIN_BANDS)
    for b in range(3):
        assert abs(np.median(gap_ath[band == b]) + 0.0092) < 2e-4


def _boardGap(log_adj, sport, ath, rating, n_ath):
    """What board_sanity reads: per athlete-season and sport the 80th
    percentile rating (rating ~ 1/adjusted time), 3+ races each, then the
    median of ln(TF/XC) per band, as a log-time gap (negative = track higher)."""
    out = {}
    band = np.digitize(rating, js.SPORT_GAIN_BANDS)
    for i in range(n_ath):
        tf = log_adj[(ath == i) & (sport == 1)]
        xc = log_adj[(ath == i) & (sport == 0)]
        if tf.size < 3 or xc.size < 3:
            continue
        r_tf = np.percentile(np.exp(-tf), 80)
        r_xc = np.percentile(np.exp(-xc), 80)
        out.setdefault(band[i], []).append(-np.log(r_tf / r_xc))
    return {b: float(np.median(v)) for b, v in out.items()}


def test_the_gap_is_held_on_the_season_number_the_boards_show():
    """2026-10-07 (run 20261006_120609's 10a): XC rows scatter more than
    track rows, so on the boards' 80th-percentile season number the mean-held
    gap came out short (hs 0.39% of 0.92%). Held on the quantile, the board
    reads the stated gain; held on the mean, it does not."""
    rng = np.random.default_rng(5)
    n_ath = 1500
    rating = rng.uniform(90, 135, n_ath)
    pool = np.zeros(n_ath, dtype=int)
    rows = []
    for i in range(n_ath):
        for _ in range(rng.integers(3, 9)):
            rows.append((i, 0, rng.normal(0, 0.035)))           # XC: courses, days
        for _ in range(rng.integers(3, 9)):
            rows.append((i, 1, 0.004 + rng.normal(0, 0.012)))   # track: tighter
        if rng.random() < 0.2:
            rows.append((i, 1, 0.0))                            # a lone extra: fine
    ath = np.array([r[0] for r in rows])
    sport = np.array([r[1] for r in rows])
    log_adj = np.array([r[2] for r in rows]) - 0.3 * (rating[ath] - 100) / 100
    gains = (0.0092, 0.0092, 0.0092)

    def _after(**kw):
        shift, _g, _n = js.sportGainShift(log_adj, sport, ath, rating, pool, 1, gains, **kw)
        return log_adj - np.where(sport == 1, js.sportGainRow(rating[ath], pool[ath], shift), 0.0)

    held = _boardGap(_after(), sport, ath, rating, n_ath)
    for b, g in held.items():
        assert abs(g + 0.0092) < 1.5e-3, (b, g)
    mean_held = _boardGap(_after(row_q=None, min_rows=1), sport, ath, rating, n_ath)
    assert all(g > -0.0092 + 3e-3 for g in mean_held.values()), mean_held


def test_group_quantile_is_percentile_cont():
    key = np.array([0, 0, 0, 0, 1, 1, 2])
    val = np.array([4.0, 1.0, 3.0, 2.0, 10.0, 20.0, 7.0])
    q = js._groupQuantile(key, val, 4, 0.2)
    assert np.allclose(q[:3], [np.percentile([1, 2, 3, 4], 20),
                               np.percentile([10, 20], 20), 7.0])
    assert np.isnan(q[3])


def test_the_quantile_mirrors_the_boards_season_number():
    src = open(os.path.join(ROOT, "racecast", "build_ranking_results.py"), encoding="utf-8").read()
    import re
    season_q = float(re.search(r"^_SEASON_Q = ([0-9.]+)", src, re.M).group(1))
    assert abs((1 - season_q) - js.SPORT_GAIN_ROW_Q) < 1e-9
    bs = open(os.path.join(ROOT, "scripts", "board_sanity.py"), encoding="utf-8").read()
    assert "n_races >= 3 AND t.n_races >= 3" in bs and js.SPORT_GAIN_MIN_ROWS == 3
