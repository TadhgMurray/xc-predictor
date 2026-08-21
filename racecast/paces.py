# Project: xc-predictor / racecast
# File:    racecast/paces.py
# Purpose: Training paces and an estimated VO2max, from the same
#          normalized_time everything else on the conversions page routes
#          through.
#
# ★ TWO KINDS OF PACE, AND ONLY ONE OF THEM IS OURS TO DERIVE.
#
#   Interval and tempo are RACE-EQUIVALENT paces: "what could this athlete
#   race for ten minutes / for twenty". That is precisely the question
#   distance_spline.pkl was fitted to answer, on 60M real races and per pool,
#   so we answer it with our own curve and no imported constants.
#
#   Threshold, steady and easy are NOT race paces. Nobody races ninety
#   minutes easy, so no amount of race data can say what easy pace is. Those
#   come from coaching convention, are marked as such on every row, and are
#   anchored on the distance the pool actually races.
#
# ⚠ AND THE LINE BETWEEN THEM IS NOT A MATTER OF TASTE -- IT IS THE FIT'S
#   DOMAIN. fit_distance_exponent caps every XC pool at the distance it
#   legitimately races (hs 8000m, college_f 6000m) because, in its own words,
#   "sparse long pairs bend the pool's exponent and make it extrapolate to
#   nonsense". Asking the spline for a threshold pace means asking it about a
#   race nobody in that pool has ever run:
#
#       a HS boy at 16:00 for 5K, what distance is each pace a race for?
#         repetition   2 min      703 m     in domain
#         interval    10 min    3,209 m     in domain
#         tempo       20 min    6,172 m     in domain
#         tempo-long  30 min    9,047 m     1.1x beyond the cap
#         threshold   40 min   11,868 m     1.5x beyond
#         threshold   60 min   17,398 m     2.2x beyond
#
#   So the spline is used where it interpolates and refused where it would
#   extrapolate. A pace that says "coaching convention" is a weaker claim
#   than one that says "your own distance curve", and the page has to say
#   which it is rather than presenting six numbers of equal confidence.

import math
import sys

sys.path.insert(0, "engine")
from normalize_distance import targetFor          # noqa: E402

MILE_M = 1609.344

# ⚠ A MIRROR OF fit_distance_exponent.POOL_MAX_DISTANCE_XC, NOT AN IMPORT.
#   That module pulls in corrections.py, which is 1.45M lines and ~13s to
#   import -- unacceptable on a request path. scripts/check_paces.py reads
#   the constant out of the fitter's SOURCE and fails if these two drift.
FIT_MAX_XC = {
    "college_f": 6000,
    "college_m": 10000,
    "elem_f":    5000,
    "elem_m":    5000,
    "hs_f":      8000,
    "hs_m":      8000,
    "ms_f":      6000,
    "ms_m":      6000,
    "pro_f":     12000,
    "pro_m":     12000,
}
DEFAULT_FIT_MAX_XC = 8000


# ===================================================================== #
#  THE PACES
# ===================================================================== #
#
# duration paces: solved on our own curve, if the distance lands in domain.
# ratio paces:    a fraction of THRESHOLD SPEED, from convention.
#
# ! FRACTIONS OF SPEED, NOT OF PACE. Halving a pace doubles a speed; the
#   coaching numbers are all stated as "percent effort", which is a speed.
#   Applying them to seconds-per-mile would invert every one of them.
_DURATION_PACES = (
    ("interval", "Interval",  600,  "10-minute race effort — around vVO2max"),
    ("tempo",    "Tempo",    1200,  "20-minute race effort"),
)

# ⚠ EVERY NUMBER BELOW IS A COACHING CONVENTION AND SHOULD BE CHECKED
#   AGAINST A SOURCE BEFORE IT IS SHOWN TO ANYBODY. They are in the range
#   commonly prescribed (threshold ~20-25 s/mile slower than 5K pace for a
#   high schooler; easy roughly three quarters of threshold speed), and they
#   are the part of this file with no evidence behind it.
_THRESHOLD_OF_RACE_SPEED = 0.94     # of the pool's own race-distance speed
_RATIO_PACES = (
    ("threshold", "Threshold", 1.00,
     "sustainable ~40-60 min effort; conventional, not derived"),
    ("steady",    "Steady",    0.92, "conventional fraction of threshold"),
    ("easy",      "Easy",      0.76, "conventional fraction of threshold"),
)


def _paceStrings(sec_per_mile):
    """(per mile, per km) as m:ss, which is how a runner reads a pace."""
    def fmt(s):
        s = int(round(s))
        return f"{s // 60}:{s % 60:02d}"
    return fmt(sec_per_mile), fmt(sec_per_mile * 1000.0 / MILE_M)


def _distanceForDuration(norm, pool, sport, seconds, to_time,
                         lo=400.0, hi=25000.0):
    """The distance this athlete would race in `seconds`, by bisection.

    ! BISECTION, NOT ALGEBRA. The forward chain is a spline, geometry and an
      era curve multiplied together; it has no closed-form inverse, and it is
      monotonic in distance, which is the only property bisection needs.
    """
    for _ in range(60):
        mid = (lo + hi) / 2.0
        t = to_time(norm, {"distance": mid, "pool": pool, "sport": sport})
        if t is None:
            return None
        if t < seconds:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1.0:
            break
    return (lo + hi) / 2.0


