"""
rust_fitness.py -- the season-form correction.  [v2]

WHAT CHANGED IN v2, AND WHY
    v1 modelled in-season improvement as a LINEAR ramp, %/week, capped at 42
    days, with the rate falling to zero at rating 127. A fixed-effects
    measurement over 9.1M hs_m rows -- residual demeaned within
    (venue, distance), so course difficulty cancels EXACTLY on both sides --
    found three things wrong with that:

      1. WRONG SHAPE. The decline decelerates. Per-week decrements from the
         opener run 0.0066, 0.0062, 0.0047, 0.0041, 0.0033, 0.0031, 0.0018 --
         about 60% of the total drop lands in the first three weeks. A straight
         line is ~3x too slow early and roughly right late.

      2. WRONG SIZE. Measured span week 1 -> week 8 is 2.95% at rating 100.
         v1 supplied rate 0.216%/wk x 6 weeks = 1.30%, i.e. ~44% of it. The
         missing ~1.7% was left in the residual, where the difficulty step
         charged it to whichever venue hosted the race. September venues came
         out hard, November venues soft.

      3. WRONG TILT. v1 sent the rate to zero at rating 127 on the theory that
         elite athletes "arrive fit". By measured amplitude relative to the
         slowest band -- 1.00, 0.93, 0.78, 0.55 across rating bands <90,
         90-105, 105-120, >=120 -- the >=120 band retains 55%, not 0%. A line
         through those four points crosses zero near rating 188, which is
         outside the population. The max(0, ...) floor never actually binds.

    NET EFFECT ON DIFFICULTY. This is a nuisance term in the difficulty fit
    ONLY (speed_ratings.computeCourseDifficulties, one line). It is absent from
    computeAthleteAbilities and from the result-rating formula, which is
    100 * pool_mean * (1 + d) / normalized_time per row. So it does NOT remove
    in-season fitness from anybody's rating: a November race still rates higher
    than a September race if it was better. What it removes is the VENUE-
    MEDIATED distortion of that signal -- the engine currently credits part of
    your autumn improvement to "this course is hard", which inflates ratings
    earned at fast early-season venues and deflates late-season ones. Ratings
    WILL move, because d is in the formula. That is the point.

WHY ABILITY STAYS A SCALAR
    The effect is a CONSENSUS -- every athlete improves in the same direction
    on roughly the same schedule -- so it is ~8 shared numbers, not 4.5M
    individual trajectories. A per-athlete date trend would be ~9M parameters,
    and for an athlete who races only in September that trend is perfectly
    confounded with the venues they raced: a new channel for laundering terrain
    into fitness. One scalar per (person, pool) plus a shared curve is the
    correct factorisation.

WHAT IS STILL ESTIMATED OFF-LINE
    Unchanged from v1, and the reason both terms are trustworthy: they come
    from designs where difficulty cancels rather than averages out. RUST from
    same-athlete/same-season/same-venue/same-distance pairs; the FITNESS shape
    from within-(venue, distance) demeaning. Neither is fitted inside the ALS
    loop, so neither can trade off against the course term it is correcting.

    ⚠ ONLY hs_m IS MEASURED. See _AMPLITUDE_AT_100 for what the other pools do
      and what must be re-run to fix them.
"""

import numpy as np


# ------------------------------------------------------------------ #
#  CHUNK 1 -- FITTED CONSTANTS
# ------------------------------------------------------------------ #

# Rust, as a FRACTION of normalized time, applied to an athlete's first race
# of a season.
#
# ★ UNCHANGED IN v2, DELIBERATELY. The fixed-effects table that produced the
#   new fitness curve buckets by floor(days / 7), so its week-0 bucket mixes
#   genuine openers with second races in the same week. It CANNOT re-measure a
#   first-race discontinuity. v1's numbers came from a paired
#   difference-in-differences estimator built for exactly that, and they stand.
#   The new shape curve is anchored at week 1 (see _SHAPE_KNOTS) so the two
#   terms cannot double-count the opener.
#
# ⚠ TWO ESTIMATES DISAGREED AND THIS USES THE LOWER ONE.
#     weighted medians per days-bucket -> hs_m 1.17%
#     regr_intercept on raw pairs      -> hs_m 1.55%
#   The gap is skew: race times have a long slow tail, so least-squares sits
#   above a median even trimmed at +-15%. The median is the robust estimator.
#   v1 noted "if early-season venues still come out inflated, raise this
#   first". They were inflated -- but v2 attributes that to the fitness shape,
#   which was measured to be 2.4x too small. Fix the measured thing before
#   nudging the robust one.
_RUST = {
    "hs_m":      0.0117,
    "hs_f":      0.0130,
    "ms_m":      0.0067,
    "ms_f":      0.0096,
    "college_m": 0.0045,
    # college_f measured -0.12%/wk fitness on ~300-700 pairs per cell, i.e.
    # noise. Borrowing college_m's rust rather than fitting to nothing.
    "college_f": 0.0045,
    # NOT YET MEASURED -- these pools do not exist until the elem split lands.
    # 0.0 is the honest default: no correction beats a guessed one.
    "elem_m":    0.0,
    "elem_f":    0.0,
}


