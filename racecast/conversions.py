# Project: xc-predictor / racecast
# File:    conversions.py
# Purpose: The math behind the conversions tool. ONE idea: everything routes
#          through normalized_time (the engine's universal currency).
#
#     any SOURCE  ->  normalized_time  ->  any number of OUTPUT contexts
#
# A "source" is one input (a time, a rating, a stored result, an athlete's
# ability). An "output context" is a distance + course/venue + weather to
# express that normalized_time as a raw finish time.
#
# WHY THIS INVERTS CLEANLY
# ------------------------
# The forward engine is a PRODUCT of independent multipliers:
#     normalized_time = raw_time * distance_factor * geometry * era
#     normalized_time = normalized_time / wmult            (weather, per race)
# and the rating step is algebra:
#     speed_rating = 100 * pool_mean * (1 + difficulty) / normalized_time
#
# None of the splines need inverting. _normalizationFactorCached evaluates the
# whole distance/geometry/era chain at a PROBE TIME of 1.0, so what it returns
# IS the multiplier. To go backward we compute that same factor and MULTIPLY
# instead of divide. Weather is the same trick (probe _applyWeather at 1.0).

import math
import os
import statistics
import sys
import threading
import time
sys.path.insert(0, "engine")
# The engine's forward machinery -- we reuse it, never reimplement it.
from normalize_distance import (
    normalizeTime,
    _normalizationFactorCached,
    _applyWeather,
    poolFor,
)

# DB access, same pattern as the rest of the app.
import sys
sys.path.insert(0, "scripts")
from database import getConn


# ===================================================================== #
#  POOL MEAN RECOVERY
# ===================================================================== #
#
# pool_mean is computed in memory during a solve and never stored. But it is
# RECOVERABLE from any rated result, because the engine wrote all the other
# terms of its own formula:
#
#     speed_rating = 100 * pool_mean * (1 + difficulty) / normalized_time
#  => pool_mean    = speed_rating * normalized_time / (1 + difficulty) / 100
#
# It is constant within a pool, so we median it over a sample of that pool's
# rated rows to cancel per-row noise, then cache it for the process lifetime.

_POOL_MEAN_CACHE = {}          # bare pool -> recovered mean
_SAMPLE_PER_POOL = 1500        # rows per sport to median over; a constant
                               # needs few, and the fetch is index-served

# ★ SCOPED BY THE ROW'S OWN SEASON POOL, NOT BY MEMBERSHIP. The first
#   version sampled "results of athletes who hold a rating in this pool"
#   (athlete_ratings membership) -- but a college athlete's results are
#   mostly their own HS-era rows, so every pool's sample medianed to the
#   corpus-dominant HS constant (~1227 men / ~1466 women) and non-HS pools
#   recovered the WRONG mean: a college rating entered on the conversions
#   page produced times ~25% too fast. ranking_results.pool is the pool of
#   the row's own season, which is the population the constant is ABOUT.
#   (Same discovery as racecast/pool_view.py; see its header for the
#   full story.)
#
# ★ AND NO PER-ROW DIFFICULTY JOINS. The venue joins matched
#   course_canonical on round(gps, 5) equality, which no index serves, so a
#   recovery was a 1,500-row nested-loop scan per pool. The sport's default
#   difficulty replaces them: the median shrugs off the per-row spread, and
#   what remains is the pool's venue-mix deviation from the default, ~1%.
#   The pool/sport fetch is index-served (rr_board_rating_idx) and the
#   results join is by primary key: milliseconds instead of seconds.
_MEAN_SQL = {
    "XC": """
        WITH sample AS (
            SELECT rr.result_id
            FROM   ranking_results rr
            WHERE  rr.pool = %(pool)s AND rr.sport = 'XC'
            LIMIT  %(n)s
        )
        SELECT r.speed_rating, r.normalized_time, NULL::real AS distance_m
        FROM   sample s
        JOIN   results r ON r.result_id = s.result_id
        WHERE  r.speed_rating > 0 AND r.normalized_time > 0
    """,
    "TF": """
        WITH sample AS (
            SELECT rr.result_id
            FROM   ranking_results rr
            WHERE  rr.pool = %(pool)s AND rr.sport = 'TF'
            LIMIT  %(n)s
        )
        SELECT r.speed_rating, r.normalized_time, m.distance_meters
        FROM   sample s
        JOIN   results_tf r ON r.result_id = s.result_id
        -- the event's distance, for the track distance offset the rating
        -- carries (distance_offset); the join is meets_tf's own key
        LEFT JOIN meets_tf m
               ON m.meet_id  = r.meet_id
              AND m.div_id   = r.div_id
              AND m.event_id = r.event_id
        WHERE  r.speed_rating > 0 AND r.normalized_time > 0
    """,
}


def _bare(pool):
    """Strip any '|SPORT' suffix. poolFor() returns bare pools ('hs_m'); the
    engine appends '|XC' later. pool_mean and poolFor must both see the bare
    form, so callers can pass either and we normalize here."""
    if pool and "|" in pool:
        return pool.rsplit("|", 1)[0]
    return pool


# ===================================================================== #
#  THE TRACK DISTANCE OFFSET (issue 148 / 150)
# ===================================================================== #
#
# The joint solve fits one log-time offset per (pool, track distance) --
# the error of the distance potential by event, pinned at the pool's 1600
# -- and divides every track row's normalized_time by exp(offset) before
# it becomes a rating, the same way it divides by (1 + difficulty). A
# conversion that ignored it would put a 3200 converted from a 1600 a few
# percent from where the ratings put it. So the forward path divides by
# exp(offset) and the inverse multiplies, on the same key the engine wrote
# to distance_offset: bare pool, 'TF', the distance rounded to 100 m. XC
# rows carry no offset (their cells absorb it). Missing table, missing row
# or the pinned event all read 0, so an older database converts exactly as
# before.
_OFFSET_TTL = 3600.0
_offsets = {"at": 0.0, "map": {}}
_offset_lock = threading.Lock()
# ! MUST MATCH engine/joint_solve.DIST_BANDS (tests/test_conversions_
#   distance_offset.py pins it): the offsets are by the athlete's rating
#   band since issue 167, three per (pool, event), the 1600 pinned in each.
_DIST_BANDS = (105.0, 120.0, 135.0)
# the band anchors the offsets interpolate between (joint_solve.DIST_BAND_ANCHORS)
_BAND_ANCHORS = (90.0, 112.0, 127.0, 145.0)


