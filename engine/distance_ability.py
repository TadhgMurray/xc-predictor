# Project: xc-predictor
# File:    engine/distance_ability.py
# Purpose: The distance curve indexed by ABILITY, not by pool -- the
#          evaluator. engine/fit_distance_ability.py writes the artifact
#          (engine/data/distance_ability.pkl); normalize_distance and the
#          conversions read it through this module, and only when the
#          switch says so (XCP_DISTANCE_BY=ability; see normalize_distance).
#
# ★ WHY (owner, 2026-09-29: "try to be safe and test it"). 28:34 over Gans
#   Creek's 10000 m read HS-equivalent 156.6 as hs_m, 150.0 as college_m and
#   147.6 as ms_m. The largest term was the distance curve: 10000 -> 5000 is
#   x0.4623 on hs_m's curve and x0.4855 on college_m's, 5.0%, and hs_m's
#   curve at 10 km is an extrapolation (its data spans 2.8k-5k). How much a
#   runner slows from 5K to 10K is a fact about how fast they are, not about
#   their grade -- so the curve's shape is a smooth function of the runner's
#   ability, fitted once per (gender, sport) from every pool's pairs, and the
#   pool label cannot move it.
#
# THE MODEL. x = log(d / 5000), a = log(T5 / ref_5k), T5 the runner's 5K
# equivalent in this family, ref_5k the family's high-school median (the
# fitter derives it). The log-time potential is
#
#       g(x; a) = sum_j (alpha_j + beta_j * A) * x^j        (j = 1..J, no x^0:
#                                                            g(0) = 0 anchors
#                                                            every row on 5000)
#       A = clamp(a, a_lo, a_hi)   the ability range the pairs cover
#
# so log T5 = log t - g(x; A). Beyond the family's distance span g continues
# with its boundary slope (constant local exponent), the same extrapolation
# POLICY as normalize_distance._evalDistancePotential.
#
# ★ THE ABILITY IS READ OFF THE ROW'S OWN TIME, IN CLOSED FORM. Written as
#   g(x; A) = P(x) + A * Q(x), the unclamped ability solves
#       a = log t - log ref - P(x) - a Q(x)   =>   a = (L - P) / (1 + Q)
#   with L = log(t / ref). One division, no iteration, and the forward map
#   is continuous and increasing in t wherever 1 + Q(x) > 0 (the fitter
#   refuses an artifact that breaks it on its own support). The inverse is
#   exact: A comes straight off the 5K equivalent.
#
# ★ THE PER-POOL RESIDUAL (owner, 2026-09-29: "adopt the ability curve with
#   the per-pool residual applied"). At equal ability the pools still differ
#   a little in how they slow over distance (fit_distance_ability's dry run:
#   ms_m XC -0.058 of exponent against a high schooler, college_m -0.017),
#   so a family may carry a SECOND fit, `joint`: the curve and one extra
#   exponent per pool fitted together, the high-school pool the reference,
#       g_p(x; A) = P(x) + A Q(x) + delta_p * x          delta_hs = 0
#   The term is linear in x, so it is its own boundary continuation, g_p(0)
#   is still 0 (every pool still anchors on 5000 m), and it adds to P only:
#   the closed form below reads the ability with P + delta_p x in place of
#   P, the denominator 1 + Q is untouched, and the inverse stays exact.
#   Only the deltas the fitter APPLIED are evaluated -- the entry's
#   `applied_residual`, never the measured `pool_residual` beside the shared
#   curve -- so a pool with no significant residual, or no support, is 0.
#   curve(fam, residual) picks which fit a caller evaluates; every function
#   here takes the row's pool, and a curve with no applied residual ignores
#   it (the shared fit, and every pre-residual artifact, exactly as before).
#
# ! PURE PYTHON, NO NUMPY. normalize_distance is imported by the site and
#   by every pipeline step and keeps to the standard library (its own
#   potential evaluator bisects rather than import numpy); this module is
#   its dependency and keeps the same rule.

import math

KIND = "distance_ability"
TARGET_M = 5000.0

# the family lookup, most specific first: the row's gender and sport, then
# the gender-blind family of that sport, then the sport-blind ones. "u" is
# every gender pooled (fitted from all pairs), "*" is both sports pooled --
# the sportless callers' curve, as the pool artifact's "global" was.
_GENDER = {"M": "m", "F": "f"}


def genderOf(pool):
    """'m' / 'f' from a pool name ('college_f', 'hs_m|XC'), else 'u'."""
    p = (pool or "").split("|", 1)[0]
    if p.endswith("_m"):
        return "m"
    if p.endswith("_f"):
        return "f"
    return "u"


def familyKeys(pool, sport):
    g = genderOf(pool)
    s = (str(sport).strip().upper() if sport else "*")
    keys = [f"{g}|{s}", f"u|{s}"]
    if s != "*":
        keys += [f"{g}|*", "u|*"]
    return keys


