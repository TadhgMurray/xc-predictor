"""
distance_tables.py -- the published distance-equivalence relation, as a prior.

★ WHY THIS EXISTS (2026-09-11, issues 9 / 13 / 21). normalized_time puts
  every track event on the pool's anchor distance through a distance
  potential fitted on within-21-day pairs of the same athlete. Beyond the
  fitted span (every TF pool's span ends at 3200 m while hs anchors at
  5000 and college_m at 8000) the potential is a tangent extension: the
  college 10k rides a slope measured from FOUR pairs, the 600 from none.
  The solve's per-(pool, event, band) offsets e correct the potential, but
  a class with too few season-best pairs was pulled toward ZERO -- i.e.
  toward "the tangent is right". This module gives such a class the
  relation every published table agrees on instead.

★ THE NUMBERS. Local log-log exponents b = ln(t2/t1) / ln(d2/d1) per
  segment, by sex and by rating band, read off the World Athletics 2025
  scoring tables (Spiriev) at equal points, cross-checked against Purdy
  (1970/1974), Mercier and Cameron. Three facts the tables agree on:

    - the exponent RISES as ability falls (men 800->mile 1.141 at 1000
      points, 1.160 at 400; mile->2mi 1.100 -> 1.112; 5k->10k 1.074 ->
      1.102), so a band-independent curve under-credits the fast and
      over-credits the slow at every distance but the anchor
    - women are 0.01-0.02 steeper than men over 800-3200 and 3200-5000,
      and the same from 5k to 10k
    - the 3200->5000 step is nearly flat with ability (men 1.056)

  The bands are joint_solve.DIST_BANDS on the pool's own rating scale
  (<105 / 105-120 / 120-135 / >=135) and are mapped onto the tables at
  roughly 500 / 700 / 900 / 1100 points.

! A PRIOR MEAN, NOT A CORRECTION. The offset returned is the table's
  relation MINUS what the potential already applied, so it is zero wherever
  the potential already agrees with the tables. The season-best pairs
  still decide every class they can (joint_solve.DIST_CAL_SHARE); this
  only decides the classes they cannot.
"""
import math

import numpy as np

# segment knots, metres; the exponent is piecewise constant between them
SEGMENT_KNOTS = (800.0, 1600.0, 3200.0, 5000.0, 10000.0)

# exponent per segment, by rating band (low, mid, high, top)
EXPONENTS = {
    "M": {
        (800.0, 1600.0): (1.158, 1.150, 1.144, 1.138),
        (1600.0, 3200.0): (1.110, 1.106, 1.102, 1.098),
        (3200.0, 5000.0): (1.056, 1.056, 1.056, 1.056),
        (5000.0, 10000.0): (1.097, 1.088, 1.079, 1.069),
    },
    "F": {
        (800.0, 1600.0): (1.168, 1.157, 1.146, 1.133),
        (1600.0, 3200.0): (1.128, 1.117, 1.106, 1.094),
        (3200.0, 5000.0): (1.084, 1.080, 1.076, 1.072),
        (5000.0, 10000.0): (1.088, 1.084, 1.080, 1.075),
    },
}
# below 800 the anaerobic share climbs and no table reaches it: the
# 800-1600 exponent plus a stated 0.02; above 10 km the last segment holds
BELOW_800_EXTRA = 0.02


def sexOfPool(pool):
    """'M' or 'F' from the pool name's suffix; unknown genders read as men
    (the more permissive curve)."""
    base = str(pool or "").split("|", 1)[0]
    if base.endswith("_f"):
        return "F"
    return "M"


def _segmentExponent(sex, band, lo, hi):
    table = EXPONENTS[sex]
    b = int(min(max(band, 0), 3))
    if hi <= SEGMENT_KNOTS[0]:
        return table[(800.0, 1600.0)][b] + BELOW_800_EXTRA
    if lo >= SEGMENT_KNOTS[-1]:
        return table[(5000.0, 10000.0)][b]
    for (a, z), exps in table.items():
        if a <= lo and hi <= z:
            return exps[b]
    raise ValueError((lo, hi))


def logTimeRatio(sex, band, d_from, d_to):
    """ln(t(d_to) / t(d_from)) under the tables: the piecewise-constant
    exponent integrated over ln d, so it is exact for any two distances
    and antisymmetric in its arguments."""
    d_from, d_to = float(d_from), float(d_to)
    if d_from <= 0 or d_to <= 0:
        raise ValueError("distances must be positive")
    if d_from == d_to:
        return 0.0
    if d_from > d_to:
        return -logTimeRatio(sex, band, d_to, d_from)
    knots = [d_from] + [k for k in SEGMENT_KNOTS if d_from < k < d_to] + [d_to]
    total = 0.0
    for lo, hi in zip(knots, knots[1:]):
        total += _segmentExponent(sex, band, lo, hi) * math.log(hi / lo)
    return total


def tableOffset(pool, dist_m, ref_m, band, curve_log_factor):
    """The prior mean for one (pool, event, band) class: what the tables
    say the event's log time is against the reference event, less what
    the potential already moved it by.

    curve_log_factor(d) must return ln(normalized / raw) at distance d for
    this pool on the track curve, i.e. ln F(d). Then the potential's own
    relation between d and ref is ln F(ref) - ln F(d), and

        e_table = b_table * ln(d / ref) - (ln F(ref) - ln F(d))
    """
    want = logTimeRatio(sexOfPool(pool), band, ref_m, dist_m)
    have = float(curve_log_factor(ref_m)) - float(curve_log_factor(dist_m))
    return want - have


def curveLogFactorFor(pool):
    """ln F(d) on the live distance potential for this pool's TRACK curve,
    distance only (no era, no geometry, no weather). None when the
    artifact is unavailable, so a caller can fall back to no table."""
    try:
        import normalize_distance as nd
    except Exception:                                    # noqa: BLE001
        return None
    if getattr(nd, "_SPLINES", None) is None:
        return None

    def f(d):
        return math.log(nd._normalizationFactorCached(
            float(d), str(pool).split("|", 1)[0], None, None, None, "TF", None))
    try:
        f(1600.0)
    except Exception:                                    # noqa: BLE001
        return None
    return f


def tablePrior(dist_labels, dist_refs, n_band, curve_factor_for=curveLogFactorFor):
    """One prior mean per (class, band) in the solve's own layout
    (class-major, band-minor), or None when no curve is reachable.
    dist_labels are the unbanded 'pool:dist' labels, dist_refs the
    reference distance per pool name."""
    if not dist_labels:
        return None
    out = np.full(len(dist_labels) * n_band, np.nan)
    cache = {}
    any_ok = False
    for i, lab in enumerate(dist_labels):
        pool, dist = lab.rsplit(":", 1)
        ref = dist_refs.get(pool)
        if ref is None:
            continue
        f = cache.get(pool)
        if f is None:
            f = curve_factor_for(pool)
            cache[pool] = f if f is not None else False
        if not f:
            continue
        for b in range(n_band):
            try:
                out[i * n_band + b] = tableOffset(pool, float(dist), float(ref),
                                                  b, f)
                any_ok = True
            except Exception:                            # noqa: BLE001
                out[i * n_band + b] = np.nan
    return out if any_ok else None