def _bandOf(rating):
    if rating is None:
        return 1                                   # the middle band
    r = float(rating)
    return sum(1 for t in _DIST_BANDS if r >= t)


# ===================================================================== #
#  THE ENGINE'S SCALE (issue 177)
# ===================================================================== #
#
# ★ A RATING IS 100 * pool_mean / adjusted, where adjusted is the normalised
#   time with the row's WHOLE applied effect divided out: the tilt times the
#   raw cell delta and the day, plus the event offset. The page used to
#   define its neutral time as normalised time over (1 + displayed
#   difficulty) -- the displayed difficulty is anchored to a zero mean, is
#   not tilted, and carries no day or offset -- so a rating and a time did
#   not land on one scale, and a 9:01 converted to its own event came back
#   as a 9:30. The go-live now writes engine_scale: the pool mean the
#   ratings hang on, the median venue effect the rated rows carried per
#   (pool, sport), and the shift from the raw cell scale to the displayed
#   one. With it, every path here works in the engine's adjusted time:
#       time  -> adjusted : normalised / exp(effect)
#       rating-> adjusted : 100 * pool_mean / rating
#       adjusted -> time  : adjusted * exp(effect) / factor
#   where effect = tilt(rating) * (log1p(displayed difficulty) + shift)
#   + event offset for a chosen venue, or the sport's median effect for
#   none. The same context forward and back cancels exactly. A database
#   without the table falls back to the old (1 + difficulty) arithmetic.
_TILT_K = -0.031            # must match joint_solve.TILT_K (pinned by test)
_TILT_LO, _TILT_HI = 40.0, 200.0     # must match joint_solve.TILT_RATING_LO/HI (pinned by test)
_scale = {"at": 0.0, "map": {}}


def _loadEngineScale():
    out = {}
    try:
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT pool, sport, pool_mean, median_effect, "
                        "anchor_shift FROM engine_scale")
            for pool, sport, pm, me, sh in cur.fetchall():
                out[(_bare(pool), (sport or "").upper())] = (
                    float(pm), float(me), float(sh))
    except Exception:                                    # noqa: BLE001
        out = {}
    _scale["map"] = out
    # ! AN EMPTY ANSWER IS RE-ASKED IN A MINUTE. The go-live writes these
    #   tables while the site is up; a worker that loaded an empty one
    #   cached it for the hour and converted without it (a 136.1 came back
    #   as a 9:15 for an hour after run12, 2026-09-06).
    _scale["at"] = time.time() if out else time.time() - _OFFSET_TTL + 60.0


def engineScale(pool, sport):
    """(pool_mean, median_effect, anchor_shift) or None."""
    if not pool:
        return None
    with _offset_lock:
        if time.time() - _scale["at"] > _OFFSET_TTL:
            _loadEngineScale()
        m = _scale["map"]
    sp = (sport or "").upper()
    return m.get((_bare(pool), sp)) or m.get((_bare(pool), "XC")) \
        or m.get((_bare(pool), "TF"))


def _tilt(rating):
    r = min(max(float(rating), _TILT_LO), _TILT_HI)
    return 1.0 + _TILT_K * (r - 100.0) / 10.0


# ★ THE WINTER GAIN PER ABILITY (issue 194): the go-live shifts every track
#   row by the pool's band shifts interpolated at the athlete's rating, so
#   a dual-sport page shows the stated gain in each band. The same shift
#   goes into a converted track time here, from sport_gain; an empty or
#   missing table is 0, as the go-live applied none.
_gain = {"at": 0.0, "map": {}}


def _loadSportGain():
    out = {}
    try:
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT pool, sport, anchor_rating, log_shift "
                        "FROM sport_gain ORDER BY pool, sport, anchor_rating")
            for pool, sport, anchor, shift in cur.fetchall():
                out.setdefault((_bare(pool), (sport or "").upper()), []).append(
                    (float(anchor), float(shift or 0.0)))
    except Exception:                                    # noqa: BLE001
        out = {}
    _gain["map"] = out
    # ! AN EMPTY ANSWER IS RE-ASKED IN A MINUTE. The go-live writes these
    #   tables while the site is up; a worker that loaded an empty one
    #   cached it for the hour and converted without it (a 136.1 came back
    #   as a 9:15 for an hour after run12, 2026-09-06).
    _gain["at"] = time.time() if out else time.time() - _OFFSET_TTL + 60.0


def sport_gain(pool, sport, rating):
    """The log-time shift the engine applied to a track row at this
    rating (0 for XC, or without the table).

    ! OFF UNLESS ASKED FOR (issue 306, 2026-09-08). The stored track
      ratings do not carry the band shift the sport_gain table records:
      across 300 random 2025 hs_m track rows the stored rating sat
      3 percent above what the raw time gives through venue, distance
      and shift at the top bands, which is the shift to within a point.
      Adding it here turned a stored 9:01 into a 9:28 at its own
      distance. Until the go-live and the table agree, conversions run
      on the ratings as stored; XCP_CONVERT_SPORT_GAIN=1 puts it back."""
    if not pool or (sport or "").upper() != "TF":
        return 0.0
    if os.environ.get("XCP_CONVERT_SPORT_GAIN") != "1":
        return 0.0
    with _offset_lock:
        if time.time() - _gain["at"] > _OFFSET_TTL:
            _loadSportGain()
        pts = _gain["map"].get((_bare(pool), "TF"))
    if not pts:
        return 0.0
    r = float(rating if rating is not None else 100.0)
    if r <= pts[0][0]:
        return pts[0][1]
    if r >= pts[-1][0]:
        return pts[-1][1]
    for (a0, s0), (a1, s1) in zip(pts, pts[1:]):
        if a0 <= r <= a1:
            return s0 + (s1 - s0) * (r - a0) / (a1 - a0) if a1 > a0 else s0
    return pts[-1][1]


def venueEffect(pool, sport, rating, chosen_difficulty, distance_meters):
    """The applied effect (log) a race in this context carries on the
    engine's scale: a chosen venue's own, else the sport's median."""
    sc = engineScale(pool, sport)
    if sc is None:
        return None
    _pm, med, shift = sc
    if chosen_difficulty is None:
        base = med
    else:
        base = _tilt(rating) * (math.log1p(float(chosen_difficulty)) + shift)
    return (base + distance_offset(pool, sport, distance_meters, rating=rating)
            + sport_gain(pool, sport, rating))