def family(art, pool, sport, residual=False):
    """The family entry for a row, or None when the artifact has none. With
    `residual` the family's joint fit (curve + applied per-pool residuals)
    when it has one; the shared curve otherwise."""
    fams = (art or {}).get("families") or {}
    for k in familyKeys(pool, sport):
        if k in fams:
            return curve(fams[k], residual)
    return None


JOINT_KEY = "joint"                  # the family's curve-with-residual fit
APPLIED_KEY = "applied_residual"     # {pool: delta} the evaluator applies


def curve(fam, residual):
    """The fit to evaluate: the joint one when asked for and present."""
    if residual and fam is not None and fam.get(JOINT_KEY) is not None:
        return fam[JOINT_KEY]
    return fam


def hasResidual(art):
    """True when any family of the artifact carries a joint fit."""
    return any((f or {}).get(JOINT_KEY) is not None
               for f in ((art or {}).get("families") or {}).values())


def poolBase(pool):
    return (pool or "").split("|", 1)[0]


def delta(fam, pool):
    """The applied extra exponent of `pool` on this curve (0 for the
    high-school pool, for a pool the fitter did not apply, and on any curve
    that applies none)."""
    got = (fam.get(APPLIED_KEY) or {}) if pool else {}
    return float(got.get(poolBase(pool), 0.0)) if got else 0.0


def _poly(c, x):
    """sum_j c[j-1] x^j, j >= 1 (Horner, no constant term)."""
    s = 0.0
    for v in reversed(c):
        s = (s + v) * x
    return s


def _dpoly(c, x):
    """d/dx of _poly."""
    s = 0.0
    for j in range(len(c), 0, -1):
        s = s * x + j * c[j - 1]
    return s


def _pq(fam, x, pool=None):
    """(P(x), Q(x)): g(x; A) = P + A Q, with the boundary-slope
    continuation outside [x_lo, x_hi], and the pool's applied residual
    delta_p * x in P (linear: its own continuation). Linear in the
    coefficients either way, so the closed-form ability below holds
    everywhere."""
    al, be = fam["alpha"], fam["beta"]
    lo, hi = fam["x_lo"], fam["x_hi"]
    r = delta(fam, pool) * x
    xb = lo if x < lo else hi if x > hi else None
    if xb is None:
        return _poly(al, x) + r, _poly(be, x)
    dx = x - xb
    return (_poly(al, xb) + _dpoly(al, xb) * dx + r,
            _poly(be, xb) + _dpoly(be, xb) * dx)


def clampAbility(fam, a):
    return min(max(a, fam["a_lo"]), fam["a_hi"])


def g(fam, x, a, pool=None):
    """The potential at log-distance-ratio x for (unclamped) ability a."""
    p, q = _pq(fam, x, pool)
    return p + clampAbility(fam, a) * q


def forwardLog(fam, log_t, x, pool=None):
    """log T5 for a raw log-time at x: the row's own ability, closed form."""
    p, q = _pq(fam, x, pool)
    lr = math.log(fam["ref_5k"])
    den = 1.0 + q
    # ! A NON-POSITIVE DENOMINATOR IS A BROKEN ARTIFACT, not a runner. The
    #   fitter refuses one on its support; past it (a 30 km row through a
    #   10 km family) the reference ability's curve is the honest fallback.
    if den <= 1e-6:
        return log_t - p - clampAbility(fam, 0.0) * q
    a = (log_t - lr - p) / den
    ac = clampAbility(fam, a)
    return log_t - p - ac * q


def inverseLog(fam, log_t5, x, pool=None):
    """log raw time at x for a 5K equivalent: the exact inverse."""
    p, q = _pq(fam, x, pool)
    a = log_t5 - math.log(fam["ref_5k"])
    return log_t5 + p + clampAbility(fam, a) * q


def factorForTime(fam, t, d, pool=None):
    """T5 / t for a raw time t at d metres (the multiplier normalizeTime
    applies for the distance leg)."""
    x = math.log(float(d) / TARGET_M)
    lt = math.log(float(t))
    return math.exp(forwardLog(fam, lt, x, pool) - lt)


def factorForNorm(fam, t5, d, pool=None):
    """T5 / t for a 5K equivalent t5 at d metres: the same multiplier as
    factorForTime(fam, t, d, pool) when t5 is that time's equivalent."""
    x = math.log(float(d) / TARGET_M)
    l5 = math.log(float(t5))
    return math.exp(l5 - inverseLog(fam, l5, x, pool))


def factorAtReference(fam, d, pool=None):
    """T5 / t for the family's reference runner (A = 0 clamped) filed in
    `pool`: the one number for callers that carry no time (anchor_repair's
    probe, the distance tables). With a residual applied it differs by pool,
    by exactly that pool's residual."""
    x = math.log(float(d) / TARGET_M)
    return math.exp(-g(fam, x, 0.0, pool))