# THE SHAPE CURVE. (days since this athlete's opener, dimensionless 0..1)
#
# ★ THIS IS THE HEART OF v2. Read as "what fraction of the full season-form
#   decline has happened by day D". Anchored 0.0 at day 10 and 1.0 at day 59,
#   so the curve carries SHAPE only -- its overall size comes from
#   _AMPLITUDE_AT_100 and its ability tilt from _TILT_PER_POINT.
#
# Derivation. Measured mean residual by week (hs_m, 9.1M rows, demeaned within
# venue x distance), then expressed as decline from the week-1 value and
# divided by the week-1 -> week-8 span of 0.02979:
#
#     wk  mean_resid   decline    shape
#      1   +0.01283    0.00000    0.000
#      2   +0.00628    0.00655    0.220
#      3   +0.00008    0.01275    0.428
#      4   -0.00459    0.01742    0.585
#      5   -0.00873    0.02156    0.724
#      6   -0.01204    0.02487    0.835
#      7   -0.01518    0.02801    0.940
#      8   -0.01696    0.02979    1.000
#
# Knot days are week MIDPOINTS (7 * wk + 3), because a bucket labelled "week 4"
# holds days 28-34 and its mean sits near day 31, not day 28.
#
# ★ FLAT AFTER DAY 59, AND THE TAIL IS NOT MODELLED. Weeks 9-13 in the pooled
#   data appear to RISE back toward zero (-0.01696 at wk8 -> -0.01091 at wk13).
#   Splitting by rating band showed that rise is absent in the >=120 band --
#   which is the only band with trustworthy late-season data, because those are
#   the athletes who actually race thirteen weeks. Weather would be
#   band-independent, so late-season conditions do NOT explain it. The cause is
#   unresolved; a sub-90 athlete still racing in week 12 is a strange survivor
#   of a strange selection. Rather than model a shape we cannot explain, the
#   curve plateaus at the minimum. This is safe: the gauge anchor in
#   computeCourseDifficulties recentres difficulty to weighted mean zero every
#   iteration, so an additive constant in this curve is absorbed -- only the
#   SHAPE is identified.
#
# ⚠ v1's _FITNESS_MAX_DAYS = 42 is GONE. It cut the curve off at week 6, two
#   weeks before the measured minimum, discarding the last ~0.005 of decline.
#   The plateau below replaces it explicitly.
_SHAPE_KNOT_DAYS  = np.array([10.0, 17.0, 24.0, 31.0, 38.0, 45.0, 52.0, 59.0])
_SHAPE_KNOT_VALUE = np.array([0.000, 0.220, 0.428, 0.585, 0.724, 0.835, 0.940,
                              1.000])

# Below the first knot the curve is held at 0.0 rather than extrapolated.
# Days 0-6 are the opener's own week: the opener itself is handled by _RUST, and
# a genuine second race three days later gets no fitness credit. Conservative
# and deliberate -- the week-0 bucket is rust-contaminated and cannot tell us
# what a day-3 second race should get.
_SHAPE_PLATEAU_DAY = float(_SHAPE_KNOT_DAYS[-1])


