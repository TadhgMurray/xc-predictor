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

import statistics
import sys
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
_SAMPLE_PER_POOL = 500         # rows to median over; plenty to pin a constant
_ATHLETE_SAMPLE = 2000         # athletes to draw those rows from

# The venue join per sport, so difficulty can be divided out of each sampled
# row. Same two key shapes as venue_difficulty and _RESULT_SQL.
_MEAN_SQL = {
    "XC": """
        WITH ath AS (
            SELECT athlete_id FROM athlete_ratings
            WHERE pool = %(pool)s LIMIT %(nath)s
        )
        SELECT r.speed_rating, r.normalized_time, cd.difficulty
        FROM results r
        JOIN ath ON ath.athlete_id = r.person_id
        LEFT JOIN meets m
               ON m.div_id = r.div_id AND m.meet_id = r.meet_id
              AND m.source = r.source
        LEFT JOIN course_canonical cc
               ON cc.course_name = m.course_name
              AND round(cc.gps_lat::numeric,  5) = round(m.gps_lat::numeric,  5)
              AND round(cc.gps_long::numeric, 5) = round(m.gps_long::numeric, 5)
        LEFT JOIN course_difficulties cd
               ON cd.canonical_id = cc.canonical_id
              AND cd.distance_m   = (round(m.distance / 100.0) * 100)::int
        WHERE r.speed_rating > 0 AND r.normalized_time > 0
        LIMIT %(n)s
    """,
    "TF": """
        WITH ath AS (
            SELECT athlete_id FROM athlete_ratings
            WHERE pool = %(pool)s LIMIT %(nath)s
        )
        SELECT r.speed_rating, r.normalized_time, cd.difficulty
        FROM results_tf r
        JOIN ath ON ath.athlete_id = r.person_id
        -- LATERAL on meet_id ALONE. tfrrs rows often carry a blank div_id and
        -- their event_id is anet-local, so joining all three silently drops
        -- them; (meet_id, event_id) is not unique in meets_tf either, so a
        -- loosened plain join could emit two rows per result. LIMIT 1 makes
        -- both impossible. Same shape app.py's get_races uses -- copied
        -- deliberately, because it is the join that was already proven right.
        LEFT JOIN LATERAL (
            SELECT m.location_id, m.is_indoor
            FROM meets_tf m WHERE m.meet_id = r.meet_id LIMIT 1
        ) m ON TRUE
        LEFT JOIN course_difficulties cd
               ON m.location_id IS NOT NULL AND m.location_id <> 0
              AND cd.course_name = 'TF:loc:' || m.location_id || ':'
                                || CASE WHEN COALESCE(m.is_indoor, 0) = 1
                                        THEN 'in' ELSE 'out' END
        WHERE r.speed_rating > 0 AND r.normalized_time > 0
        LIMIT %(n)s
    """,
}


def _bare(pool):
    """Strip any '|SPORT' suffix. poolFor() returns bare pools ('hs_m'); the
    engine appends '|XC' later. pool_mean and poolFor must both see the bare
    form, so callers can pass either and we normalize here."""
    if pool and "|" in pool:
        return pool.rsplit("|", 1)[0]
    return pool


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
    if key not in _POOL_MEAN_CACHE:
        _POOL_MEAN_CACHE[key] = _recover_pool_mean(key)
    return _POOL_MEAN_CACHE[key]


def _recover_pool_mean(pool):
    """Median of the recovered mean over a sample of that pool's rated rows,
       ACROSS BOTH SPORTS.

    ★ POOL IS SPORT-AGNOSTIC BY DESIGN. `hs_m` is one pool spanning XC and TF,
      and pool_mean is one mean normalized_time for it -- so there is no
      per-sport mean to recover and no namespaced pool to filter on
      (athlete_ratings.pool is bare: hs_m, hs_f, ms_m ...).

      An earlier version of this function filtered on 'hs_m|TF', matched
      nothing, and silently fell back to the stale table. Sampling one sport
      would be just as wrong in a quieter way: it would return that sport's
      slice of a constant defined over both.

      The two SQL shapes below differ ONLY in how each sport's venue is joined
      for its difficulty -- not in what is being measured.
    """
    vals = []
    with getConn() as conn:
        with conn.cursor() as cur:
            for sport, sql in _MEAN_SQL.items():
                cur.execute(sql, {"pool": pool,
                                  "nath": _ATHLETE_SAMPLE,
                                  "n": _SAMPLE_PER_POOL})
                for rating, norm, difficulty in cur.fetchall():
                    if not rating or not norm:
                        continue
                    # An unrated venue is unknown, not average.
                    d = (difficulty if difficulty is not None
                         else default_difficulty(sport))
                    vals.append(float(rating) * float(norm) / (1.0 + d) / 100.0)

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
                    sport=None, event_short=None, weather=None, course=None):
    """A raw time in a stated context -> normalized_time. The forward call."""
    norm = normalizeTime(time_seconds, distance_meters, pool,
                         season=season, track_length=track_length,
                         track_type=track_type, sport=sport,
                         event_short=event_short, weather=weather, course=course)
    if norm is not None and difficulty:
        norm = norm / (1.0 + difficulty)    # remove course difficulty
    return norm