def chosen_difficulty(spec, sport):
    """An explicit or venue-fitted difficulty, or None for 'a typical
    race' -- resolve_difficulty without its default."""
    if spec.get("difficulty") is not None:
        return spec["difficulty"]
    return venue_difficulty(
        sport,
        canonical_id=spec.get("canonical_id"),
        distance_meters=spec.get("distance"),
        location_id=spec.get("location_id"),
        is_indoor=spec.get("is_indoor"))


def _loadDistanceOffsets():
    out = {}
    try:
        with getConn() as conn, conn.cursor() as cur:
            try:
                cur.execute("SELECT pool, sport, distance_m, band, log_offset "
                            "FROM distance_offset")
                rows = cur.fetchall()
            except Exception:                            # noqa: BLE001
                conn.rollback()                          # a pre-band table
                cur.execute("SELECT pool, sport, distance_m, 1, log_offset "
                            "FROM distance_offset")
                rows = cur.fetchall()
            for pool, sport, dm, band, off in rows:
                out[(_bare(pool), (sport or "").upper(), int(dm), int(band))] = \
                    float(off or 0.0)
    except Exception:                                    # noqa: BLE001
        out = {}                                         # no table yet: 0
    _offsets["map"] = out
    # ! AN EMPTY ANSWER IS RE-ASKED IN A MINUTE. The go-live writes these
    #   tables while the site is up; a worker that loaded an empty one
    #   cached it for the hour and converted without it (a 136.1 came back
    #   as a 9:15 for an hour after run12, 2026-09-06).
    _offsets["at"] = time.time() if out else time.time() - _OFFSET_TTL + 60.0


def distance_offset(pool, sport, distance_meters, rating=None):
    """log-time offset the engine applied to this (pool, sport, distance)
    at this rating's band; 0.0 wherever it applied none."""
    if not pool or not sport or not distance_meters:
        return 0.0
    if (sport or "").upper() != "TF":
        return 0.0
    with _offset_lock:
        if time.time() - _offsets["at"] > _OFFSET_TTL:
            _loadDistanceOffsets()
        m = _offsets["map"]
    dm = int(round(float(distance_meters) / 100.0)) * 100
    bands = [m.get((_bare(pool), "TF", dm, b)) for b in range(len(_BAND_ANCHORS))]
    if all(v is None for v in bands):
        return 0.0
    # ★ CONTINUOUS IN THE RATING, WHATEVER THE TABLE HOLDS (issue #21,
    #   2026-09-11). A band the table lacks borrows its nearest present
    #   band and the interpolation runs over all four anchors. This used to
    #   fall back to a HARD step on _DIST_BANDS whenever any band was
    #   missing -- and because the forward and the inverse legs evaluate
    #   the offset at slightly different ratings, a rating near a band
    #   edge took the step on one leg and not the other: one full
    #   inter-band step of the 3200 offset, -3.34%, reproduced exactly.
    have = [i for i, v in enumerate(bands) if v is not None]
    vals = [v if v is not None
            else bands[min(have, key=lambda j, i=i: abs(j - i))]
            for i, v in enumerate(bands)]
    # interpolated by rating between the band anchors, as the go-live
    # applies it (joint_solve.distOffsetRow): no step at any band edge
    r = float(rating) if rating is not None else 100.0
    r = min(max(r, _BAND_ANCHORS[0]), _BAND_ANCHORS[-1])
    for (a0, v0), (a1, v1) in zip(zip(_BAND_ANCHORS, vals), zip(_BAND_ANCHORS[1:], vals[1:])):
        if a0 <= r <= a1:
            return v0 + (v1 - v0) * (r - a0) / (a1 - a0)
    return vals[-1]


def pool_mean(pool, sport=None):
    """The pool mean the ENGINE used, RECOVERED rather than read.

    ★ WHY NOT THE `pool_means` TABLE ANY MORE.
      That table is keyed on the BARE pool -- one number for 'hs_m'. But the
      engine's athlete key is (person_id, pool) with SPORT INSIDE THE POOL, so
      it never computes a mean for "HS boys"; it computes one for 'hs_m|XC'
      and another for 'hs_m|TF'. The stored 1237.15 for hs_m sits between the
      two, and using it made every TF rating 1.2% high -- a 9:01.10 at Arcadia
      read 136.3 against its own stored 134.7.

      The table is also written by NOTHING in the codebase. It is a hand-made
      snapshot, so it cannot follow the engine even if the sport were added.

    ★ RECOVERY IS EXACT, NOT AN ESTIMATE. The engine wrote every other term of
      its own formula down:

          speed_rating = 100 * pool_mean * (1 + difficulty) / normalized_time
       => pool_mean    = speed_rating * normalized_time / (1 + difficulty) / 100

      It is a constant within a pool, so any single rated row pins it. We
      median over a sample only to shrug off per-row noise, and the result
      tracks the engine automatically: re-solve, and this follows.

    ⚠ `sport` IS ACCEPTED AND DELIBERATELY UNUSED. Pool is sport-agnostic, so
      the mean is one number for both -- the parameter exists only so callers
      that legitimately know the sport (for difficulty, for the distance law)
      do not have to special-case this one call. Do not add a sport dimension
      here without changing what pool_mean MEANS in the engine first.
    """
    key = _bare(pool)
    sc = engineScale(key, sport)
    if sc is not None:
        return sc[0]                       # the engine's own number (177)
    if key not in _POOL_MEAN_CACHE:
        _POOL_MEAN_CACHE[key] = _recover_pool_mean(key)
    return _POOL_MEAN_CACHE[key]