# AMPLITUDE at rating 100, as a fraction of normalized time: the full week-1 to
# plateau decline for a median athlete of each pool.
#
# ★ hs_m = 0.0295 IS MEASURED (this table, 9.1M rows).
#
# ⚠ EVERY OTHER POOL IS CARRIED OVER FROM v1, NOT RE-MEASURED. Each is that
#   pool's v1 rate x 6 weeks, i.e. exactly the total correction v1 applied at
#   plateau. So v2 changes those pools' SHAPE (decelerating, correct plateau,
#   correct tilt) while holding their SIZE at the previously-measured value.
#
#   This is deliberately NOT scaled by hs_m's 2.4x underfit. Propagating a
#   correction factor derived from one pool into pools whose amplitude was
#   never measured the same way would be inventing data. The likely truth is
#   that they are all too small -- but "likely" is not measured.
#
#   TO FIX: re-run the fixed-effects week table with ar.pool changed, one pool
#   at a time, and replace the number here. ms_m and college_m bracket the
#   range and are the two worth doing first.
# ★ RE-MEASURED 2026-08-27 (scripts/fit_rust_amplitude.py, all pools in one
#   pass, board rows, demeaned within cell x pool). The estimator reproduces
#   the original hs_m week table EXACTLY through week 4 and diverges after
#   -- late weeks are selection (wk8 retention: hs 37%, college 73%, ms 5%),
#   so ABSOLUTE spans read high, but the CROSS-POOL RATIOS share the bias
#   and are trusted. Values below = 0.0295 x (pool span / hs_m span), which
#   keeps hs_m at its careful paired-estimator value and moves the others by
#   what the same instrument measured on the same rows.
#
#     measured spans: hs_m .0511  hs_f .0532  ms_m .0486  ms_f .0469
#                     college_m .0249  college_f .0293
#
#   The headline: hs_f tracks hs_m within +-0.002 at EVERY week yet carried
#   half its amplitude; college carried a quarter of its measured share.
#   ms scales out BELOW its v1 carry-over (.028 vs .034) -- but its week-8
#   sample is 5% survivors, so lowering it on that evidence is not
#   justified; ms holds at v1 until a within-athlete estimator rules.
_AMPLITUDE_AT_100 = {
    "hs_m":      0.0295,    # MEASURED (v2 paired estimator; ratified 8/27)
    "hs_f":      0.0307,    # MEASURED 8/27: tracks hs_m week-for-week
    "ms_m":      0.03402,   # v1 carry-over HELD -- see note above
    "ms_f":      0.03564,   # v1 carry-over HELD -- see note above
    "college_m": 0.0143,    # MEASURED 8/27 (was 0.00648, a v1 carry-over)
    "college_f": 0.0169,    # MEASURED 8/27 (was borrowed college_m)
    "elem_m":    0.03402,   # borrowed from ms_m until the elem split lands
    "elem_f":    0.03564,
}

# ABILITY TILT, as a fraction of the pool's amplitude per rating point above
# 100. Multiplicative so one number serves every pool:
#
#     amplitude(rating) = _AMPLITUDE_AT_100[pool]
#                         * (1 - _TILT_PER_POINT * (rating - 100))
#
# From the hs_m band amplitudes (0.03423, 0.03170, 0.02653, 0.01887 at band
# mid-ratings ~82, 97.5, 112.5, ~128): slope -0.000335 per point, which against
# the rating-100 amplitude of 0.0295 is 0.01135 per point.
#
# ★ NO ZERO CROSSING IN THE POPULATION. This form reaches zero at rating 188.
#   v1 put the crossing at 127 and floored below it, which zeroed the whole
#   correction for the >=120 band -- the band that measurably retains 55% of
#   the slowest band's amplitude. 127 was not a physiological threshold; it was
#   where a straight line through a smoothly-flattening decline happened to
#   cross, fitted over bins whose top end was noise around zero.
_TILT_PER_POINT = 0.01135

# Clamps, as multiples of _AMPLITUDE_AT_100. Guard rails against extrapolation,
# not model features: the tilt was fitted over ratings ~82-128 and these bound
# what happens outside that. The floor is positive -- never zero, never
# negative -- because "athletes get slower across a season" is unsupported at
# every rating measured.
_AMPLITUDE_FLOOR_FRAC = 0.15
_AMPLITUDE_CEIL_FRAC  = 1.80

# An athlete with no prior rating gets the pool median by definition, which is
# 100 on this scale. NOT the pool mean -- `rating` here is the 100-centred
# points scale, not a time.
_DEFAULT_RATING = 100.0


# ------------------------------------------------------------------ #
#  CHUNK 2 -- THE SHAPE CURVE
# ------------------------------------------------------------------ #

