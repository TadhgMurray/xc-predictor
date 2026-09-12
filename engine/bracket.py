"""
bracket.py -- the same-athlete window comparison, once, vectorised.

★ WHAT (owner, 2026-09-12: "for each person, compare it to what they ran
  the past couple of weeks and next couple of weeks; that's how hard the
  course is, after you account for fitness"; "use the fitness curve to
  make 21 days more clean"). For every row: its log normalized time, less
  the athlete's own point on the season form curve, minus the mean of the
  same athlete-season's other rows in the same sport within +-window days
  at OTHER base cells, each less their curve point. Positive = slower here
  than in their own bracket. The curve takes the fitness gained or lost
  inside the window out of the comparison; without a curve in the file
  the raw log times are compared, which is the same thing over a window
  short enough for fitness to hold still.

★ HOW. Two sorts and prefix sums, O(n log n): the window sum over the
  athlete-season's rows in the sport, minus the window sum over its rows
  at the same cell. No per-row Python. course_bracket.py, the indoor
  check, the track diagnostic and the bracket engine all read this one
  function, so they cannot disagree about what a bracket is.
"""
import numpy as np

import joint_solve as js
import pair_engine as pe

_BIG = 100_000                     # > the range of days ago, for composite keys


def curveOnRows(npz, pool_row, doy, rating=None):
    """The form curve's contribution per row: amp(rating) * f_pool(day),
    from the solve file's `curve` grid ((pool, knot), the reference knot at
    zero, exactly as the Design evaluates it). Zeros without a curve."""
    if npz is None or "curve" not in npz:
        return np.zeros(np.asarray(doy).size)
    c = np.asarray(npz["curve"], dtype=np.float64)
    if c.ndim == 1:
        c = c.reshape(-1, js.CURVE_N_KNOTS)
    n_pool, n_knot = c.shape
    # ! `curve_knot_days` in the solve file is the knot POSITIONS in academic
    #   days (0, 30, 60, ...), not the spacing; reading its first entry as
    #   the spacing divided by zero (2026-09-12). The spacing is the step.
    kp = np.atleast_1d(np.asarray(npz.get("curve_knot_days", js.CURVE_KNOT_DAYS),
                                  dtype=np.float64))
    kd = float(kp[1] - kp[0]) if kp.size >= 2 else float(kp[0])
    if not np.isfinite(kd) or kd <= 0:
        kd = float(js.CURVE_KNOT_DAYS)
    pool = np.asarray(pool_row, dtype=np.int64)
    t = js.academicDay(np.asarray(doy, dtype=np.float64))
    k0 = np.minimum((t // kd).astype(np.int64), n_knot - 2)
    w1 = np.clip((t - k0 * kd) / kd, 0.0, 1.0)
    ok = (pool >= 0) & (pool < n_pool)
    p = np.where(ok, pool, 0)
    v = (1.0 - w1) * c[p, k0] + w1 * c[p, k0 + 1]
    v = np.where(ok, v, 0.0)
    if rating is not None:
        v = v * js.amplitudeFromRating(np.nan_to_num(np.asarray(rating, dtype=np.float64),
                                                     nan=100.0))
    return v


def windowSumsAt(key_ref, days_ref, z_ref, key_q, days_q, window):
    """For each query (key, day): the sum and count of z over REFERENCE rows
    with the same key and days within +-window. Vectorised: one sort of the
    references, two searchsorteds per query, prefix sums."""
    key_ref = np.asarray(key_ref, dtype=np.int64)
    days_ref = np.round(np.asarray(days_ref, dtype=np.float64)).astype(np.int64)
    z_ref = np.asarray(z_ref, dtype=np.float64)
    key_q = np.asarray(key_q, dtype=np.int64)
    days_q = np.round(np.asarray(days_q, dtype=np.float64)).astype(np.int64)
    order = np.lexsort((days_ref, key_ref))
    k_s = key_ref[order] * _BIG + days_ref[order]
    lo = np.searchsorted(k_s, key_q * _BIG + days_q - int(window), side="left")
    hi = np.searchsorted(k_s, key_q * _BIG + days_q + int(window), side="right")
    P = np.r_[0.0, np.cumsum(z_ref[order])]
    return P[hi] - P[lo], (hi - lo).astype(np.int64)


def _windowSums(key, days, z, window):
    """Per row, the sum and count of z over rows sharing `key` with days
    within +-window (the row itself included)."""
    return windowSumsAt(key, days, z, key, days, window)


class WindowIndex:
    """windowSumsAt with the sort and the searches done once: the bracket
    engine iterates on the values but never on the geometry. `sums(z_ref)`
    returns (sum, count) per query over the reference rows in the window."""

    def __init__(self, key_ref, days_ref, key_q, days_q, window):
        key_ref = np.asarray(key_ref, dtype=np.int64)
        days_ref = np.round(np.asarray(days_ref, dtype=np.float64)).astype(np.int64)
        key_q = np.asarray(key_q, dtype=np.int64)
        days_q = np.round(np.asarray(days_q, dtype=np.float64)).astype(np.int64)
        self.order = np.lexsort((days_ref, key_ref))
        k_s = key_ref[self.order] * _BIG + days_ref[self.order]
        self.lo = np.searchsorted(k_s, key_q * _BIG + days_q - int(window), side="left")
        self.hi = np.searchsorted(k_s, key_q * _BIG + days_q + int(window), side="right")
        self.count = (self.hi - self.lo).astype(np.int64)

    def sums(self, z_ref):
        P = np.r_[0.0, np.cumsum(np.asarray(z_ref, dtype=np.float64)[self.order])]
        return P[self.hi] - P[self.lo], self.count


def windowBracket(z, season, sport, cell, days, window):
    """Per row: z minus the mean z of the same (season, sport)'s other rows
    within +-window days at a different base cell; NaN where there are
    none or the row has no cell. Returns (bracket, n_other)."""
    z = np.asarray(z, dtype=np.float64)
    season = np.asarray(season, dtype=np.int64)
    sport = np.asarray(sport, dtype=np.int64)
    cell = np.asarray(cell, dtype=np.int64)
    days = np.round(np.asarray(days, dtype=np.float64)).astype(np.int64)
    n = z.size
    out = np.full(n, np.nan)
    n_other = np.zeros(n, dtype=np.int64)
    ok = (cell >= 0) & np.isfinite(z)
    if not ok.any():
        return out, n_other
    idx = np.flatnonzero(ok)
    zz, ss, sp, cc, dd = z[idx], season[idx], sport[idx], cell[idx], days[idx]
    run = ss * 2 + np.clip(sp, 0, 1)
    sum_all, n_all = _windowSums(run, dd, zz, int(window))
    C = int(cc.max()) + 1
    sum_same, n_same = _windowSums(run * C + cc, dd, zz, int(window))
    n_o = n_all - n_same
    with np.errstate(invalid="ignore", divide="ignore"):
        other = (sum_all - sum_same) / n_o
    got = n_o > 0
    out[idx[got]] = zz[got] - other[got]
    n_other[idx] = n_o
    return out, n_other


def bracketRows(cols, npz=None, window=21, use_curve=True):
    """The bracket for every row of a pack, curve-corrected when the solve
    file carries a curve and ratings. Returns dict(bracket, n_other, z,
    season, n_season, curve)."""
    ath_raw = np.asarray(cols["athlete"]).astype(np.int64)
    year = np.asarray(cols["year"]).astype(np.int64)
    season, n_season = pe.athleteSeasonCodes(ath_raw, year)
    ln = np.log(np.asarray(cols["norm"], dtype=np.float64))
    curve = np.zeros(ln.size)
    if use_curve and npz is not None and "curve" in npz and "doy" in cols:
        pool_of_raw, _names = _poolCodes(cols["athlete_keys"])
        rating = None
        if "rating" in npz and np.asarray(npz["rating"]).size == n_season:
            rating = np.asarray(npz["rating"], dtype=np.float64)[season]
        curve = curveOnRows(npz, pool_of_raw[ath_raw], cols["doy"], rating)
    z = ln - curve
    sport = np.asarray(cols["sport"]).astype(np.int64) if "sport" in cols else np.zeros(ln.size, np.int64)
    bracket, n_other = windowBracket(z, season, sport, cols["course"], cols["days"], window)
    return dict(bracket=bracket, n_other=n_other, z=z, season=season,
                n_season=n_season, curve=curve)


def _poolCodes(athlete_keys):
    import run_joint as rj
    return rj.poolCodes(athlete_keys)