def _recover_pool_mean(pool):
    """Median of the recovered mean over a sample of that pool's rated rows,
       ACROSS BOTH SPORTS.

    ★ POOL IS SPORT-AGNOSTIC BY DESIGN. `hs_m` is one pool spanning XC and TF,
      and pool_mean is one mean normalized_time for it -- so both sports'
      rows go into one median (ranking_results.sport only picks which
      results table each half joins).

      An earlier version of this function filtered on 'hs_m|TF', matched
      nothing, and silently fell back to the stale table. Sampling one sport
      would be just as wrong in a quieter way: it would return that sport's
      slice of a constant defined over both.

    ⚠ THE CONSTANT DIFFERS BY POOL, AND THAT IS THE POINT. Each pool's
      ratings were solved against its own mean, so hs_m recovers ~1227 while
      college_m recovers a materially different number -- see the scoping
      note on _MEAN_SQL for the membership bug that used to hide this.
    """
    vals = []
    with getConn() as conn:
        with conn.cursor() as cur:
            for sport, sql in _MEAN_SQL.items():
                # The sport's typical venue stands in for every row's --
                # see the note on _MEAN_SQL for why that trade is taken.
                d = default_difficulty(sport)
                cur.execute(sql, {"pool": pool, "n": _SAMPLE_PER_POOL})
                for rating, norm, dist in cur.fetchall():
                    if not rating or not norm:
                        continue
                    off = distance_offset(pool, sport, dist, rating=rating)
                    vals.append(float(rating) * float(norm) / (1.0 + d)
                                / math.exp(off) / 100.0)

    if not vals:
        return _read_pool_mean(pool)      # nothing sampled: better than nothing
    return statistics.median(vals)


def _read_pool_mean(pool):
    """The legacy sport-blind table. Kept only as a fallback for pools the
       recovery cannot sample."""
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pool_mean FROM pool_means WHERE pool = %(p)s",
                        {"p": pool})
            row = cur.fetchone()
    return row[0] if row else None



# ===================================================================== #
#  COURSE DIFFICULTY -- the default, and the exact lookup
# ===================================================================== #
#
# ★ THE BUG THIS REPLACES. Difficulty defaulted to 0.0 everywhere, i.e. "this
#   venue is exactly average". For TF that is never true: fitted TF cells run
#   about -0.016 to -0.045, so a converted track time came out ~3% slow --
#   seven seconds on a 4:00 mile.
#
#   And it did NOT cancel between input and output. _norm_from_time also
#   defaulted to 0.0, so a source venue and a target venue were both assumed
#   neutral while being different cells. Two wrongs, not one.
#
# ★ WHY THE DEFAULT IS A RESULT-WEIGHTED MEDIAN, NOT A MEAN OF CELLS.
#   The question a default answers is "what is a TYPICAL RACE's venue like",
#   not "what is a typical cell like". Cells range from 218,046 results to a
#   handful, so an unweighted mean lets thousands of one-day courses outvote
#   the Armory. Weighting by n_results asks the right question, and a median
#   rather than a mean keeps a few extreme thin cells from dragging it.
#
#   Note this is NOT expected to be 0.0 even though the engine anchors delta
#   to a result-weighted mean of zero: that anchor is across the WHOLE corpus,
#   and the sports sit either side of it. Tracks are uniformly fast relative
#   to an XC-dominated reference, which is exactly why TF's median is negative.
#
# ⚠ AN EXPLICIT 0.0 IS STILL HONOURED. The default only fires when difficulty
#   is ABSENT (None), so a caller who genuinely means "neutral course" can
#   still say so. `dict.get(k, 0.0)` could not express that distinction --
#   which is how the old default hid.

_DEFAULT_DIFFICULTY_CACHE = {}     # sport -> result-weighted median delta


def _weightedMedian(pairs):
    '''Median of `value` weighted by `weight`, from [(value, weight), ...].

    Walks the sorted values accumulating weight until half the total is
    passed. Equivalent to expanding each value `weight` times and taking the
    middle one, without materialising millions of rows.
    '''
    pairs = sorted((v, w) for v, w in pairs if v is not None and w)
    total = sum(w for _v, w in pairs)
    if not total:
        return None
    seen = 0.0
    for value, weight in pairs:
        seen += weight
        if seen >= total / 2.0:
            return float(value)
    return float(pairs[-1][0])