def _monotoneInterpolator(x, y):
    """
    A smooth, MONOTONE-PRESERVING curve through the knots, or None.

    Arguments: x -- knot positions, strictly increasing;
               y -- knot values.
    Output:    a callable f(days) -> ndarray, or None if scipy is unavailable.

    ★ PCHIP, NOT A CUBIC SPLINE. A natural cubic spline through these knots
      would overshoot between them -- it enforces smooth second derivatives at
      the cost of letting the curve wander outside the knot values. PCHIP
      enforces monotonicity instead, which is the property that matters here:
      an overshoot would mean the correction briefly claims athletes got slower
      between two weeks where they measurably did not.

    Returns None rather than raising so the caller can fall back to linear
    interpolation. A missing scipy must not stop a solve.
    """
    try:
        from scipy.interpolate import PchipInterpolator
    except Exception:
        return None
    return PchipInterpolator(x, y, extrapolate=False)


# Built once at import. Module-level because it is a pure function of the
# constants above and is called on a 61.6M-row array.
_SHAPE_SPLINE = _monotoneInterpolator(_SHAPE_KNOT_DAYS, _SHAPE_KNOT_VALUE)


def shapeAtDays(days_since):
    """
    Fraction of the season-form decline completed, per row.

    Arguments: days_since -- ndarray[float64], days since this athlete's opener.
    Output:    ndarray[float64] in [0, 1].

    Clamped at BOTH ends before interpolation, so the spline is only ever
    evaluated inside its knot range and `extrapolate=False` can never produce
    NaN:
      - below day 10 -> 0.0  (the opener's own week; _RUST covers it)
      - above day 59 -> 1.0  (plateau; see the _SHAPE_KNOT_DAYS note)

    Syntax: np.clip returns a new array bounded to [lo, hi]. np.interp is the
    fallback and clamps to the end knot values automatically, which is why the
    `left`/`right` arguments are not needed there.
    """
    clamped = np.clip(days_since, _SHAPE_KNOT_DAYS[0], _SHAPE_PLATEAU_DAY)

    if _SHAPE_SPLINE is not None:
        return np.asarray(_SHAPE_SPLINE(clamped), dtype=np.float64)

    # Linear fallback. With eight measured knots at 7-day spacing the kinks are
    # small, and the knots themselves each rest on >1M rows, so this is a
    # cosmetic downgrade rather than a modelling one.
    return np.interp(clamped, _SHAPE_KNOT_DAYS, _SHAPE_KNOT_VALUE)


# ------------------------------------------------------------------ #
#  CHUNK 3 -- AMPLITUDE
# ------------------------------------------------------------------ #

def _poolOf(key):
    """
    Bare pool name from an athlete key.

    Merged runs carry BARE pools ("hs_m"); per-sport runs carry a sport suffix
    ("hs_m|XC"). Splitting on "|" handles both, so this module never needs to
    know which mode the engine is in.
    """
    pool = key[1] if key and len(key) > 1 else ""
    return pool.split("|", 1)[0] if pool else ""


def _lookupPerAthlete(athlete_keys, table, default=0.0):
    """
    Per athlete code: table[pool], or `default` for an unrecognised pool.

    Arguments: athlete_keys -- list of (person_id, pool) indexed by code;
               table -- {pool: value}; default -- value for a missing pool.
    Output:    ndarray[float64] indexed by athlete code.

    One helper for both _RUST and _AMPLITUDE_AT_100, so the two cannot drift
    apart in how they resolve a pool name. An unrecognised pool gets the
    default rather than a guess -- unknown_gender pools exist and hold 9
    athletes corpus-wide.
    """
    out = np.full(len(athlete_keys), float(default), dtype=np.float64)
    for code, key in enumerate(athlete_keys):
        value = table.get(_poolOf(key))
        if value is not None:
            out[code] = value
    return out


def amplitudePerRow(amp_at_100, rating):
    """
    The full season-form decline for each row, tilted by ability.

    Arguments: amp_at_100 -- per-row pool amplitude at rating 100;
               rating     -- per-row prior rating.
    Output:    ndarray[float64], a fraction of normalized time.

    Multiplicative tilt, then clamped to [floor, ceil] multiples of the pool's
    own amplitude -- so both bounds scale with the pool and one pair of
    fractions serves all of them.

    Syntax: np.clip with ARRAY bounds applies them element-wise, which is what
    makes the per-pool scaling work in a single call.
    """
    tilt = 1.0 - _TILT_PER_POINT * (rating - 100.0)
    amp = amp_at_100 * tilt
    return np.clip(amp,
                   amp_at_100 * _AMPLITUDE_FLOOR_FRAC,
                   amp_at_100 * _AMPLITUDE_CEIL_FRAC)