def _norm_from_rating(rating, pool, difficulty=0.0, sport=None):
    """A speed rating -> normalized_time, via the engine's own algebra.
       norm = 100 * pool_mean * (1 + difficulty) / rating"""
    pm = pool_mean(pool, sport)
    if pm is None or not rating:
        return None
    return 100.0 * pm * (1.0 + difficulty) / rating


# The stored normalized_time is NOT difficulty-neutral, so each sport needs its
# venue joined in to recover the cell. Two shapes, because the two sports key
# their cells differently -- see venue_difficulty above.
_RESULT_SQL = {
    "XC": """
        SELECT r.normalized_time, cd.difficulty
        FROM results r
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
        SELECT r.normalized_time, cd.difficulty
        FROM results_tf r
        LEFT JOIN meets_tf m
               ON m.meet_id  = r.meet_id
              AND m.div_id   = r.div_id
              AND m.event_id = r.event_id
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

    norm, difficulty = row[0], row[1]
    if difficulty is None:
        difficulty = default_difficulty(sport)
    return norm / (1.0 + difficulty)


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
            weather=source.get("weather"), course=source.get("course"))
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
    difficulty = resolve_difficulty(context, context.get("sport"))

    # norm = time * factor / wmult  ->  time = norm / factor * wmult
    base = norm / factor * wmult
    # apply the course/venue difficulty (harder course -> slower time)
    return base * (1.0 + difficulty)


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

def convert_spread(source, xc_targets, tf_targets):
    """The whole tool in one call.

    source     -- a source spec (see source_to_normalized)
    xc_targets -- [ {label, distance, pool, difficulty, course?, weather?}, ... ]
    tf_targets -- [ {label, distance, pool, ...}, ... ]

    Returns {normalized_time, xc: [{label, time, rating}], tf: [...]}.
    Every cell is the SAME normalized_time expressed in that target's context.
    """
    norm = source_to_normalized(source)
    if norm is None:
        return {"normalized_time": None, "base_rating": None,
                "xc": [], "tf": [], "error": "unresolvable source"}

    # ONE rating for the whole source -- it's the athlete's, not the course's.
    pool = source.get("pool", "hs_m")
    base_rating = normalized_to_rating(norm, pool,
                                       sport=source.get("sport"))

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
    # ⚠ TRAINING PACES NEED TWO RACES AND THIS TOOL TAKES ONE. Critical speed
    #   is the slope of an athlete's distance-time line, so a single
    #   performance cannot produce it: three athletes with the same 4:10 1600
    #   and 3200s of 8:45, 8:58 and 9:20 have critical speeds 35 s/mile apart.
    #   Rather than manufacture a second race off our own distance curve --
    #   which would make the answer a restatement of this project's exponent
    #   instead of a measurement of the athlete -- the spread carries the one
    #   rule that needs only a mile, clearly labelled, and says what a second
    #   race would buy.
    #
    # ⚠ VDOT ANCHORS ON THE XC DISTANCE WHATEVER SPORT THE SOURCE WAS.
    #   targetFor is per sport -- hs is 5000m for XC and 1600m for TF -- so
    #   reading it off source["sport"] gave the SAME athlete two different
    #   VDOTs depending on how they typed the same fitness: 72.5 against 75.7.
    from paces import coachRuleTempo, vdot, MILE_M     # noqa: E402
    from normalize_distance import targetFor           # noqa: E402
    ref_d = targetFor(_bare(pool), "XC") or 5000.0
    ref_t = normalized_to_time(norm, {"distance": ref_d, "pool": pool,
                                      "sport": "XC"})
    mile_t = normalized_to_time(norm, {"distance": MILE_M, "pool": pool,
                                       "sport": "XC"})

    return {
        "normalized_time": round(norm, 2),
        "base_rating": round(base_rating, 1) if base_rating else None,
        "paces": [p for p in [coachRuleTempo(mile_t)] if p],
        "paces_note": ("training paces need two races at different distances "
                       "— one result cannot tell a miler from a 5K runner"),
        "vdot": vdot(ref_t, ref_d, pool),
        "vdot_basis_m": round(ref_d),
        "xc": _cells(xc_targets, "XC"),
        "tf": _cells(tf_targets, "TF"),
    }