def default_difficulty(sport):
    '''The difficulty of a TYPICAL race in this sport. Cached per process.

    Cell keys are namespaced by sport ('XC:...' / 'TF:loc:...'), so the LIKE
    is the sport filter -- there is no sport column to select on.
    '''
    sport = (sport or "XC").upper()
    if sport in _DEFAULT_DIFFICULTY_CACHE:
        return _DEFAULT_DIFFICULTY_CACHE[sport]

    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT difficulty, n_results
                FROM course_difficulties
                WHERE course_name LIKE %(pfx)s
                  AND difficulty IS NOT NULL
                  AND n_results  > 0
            ''', {"pfx": f"{sport}:%"})
            rows = cur.fetchall()

    # 0.0 only if the table is empty -- the old behaviour, but now it means
    # "no cells exist" rather than silently meaning "every venue is average".
    value = _weightedMedian(rows) or 0.0
    _DEFAULT_DIFFICULTY_CACHE[sport] = value
    return value


def venue_difficulty(sport, canonical_id=None, distance_meters=None,
                     location_id=None, is_indoor=None):
    '''The EXACT fitted delta for one venue, or None if it cannot be resolved.

    ⚠ THE TWO SPORTS ARE KEYED DIFFERENTLY and neither can be looked up by
      name. Passing a course name here would silently match nothing, or match
      several venues sharing a name (there are 21 Woodward Parks).

        XC  (canonical_id, distance_m)     distance rounded to the nearest 100
        TF  'TF:loc:<location_id>:<in|out>'  canonical_id and distance_m are
                                             NULL on every TF row

    Callers that have no venue identifier should not call this -- they should
    take default_difficulty(sport) instead.
    '''
    sport = (sport or "XC").upper()
    if canonical_id is None and location_id is None:
        return None                      # nothing names a venue
    with getConn() as conn:
        with conn.cursor() as cur:
            if sport == "TF":
                if not location_id:            # 0 and None are both "unknown"
                    return None
                key = "TF:loc:%s:%s" % (
                    location_id, "in" if is_indoor else "out")
                cur.execute('''SELECT difficulty FROM course_difficulties
                               WHERE course_name = %(k)s''', {"k": key})
            else:
                if canonical_id is None or not distance_meters:
                    return None
                cur.execute('''
                    SELECT difficulty FROM course_difficulties
                    WHERE canonical_id = %(cid)s
                      AND distance_m   = %(d)s
                ''', {"cid": canonical_id,
                       "d": int(round(distance_meters / 100.0) * 100)})
            row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def resolve_difficulty(spec, sport):
    '''The one rule every caller uses, so the default cannot drift.

      explicit difficulty (even 0.0)  -> use it
      venue identifiers present       -> the exact fitted delta
      neither                         -> this sport's typical race
    '''
    if spec.get("difficulty") is not None:
        return spec["difficulty"]
    exact = venue_difficulty(
        sport,
        canonical_id=spec.get("canonical_id"),
        distance_meters=spec.get("distance"),
        location_id=spec.get("location_id"),
        is_indoor=spec.get("is_indoor"))
    return exact if exact is not None else default_difficulty(sport)


# ===================================================================== #
#  SOURCE -> normalized_time    (four input types)
# ===================================================================== #

def _norm_from_time(time_seconds, distance_meters, pool, difficulty=0.0,
                    season=None, track_length=None, track_type=None,
                    sport=None, event_short=None, weather=None, course=None,
                    chosen=None):
    """A raw time in a stated context -> normalized_time. The forward call."""
    norm = normalizeTime(time_seconds, distance_meters, pool,
                         season=season, track_length=track_length,
                         track_type=track_type, sport=sport,
                         event_short=event_short, weather=weather, course=course)
    sc = engineScale(pool, sport)
    if norm is not None and sc is not None:
        # the engine's scale (177): divide the applied effect out, with the
        # tilt and the band at the rating this time implies.
        # ★ A FIXED POINT, SOLVED (issue #21, 2026-09-11). adjusted =
        #   norm / exp(eff(rating)) with rating = 100 pm / adjusted. Two
        #   undamped passes returned an `adjusted` built from the effect at
        #   the PREVIOUS iterate, while the rating the page then reports --
        #   and the inverse evaluates the effect at -- is 100 pm / adjusted.
        #   The round trip was therefore t * exp(eff(r3) - eff(r2)), and
        #   with a band step between r2 and r3 that was -3.34% on a 3200.
        #   Damped and iterated to 1e-10, then one plain step, so the
        #   effect divided out IS the effect at the rating returned.
        ln_norm = math.log(norm)
        x = ln_norm
        for _ in range(80):
            est = 100.0 * sc[0] / math.exp(x)
            eff = venueEffect(pool, sport, est, chosen, distance_meters)
            x_new = ln_norm - eff
            if abs(x_new - x) < 1e-10:
                x = x_new
                break
            x = 0.5 * (x + x_new)
        est = 100.0 * sc[0] / math.exp(x)
        eff = venueEffect(pool, sport, est, chosen, distance_meters)
        adjusted = norm / math.exp(eff)
        return adjusted
    if norm is not None and difficulty:
        norm = norm / (1.0 + difficulty)    # remove course difficulty
    if norm is not None:
        # the band from the rating this time implies -- the same fixed
        # point as above, so the inverse (which reads the rating AFTER the
        # offset is out) evaluates the offset the forward divided out
        pm = pool_mean(pool, sport)
        if pm:
            ln_base = math.log(norm)
            x = ln_base
            for _ in range(80):
                est = 100.0 * pm / math.exp(x)
                off = distance_offset(pool, sport, distance_meters, rating=est)
                x_new = ln_base - off
                if abs(x_new - x) < 1e-10:
                    x = x_new
                    break
                x = 0.5 * (x + x_new)
            est = 100.0 * pm / math.exp(x)
            off = distance_offset(pool, sport, distance_meters, rating=est)
            if off:
                norm = norm / math.exp(off)     # remove the event's own error
    return norm


def _norm_from_rating(rating, pool, difficulty=0.0, sport=None):
    """A speed rating -> normalized_time, via the engine's own algebra.
       norm = 100 * pool_mean * (1 + difficulty) / rating"""
    pm = pool_mean(pool, sport)
    if pm is None or not rating:
        return None
    if engineScale(pool, sport) is not None:
        return 100.0 * pm / rating          # a rating is neutral already (177)
    return 100.0 * pm * (1.0 + difficulty) / rating


# The stored normalized_time is NOT difficulty-neutral, so each sport needs its
# venue joined in to recover the cell. Two shapes, because the two sports key
# their cells differently -- see venue_difficulty above.
_RESULT_SQL = {
    "XC": """
        SELECT r.normalized_time, cd.difficulty, m.distance, rr.pool,
               r.speed_rating, r.time_seconds, r.rating_pool, NULL::text
        FROM results r
        LEFT JOIN ranking_results rr
               ON rr.result_id = r.result_id AND rr.sport = 'XC'
        LEFT JOIN meets m
               ON m.div_id  = r.div_id
              AND m.meet_id = r.meet_id
              AND m.source  = r.source
        LEFT JOIN course_canonical cc
               ON cc.course_name = m.course_name
              AND round(cc.gps_lat::numeric,  5) = round(m.gps_lat::numeric,  5)
              AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
        LEFT JOIN course_difficulties cd
               ON cd.canonical_id = cc.canonical_id
              AND cd.distance_m   = (round(m.distance / 100.0) * 100)::int
        WHERE r.result_id = %(rid)s
    """,
    "TF": """
        SELECT r.normalized_time, cd.difficulty, m.distance_meters, rr.pool,
               r.speed_rating, r.time_seconds, r.rating_pool, r.event_short
        FROM results_tf r
        LEFT JOIN meets_tf m
               ON m.meet_id  = r.meet_id
              AND m.div_id   = r.div_id
              AND m.event_id = r.event_id
        -- the row's own pool, for the track distance offset; a row that
        -- never reached a board has none and takes no offset
        LEFT JOIN ranking_results rr
               ON rr.result_id = r.result_id AND rr.sport = 'TF'
        LEFT JOIN course_difficulties cd
               ON m.location_id IS NOT NULL
              AND m.location_id <> 0
              AND cd.course_name = 'TF:loc:' || m.location_id || ':'
                                || CASE WHEN COALESCE(m.is_indoor, 0) = 1
                                        THEN 'in' ELSE 'out' END
        WHERE r.result_id = %(rid)s
    """,
}


def _norm_from_result(result_id, sport):
    """A specific stored result -> a DIFFICULTY-NEUTRAL normalized_time.

    ★ "Just read the column" WAS WRONG, and it was the only source path that
      got this wrong. normalized_time corrects distance, geometry, era and
      weather -- but NOT the course. The engine applies difficulty at the
      RATING step instead:

          speed_rating = 100 * pool_mean * (1 + difficulty) / normalized_time

      Every other path here returns a norm with difficulty already divided
      out (see _norm_from_time), because normalized_to_rating deliberately
      does NOT re-apply it -- a rating belongs to the athlete, not the course.
      Returning the raw column therefore left the venue baked in and inflated
      the rating by 1/(1+delta): a 9:01.10 at Arcadia (delta about -0.037)
      came back as 139.9 against its stored 134.7.

    ⚠ NO fallback to 0.0 when the venue has no fitted cell. The result names a
      real venue, so an unrated one is "unknown", not "average" -- the same
      distinction resolve_difficulty draws, reused here rather than restated.
    """
    sport = (sport or "XC").upper()
    sql = _RESULT_SQL.get(sport, _RESULT_SQL["XC"])
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, {"rid": result_id})
            row = cur.fetchone()
    if not row or not row[0]:
        return None

    norm, difficulty, dist, pool, rating = row[0], row[1], row[2], row[3], row[4]
    t, rating_pool, ev = row[5], row[6], row[7]
    pool = pool or (rating_pool or "").split("|")[0] or None
    # ★ THE STORED RATING IS THE ANSWER FOR ANY ROW THAT HAS ONE
    #   (2026-09-11, issue #21). This used to route a "filled" row (issue
    #   306: priced by 09b as pool constant over normalized time, no venue
    #   term) through its raw time instead, telling the two apart by the
    #   '|' the go-live wrote into rating_pool. The go-live writes the BARE
    #   pool now too, so that test was true of EVERY row and every stored
    #   result was re-priced from its raw time with season=None -- era,
    #   weather and altitude lost, and the issue-162 branch below dead.
    #   Both kinds invert correctly through the rating: a solved one to its
    #   own adjusted time exactly, a filled one (K / nt, K = 100 pm times
    #   the pool's median venue factor) to nt over that typical-venue
    #   factor, which is precisely what a target with no named venue puts
    #   back. The time path stays for a row with no rating at all.
    # ★ A RATED ROW INVERTS ITS OWN RATING (issue 162). The engine's number
    #   is 100 * pool_mean / adjusted, with the tilt, the day and the event
    #   offset inside `adjusted`; re-deriving the neutral time from the
    #   stored normalized_time and the CELL's difficulty alone missed the
    #   day and the tilt, so a 132.3 at Mt. SAC converted 30 seconds slower
    #   over 3200 than a 132.8 at Arcadia. Two equal ratings now convert to
    #   equal times, by construction. The cell path stays for unrated rows.
    if rating and float(rating) > 0 and pool:
        pm = pool_mean(pool, sport)
        if pm:
            return 100.0 * pm / float(rating)
    # no rating at all: the raw time through the same terms every other
    # source uses, else the cell path
    if t and dist and pool:
        out = _norm_from_time(float(t), float(dist), pool, sport=sport,
                              event_short=ev, chosen=difficulty)
        if out:
            return out
    if difficulty is None:
        difficulty = default_difficulty(sport)
    out = norm / (1.0 + difficulty)
    if sport == "TF" and pool and dist:
        out = out / math.exp(distance_offset(pool, "TF", dist, rating=rating))
    return out


def _norm_from_athlete(person_id, pool, sport):
    """An athlete's ability -> normalized_time. athlete_ratings stores a
       speed_rating; convert it back via pool_mean at neutral difficulty.
       (ability is already a 5K-equivalent normalized time, so difficulty=0.)"""
    # athlete_ratings.pool may be stored bare ('hs_m') or namespaced ('hs_m|XC').
    # Try the namespaced form first (engine convention), then bare.
    candidates = []
    bare = _bare(pool)
    if sport:
        candidates.append(f"{bare}|{sport}")
    candidates.append(bare)

    row = None
    with getConn() as conn:
        with conn.cursor() as cur:
            for p in candidates:
                cur.execute("""
                    SELECT speed_rating FROM athlete_ratings
                    WHERE athlete_id = %(pid)s AND pool = %(pool)s
                """, {"pid": person_id, "pool": p})
                row = cur.fetchone()
                if row and row[0]:
                    break
    if not row or not row[0]:
        return None
    return _norm_from_rating(row[0], bare, difficulty=0.0, sport=sport)


def source_to_normalized(source):
    """Dispatch a source spec to a normalized_time.

    source is a dict with a 'type' and type-specific fields:
      {type:'time',    time, distance, pool, ...context}
      {type:'rating',  rating, pool, difficulty?}
      {type:'result',  result_id, sport}
      {type:'athlete', person_id, pool, sport}
    Returns a normalized_time (float seconds) or None if unresolvable.
    """
    t = source.get("type")
    if t == "time":
        # ★ BOTH SIDES USE THE SAME RULE, AND THAT IS THE POINT.
        #
        #   "blank course" means ONE thing everywhere: an AVERAGE venue. The
        #   source and the target must agree, because if they disagree the
        #   default becomes a one-way tax and the round trip stops closing:
        #
        #       541s / 3200m / no course  ->  8:51.87 at 3200m / no course
        #
        #   which is the tool contradicting itself. With one shared rule the
        #   algebra cancels exactly:
        #
        #       norm = t * factor / wmult / (1 + d_src)
        #       out  = norm / factor * wmult * (1 + d_tgt)
        #            = t * (1 + d_tgt) / (1 + d_src)     ->  t  when equal
        #
        #   A course the user DID pick still overrides on either side, so
        #   541s at Arcadia converts to a different number than 541s nowhere.
        #   That difference is real; the round-trip gap was not.
        return _norm_from_time(
            source["time"], source["distance"], source["pool"],
            resolve_difficulty(source, source.get("sport")),
            season=source.get("season"), track_length=source.get("track_length"),
            track_type=source.get("track_type"), sport=source.get("sport"),
            event_short=source.get("event_short"),
            weather=source.get("weather"), course=source.get("course"),
            chosen=chosen_difficulty(source, source.get("sport")))
    if t == "rating":
        return _norm_from_rating(source["rating"], source["pool"],
                                 difficulty=source.get("difficulty", 0.0),
                                 sport=source.get("sport"))
    if t == "result":
        return _norm_from_result(source["result_id"], source["sport"])
    if t == "athlete":
        return _norm_from_athlete(source["person_id"], source["pool"],
                                  source.get("sport"))
    return None


# ===================================================================== #
#  normalized_time -> OUTPUT     (the inverse: probe the factor, multiply)
# ===================================================================== #

def _forward_factor(distance_meters, pool, season, track_length,
                    track_type, sport, event_short):
    """The distance*geometry*era multiplier for a context, via the engine's
       cached factor function (already evaluated at probe time 1.0)."""
    return _normalizationFactorCached(distance_meters, pool, season,
                                      track_length, track_type,
                                      sport, event_short)


def _weather_mult(weather, course, sport, distance_meters):
    """The weather multiplier wmult for a context. Recovered by probing:
       _applyWeather(x, ...) returns x / wmult, so wmult = x / result."""
    if not weather:
        return 1.0
    probe = 1000.0
    out = _applyWeather(probe, weather, course, sport, distance_m=distance_meters)
    if not out or out == probe:
        return 1.0                       # no-op weather (reference / refused)
    return probe / out


def normalized_to_time(norm, context):
    """Express a normalized_time as a raw finish time in an output context.

    Forward was:  norm = time * factor / wmult
    So inverse:   time = norm / factor * wmult
    then course difficulty (the engine's line-38 formula) multiplies:
                  race_time = time * (1 + difficulty)

    context: {distance, pool, sport, season?, track_length?, track_type?,
              event_short?, difficulty?(0), weather?, course?}
    """
    if norm is None:
        return None
    factor = _forward_factor(
        context["distance"], context["pool"], context.get("season"),
        context.get("track_length"), context.get("track_type"),
        context.get("sport"), context.get("event_short"))
    if not factor:
        return None

    wmult = _weather_mult(context.get("weather"), context.get("course"),
                          context.get("sport"), context["distance"])
    # A target with no stated venue is a TYPICAL venue for its sport, not a
    # neutral one. For TF the difference is ~3%.
    sc = engineScale(context["pool"], context.get("sport"))
    if sc is not None:
        # the engine's scale (177): put the target's applied effect back
        rating = 100.0 * sc[0] / norm
        eff = venueEffect(context["pool"], context.get("sport"), rating,
                          chosen_difficulty(context, context.get("sport")),
                          context["distance"])
        return norm * math.exp(eff) / factor * wmult

    difficulty = resolve_difficulty(context, context.get("sport"))

    # norm = time * factor / wmult  ->  time = norm / factor * wmult
    base = norm / factor * wmult
    # apply the course/venue difficulty (harder course -> slower time), and
    # the event's own normalisation error the ratings carry (issue 148)
    pm = pool_mean(context["pool"], context.get("sport"))
    off = distance_offset(context["pool"], context.get("sport"),
                          context["distance"],
                          rating=(100.0 * pm / norm) if pm else None)
    return base * (1.0 + difficulty) * math.exp(off)


def normalized_to_rating(norm, pool, difficulty=0.0, sport=None):
    """Express a normalized_time as a speed rating.

    IMPORTANT: normalized_time is ALREADY course-neutral (the engine divided
    difficulty out). A rating is a property of the ATHLETE, not the course, so
    difficulty must NOT be re-applied -- doing so double-counts it and inflates
    ratings on hard courses. The `difficulty` arg is ignored; every cell for one
    source shows the SAME rating, which is the point.

        speed_rating = 100 * pool_mean / norm
    """
    if norm is None or norm <= 0:
        return None
    pm = pool_mean(pool, sport)
    if pm is None:
        return None
    return 100.0 * pm / norm


# ===================================================================== #
#  THE SPREAD    (one source -> a grid of targets)
# ===================================================================== #


# ★ THE ATHLETE'S OWN RACES, WHICH IS THE ONLY INPUT THAT CAN PRODUCE PACES.
#   Critical speed is the slope of a distance-time line, so it needs two races
#   at different distances. A typed time cannot give that -- one performance
#   cannot separate a miler from a 5K runner -- but an athlete SOURCE names
#   somebody whose whole season is already on file, so picking them here is
#   enough.
#
# ⚠ ONE SEASON, NEWEST FIRST, NEVER A CAREER. CS is a fitness and fitness
#   moves; pairing a freshman 3200 against a senior 5K measures growing up.
#   Walks back a year at a time and stops at the first that fits, so a runner
#   whose current season is all 5Ks still gets last year's, labelled.
#
# ! RAW time_seconds AND real distance. NOT normalized_time: that column
#   already carries the distance correction, so a distance-time line built
#   from it would be fitting this project's own exponent back to itself.
_PACE_RACES_SQL = """
    SELECT year, pool, distance, time_seconds
    FROM   ranking_results
    WHERE  person_id = %(pid)s
      AND  time_seconds > 0
      AND  distance > 0
    ORDER  BY year DESC
"""


def athlete_paces(person_id):
    """{year, n_races, paces, dprime, vdot} for one athlete, or a refusal."""
    import paces as _p
    with getConn() as conn:
        with conn.cursor() as cur:
            cur.execute(_PACE_RACES_SQL, {"pid": person_id})
            rows = cur.fetchall()
        conn.rollback()
    if not rows:
        return None

    by_year = {}
    for year, pool, dist, secs in rows:
        by_year.setdefault(year, []).append(
            (float(dist), float(secs), pool))

    for year in sorted(by_year, reverse=True):
        races = [(d, t) for d, t, _ in by_year[year]]
        got, why = _p.criticalSpeed(races)
        if got is None:
            continue
        ladder = _p.trainingPaces(races)
        if not ladder:
            continue
        cs, dprime = got
        pool = by_year[year][0][2]
        return {
            "year": year, "n_races": len(races), "paces": ladder,
            "dprime": round(dprime),
            "vdot": _p.vdot(_p._timeFor(cs, dprime, 5000.0), 5000.0, pool),
        }

    # ! THE REASON, NOT SILENCE. "No paces" and "every race you ran was the
    #   same distance" are different messages and only one is actionable.
    newest = sorted(by_year, reverse=True)[0]
    _, why = _p.criticalSpeed([(d, t) for d, t, _ in by_year[newest]])
    return {"year": newest, "paces": [], "reason": why}


def convert_spread(source, xc_targets, tf_targets):
    """The whole tool in one call.

    source     -- a source spec (see source_to_normalized)
    xc_targets -- [ {label, distance, pool, difficulty, course?, weather?}, ... ]
    tf_targets -- [ {label, distance, pool, ...}, ... ]

    Returns {normalized_time, xc: [{label, time, rating}], tf: [...]}.
    Every cell is the SAME normalized_time expressed in that target's context.
    """
    # ★ A TYPED RATING IS ON THE SCALE THE READER WAS LOOKING AT (issue 49).
    #   The site shows HS-equivalents by default, so a number copied off a
    #   college athlete's page is an HS-equivalent; source["scale"] says so,
    #   and it is brought back to the pool's own scale before anything is
    #   converted. pool_view is imported here, not at the top: it imports
    #   this module for pool_mean.
    pool = source.get("pool", "hs_m")
    hs_factor = None
    if source.get("type") == "rating" and source.get("scale") == "hs":
        from pool_view import repFactor
        hs_factor = repFactor(pool, source.get("sport"))
        if hs_factor and source.get("rating"):
            source = dict(source, rating=float(source["rating"]) / hs_factor)

    norm = source_to_normalized(source)
    if norm is None:
        return {"normalized_time": None, "base_rating": None,
                "base_rating_hs": None,
                "xc": [], "tf": [], "error": "unresolvable source"}

    # ONE rating for the whole source -- it's the athlete's, not the course's.
    base_rating = normalized_to_rating(norm, pool,
                                       sport=source.get("sport"))
    if hs_factor is None:
        try:
            from pool_view import repFactor
            hs_factor = repFactor(pool, source.get("sport"))
        except Exception:                           # noqa: BLE001
            hs_factor = None

    def _cells(targets, sport):
        out = []
        for t in targets:
            ctx = dict(t)
            ctx["sport"] = sport
            time = normalized_to_time(norm, ctx)
            out.append({
                "label": t.get("label"),
                "time":  round(time, 2) if time else None,
            })
        return out

    # ★ PACES AND VDOT ARE THE SAME normalized_time IN TWO MORE CONTEXTS, so
    #   they belong here rather than in the route: every caller of this
    #   function gets them, and none has to know how they are derived.
    #
    # ⚠ VDOT IS ANCHORED ON THE POOL'S OWN RACE DISTANCE, NOT ON WHATEVER THE
    #   USER TYPED. Daniels' percentage-of-VO2max curve is a function of
    #   DURATION, and our distance curve is a different function -- so a 1600m
    #   time and its own 5K equivalent do not yield the same VDOT. Reading it
    #   off one fixed distance makes it a property of the athlete, like the
    #   rating beside it, instead of a number that moves when you retype the
    #   same fitness a different way.
    # ⚠ TRAINING PACES NEED TWO RACES AND THIS TOOL TAKES ONE, so a
    #   one-race source gets NO PACE TABLE -- only a line saying what would
    #   produce one. Critical speed is the slope of an athlete's distance-time
    #   line, so a single performance cannot produce it: three athletes with
    #   the same 4:10 1600 and 3200s of 8:45, 8:58 and 9:20 have critical
    #   speeds 35 s/mile apart.
    #
    #   An earlier version filled the gap with coachRuleTempo (mile + 60-80
    #   s/mi). That is a real coaching rule and it is still in paces.py, but
    #   as the ONLY row on the page it was doing exactly the thing this work
    #   was rebuilt to stop doing: handing all three of those athletes the
    #   same pace, off a constant, under a heading that says "training paces".
    #   A rule of thumb is a fine thing to know and a bad thing to be the
    #   answer. One honest empty state beats one dishonest row.
    #
    # ⚠ VDOT ANCHORS ON THE XC DISTANCE WHATEVER SPORT THE SOURCE WAS.
    #   targetFor is per sport -- hs is 5000m for XC and 1600m for TF -- so
    #   reading it off source["sport"] gave the SAME athlete two different
    #   VDOTs depending on how they typed the same fitness: 72.5 against 75.7.
    from paces import vdot, projectedPaces, MILE_M     # noqa: E402
    from normalize_distance import targetFor           # noqa: E402
    ref_d = targetFor(_bare(pool), "XC") or 5000.0
    ref_t = normalized_to_time(norm, {"distance": ref_d, "pool": pool,
                                      "sport": "XC"})
    # ★ THE SAME PROJECTION THE TABLE BELOW IS ALREADY SHOWING, reused as the
    #   pace anchor. Expressing this performance at 10,000m is what the
    #   conversion tool does; the paces just read the answer off it.
    mile_t = normalized_to_time(norm, {"distance": MILE_M, "pool": pool,
                                       "sport": "TF"})
    t10 = normalized_to_time(norm, {"distance": 10000.0, "pool": pool,
                                    "sport": "TF"})
    p10 = t10 / (10000.0 / MILE_M) if t10 else None
    training = (athlete_paces(source["person_id"])
                if source.get("type") == "athlete" and source.get("person_id")
                else None)

    return {
        "normalized_time": round(norm, 2),
        "base_rating": round(base_rating, 1) if base_rating else None,
        # the same number on the HS scale, for the page's toggle (issue 49)
        "base_rating_hs": (round(base_rating * hs_factor, 1)
                           if base_rating and hs_factor else None),
        # ★ THE FITTED LADDER WHEN THE SOURCE NAMES SOMEBODY, THE PROJECTED
        #   ONE OTHERWISE. Both are built from something measured -- an
        #   athlete's own critical speed, or this performance projected to
        #   10,000m against 60M results -- and each row says which. What is
        #   NOT here is a constant added to a mile: that hands three
        #   different athletes one answer, which is the whole reason the
        #   fitted ladder exists. See paces.projectedPaces.
        "paces": (training["paces"] if training and training.get("paces")
                  else projectedPaces(mile_t, p10)),
        "paces_from": training,
        "paces_note": (None if training and training.get("paces") else
                       "Projected from this one performance using the site's "
                       "own distance curve. Pick an athlete as the source and "
                       "the ladder is fitted to their own races instead, "
                       "which adds a measured critical speed."),
        "vdot": vdot(ref_t, ref_d, pool),
        "vdot_basis_m": round(ref_d),
        "xc": _cells(xc_targets, "XC"),
        "tf": _cells(tf_targets, "TF"),
    }