def trainingPaces(norm, pool, sport="XC", to_time=None):
    """[{key, label, per_mile, per_km, basis, note}] -- slowest last.

    `to_time` is conversions.normalized_to_time, passed in rather than
    imported so this module stays testable without a database.
    """
    if to_time is None:
        from conversions import normalized_to_time as to_time   # noqa: E402
    if not norm or norm <= 0:
        return []

    bare = pool.replace("_unknown_gender", "_f") if pool else "hs_m"
    fit_max = FIT_MAX_XC.get(bare, DEFAULT_FIT_MAX_XC)

    out = []
    for key, label, seconds, note in _DURATION_PACES:
        d = _distanceForDuration(norm, pool, sport, seconds, to_time)
        if d is None:
            continue
        # ! REFUSED, NOT CLAMPED. Clamping to the cap would answer a
        #   different question (the pace for a race they did not ask about)
        #   while looking like an answer to the one they did.
        # ! THIS GUARD RARELY FIRES AT 10 AND 20 MINUTES, WHICH IS THE POINT.
        #   Those durations were chosen to sit inside every pool's fitted
        #   range: a 20-minute race is 6,171m for a 16:00 5K runner and
        #   SHORTER for anyone slower, since a slower athlete covers less
        #   ground in the same time. The guard is here so that raising a
        #   duration -- someone adding a 30-minute tempo -- fails loudly
        #   instead of quietly extrapolating.
        if d > fit_max:
            out.append({"key": key, "label": label, "per_mile": None,
                        "per_km": None, "basis": "out of range",
                        "note": (f"a {seconds // 60}-minute race is "
                                 f"{d:,.0f}m for this athlete, past the "
                                 f"{fit_max:,}m your distance curve was "
                                 f"fitted on")})
            continue
        per_mile = seconds / (d / MILE_M)
        pm, pk = _paceStrings(per_mile)
        out.append({"key": key, "label": label, "per_mile": pm, "per_km": pk,
                    "basis": "your distance curve", "note": note,
                    "equivalent_race_m": round(d)})

    # The convention paces hang off the pool's OWN race distance -- the one
    # targetFor names and the one the spline is dense at.
    ref_d = targetFor(bare, sport) or 5000.0
    ref_t = to_time(norm, {"distance": ref_d, "pool": pool, "sport": sport})
    if ref_t:
        ref_speed = (ref_d / MILE_M) / ref_t          # miles per second
        thr_speed = ref_speed * _THRESHOLD_OF_RACE_SPEED
        for key, label, frac, note in _RATIO_PACES:
            pm, pk = _paceStrings(1.0 / (thr_speed * frac))
            out.append({"key": key, "label": label, "per_mile": pm,
                        "per_km": pk, "basis": "coaching convention",
                        "note": note, "anchored_on_m": round(ref_d)})

    return out


# ===================================================================== #
#  VO2 MAX
# ===================================================================== #
#
# ⚠ THIS IS A REGRESSION FROM PERFORMANCE, NOT A MEASUREMENT, and the page
#   must not call it anything else. VO2max is measured on a treadmill with a
#   gas analyser; every calculator that reports it from a race time is
#   reporting how fast you ran, rescaled. Daniels and Gilbert's is the
#   standard rescaling and the one other calculators use, so the number is at
#   least comparable to what a runner will see elsewhere.
#
# ! COEFFICIENTS TRANSCRIBED FROM DANIELS' RUNNING FORMULA AND NOT VERIFIED
#   AGAINST THE BOOK BY THIS AUTHOR. They are widely republished and produce
#   sane values on the self-check below, but a transposed digit here yields
#   plausible-looking numbers rather than an error. Check before shipping.
_VO2_A, _VO2_B, _VO2_C = -4.60, 0.182258, 0.000104
_PCT_A, _PCT_B, _PCT_C = 0.8, 0.1894393, 0.2989558
_PCT_D, _PCT_E = -0.012778, -0.1932605

# ⚠ FITTED ON TRAINED ADULTS. Children carry high VO2max per kg with poor
#   running economy, and this formula cannot separate the two -- so a
#   12-year-old's 3200m time yields a number that does not mean what an
#   adult's does. Withheld for the pools where that applies rather than
#   printed with a footnote nobody reads.
_VDOT_POOLS = ("hs_m", "hs_f", "college_m", "college_f")


def vdot(time_seconds, distance_meters, pool=None):
    """{value, note} -- estimated VO2max, or a refusal with a reason."""
    if not time_seconds or not distance_meters or time_seconds <= 0:
        return {"value": None, "note": "needs a time and a distance"}

    bare = (pool or "hs_m").replace("_unknown_gender", "_f")
    if bare not in _VDOT_POOLS:
        return {"value": None,
                "note": ("VO2max estimates are fitted on trained adults and "
                         "do not transfer to this age group")}

    minutes = time_seconds / 60.0
    # ! THE PERCENTAGE CURVE IS ONLY SENSIBLE OVER RACE DURATIONS. Outside
    #   this band the exponentials are being asked about efforts they were
    #   never fitted to -- a 30-second sprint is not aerobic, and a 4-hour
    #   run is not a maximum.
    if not (3.0 <= minutes <= 240.0):
        return {"value": None,
                "note": "only meaningful for efforts of about 3 to 240 minutes"}

    v = distance_meters / minutes                      # metres per minute
    vo2 = _VO2_A + _VO2_B * v + _VO2_C * v * v
    pct = (_PCT_A + _PCT_B * math.exp(_PCT_D * minutes)
           + _PCT_C * math.exp(_PCT_E * minutes))
    if pct <= 0:
        return {"value": None, "note": "unresolvable"}
    return {"value": round(vo2 / pct, 1),
            "note": "estimated from performance (Daniels-Gilbert), "
                    "not a measured VO2max"}