# ------------------------------------------------------------------ #
#  CHUNK 4 -- PER-ROW SEASON POSITION   (unchanged from v1)
# ------------------------------------------------------------------ #

def seasonPosition(athlete, season, day):
    """
    Per row: days since that athlete's first race of that season, and whether
    this row IS that first race.

    Arguments: athlete -- int athlete code per row
               season  -- int season year per row
               day     -- FORWARD day ordinal per row (any consistent epoch).
                          Must increase with time -- see the note in
                          buildCorrection about cols["days"] counting backwards.
    Output:    (days_since ndarray[float64], is_first ndarray[bool])

    ONE SORT, NO PYTHON LOOP. 61.6M rows through a per-athlete loop would be
    minutes; lexsort plus two boundary scans is seconds.

    lexsort sorts by the LAST key first, so (day, season, athlete) orders by
    athlete, then season, then day -- which is what puts each athlete-season's
    races together in date order.

    ⚠ TIES: two races on the same day are BOTH flagged first. That is correct
      for the duplicate rows in this corpus (~336K same-day pairs with
      identical times), where both copies are genuinely the same unraced
      appearance. It slightly over-applies rust to a real same-day double,
      which is rare enough to ignore.
    """
    order = np.lexsort((day, season, athlete))
    a = athlete[order]
    s = season[order]
    d = day[order].astype(np.float64)

    # First row of each (athlete, season) run. The array is sorted, so a group
    # boundary is simply a change in either key.
    starts = np.empty(a.size, dtype=bool)
    starts[0] = True
    np.not_equal(a[1:], a[:-1], out=starts[1:])
    starts[1:] |= s[1:] != s[:-1]

    # cumsum over a boolean gives a dense group id: 0 for the first run, 1 for
    # the second, and so on.
    group = np.cumsum(starts) - 1
    first_day = d[starts][group]

    days_since_sorted = d - first_day

    days_since = np.empty_like(days_since_sorted)
    days_since[order] = days_since_sorted

    is_first = days_since == 0.0
    return days_since, is_first


# ------------------------------------------------------------------ #
#  CHUNK 5 -- PER-ATHLETE LOOKUPS
# ------------------------------------------------------------------ #

def ratingPerAthlete(athlete_keys, prior_ratings):
    """
    Per athlete code: their rating from the PREVIOUS engine run.

    Arguments: athlete_keys  -- list of (person_id, pool) indexed by code
               prior_ratings -- {(person_id, pool): speed_rating} from
                                athlete_ratings, loaded once at startup
    Output:    ndarray[float64] indexed by athlete code

    ★ PREVIOUS RUN, NOT THIS ONE. The amplitude is a function of rating, and
      rating is what the engine is solving. Reading it from the current
      iteration would make the correction chase its own output round the loop.
      Frozen at load time, the feedback is broken: the correction is a fixed
      input for the whole solve, exactly like the pickles.

    Athletes with no prior row get 100 -- the pool median by definition, so
    they receive the average correction rather than an extreme one.
    """
    out = np.full(len(athlete_keys), _DEFAULT_RATING, dtype=np.float64)
    for code, key in enumerate(athlete_keys):
        rating = prior_ratings.get(key)
        if rating is not None and rating > 0:
            out[code] = rating
    return out


def rustPerAthlete(athlete_keys):
    """Per athlete code: the rust constant for their pool. 0.0 if unknown."""
    return _lookupPerAthlete(athlete_keys, _RUST, default=0.0)


def amplitudePerAthlete(athlete_keys):
    """Per athlete code: the pool's rating-100 amplitude. 0.0 if unknown."""
    return _lookupPerAthlete(athlete_keys, _AMPLITUDE_AT_100, default=0.0)


# ------------------------------------------------------------------ #
#  CHUNK 6 -- THE CORRECTION
# ------------------------------------------------------------------ #

def buildCorrection(cols, athlete_keys, prior_ratings):
    """
    The per-row season-form correction, as a fraction of normalized time.

    Arguments: cols          -- packed arrays; needs "athlete", "year", "days",
                                and "sport" when present
               athlete_keys  -- list of (person_id, pool) indexed by code
               prior_ratings -- {(person_id, pool): speed_rating}
    Output:    ndarray[float64], one value per row.

        correction = rust[pool] * is_first
                     - amplitude(rating, pool) * shape(days_since)

    Sign: POSITIVE means "this race was slower than the athlete's true level
    for reasons of season form", so subtracting it removes that slowness from
    the course's account. A first race is slow, so rust is positive. A late
    race is fast relative to the opener, so the fitness term enters NEGATIVE.

    ★ THE TWO TERMS DO NOT OVERLAP. shape() is 0.0 below day 10, so an opener
      receives rust and nothing else. See _SHAPE_KNOT_DAYS.

    ⚠ COMPUTE ONCE PER RUN, NOT PER ITERATION. Nothing here depends on the
      current ability or difficulty estimates, so recomputing inside the loop
      would burn a lexsort over 61.6M rows every iteration for an identical
      answer. Hoist it, exactly like the anchor statistics.
    """
    athlete = cols["athlete"]

    # ★ cols["days"] COUNTS BACKWARDS -- it is DAYS AGO, not a forward ordinal,
    #   so the FIRST race of a season has the LARGEST value. Negating flips it
    #   so that min() finds the opener, which is what seasonPosition assumes.
    #   Left unnegated, every "first race" would be the athlete's LAST one and
    #   rust would land on exactly the wrong rows.
    day_ordinal = -cols["days"]

    # ★ THE SEASON RESETS AT THE SPORT BOUNDARY.
    #
    #   speed_ratings treats the calendar year as the season for both sports, so
    #   season 2024 runs TRACK Feb-Jul and then CROSS COUNTRY Aug-Dec. Grouping
    #   on (athlete, year) alone means a dual-sport athlete's season "opens" at a
    #   February track meet, and their September cross country race lands near
    #   day 210 -- far past the week-8 plateau -- receiving the full -2.95%. An
    #   XC-only athlete's identical race sits near day 30 and receives about 40%
    #   of it.
    #
    #   Nobody carries February form into September. Measured within
    #   days-since-opener bands, dual-sport athletes run ~0.9% SLOWER in cross
    #   country than the correction predicts -- consistently positive in every
    #   band, and +0.00915 in the 160-400 day band, which holds 7.99M rows.
    #
    #   The overall gap looked like +0.0003 only because the two populations
    #   barely overlap: XC-only athletes live at 0-60 days, track-openers at
    #   160-400. Comparing them without conditioning on days cancels the effect
    #   entirely -- Simpson's paradox.
    #
    #   Folding sport into the season key gives each sport its own opener, its
    #   own rust, and its own curve. year*2 + sport is unique because sport is
    #   0 or 1.
    sport = cols.get("sport")
    if sport is None:
        season_key = cols["year"]
    else:
        season_key = cols["year"].astype(np.int64) * 2 + sport.astype(np.int64)

    days_since, is_first = seasonPosition(athlete, season_key, day_ordinal)

    # --- rust: a one-off positive on the opener -------------------------
    rust = rustPerAthlete(athlete_keys)
    correction = np.where(is_first, rust[athlete], 0.0)

    # --- fitness: amplitude x shape, entering negative ------------------
    amp_at_100 = amplitudePerAthlete(athlete_keys)[athlete]
    rating = ratingPerAthlete(athlete_keys, prior_ratings)[athlete]

    amplitude = amplitudePerRow(amp_at_100, rating)
    correction -= amplitude * shapeAtDays(days_since)

    return correction


def reportCorrection(correction, cols):
    """
    One-line summary plus the v2 shape check, printed once per run.

    Worth having because this term silently shifts every difficulty. If the
    mean drifts far from zero between runs, something upstream changed --
    prior ratings missing, seasons mis-parsed -- and the difficulties will move
    with no other warning.

    The second line is the v2 addition: the expected plateau for a median hs_m
    athlete, computed from the constants rather than from the data, so a
    mis-edited constant shows up immediately instead of silently reshaping
    81,135 difficulties.
    """
    first = correction > 0
    print(f"[form] correction over {correction.size:,} rows: "
          f"mean {correction.mean():+.5f}  "
          f"min {correction.min():+.5f}  max {correction.max():+.5f}  "
          f"first-race rows {int(first.sum()):,}")

    expected = _AMPLITUDE_AT_100.get("hs_m", 0.0)
    engine = "pchip" if _SHAPE_SPLINE is not None else "linear"
    print(f"[form] v2 shape: {engine}, plateau day "
          f"{_SHAPE_PLATEAU_DAY:.0f}, hs_m rating-100 amplitude "
          f"{expected:.4f} (v1 supplied 0.0130)")