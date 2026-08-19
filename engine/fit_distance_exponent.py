# Project: xc-predictor
# File:    engine/fit_distance_exponent.py  (REWRITE 2026-07-04)
# Purpose: Fit the per-pool distance-conversion splines that
#          normalize_distance.py loads from engine/data/distance_spline.pkl.
#          The spline answers: same athlete, distance D1 -> D2, how does the
#          time scale?  Surface over (log(D2/D1), log(avg distance)).
#
# WHY THE REWRITE (the v1 loader's defects, each vs the convention that
# kills it — the fit half was sound and is preserved below):
#   1. Fit on normalized_time — CIRCULAR: that column already contains the
#      old distance correction; ratios of corrected times carry no distance
#      signal. This fitter consumes RAW time_seconds only.
#   2. Joins without source (§0) and meets_tf joined on div_id alone (PK is
#      div_id+meet_id+event_id -> row multiplication). Era-loader joins now.
#   3. No sentinels / relay / field / racewalk / flat-only / indoor rules.
#   4. SQL self-join + fetchall at 193M-row scale. Now: one stream per
#      sport, ORDER BY aid, pairs built per-athlete in memory.
#   5. Private _toMeters disagreed with the consumer's metersFromDistance
#      (100 vs 50 miles threshold). The consumer's is imported instead.
#
# DESIGN RULES (established elsewhere, applied here):
#   - Outdoor-only TF + flat-only events: geometry is avoided by SELECTION
#     (all pairs flat-400-vs-flat-400 by census C), never by correction —
#     which is why this fitter may run BEFORE the geometry rerun.
#   - Same-athlete pairs: the athlete's ability constant cancels in the
#     ratio; what does NOT cancel across DIFFERENT distances is any
#     event-class offset — hence flat-only.
#   - One pair per (athlete, distance-transition, season), closest in time:
#     geometry's dedup rule, against pseudo-replication by prolific racers.
#   - Exclusion by rule, one ledger line per reason, ledger read FIRST.
#
# Output:  engine/data/distance_spline.pkl — {pool: SmoothBivariateSpline,
#          "global": spline}. normalize_distance loads it at import.
# Usage:   python engine/fit_distance_exponent.py

import sys
import os
import re
import math
import pickle
import numpy as np
from scipy.interpolate import SmoothBivariateSpline

sys.path.insert(0, "scripts")   # database.py
sys.path.insert(0, "engine")    # normalize_distance.py
from database import getConn
# The consumer's own definitions — the fitter and normalize_distance are two
# callers of one truth (the parseEventShort precedent). No local mirrors.
from normalize_distance import (getPool, poolFor, metersFromDistance,
                                parseEventShort)
from corrections import distanceOverrideSQL, distanceDropSQL
# Hand-verified distance corrections, GENERATED from corrections.py so the
# fitter and the backfill can never disagree. Empty table -> empty strings
# -> the query below is byte-for-byte what it was before this change.
_OV_JOIN, _OV_COALESCE = distanceOverrideSQL("r", "XC")
_OV_DROP = distanceDropSQL("r", "XC")


# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

MAX_DAYS_APART = 21          # pair window: same athlete, races <= 21d apart
                             # (fitness ~constant; the ability term cancels)
MIN_DISTANCE_DIFF_METERS = 10    # same-distance pairs carry no signal
MIN_DISTANCE_METERS = 800        # power law breaks below 800m (anaerobic)
MAX_DISTANCE_METERS = 12_000     # ...and above this nothing is a race this
                                 # law covers: 10K championships + fuzz
                                 # margin. THE FINDING (residual run,
                                 # 2026-07-05): the loader had a floor but
                                 # NO CEILING, and unit-corrupted rows
                                 # (metric values x1609.344 — 8,046,720m
                                 # "races") sailed in. In x = log d - log 5K
                                 # such an endpoint sits at x ~ 7.4, so its
                                 # cubic column is x^3 ~ 405 — one corrupt
                                 # edge with delta ~ 0 out-levers hundreds
                                 # of honest ones and flattens g past 5K.
                                 # Also drops road half-marathons riding in
                                 # the XC table: correct — 800-10K is this
                                 # law's whole jurisdiction.
TARGET_DISTANCE_METERS = 5000.0  # the normalization target (5K)

MIN_PAIRS_FOR_POOL_SPLINE = 500  # below this a pool falls back to "global"

# ------------------------------------------------------------------ #
#  PER-POOL NORMALIZATION ANCHORS
#
#  ★ 5000 IS A HIGH-SCHOOL CONSTANT THAT WAS BEING APPLIED TO EVERYONE.
#    Measured median race distance by pool, over 35.3M XC results:
#
#        elem_*      median 2414   p90 3218    5000 is off the chart
#        ms_*        median 3200   p90 3305    5000 is past p90
#        hs_*        median 5000   p90 5000    5000 is the middle  <- fine
#        college_m   median 8000   p10 5000    5000 is the bottom decile
#        college_f   median 5000   p75 6000
#
#    Three of five levels were anchored outside their own data, in both
#    directions. The consumer converts every time to g(log target), and
#    _sampleClamped only trusts the polynomial inside the fitted span --
#    so for ms, whose span is (800, 3200), EVERY row was converted using
#    a linear extrapolation across log(5000/3200) = 0.446. Not a subtle
#    tail effect: 100% of ms rows, extrapolated.
#
#  ⚠ THE ANCHOR IS PER POOL, NEVER PER pool|SPORT. resolvePool's
#    merge=True drops the sport from the athlete key, so one person's XC
#    and TF share a single ability unknown. Two anchors on one athlete
#    would put two scales in one term with nothing to absorb the
#    difference. Keyed on the pool alone, that cannot happen.
#
#  ⚠ AND IT COSTS THE SOLVE NOTHING. The athlete term is keyed
#    (person_id, pool), so a constant rescale of a pool's normalized_time
#    is absorbed entirely by that athlete-pool's ability; pool_mean comes
#    out on the new scale and ratings are unchanged. Course difficulty is
#    shared across pools but sees a constant within each athlete-pool, so
#    it does not move either.
POOL_TARGET_METERS = {
    "elem_m": 2414.0, "elem_f": 2414.0, "elem_unknown_gender": 2414.0,
    "ms_m":   3200.0, "ms_f":   3200.0, "ms_unknown_gender":   3200.0,
    "hs_m":   5000.0, "hs_f":   5000.0, "hs_unknown_gender":   5000.0,
    "college_m": 8000.0,
    # college_f at 6000, not its 5000 median: the p75 is 6000 and the
    # distribution is bimodal (5K and 6K championship courses), so 6000
    # sits inside the mass with span on both sides. Chosen, not derived.
    "college_f": 6000.0,
    "college_unknown_gender": 6000.0,

    # ============================================================== #
    #  PER-SPORT ANCHORS. A KEY WITH A SPORT WINS OVER THE BARE POOL.
    #
    #  ★ THE FITTED SPANS SAY XC AND TF ARE DIFFERENT POPULATIONS, AND THE
    #    ARTIFACT ALREADY KNOWS IT -- d["pools"] holds hs_m|XC and hs_m|TF as
    #    SEPARATE CURVES. Measured spans:
    #
    #        hs_unknown_gender|XC   (3000, 5000)
    #        ms_unknown_gender|XC   (1609, 4002)
    #        hs_f|TF, ms_f|TF, ms_m|TF, hs_unknown|TF   ALL (800, 3200)
    #
    #    EVERY TF span stops at 3200. Anchoring TF at its pool's XC distance
    #    means g(log 5000) -- or 8000 for college_m -- is read from a LINEAR
    #    EXTRAPOLATION off the boundary slope, on every single TF row. That is
    #    the same defect that made ms normalise at 5000 with a span of
    #    (800, 3200), and it is currently live for all of track.
    #
    #  ⚠ THESE KEYS ARE INERT UNDER --sport merged, AND MUST BE. Merging drops
    #    the sport from the athlete key, so one person's XC and TF share a
    #    single ability term. Two anchors on one athlete would put two scales
    #    in one unknown with nothing to absorb the difference -- which is why
    #    the bare-pool anchors above exist and why targetFor falls back to
    #    them. Run the sports SEPARATELY to use these.
    #
    #  ! THE TF VALUES ARE THE MIDDLE OF EACH FITTED SPAN, NOT A MEDIAN RACE
    #    DISTANCE. Confirm against the corpus before adopting -- the XC values
    #    above came from measured medians and these have not had the same
    #    treatment.
    "elem_m|TF": 1200.0, "elem_f|TF": 1200.0,
    "elem_unknown_gender|TF": 1200.0,
    "ms_m|TF": 1600.0, "ms_f|TF": 1600.0, "ms_unknown_gender|TF": 1600.0,
    "hs_m|TF": 1600.0, "hs_f|TF": 1600.0, "hs_unknown_gender|TF": 1600.0,
    "college_m|TF": 3000.0, "college_f|TF": 3000.0,
    "college_unknown_gender|TF": 3000.0,
}


# _targetFor
# Purpose:   The anchor distance for one pool, defaulting to the global
#            constant for anything unlisted (pro_*, and the two globals).
# Arguments: pool -- a bare pool name, or None.
# Output:    metres as float.
def _targetFor(pool, sport=None):
    """The anchor for one pool, preferring a sport-specific key when present.

    ! SPORT FIRST, THEN THE BARE POOL, THEN THE GLOBAL DEFAULT. A corpus with
      no per-sport keys behaves exactly as before, so the two can be mixed
      during a rollout and a half-populated table cannot silently strand a
      pool on the wrong scale.
    """
    if sport and f"{pool}|{sport}" in POOL_TARGET_METERS:
        return POOL_TARGET_METERS[f"{pool}|{sport}"]
    return POOL_TARGET_METERS.get(pool, float(TARGET_DISTANCE_METERS))

AGG_BIN_LOG = 0.03           # aggregate-then-fit bin width, in LOG units
                             # (~3% distance resolution). FITPACK's surfit
                             # workspace arithmetic OVERFLOWS past ~1M
                             # scattered points (measured: a negative
                             # workspace dimension at 1.2M) — and a million
                             # noisy points describe the same surface as a
                             # few hundred bin-medians, minus the noise.
                             # Same doctrine as the geometry banking fit:
                             # spline THROUGH medians, weighted by support.

TIME_SANE = (60.0, 7200.0)   # raw-time sanity band for 800m+ races
                             # (kept as a coarse pre-filter; the PACE guard below
                             # is the real, distance-aware sanity check)

# --- PACE GUARD -------------------------------------------------------------- #
# A flat time band can't tell a 12s/3218m garbage row (impossible) from a fast
# 400m: 12s is "sane" as a raw time but absurd as a pace. So we gate on PACE
# (seconds per mile), which scales with distance. A pair is dropped if EITHER
# leg's pace is impossible.
METERS_PER_MILE = 1609.34
PACE_FLOOR_F    = 210.0   # 3:30 / mile — no woman runs faster; below = garbage
PACE_FLOOR_M    = 180.0   # 3:00 / mile — no man runs faster; below = garbage
# TRAIN/SERVE ALIGNMENT: this MUST match backfill_normalize's slow ceiling
# (_SLOW_CEILING_MIN_PER_MILE = 20.0), or the splines are fitted on rows the
# backfill will never write. It was 1800.0 (30:00/mile): every row paced between
# 20:00 and 30:00 a mile was FIT but not WRITTEN -- a silent skew between what
# the model learns and what it is served.
#
# 20:00/mile is slower than walking; a 35:00 5K is 11:16/mile and a 62:00 5K is
# 19:57/mile, so real finishers survive with room to spare. The garbage cluster
# sits near 40:00/mile (a stopped clock / DNF recorded as a time).
PACE_CEILING    = 1200.0  # 20:00 / mile — gender-independent (a walk is a walk)

# --- RATIO GUARD ------------------------------------------------------------- #
# The distance exponent is log(t2/t1) / log(d2/d1). When d1 ~= d2 the denominator
# -> 0 and the exponent explodes (we saw -7,000,000 from 3218m<->3218m pairs).
# Such pairs carry NO distance signal anyway. Drop any pair whose two distances
# are within this log-ratio of each other. 0.05 ~= a 5% distance gap; e.g.
# 3000<->3200 (log-ratio 0.064) survives, 3000<->3100 (0.033) is dropped.
MIN_LOG_RATIO = 0.05


# --- PER-POOL XC DISTANCE CAP ------------------------------------------------ #
# A distinct problem from the pace/ratio guards: some pairs are physically valid
# (sane pace, real distance gap) but sit at a distance the POOL never actually
# races. College women's XC tops out at 6000m (the NCAA champ distance); an 8k or
# 10k "college_f" XC race is thin, noisy, and mislabeled often enough that those
# sparse long pairs bend the pool's exponent and make it extrapolate to nonsense
# (the 0.474 we saw). The fix is domain, not physics: cap each XC pool at the
# distance it legitimately races, and drop pairs with a leg beyond that.
#
# Keys are the BARE pool (gender+level); this table is consulted for XC ONLY —
# TF is left untouched (its events carry their own distance meaning). Unlisted
# pools fall to DEFAULT_POOL_MAX_XC (= the global ceiling, i.e. no extra cap).
POOL_MAX_DISTANCE_XC = {
    "college_f": 6000,    # NCAA women's championship distance; 8k/10k = garbage
    "college_m": 10000,   # men DO race 8k/10k in college XC — keep them
    "hs_f":      8000,    # HS races top ~5k; 8k of slack for oddball courses
    "hs_m":      8000,
    "ms_f":      6000,    # middle-school distances are short
    "ms_m":      6000,
    "elem_f":    5000,    # elementary tops out around 3k; 5k of slack
    "elem_m":    5000,
    "pro_f":    12000,    # pros race the full range -- no extra cap
    "pro_m":    12000,
}
DEFAULT_POOL_MAX_XC = 12_000   # unlisted pools -> global ceiling (no extra cap)

# ⚠ THIS CAPS THE FIT, NOT THE APPLICATION. normalize_distance evaluates the
#   curve at whatever distance a race actually was, so a race beyond its pool's
#   cap is scored on an EXTRAPOLATED polynomial -- and a cubic outside its
#   fitted range diverges fast. Measured residuals (dev = norm/ability - 1, by
#   pool and distance bin): ms_m reads -0.145 at 8000m and -0.138 at 10000m,
#   both far past its 6000m cap, against roughly zero inside the fitted range.
#
#   Two honest options, neither implemented here: widen the cap where the pool
#   really does race that far, or clamp the applied factor at the cap so a
#   9000m ms race is scored as if it were 6000m. Clamping is wrong too, just
#   wrong in a bounded direction. Until one is chosen, treat any difficulty at a
#   venue whose races sit beyond the cap as unreliable.



# ------------------------------------------------------------------ #
# GUARD HELPERS  (shared by BOTH the stream path and the cache path)
# ------------------------------------------------------------------ #

# _paceFloorFor
# Purpose:   The impossible-fast pace threshold for a gender. Women 3:30/mi,
#            men 3:00/mi. Unknown gender uses the MEN'S (more permissive) floor,
#            so we only ever drop what's impossible even for the fastest case.
# Arguments: gender — "M" | "F" | None/other.
# Output:    the floor in seconds per mile.
def _paceFloorFor(gender):
    return PACE_FLOOR_F if str(gender).strip().upper() == "F" else PACE_FLOOR_M


# _paceSecPerMile
# Purpose:   Convert a (distance, time) into pace, seconds per mile — the
#            distance-aware unit the guard judges on.
# Arguments: dist_m — race distance in metres (> 0); time_s — finish seconds.
# Output:    seconds per mile (float), or None if inputs are unusable.
def _paceSecPerMile(dist_m, time_s):
    if not dist_m or dist_m <= 0 or not time_s or time_s <= 0:
        return None
    miles = dist_m / METERS_PER_MILE           # metres -> miles
    return time_s / miles                       # seconds per mile


# _paceOk
# Purpose:   Is ONE leg's pace physically possible for its gender? Too fast
#            (below the gender floor) or too slow (above the ceiling) = drop.
# Arguments: dist_m, time_s — the leg; gender — for the floor.
# Output:    True if the pace is within [floor, ceiling], else False.
def _paceOk(dist_m, time_s, gender):
    pace = _paceSecPerMile(dist_m, time_s)
    if pace is None:
        return False                            # unusable -> not ok
    return _paceFloorFor(gender) <= pace <= PACE_CEILING


# _ratioOk
# Purpose:   Do two distances differ ENOUGH to carry a real distance signal?
#            Guards the exponent's denominator, log(d2/d1), away from ~0.
# Arguments: d1, d2 — the pair's two distances (both > 0).
# Output:    True if |log(d2/d1)| >= MIN_LOG_RATIO, else False.
def _ratioOk(d1, d2):
    if not d1 or not d2 or d1 <= 0 or d2 <= 0:
        return False
    return abs(math.log(d2 / d1)) >= MIN_LOG_RATIO


# _genderFromPool
# Purpose:   Recover the gender letter from a pool string, for the CACHE path
#            (cached pairs store `pool` like "college_f" but no gender field).
# Arguments: pool — e.g. "college_f", "hs_m", "ms_unknown_gender".
# Output:    "F", "M", or None (unknown -> caller uses the men's floor).
def _genderFromPool(pool):
    p = str(pool)
    if p.endswith("_f"):
        return "F"
    if p.endswith("_m"):
        return "M"
    return None                                 # unknown_gender -> None -> M floor


# _pairPassesGuards
# Purpose:   The ONE place both guards are applied to a whole pair, so the
#            stream and cache paths judge a pair identically. A pair survives
#            only if the two distances differ enough (ratio) AND both legs'
#            paces are possible (pace, either-leg-fails -> drop).
# Arguments: d1,t1,d2,t2 — the pair's legs; gender — for the pace floor.
# Output:    True to keep the pair, False to drop it.
def _pairPassesGuards(d1, t1, d2, t2, gender):
    if not _ratioOk(d1, d2):                     # near-equal distances -> no signal
        return False
    if not _paceOk(d1, t1, gender):              # leg 1 pace impossible -> drop
        return False
    if not _paceOk(d2, t2, gender):              # leg 2 pace impossible -> drop
        return False
    return True



SANE_YEARS = (1995, 2026)    # the date gate (0023/2223 rows refuse here)
OUTDOOR_MONTHS = set(range(4, 10))   # Apr..Sep: a TF race in these months
                             # is outdoor at ~100% (admitted domain rule,
                             # used ONLY when meets_tf.is_indoor is NULL)

TRANSITION_BUCKET_M = 100    # distances bucketed to 100m for the
                             # one-pair-per-transition dedup key
DUP_TIME_BUCKET_S = 0.5      # canon duplicate detection: raw times equal
                             # to half a second are one result (era's rule)

ITERSIZE = 50_000            # rows per server round-trip on the stream
SORT_MEM_MB = 512            # work_mem for the one big ORDER BY aid sort

OUTPUT_DIR  = "engine/data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "distance_spline.pkl")

# The pair cache: streaming the two tables costs ~an hour of sorted scans;
# everything after loading costs minutes. A crash past loading (it has
# happened twice) should never charge the streams again. Pairs are cached
# as PLAIN TUPLES, not dicts — same data at ~1/4 the pickle size.
CACHE_FILE = os.path.join(OUTPUT_DIR, "distance_pairs_cache.pkl")

# ------------------------------------------------------------------ #
# SMALL SHARED HELPERS (dates, pools)
# ------------------------------------------------------------------ #

# Two compiled date patterns (ISO and US) + cumulative-days table: cheaper
# than strptime across ~200M rows; leap day ignored (a 1-day error is
# nothing against a 21-day window). Same machinery as the era loader.
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})")
_US_DATE  = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_CUM_DAYS = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)


# _parseDateOrdinal
# Purpose:   Turn the TEXT date into (day ordinal, season year, month).
#            The ordinal (year*365 + days-into-year) exists ONLY to take
#            differences — gaps are exact to ±1 day across year boundaries,
#            ample at a 21-day window. String comparison is NOT used
#            anywhere (US-format dates misorder as strings — v1's bug).
# Arguments: date_text — raw r.date string, either format, or None/garbage.
# Output:    (ordinal, year, month), or None when unparseable or the year
#            fails the sane gate (the 0023/2223 rows die here, ledgered).
def _parseDateOrdinal(date_text):
    if not date_text:
        return None
    m = _ISO_DATE.match(date_text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _US_DATE.match(date_text)
        if not m:
            return None
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (SANE_YEARS[0] <= y <= SANE_YEARS[1]) or not (1 <= mo <= 12):
        return None
    return y * 365 + _CUM_DAYS[mo - 1] + d, y, mo


# _cleanGender / _poolForPair
# Purpose:   The competitive pool for one row. Wraps getPool with era's ONE
#            admitted domain fact: a tfrrs row with unusable grade is a
#            COLLEGE athlete (TFRRS is a collegiate service, error ~0).
#            (STALE NOTE, resolved: fit_era_corrections._poolFor was moved
#            into normalize_distance on 2026-07-04 and that fitter now
#            imports poolFor directly. There is one definition.)
#            fitters import it; until then this is a marked mirror. !!
# Arguments: grade — raw grade string; gender — raw gender string;
#            source — 'anet' | 'tfrrs'.
# Output:    pool string (unknown-gender pools allowed — they fit their own
#            spline or fall to "global"), or None = unknowable, drop.
def _cleanGender(gender):
    g = str(gender or "").strip().upper()
    return g if g in ("M", "F") else None


# _genderFromName
# Purpose:   Recover gender from a tfrrs division title when the athletes lateral
#            gave none (unlinked tfrrs rows have no person_id -> no gender). Used
#            as a fallback so those rows can still get a pool instead of dropping.
# Arguments: div_name — the blob's division title string, or None.
# Output:    "M", "F", or None.
#   THE TRAP: "men" is a substring of "women", so test women/girls FIRST or every
#   women's race misreads as M. (Same rule the era patch and the distance
#   inference use; mirrored here so this fitter stays self-contained.)
def _genderFromName(div_name):
    if not div_name:
        return None
    low = div_name.lower()
    if "women" in low or "girls" in low:   # F first — women contains "men"
        return "F"
    if "men" in low or "boys" in low:
        return "M"
    return None


# _poolForPair
# Purpose: pool for one row. This used to be a LOCAL COPY of normalize_distance's
#          pooling rule -- i.e. a second source of truth, exactly the drift that
#          poolFor was created to prevent. It now DELEGATES to the real poolFor,
#          so this fitter's spline keys can never diverge from the backfill's.
# Arguments: grade, gender, source — as before;
#            school — raw school string. poolFor uses it ONLY when the grade is
#            unusable AND the school is unambiguous (tfrrs rows have grade=NULL,
#            so the school is the only honest level signal they carry).
# Output: pool string, or None (caller ledgers as unknown_pool and drops).
def _poolForPair(grade, gender, source, school=None):
    # gender is cleaned first because the old local copy did so before applying
    # the tfrrs fallback; poolFor cleans internally too, so this is idempotent.
    return poolFor(grade, _cleanGender(gender), source, school)

# ------------------------------------------------------------------ #
# THE TWO SQL STREAMS
# ------------------------------------------------------------------ #
# Conventions encoded (per the master doc + dedup):
#   - sentinels per source in one predicate (999999 / place 0 / NULL time);
#   - EVERY join carries source (§0) — and tfrrs XC rows self-exclude by
#     rule here: the INNER JOIN to meets finds no tfrrs rows (meets is
#     anet-only), which is the era loader's no_distance drop, done in SQL;
#   - meets_tf joined on its FULL key (meet_id, div_id, event_id, source);
#   - relays/field cut in SQL (usability); indoor is a VALUE judgment ->
#     decided in memory where it can be ledgered;
#   - aid IS NOT NULL: a pair needs an identity; NULL-aid rows can never
#     pair (era streams them for its benchmark — we have no benchmark);
#   - ORDER BY aid: one athlete's rows arrive consecutively.

_ATHLETE_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT a.gender
        FROM athletes a
        WHERE a.athlete_id = COALESCE(r.person_id, r.athlete_id)
        LIMIT 1
    ) g ON TRUE
"""

_XC_SQL = f"""
    SELECT COALESCE(r.person_id, r.athlete_id) AS aid,
           r.date, r.time_seconds, r.grade, r.source, r.canon_meet_id,
           -- Per-source distance: anet reads meets.distance (as before); tfrrs XC
           -- reads the meets_tfrrs jsonb blob, keyed by the row's div_id (jsonb
           -- keys are strings -> ::text). COALESCE tries anet first; for tfrrs
           -- m.distance is NULL (mt supplies it).
           COALESCE(
               {_OV_COALESCE}
               m.distance,
               (mt.division_distances -> r.div_id::text ->> 'distance')::float
           ) AS distance,
           g.gender,
           -- div_name from the same blob, for the gender fallback in Python when
           -- the athletes lateral returns nothing (unlinked tfrrs rows). NULL for
           -- anet rows. LAST column -> unpacks last in _prepareXCRow.
           (mt.division_distances -> r.div_id::text ->> 'div_name') AS div_name,
           -- NEW, LAST column: the school. tfrrs rows have grade=NULL, so the
           -- school is their only honest level signal; poolFor uses it when the
           -- grade is unusable AND the school is unambiguous. Appended last so
           -- no existing positional unpack shifts.
           r.school AS school
    FROM results r
    -- LEFT (was INNER): an INNER JOIN to the anet-only meets table ELIMINATED
    -- every tfrrs XC row before it could reach the stream. LEFT lets tfrrs rows
    -- through with m.distance NULL, so the COALESCE above can fill from the blob.
    LEFT JOIN meets m
      ON m.div_id = r.div_id AND m.source = r.source
    -- tfrrs XC distance/title live in meets_tfrrs. r.source='tfrrs' in the ON
    -- (not WHERE): in a LEFT JOIN the ON only decides MATCHING, so anet rows pass
    -- through with mt all-NULL; in the WHERE it would DELETE every anet row.
    LEFT JOIN meets_tfrrs mt
      ON mt.meet_id = r.meet_id
     AND mt.sport   = 'XC'
     AND r.source   = 'tfrrs'
{_OV_JOIN}    {_ATHLETE_LATERAL}
    WHERE r.time_seconds IS NOT NULL
      AND r.time_seconds > 0
      AND r.time_seconds <> 999999
      AND COALESCE(r.place, -1) <> 0
      AND COALESCE(r.person_id, r.athlete_id) IS NOT NULL
{_OV_DROP}    ORDER BY aid
"""

_TF_SQL = f"""
    SELECT COALESCE(r.person_id, r.athlete_id) AS aid,
           r.date, r.time_seconds, r.grade, r.source, r.canon_meet_id,
           r.event_short, m.distance_meters, m.is_indoor, g.gender,
           -- NEW, LAST column. results_tf.school is 100% populated; tfrrs TF
           -- rows have grade=NULL, so school is their only honest level signal.
           r.school AS school
    FROM results_tf r
    JOIN meets_tf m
      ON m.meet_id  = r.meet_id
     AND m.div_id   = r.div_id
     AND m.event_id = r.event_id
     AND m.source   = r.source
    {_ATHLETE_LATERAL}
    WHERE r.time_seconds IS NOT NULL
      AND r.time_seconds > 0
      AND r.time_seconds <> 999999
      AND COALESCE(r.place, -1) <> 0
      AND COALESCE(r.is_relay, 0) = 0
      AND COALESCE(r.is_field, 0) = 0
      AND COALESCE(r.person_id, r.athlete_id) IS NOT NULL
    ORDER BY aid
"""

# ------------------------------------------------------------------ #
# ROW PREPARATION (usability checks; one ledger line per drop reason)
# ------------------------------------------------------------------ #

# _prepareXCRow
# Purpose:   One XC stream row -> a buffer record, or None + a ledger tick.
#            XC is all flat running by nature; distance may be miles or
#            meters (the CONSUMER'S converter decides — no local mirror).
# Arguments: row — tuple per _XC_SQL's SELECT order; ledger — {reason: n}.
# Output:    record dict {ord, season, dist, time, canon, pool}, or None.
def _prepareXCRow(row, ledger):
    # div_name then school are the last two columns (see _XC_SQL). div_name is
    # NULL for anet rows; school is present on both sources.
    aid, date_t, t, grade, source, canon, distance, gender, div_name, school = row

    parsed_date = _parseDateOrdinal(date_t)
    if parsed_date is None:
        ledger["bad_date"] += 1; return None
    ordinal, year, _month = parsed_date

    dist = metersFromDistance(distance)
    if dist is None:
        ledger["no_distance"] += 1; return None
    if dist < MIN_DISTANCE_METERS:
        ledger["below_min_distance"] += 1; return None
    if dist > MAX_DISTANCE_METERS:                  # the ceiling (Fix A):
        ledger["above_max_distance"] += 1; return None   # unit-corrupt rows

    # GENDER FALLBACK: unlinked tfrrs rows have no gender from the lateral. Recover
    # it from the division title so they can get a pool instead of dropping. anet
    # rows have div_name NULL -> fallback is a no-op, gender stays as the lateral
    # gave it. _finishRecord/_poolForPair clean/validate the value either way.
    if _cleanGender(gender) is None:
        gender = _genderFromName(div_name)

    return _finishRecord(ordinal, year, dist, t, canon,
                         grade, gender, source, ledger, school)


# _prepareTFRow
# Purpose:   One TF stream row -> record or None. TF-only concerns:
#            flat events only (an event-class offset does NOT cancel
#            across different distances) and the outdoor rule:
#              is_indoor == 1        -> DROP (geometry contamination)
#              is_indoor == 0        -> keep
#              is_indoor is None     -> Apr..Sep = outdoor (admitted rule),
#                                       otherwise DROP — None is NOT 0;
#                                       absence of a claim is not a claim.
# Arguments: row — tuple per _TF_SQL's SELECT order; ledger — {reason: n}.
# Output:    record dict, or None.
def _prepareTFRow(row, ledger):
    (aid, date_t, t, grade, source, canon,
     event_short, dm, is_indoor, gender, school) = row

    parsed_date = _parseDateOrdinal(date_t)
    if parsed_date is None:
        ledger["bad_date"] += 1; return None
    ordinal, year, month = parsed_date

    ev = parseEventShort(event_short)          # the consumer's parser
    if ev["kind"] == "racewalk":               # belt (SQL flags) + braces
        ledger["racewalk"] += 1; return None   # (the parser) — some rows
    if ev["kind"] == "relay":                  # have flags unset
        ledger["relay"] += 1; return None
    if ev["kind"] != "flat":                   # hurdles/steeple: their
        ledger["not_flat"] += 1; return None   # constant rides the ratio

    if is_indoor == 1:
        ledger["indoor"] += 1; return None
    if is_indoor is None and month not in OUTDOOR_MONTHS:
        ledger["unknown_venue_offseason"] += 1; return None

    # Column-first distance resolution (the geometry lesson: the column is
    # ~30% populated; the event parser is the workhorse fallback).
    dist = dm if (dm is not None and dm > 0) else ev["meters"]
    if dist is None or dist <= 0:
        ledger["no_distance"] += 1; return None
    if dist < MIN_DISTANCE_METERS:
        ledger["below_min_distance"] += 1; return None
    if dist > MAX_DISTANCE_METERS:                  # the ceiling (Fix A)
        ledger["above_max_distance"] += 1; return None

    return _finishRecord(ordinal, year, float(dist), t, canon,
                         grade, gender, source, ledger, school)


# _finishRecord
# Purpose:   The checks both sports share, in one place: raw-time sanity
#            band and pool resolution. Splitting this out keeps the two
#            prepare functions to their sport-specific concerns only.
# Arguments: the resolved fields; ledger — {reason: n}.
# Output:    the record dict, or None.
def _finishRecord(ordinal, year, dist, t, canon, grade, gender, source,
                  ledger, school=None):
    # `school` defaults to None so the TF caller (which does not have one) keeps
    # EXACTLY its old behaviour: poolFor with school omitted is byte-for-byte the
    # old function, verified across every grade/gender/source combination.
    t = float(t)
    if not (TIME_SANE[0] <= t <= TIME_SANE[1]):
        ledger["insane_time"] += 1; return None    # coarse pre-filter (kept)
    # PACE GUARD: distance-aware sanity. Gender is known on this path, so we use
    # the correct floor. Catches the garbage a flat band misses (12s/3218m is a
    # "sane" 12s raw but an impossible ~6s/mile pace).
    if not _paceOk(dist, t, gender):
        ledger["insane_pace"] += 1; return None

    pool = _poolForPair(grade, gender, source, school)
    if pool is None:
        ledger["unknown_pool"] += 1; return None

    ledger["kept"] += 1
    return {"ord": ordinal, "season": year, "dist": dist, "time": t,
            "canon": canon, "pool": pool}

# ------------------------------------------------------------------ #
# PAIR BUILDING (per-athlete, in memory)
# ------------------------------------------------------------------ #

# _flushAthletePairs
# Purpose:   ONE athlete's records -> pairs, then forget the athlete.
#            Three stages, each a lesson already paid for:
#              1. canon dedupe — a canon-linked meet carries BOTH sources'
#                 rows for one physical race (dedup warning #1); the copy
#                 is dropped so it can't double-pair against third races;
#              2. windowed pairing — records sorted by date; each pairs
#                 with later records within MAX_DAYS_APART whose distance
#                 differs by >= MIN_DISTANCE_DIFF (earlier race is race 1);
#              3. transition dedupe — ONE pair per (bucketed d1<->d2,
#                 season), keeping the smallest date gap: a kid racing
#                 1600/3200 weekly is one observation per season, not 12.
# Arguments: records — this athlete's record dicts (any order);
#            pairs_out — the global pair list (appended in place);
#            ledger — {reason: n} (dup_result / pairs ticks).
# Output:    None (mutates pairs_out and ledger).
def _flushAthletePairs(records, pairs_out, ledger):
    rows = _dropCanonDuplicates(records, ledger)
    rows.sort(key=lambda r: r["ord"])          # date order for the window

    best = {}                                  # transition key -> (gap, pair)
    for i, r1 in enumerate(rows):
        for r2 in rows[i + 1:]:                # only later races
            gap = r2["ord"] - r1["ord"]
            if gap > MAX_DAYS_APART:
                break                          # sorted: no later match either
            if abs(r2["dist"] - r1["dist"]) < MIN_DISTANCE_DIFF_METERS:
                continue
            # RATIO GUARD: even past the absolute-metres check, guard the
            # exponent's log(d2/d1) denominator away from ~0. (Absolute and
            # log-ratio catch different edges; both matter.)
            if not _ratioOk(r1["dist"], r2["dist"]):
                ledger["near_equal_ratio"] += 1
                continue
            key = _transitionKey(r1, r2)
            if key not in best or gap < best[key][0]:
                best[key] = (gap, _makePair(r1, r2))

    for _gap, pair in best.values():
        pairs_out.append(pair)
        ledger["pairs"] += 1


# _dropCanonDuplicates
# Purpose:   Remove same-race copies inside one athlete: at canon-linked
#            meets the anet and tfrrs rows describe one result. Key =
#            (canon id, rounded distance, half-second time bucket) — era's
#            rule verbatim. Rows with no canon id can't duplicate this way.
# Arguments: records, ledger — as above.
# Output:    the deduplicated list (new list; input untouched).
def _dropCanonDuplicates(records, ledger):
    seen, out = set(), []
    for r in records:
        if r["canon"] is not None:
            key = (r["canon"], round(r["dist"]),
                   round(r["time"] / DUP_TIME_BUCKET_S))
            if key in seen:
                ledger["dup_result"] += 1
                continue
            seen.add(key)
        out.append(r)
    return out


# _transitionKey
# Purpose:   The dedup identity of a pair: WHICH distance transition, in
#            WHICH season. Distances bucket to 100m so 1600 and 1609 are
#            one transition; the (min, max) ordering makes A->B and B->A
#            the same key.
# Arguments: r1, r2 — the two records (r1 earlier).
# Output:    a hashable key tuple.
def _transitionKey(r1, r2):
    b1 = round(r1["dist"] / TRANSITION_BUCKET_M)
    b2 = round(r2["dist"] / TRANSITION_BUCKET_M)
    return (min(b1, b2), max(b1, b2), r1["season"])


# _makePair
# Purpose:   The pair dict in EXACTLY the shape the preserved fit half
#            expects ({pool, distance1, distance2, time1, time2}); race 1
#            is the earlier race; the pool is read off race 1 (grade can
#            change across a season boundary, not inside a 21-day window).
# Arguments: r1, r2 — the two records.
# Output:    the pair dict.
def _makePair(r1, r2):
    return {"pool": r1["pool"],
            "distance1": r1["dist"], "distance2": r2["dist"],
            "time1": r1["time"], "time2": r2["time"]}

# ------------------------------------------------------------------ #
# THE STREAM CONSUMER
# ------------------------------------------------------------------ #

# _consumePairStream
# Purpose:   Drive one sport's stream end to end: rows arrive ORDER BY aid,
#            so an aid change means the previous athlete is complete —
#            flush their buffer to pairs and forget them. Memory holds ONE
#            athlete plus the growing pair list; the 193M source rows are
#            never resident.
# Arguments: cur — an OPEN named (server-side) cursor already executing;
#            prepare — _prepareXCRow or _prepareTFRow;
#            ledger — {reason: n}, mutated throughout.
# Output:    list of pair dicts for the whole sport.
def _consumePairStream(cur, prepare, ledger):
    pairs, buffer, current_aid = [], [], None
    for row in cur:                     # named cursor: fetches ITERSIZE
        aid = row[0]                    # rows per round trip, transparently
        if aid != current_aid:
            if buffer:
                _flushAthletePairs(buffer, pairs, ledger)
            buffer, current_aid = [], aid
        rec = prepare(row, ledger)
        if rec is not None:
            buffer.append(rec)
    if buffer:                          # the last athlete has no aid-change
        _flushAthletePairs(buffer, pairs, ledger)   # to flush them — do it
    return pairs


# _setSortMemory
# Purpose:   Give the session enough work_mem that the ORDER BY aid sort
#            over the big tables spills less. One SET, session-scoped.
# Arguments: conn — open connection.
def _setSortMemory(conn):
    with conn.cursor() as cur:
        cur.execute(f"SET work_mem = '{SORT_MEM_MB}MB'")


# _loadSportPairs
# Purpose:   One sport, end to end: open the named cursor, stream, consume,
#            print the ledger. The ledger prints BEFORE any fit output —
#            plumbing problems must be read before curves are interpreted.
# Arguments: conn — open connection; sport — "XC" | "TF".
# Output:    list of pair dicts.
def _loadSportPairs(conn, sport):
    sql, prepare = ((_XC_SQL, _prepareXCRow) if sport == "XC"
                    else (_TF_SQL, _prepareTFRow))
    # PRE-EXISTING BUG (surfaced by --fresh, which forces the streaming path the
    # cache used to skip): `insane_pace` and `near_equal_ratio` are incremented
    # by _finishRecord / _flushAthletePairs but were never initialised here, so
    # the first garbage-pace row or near-equal pair raised KeyError. The ledger
    # is a plain dict of zeros, so EVERY key any code path touches must appear.
    ledger_keys = ["kept", "pairs", "dup_result", "bad_date", "no_distance",
                   "below_min_distance", "above_max_distance",
                   "insane_time", "insane_pace", "near_equal_ratio",
                   "unknown_pool",
                   "not_flat", "racewalk", "relay", "indoor",
                   "unknown_venue_offseason"]
    ledger = {k: 0 for k in ledger_keys}

    print(f"Loading {sport} pairs (streamed)...")
    # A NAMED cursor is server-side: rows stay in Postgres and arrive in
    # ITERSIZE batches as we iterate — fetchall of 193M rows can't happen.
    cur = conn.cursor(name=f"dist_pairs_{sport.lower()}")
    cur.itersize = ITERSIZE
    cur.execute(sql)
    pairs = _consumePairStream(cur, prepare, ledger)
    cur.close()

    print(f"  [{sport} ledger]  " + "  ".join(
        f"{k}={ledger[k]:,}" for k in ledger_keys if ledger[k]))
    print(f"  {len(pairs):,} pairs kept\n")
    return pairs


# _pairsToTuples / _tuplesToPairs
# Purpose:   The cache's wire format, both directions. Tuples instead of
#            dicts: identical information, a fraction of the pickle bytes
#            and load time. The dict shape is rebuilt on load so nothing
#            downstream ever knows the cache exists.
# Arguments: pairs — list of pair dicts / tuples respectively.
# Output:    the converted list.
def _pairsToTuples(pairs):
    return [(p["pool"], p["distance1"], p["distance2"],
             p["time1"], p["time2"]) for p in pairs]


def _tuplesToPairs(tuples):
    return [{"pool": pl, "distance1": d1, "distance2": d2,
             "time1": t1, "time2": t2} for pl, d1, d2, t1, t2 in tuples]


# _saveCache / _loadCache
# Purpose:   Persist / restore both sports' pairs in one file. _loadCache
#            returns None when the file is absent — the caller streams.
def _saveCache(xc_pairs, tf_pairs):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CACHE_FILE, "wb") as f:
        pickle.dump({"xc": _pairsToTuples(xc_pairs),
                     "tf": _pairsToTuples(tf_pairs)}, f)
    print(f"  pair cache written -> {CACHE_FILE}")


def _loadCache():
    if not os.path.exists(CACHE_FILE):
        return None
    with open(CACHE_FILE, "rb") as f:
        c = pickle.load(f)
    print(f"  pair cache loaded <- {CACHE_FILE}  "
          f"(--fresh to re-stream; delete it after schema changes)")
    return _tuplesToPairs(c["xc"]), _tuplesToPairs(c["tf"])


# _dropOverMaxPairs
# Purpose:   Fix A's CACHE-SIDE half: the same distance ceiling the
#            prepare functions now enforce at the stream, applied to
#            already-cached pairs — so the unit-corrupt monsters die
#            TODAY without paying the hour of re-streaming. Prints the
#            body count (a cache built post-ceiling should print 0; a
#            nonzero count on a fresh cache means the loader gate leaked).
# Arguments: pairs — one sport's cached pair dicts; sport — the tag.
# Output:    the filtered list (new list; input untouched).
def _dropOverMaxPairs(pairs, sport):
    kept = [p for p in pairs
            if p["distance1"] <= MAX_DISTANCE_METERS
            and p["distance2"] <= MAX_DISTANCE_METERS]
    dropped = len(pairs) - len(kept)
    if dropped:
        print(f"  [{sport}] {dropped:,} cached pairs dropped over the "
              f"{MAX_DISTANCE_METERS:,.0f}m ceiling (unit-corrupt class)")
    return kept


# _dropBadPairs
# Purpose:   The CACHE-SIDE half of the ratio + pace guards — mirrors the stream
#            gates (_finishRecord pace, pair-loop ratio) so already-cached pairs
#            are cleaned TODAY without paying an --fresh re-stream. A pair is
#            kept only if it clears BOTH guards via _pairPassesGuards. Gender for
#            the pace floor is recovered from the pair's `pool` string, since
#            cached pairs carry no gender field.
# Arguments: pairs — one sport's cached pair dicts; sport — the tag (for print).
# Output:    the filtered list (new list; input untouched). Prints the split so
#            the two failure modes are visible separately.
def _dropBadPairs(pairs, sport):
    kept, near_equal, bad_pace = [], 0, 0
    for p in pairs:
        gender = _genderFromPool(p["pool"])         # pool -> "F"/"M"/None
        d1, t1 = p["distance1"], p["time1"]
        d2, t2 = p["distance2"], p["time2"]
        if not _ratioOk(d1, d2):                    # near-equal distances
            near_equal += 1
            continue
        if not (_paceOk(d1, t1, gender) and _paceOk(d2, t2, gender)):
            bad_pace += 1                           # either leg's pace impossible
            continue
        kept.append(p)
    if near_equal or bad_pace:
        print(f"  [{sport}] dropped {near_equal:,} near-equal-distance pairs "
              f"(ratio < {MIN_LOG_RATIO}) + {bad_pace:,} impossible-pace pairs")
    return kept


# _poolMaxXC
# Purpose:   The max distance an XC pool legitimately races, from the cap table,
#            with the global-ceiling default for any unlisted pool. One lookup so
#            the table is consulted in exactly one place.
# Arguments: pool — a bare pool string ("college_f", "hs_m", ...).
# Output:    the cap in metres (int).
def _poolMaxXC(pool):
    return POOL_MAX_DISTANCE_XC.get(pool, DEFAULT_POOL_MAX_XC)


# _pairWithinPoolRange
# Purpose:   Does this pair sit within its pool's real racing range? BOTH legs
#            must be at or under the pool's cap — a pair reaching up to a
#            never-raced distance (its long leg) is the one that bends the curve,
#            so either leg over the cap disqualifies the pair.
# Arguments: pair — a cached pair dict {pool, distance1, distance2, ...}.
# Output:    True to keep, False to drop.
def _pairWithinPoolRange(pair):
    cap = _poolMaxXC(pair["pool"])
    return pair["distance1"] <= cap and pair["distance2"] <= cap


# _dropOverPoolRange
# Purpose:   XC-ONLY cache-side filter: remove pairs whose distance exceeds what
#            their pool actually races (the domain cap). Called on the XC list
#            only — TF is never passed in, so TF stays untouched. Ledgers the
#            drop count so the effect is visible in the run output.
# Arguments: pairs — the XC pair list (flat, pre-grouping).
# Output:    the filtered list (new list; input untouched).
def _dropOverPoolRange(pairs):
    kept = [p for p in pairs if _pairWithinPoolRange(p)]
    dropped = len(pairs) - len(kept)
    if dropped:
        print(f"  [XC] dropped {dropped:,} pairs beyond their pool's race "
              f"range (per-pool cap; e.g. college_f > "
              f"{POOL_MAX_DISTANCE_XC['college_f']:,}m)")
    return kept


# loadAllPairs
# Purpose:   Both sports, grouped by pool — the loader's whole public face;
#            everything downstream of here is the preserved fit half.
#            Cache-first: streams only when no cache exists or --fresh
#            forced it, and writes the cache the moment streaming ends —
#            BEFORE any fitting can crash. The distance ceiling is applied
#            on BOTH paths (the stream gates in the prepare functions; the
#            cache is filtered here) so no path can carry a monster.
# Arguments: use_cache — False (--fresh) forces a re-stream.
# Output:    (xc_by_pool, tf_by_pool) — {pool: [pair, ...]} each.
def loadAllPairs(use_cache=True):
    if use_cache:
        cached = _loadCache()
        if cached is not None:
            xc_pairs, tf_pairs = cached
            xc_pairs = _dropOverMaxPairs(xc_pairs, "XC")
            tf_pairs = _dropOverMaxPairs(tf_pairs, "TF")
            xc_pairs = _dropBadPairs(xc_pairs, "XC")   # ratio + pace guards,
            tf_pairs = _dropBadPairs(tf_pairs, "TF")   # cache-side (no --fresh)
            xc_pairs = _dropOverPoolRange(xc_pairs)    # XC-ONLY per-pool cap
                                                       # (TF intentionally skipped)
            return _groupByPool(xc_pairs), _groupByPool(tf_pairs)
    with getConn() as conn:
        _setSortMemory(conn)
        xc_pairs = _loadSportPairs(conn, "XC")
        tf_pairs = _loadSportPairs(conn, "TF")
    _saveCache(xc_pairs, tf_pairs)       # persist BEFORE anything can crash
    return _groupByPool(xc_pairs), _groupByPool(tf_pairs)


# _groupByPool
# Purpose:   Flat pair list -> {pool: [pairs]}. (v1 defined groupByPool but
#            called _groupByPool — the second latent NameError. One name.)
# Arguments: pairs — flat list of pair dicts.
# Output:    dict pool -> list.
def _groupByPool(pairs):
    grouped = {}
    for pair in pairs:
        grouped.setdefault(pair["pool"], []).append(pair)
    return grouped

# ------------------------------------------------------------------ #
# STEP 2 — PAIR EDGES  (aggregate-then-fit, endpoint-true)
# ------------------------------------------------------------------ #
# THE MODEL CHANGED (2026-07-04): the 2-D SmoothBivariateSpline over
# (log ratio, log location) is RETIRED. Real pair clouds concentrate on a
# thin manifold — a huge near-zero-ratio band (XC course distances are
# quasi-continuous) with thin informative wings — and a tensor-product
# surface is unconstrained orthogonal to a manifold: FITPACK chased the
# band with knots, gave up, and the free directions blew up to 1e75 at
# evaluation (measured). Replacement: a 1-D POTENTIAL per pool,
#     g(log d) = log time at distance d, in a reference athlete's frame,
# solved from difference constraints g(ld2) - g(ld1) = log(T2/T1) — the
# SAME machinery as geometry's g1, the era fusion, and the Stage-B ladder
# (its fourth deployment). Same-ratio pairs at different locations touch
# DIFFERENT knot spans, so location-dependence lives in g's curvature —
# no second input needed. Composition and reversibility are exact by
# construction; extrapolation is the boundary slope, a choice, not an
# oscillation.

ENDPOINT_BIN_LOG = 0.02      # grouping resolution on EACH endpoint's log
                             # distance (~2%). The key GROUPS ONLY — fitted
                             # coordinates are each bin's true means (the
                             # grid-center snap was a measured ~1% bug).
KNOT_SPACING_LOG = 0.05      # knot pitch (~5% distance resolution)
MIN_EDGE_SPAN_LOG = 0.05     # a pair must differ by >= ~5% in distance to
                             # carry distance signal; nearer pairs are pure
                             # confound (course + fitness) wearing maximal
                             # weight — the near-duplicate contradictions
                             # that fueled the between-knot zigzag
SE_FLOOR = 3e-3              # a bin's authority is bounded by SYSTEMATIC
                             # error (course structure ~0.3%), not just
                             # sampling error: a 100K-pair bin does not get
                             # to claim 0.1% certainty about a 0.3% world.
                             # (Was 1e-3 -> weights of 1000 vs curvature 1:
                             # six orders of regularizer silence, measured
                             # as local exponents of 16.8 and -2.9.)
PRIOR_PAIR_SIGMA = 0.02      # the domain's known per-pair noise (~2% log
                             # time, measured throughout this project). A
                             # cell's SE may never claim less noise per
                             # pair than the domain has: se >=
                             # 1.253*PRIOR/sqrt(n). THE FINDING (residual
                             # run): the MAD of 2-4 points is not a noise
                             # estimate — two junk pairs agreeing by luck
                             # gave sigma ~ 0, the SE hit SE_FLOOR, and an
                             # n=2 cell held the SAME maximum authority as
                             # a 580K-pair cell. SE_FLOOR was built to cap
                             # the giants; it accidentally crowned the
                             # gnats. This floor demotes ONLY small cells
                             # (the prior term drops below SE_FLOOR near
                             # n ~ 70 and changes nothing above).
MIN_PAIRS_PER_EDGE = 3       # a two-pair cell cannot testify at all: its
                             # MAD is half a gap, its median is a coin
                             # flip. Consequence accepted knowingly: the
                             # thinnest pools lose edges and may fall to
                             # their sport global — the fallback chain
                             # working, not a regression.
# --- Fix D (the long-span evidence gate) RETIRED 2026-07-06: the knob
# sweep measured knife-edges exactly where borderline evidence clustered
# at its hand-drawn band (ms exp-0.68 cells at the 0.70 floor, college_m
# span-0.19 cells at the 0.15/0.20 line). Its job moved into the solve:
# the IRLS robust reweight below judges every cell by its distance from
# consensus in DATA-DERIVED noise units, so there is no fixed line for
# evidence to straddle. Its audit trail is inherited (entry
# "robust_down"). ---
TUKEY_C = 4.685              # the Tukey bisquare tuning constant, in
                             # robust-sigma units: cells within c*scale
                             # of consensus keep smoothly-graded weight,
                             # beyond it weight is exactly 0. 4.685 is
                             # the standard value (95% efficiency on
                             # clean Gaussian data); the re-run sweep
                             # perturbs it to prove nothing hinges on it.
IRLS_MAX_ITERS = 10          # reweight rounds; converges in ~3-5 (early
                             # break on coefficient stability below).
ROBUST_AUDIT_W = 0.5         # cells finishing under this robust weight
                             # are recorded by name in the entry — the
                             # exclusion-stays-visible rule, inherited
                             # from the retired gate.
STABILITY_TUKEY_PROBES = (3.5, 6.0)   # the stability gate's perturbations:
                             # every saved curve is refit at these Tukey
                             # constants. IRLS converges to the NEAREST
                             # consensus, and c controls how greedy the
                             # early rounds are about choosing it — the
                             # sweep caught college_f|XC flipping
                             # saved->GATED at c=6 (thin honest coverage
                             # past 6K let the corrupt cluster capture
                             # the basin). A curve whose verdict depends
                             # on c does not get to call itself saved.
STABILITY_TOL = 0.02         # max local-exp shift a saved curve may show
                             # across the probes — the sweep's own STABLE
                             # threshold, now enforced at fit time. This
                             # thresholds a MEASURED pool-level quantity
                             # with a printed value and a loud flip, not
                             # cell evidence that can cluster invisibly
                             # at a line (the failure that retired Fix D).
POLY_DEGREE_EDGES = (30, 10)  # edge counts to earn degree 3 / degree 2;
                             # below the second, a straight line (one
                             # exponent) is all the data can testify to.
                             # WHY A POLYNOMIAL (round 3's lesson): two
                             # rounds of armoring a 55-knot solve each
                             # DAMPED the zigzag, and real data found a
                             # new residual confound to feed it (courses,
                             # fitness direction, per-edge selection). The
                             # truth has ~3 degrees of freedom — an
                             # exponent near 1.06 drifting mildly with log
                             # distance — and 52 spare dimensions were a
                             # standing invitation for every confound to
                             # live somewhere. Rigidity is structural:
                             # a cubic CANNOT zigzag.
MIN_EDGES_FOR_POOL = 8       # a potential needs a real graph; thinner
                             # pools fall back to the global curve
MIN_EPS_MATCHED_TRANSITIONS = 10   # eps (the calendar offset, below) is
                             # measured ONLY from matched contrasts — a
                             # transition holding BOTH time orders. A pool
                             # must have at least this many for its own
                             # eps: a median over fewer voices is one or
                             # two odd meets wearing a verdict. Below the
                             # floor the pool BORROWS the level's eps, or
                             # runs undebiased (eps=0) with a loud label.
SHORT_SPAN_LOG = 0.10        # the residual report's bucket line: spans
                             # under ~10% vs over. NEUTRAL by design — the
                             # buckets are span-only so the fuzz hypothesis
                             # must PREDICT the pattern, not define it.
RESIDUAL_TOP_N = 6           # worst-tension transitions printed per pool


# _aggregatePairEdges
# Purpose:   Collapse a pool's pairs onto an endpoint grid — keyed BY TIME
#            ORDER now: one weighted difference constraint per occupied
#            (ld1, ld2, direction) cell. The old version FOLDED the two
#            time orders into one cell so the calendar bias (earlier race
#            soft/less fit: +b in one order, -b in the other) would cancel
#            in the median — a cancellation that rests on BALANCED time
#            orders, which the direction census measured at 60-86%
#            short-first in XC (TF: ~50/50, gaps ~0 — the control). The
#            direction is therefore REMEMBERED, not folded: each cell's
#            median keeps its side of the bias, and the solve carries one
#            explicit unknown (eps) for it.
#            Median delta still kills two-sided noise; weight = 1/SE
#            (median), sigma from the cell's own MAD, as before.
# Arguments: pairs — one pool's pair dicts (distance1/time1 = the EARLIER
#            race, _makePair's contract — the stored order IS the
#            direction; the cache preserves it).
# Output:    list of (ld1, ld2, delta, weight, n, s) tuples; s = +1 when
#            the SHORTER race came first, -1 when the longer did. Indices
#            0-4 are unchanged from the old tuple, so every consumer that
#            reads endpoints/weights (knot grid, span percentiles) is
#            untouched; only the solvers read s.
def _aggregatePairEdges(pairs):
    bins = {}                    # (i1, i2, sf) -> ([ld1s], [ld2s], [ys])
    for p in pairs:
        d1, d2, t1, t2 = (p["distance1"], p["distance2"],
                          p["time1"], p["time2"])
        if d1 <= 0 or d2 <= 0 or t1 <= 0 or t2 <= 0:   # degenerate guard
            continue
        ld1, ld2 = math.log(d1), math.log(d2)
        y = math.log(t2 / t1)
        # Canonicalize the AXES to low->high distance exactly as before
        # (negate the delta when the pair ran high-first) — but RECORD
        # which case it was: short_first is the pair's time order, the
        # coordinate the calendar confound actually lives on.
        short_first = ld1 < ld2
        if not short_first:
            ld1, ld2, y = ld2, ld1, -y
        if ld2 - ld1 < MIN_EDGE_SPAN_LOG:  # no distance signal, full
            continue                       # confound: not an edge
        key = (round(ld1 / ENDPOINT_BIN_LOG),
               round(ld2 / ENDPOINT_BIN_LOG), short_first)
        cell = bins.setdefault(key, ([], [], []))
        cell[0].append(ld1); cell[1].append(ld2)
        cell[2].append(y)
    edges = []
    for (_i1, _i2, sf), (l1s, l2s, ys) in bins.items():
        arr = np.asarray(ys)
        if arr.size < MIN_PAIRS_PER_EDGE:      # Fix C: two points cannot
            continue                           # testify (see the constant)
        med = float(np.median(arr))
        ld1m, ld2m = float(np.mean(l1s)), float(np.mean(l2s))
        sigma = 1.4826 * float(np.median(np.abs(arr - med)))
        # Fix B: three floors on the SE — the cell's own MAD-based
        # estimate, the systematic floor (caps the giants), and the
        # domain-prior floor (demotes small cells whose few points agree
        # by luck; 1.253*PRIOR/sqrt(n) is the SE a median of n honestly
        # ~2%-noisy pairs could at best achieve).
        se = max(1.253 * sigma / math.sqrt(arr.size),
                 SE_FLOOR,
                 1.253 * PRIOR_PAIR_SIGMA / math.sqrt(arr.size))
        edges.append((ld1m, ld2m, med, 1.0 / se, arr.size, 1 if sf else -1))
    return edges

# ------------------------------------------------------------------ #
# STEP 3 — THE POTENTIAL SOLVE  (two-stage: eps from contrasts, then
#                                a rigid low-order polynomial for shape)
# ------------------------------------------------------------------ #
#   g(x) = a1*x + a2*x^2 + a3*x^3,  x = log d - log 5000
# No constant term -> g(5000) = 0 EXACTLY (the anchor is structural, not
# a heavy row). The calendar offset eps (2026-07-05, the direction
# census's finding: the EARLIER race of a pair runs soft/less fit, so
# every edge's delta carries -s*eps, s = +1 short-first / -1 long-first)
# is estimated in a SEPARATE STAGE BEFORE the shape:
#   stage 1: eps = weighted MEDIAN over matched contrasts — transitions
#            holding BOTH time orders, each contributing
#            (lf_delta - sf_delta)/2. No polynomial is anywhere near
#            this stage, so shape misfit CANNOT reach eps.
#   stage 2: shape fit over ALL edges with eps FIXED (delta + s*eps
#            moves the known offset to the data side).
# WHY NOT A JOINT SOLVE (v1's lesson, measured on the real refit): a
# joint shape+eps lstsq is separable only while the shape model is
# CORRECT. XC's edge cloud mixes real transitions with a label-noise
# band no cubic can represent; where the cubic misfits and the misfit
# correlates with the direction pattern, the eps column is the cheapest
# parking spot — eps absorbed misfit (college_f +1.96% vs census ~0.6%,
# college_m NEGATIVE against its own gaps) and the shape bent around the
# contaminated eps. Coupled unknowns are a confound's housing just like
# spare dimensions; the wall between stages is structural, not argued.
# The median (not mean) makes one anomalous contrast (college_m's
# 8000->10000, a max-weight edge with a wrong-signed story) one voice
# among many instead of a veto.
# eps remains a NUISANCE parameter: printed as a finding, recorded in
# the entry for audit, never used at inference.
# The fitted polynomial is then SAMPLED onto a knot grid, so the saved
# artifact keeps the exact {"knots","values"} shape the consumer, its
# mirror evaluator, and the smoke test already speak — model surgery
# with zero downstream churn.

# _knotGrid
# Purpose:   The SAMPLING grid for the artifact (no longer unknowns): an
#            even log grid spanning every edge endpoint and the 5K
#            target. Linear interpolation of a smooth cubic at this
#            pitch loses < 0.01%.
# Arguments: edges — _aggregatePairEdges output.
# Output:    ascending numpy array of knot log-distances (>= 4).
def _knotGrid(edges):
    pts = [e[0] for e in edges] + [e[1] for e in edges]
    lo = min(min(pts), math.log(TARGET_DISTANCE_METERS)) - KNOT_SPACING_LOG
    hi = max(max(pts), math.log(TARGET_DISTANCE_METERS)) + KNOT_SPACING_LOG
    n = max(int(round((hi - lo) / KNOT_SPACING_LOG)) + 1, 4)
    return np.linspace(lo, hi, n)


# _polyDegree
# Purpose:   How much shape the data has earned: cubic needs a real edge
#            population; a thin pool gets a straight line (one exponent)
#            rather than freedom it cannot constrain. The count rule is
#            a CEILING, not a grant: count measures how many votes a
#            pool has, but says nothing about WHERE they stand — 254
#            transitions crammed into a narrow log-span (college_f|XC's
#            3.2k-6k band) earn a cubic by count that the span cannot
#            condition, and the stability gate catches the wobble. The
#            `cap` argument is the demotion ladder's lever: a refit may
#            request LESS freedom than the count grants, never more.
# Arguments: n_edges — the pool's edge count;
#            cap — optional 1..3 ceiling from the demotion ladder
#            (None = the count rule alone decides, the historic path).
# Output:    1, 2, or 3.
def _polyDegree(n_edges, cap=None):
    if n_edges >= POLY_DEGREE_EDGES[0]:
        degree = 3
    elif n_edges >= POLY_DEGREE_EDGES[1]:
        degree = 2
    else:
        degree = 1
    return degree if cap is None else min(degree, cap)


# _distinctTransitions
# Purpose:   How many DISTINCT distance transitions the edges cover,
#            direction IGNORED. Direction-tagging roughly doubled the
#            edge count without adding any SHAPE information, so degree
#            and the thin-pool floor must be earned by transitions, not
#            by the split — else every pool's degree silently inflates.
#            (Old folded cells WERE one-per-transition, so counting
#            transitions preserves the old thresholds' meaning exactly.)
# Arguments: edges — the tagged tuples.
# Output:    the count of unique (binned ld1, binned ld2) endpoints.
def _distinctTransitions(edges):
    return len({(round(e[0] / ENDPOINT_BIN_LOG),
                 round(e[1] / ENDPOINT_BIN_LOG)) for e in edges})


# _epsSides
# Purpose:   Edge counts per time-order side — REPORT-ONLY under v2 (the
#            rung-1 guard lives on MATCHED CONTRASTS now, not raw side
#            counts); printed so a reader can see a pool's imbalance at
#            a glance next to its eps verdict.
# Arguments: edges — the tagged tuples.
# Output:    (n_short_first, n_long_first).
def _epsSides(edges):
    n_sf = sum(1 for e in edges if e[5] > 0)
    return n_sf, len(edges) - n_sf


# _buildShapeRow
# Purpose:   One edge's polynomial difference basis: column j holds
#            x2^(j+1) - x1^(j+1) — the constraint delta = g(x2) - g(x1)
#            written on the coefficient basis (no constant term; the
#            g(5000)=0 anchor stays structural).
# Arguments: x1, x2 — endpoint log distances MINUS log 5000; degree 1..3.
# Output:    list of `degree` floats (one lstsq row, before the eps col).
def _buildShapeRow(x1, x2, degree):
    return [x2 ** (j + 1) - x1 ** (j + 1) for j in range(degree)]


# _pairedCells
# Purpose:   THE SHARED BASE for everything that reads matched
#            transitions: regroup the tagged edges direction-blind and
#            yield the (short-first cell, long-first cell) pair for every
#            transition holding BOTH. The matching logic lives HERE and
#            only here — eps (contrasts) and the residual instrument
#            (midpoints) are two consumers of one definition, so they can
#            never disagree about what "matched" means.
# Arguments: edges — the tagged tuples from _aggregatePairEdges.
# Output:    generator of (sf_edge, lf_edge) tuple pairs.
def _pairedCells(edges):
    by_trans = {}                       # direction-blind key -> {s: edge}
    for e in edges:
        key = (round(e[0] / ENDPOINT_BIN_LOG),
               round(e[1] / ENDPOINT_BIN_LOG))
        by_trans.setdefault(key, {})[e[5]] = e
    for sides in by_trans.values():
        if 1 in sides and -1 in sides:
            yield sides[1], sides[-1]


# _matchedContrasts
# Purpose:   STAGE 1's evidence, and nothing else: per matched
#            transition, the implied calendar offset and its authority.
#              sf cell median = fair - eps
#              lf cell median = fair + eps      } same transition, so the
#              implied eps = (lf - sf) / 2        fair delta CANCELS —
#            the shape never enters, which is the whole design.
#            One-sided transitions contribute NOTHING here (they are
#            still debiased in stage 2 by the pooled eps — that is what
#            one-eps-per-pool buys).
# Arguments: edges — the tagged tuples.
# Output:    list of (implied_eps, weight); weight = 1/SE of the implied
#            value (SE of a difference = sqrt of summed squared SEs,
#            halved with the /2; each cell's SE recovered as 1/e[3]).
def _matchedContrasts(edges):
    contrasts = []
    for sf, lf in _pairedCells(edges):
        implied = (lf[2] - sf[2]) / 2.0
        se = math.sqrt((1.0 / sf[3]) ** 2 + (1.0 / lf[3]) ** 2) / 2.0
        contrasts.append((implied, 1.0 / se))
    return contrasts


# _matchedMidpoints
# Purpose:   The residual instrument's evidence: per matched transition,
#            the DEBIASED fair delta.
#              sf median = fair - eps
#              lf median = fair + eps   } midpoint (sf + lf)/2 = fair —
#            eps cancels by the SAME identity stage 1 exploits, so this
#            column cannot be fooled by the calendar effect. Coordinates
#            are the mean of the two cells' (already true-mean, never
#            grid-snapped) endpoints.
# Arguments: edges — the tagged tuples.
# Output:    list of (ld1, ld2, fair_delta, weight, n_pairs) tuples;
#            weight = 1/SE of the midpoint (same arithmetic as the
#            contrast — a midpoint and a half-difference of the same two
#            numbers share an SE).
def _matchedMidpoints(edges):
    mids = []
    for sf, lf in _pairedCells(edges):
        fair = (sf[2] + lf[2]) / 2.0
        se = math.sqrt((1.0 / sf[3]) ** 2 + (1.0 / lf[3]) ** 2) / 2.0
        mids.append(((sf[0] + lf[0]) / 2.0, (sf[1] + lf[1]) / 2.0,
                     fair, 1.0 / se, sf[4] + lf[4]))
    return mids


# _epsFromContrasts
# Purpose:   The pool's eps verdict: the WEIGHTED MEDIAN of the implied
#            values. Median, not mean, by measured need — one anomalous
#            contrast at maximal (floored-SE) weight must be one voice
#            among many, not a veto; a median moves only when half the
#            authority moves. Reuses _weightedPercentile at q=50.
# Arguments: contrasts — _matchedContrasts' output (non-empty).
# Output:    eps as float.
def _epsFromContrasts(contrasts):
    return float(_weightedPercentile([c[0] for c in contrasts],
                                     [c[1] for c in contrasts], 50))


# _solveShapeOnce
# Purpose:   The plain weighted lstsq core — ONE reweighting round.
#            Pulled out so the IRLS loop below is readable as its
#            mathematical description: solve, residuals, reweight.
# Arguments: A — design matrix rows; b — targets; w — TOTAL weights
#            (admission x robust).
# Output:    coefficient array (a1 first).
def _solveShapeOnce(A, b, w):
    sw = np.sqrt(w)
    coeffs, *_ = np.linalg.lstsq(A * sw[:, None], b * sw, rcond=None)
    return coeffs


# _tukeyWeights
# Purpose:   The bisquare downweight: u = residual in robust-sigma
#            units, weight (1 - (u/c)^2)^2 inside |u| < c, exactly 0
#            beyond. Smooth near consensus (small disagreements barely
#            matter), hard zero for the impossible — with the boundary
#            set by the DATA'S scale, not a hand constant.
# Arguments: r — residual array; scale — robust sigma (from _irlsScale).
# Output:    weight array in [0, 1].
def _tukeyWeights(r, scale, tukey_c=None):
    c = TUKEY_C if tukey_c is None else tukey_c   # None -> the shipped
    u = r / (c * scale)                           # constant (default path)
    w = (1.0 - u ** 2) ** 2
    w[np.abs(u) >= 1.0] = 0.0
    return w


# _irlsScale
# Purpose:   The robust sigma the weights are measured against: 1.4826 x
#            the ADMISSION-weighted median |residual| — heavy cells
#            define what "consensus" means, exactly as they define the
#            fit. Floored at a tiny epsilon so a near-perfect iteration
#            cannot divide by zero.
# Arguments: r — residuals; w_adm — admission weights.
# Output:    scale as float.
def _irlsScale(r, w_adm):
    mad = _weightedPercentile(list(np.abs(r)), list(w_adm), 50)
    return max(1.4826 * float(mad), 1e-9)


# _solveShapeRobust
# Purpose:   STAGE 2, made robust (the 2026-07-06 methodology change —
#            the knob sweep's KNIFE-EDGE verdict retired the rule zoo's
#            growth): IRLS with Tukey bisquare. Build the design ONCE
#            (eps moves to the data side, as before), then iterate
#            solve -> residuals -> data-scaled reweight until the
#            coefficients stop moving. A corrupt cell's residual grows
#            as the fit escapes toward the honest majority, so its
#            weight walks to zero over the rounds — no enumeration of
#            WHY it is corrupt required.
#            KNOWN BLIND SPOT (why Fix A stays): robust losses defeat
#            RESIDUAL outliers, not LEVERAGE outliers — an 8,046,720m
#            endpoint (x^3 ~ 405) bends the young fit toward itself,
#            making its own residual small, and Tukey then sees a model
#            citizen. The distance ceiling is this solve's bodyguard.
#            SAFETY: if the reweight ever leaves fewer surviving cells
#            than unknowns, the reweight is abandoned for that pool
#            (all-ones) — a consensus that excludes almost everyone is
#            not a consensus.
# Arguments: edges — tagged tuples; degree — 1..3; eps — the fixed
#            stage-1 offset.
# Output:    (coeffs, downweighted) where downweighted lists every cell
#            finishing under ROBUST_AUDIT_W as
#            (d1_m, d2_m, implied_exp, n_pairs, final_weight).
def _solveShapeRobust(edges, degree, eps, tukey_c=None):
    L0 = math.log(TARGET_DISTANCE_METERS)
    A, b, w_adm = [], [], []
    for ld1, ld2, delta, weight, _cnt, s in edges:
        A.append(_buildShapeRow(ld1 - L0, ld2 - L0, degree))
        b.append(delta + s * eps)            # known offset -> data side
        w_adm.append(weight)
    A, b = np.array(A), np.array(b)
    w_adm = np.array(w_adm, dtype=float)

    w_rob = np.ones(len(edges))
    coeffs = _solveShapeOnce(A, b, w_adm * w_rob)
    for _round in range(IRLS_MAX_ITERS):
        r = b - A @ coeffs                   # per-edge disagreement
        w_new = _tukeyWeights(r, _irlsScale(r, w_adm), tukey_c)
        if np.count_nonzero(w_new) <= degree:    # consensus collapse:
            w_rob = np.ones(len(edges))          # abandon the reweight
            coeffs = _solveShapeOnce(A, b, w_adm)
            break
        w_rob = w_new
        new_coeffs = _solveShapeOnce(A, b, w_adm * w_rob)
        if np.max(np.abs(new_coeffs - coeffs)) < 1e-12:
            coeffs = new_coeffs
            break                            # converged early
        coeffs = new_coeffs

    downweighted = [(math.exp(e[0]), math.exp(e[1]),
                     e[2] / (e[1] - e[0]), int(e[4]), float(wr))
                    for e, wr in zip(edges, w_rob) if wr < ROBUST_AUDIT_W]
    return coeffs, downweighted


# _weightedPercentile
# Purpose:   A percentile that respects support: each value counts as
#            many times as the pairs behind it, without materializing
#            the repeats.
# Arguments: values, weights — parallel lists; q — 0..100.
# Output:    the weighted q-th percentile as float.
def _weightedPercentile(values, weights, q):
    order = np.argsort(values)
    v = np.asarray(values)[order]
    csum = np.cumsum(np.asarray(weights, dtype=float)[order])
    return float(v[np.searchsorted(csum, (q / 100.0) * csum[-1])])


# _polyEval / _polyDeriv
# Purpose:   g and dg/dx for the coefficient form (a1 first, no constant).
# Arguments: coeffs — the fitted a's; x — log d - log 5000.
# Output:    the value / the derivative, as floats.
def _polyEval(coeffs, x):
    return sum(c * x ** (j + 1) for j, c in enumerate(coeffs))


def _polyDeriv(coeffs, x):
    return sum((j + 1) * c * x ** j for j, c in enumerate(coeffs))


# _sampleClamped
# Purpose:   THE POISON-POINT FIX: sample the polynomial only where the
#            data testified — inside the weighted [p5, p95] span of edge
#            endpoints — and fill every knot beyond it by LINEAR extension
#            at the boundary slope. A cubic fit on [800m, 3200m] says
#            nothing trustworthy about 5000m, yet the anchor frame and
#            every conversion reference g(log 5000): unclamped, that one
#            tail evaluation poisoned every row of a short-arc pool
#            (measured: a 3000m "conversion" SLOWER than the 5K it came
#            from). Clamping bakes the extrapolation POLICY (constant
#            local exponent past the data) into the artifact itself, and
#            makes the health gate FAIR: linear tails inherit the boundary
#            slope, so the gate now asks exactly "is the curve physical
#            over its data, and is its boundary slope physical".
# Arguments: coeffs, knots — as fitted; lo, hi — the data span in log-d.
# Output:    list of sampled g values, one per knot.
def _sampleClamped(coeffs, knots, lo, hi):
    L0 = math.log(TARGET_DISTANCE_METERS)
    g_lo, s_lo = _polyEval(coeffs, lo - L0), _polyDeriv(coeffs, lo - L0)
    g_hi, s_hi = _polyEval(coeffs, hi - L0), _polyDeriv(coeffs, hi - L0)
    values = []
    for k in knots:
        if k < lo:
            values.append(g_lo + s_lo * (k - lo))    # linear, low side
        elif k > hi:
            values.append(g_hi + s_hi * (k - hi))    # linear, high side
        else:
            values.append(_polyEval(coeffs, k - L0)) # the cubic, in-data
    return [float(v) for v in values]


# _fitOnePotential
# Purpose:   One pool end to end: pairs -> tagged edges -> the eps rung
#            this pool has EARNED (stage 1) -> shape solve with eps
#            fixed (stage 2) -> support-clamped knot sampling
#            (coefficients, span, and the eps verdict kept as the audit
#            trail).
#            The rung logic, in caller-priority order:
#              eps_fixed given         -> rung 2 (borrowed)
#              matched contrasts >= floor -> rung 1 (own): weighted
#                                         median over the contrasts
#              otherwise               -> rung 3 (none): eps=0, the
#                                         exact legacy fit, loudly
#            EVERY rung then runs the SAME shape solve — the two stages
#            share no unknowns, which is v2's whole point.
# Arguments: pairs — one pool's pair list;
#            eps_fixed — None (self-determine) or a known eps to impose
#            (the level borrow — fitAllPotentials' second pass).
# Output:    {"knots","values","n_edges","degree","coeffs","span",
#             "eps","eps_source","n_matched","n_edges_sf","n_edges_lf"},
#            or None when the pool is too thin (caller: use global).
#            n_edges counts DISTINCT TRANSITIONS (direction ignored) so
#            its meaning and the degree/floor thresholds match the
#            pre-eps fitter.
def _fitOnePotential(pairs, eps_fixed=None, tukey_c=None, degree_cap=None):
    edges = _aggregatePairEdges(pairs)
    n_trans = _distinctTransitions(edges)
    if n_trans < MIN_EDGES_FOR_POOL:
        return None
    degree = _polyDegree(n_trans, cap=degree_cap)
    n_sf, n_lf = _epsSides(edges)
    contrasts = _matchedContrasts(edges)             # stage 1 evidence
    if eps_fixed is not None:                        # rung 2: borrowed
        eps, eps_source = float(eps_fixed), "level"
    elif len(contrasts) >= MIN_EPS_MATCHED_TRANSITIONS:   # rung 1: own
        eps, eps_source = _epsFromContrasts(contrasts), "own"
    else:                                            # rung 3: undebiased
        eps, eps_source = 0.0, "none"
    coeffs, downweighted = _solveShapeRobust(edges, degree, eps,
                                              tukey_c)          # stage 2
    # the span the data actually covers, pair-weighted, both endpoints
    pts = [e[0] for e in edges] + [e[1] for e in edges]
    wts = [e[4] for e in edges] + [e[4] for e in edges]
    lo, hi = (_weightedPercentile(pts, wts, 5),
              _weightedPercentile(pts, wts, 95))
    knots = _knotGrid(edges)
    return {"knots": [float(k) for k in knots],
            "values": _sampleClamped(coeffs, knots, lo, hi),
            "n_edges": n_trans, "degree": degree,
            "coeffs": [float(c) for c in coeffs],
            "span": (float(math.exp(lo)), float(math.exp(hi))),
            "eps": float(eps), "eps_source": eps_source,
            "n_matched": len(contrasts),
            "n_edges_sf": n_sf, "n_edges_lf": n_lf,
            "robust_down": downweighted,
            "n_robust_down": len(downweighted)}


# _curveHealth
# Purpose:   The garbage detector this failure class demanded: the local
#            exponent scanned across the curve's interior. A physical
#            distance exponent lives in roughly [0.85, 1.30]; a curve
#            leaving that band is broken, and the gates below make sure
#            no pickle carrying one can ever ship.
# Arguments: entry — a fitted {"knots","values"}.
# Output:    (min_exp, max_exp) over the interior knot midpoints.
def _curveHealth(entry):
    k = entry["knots"]
    exps = [_localExponent(entry, math.exp((k[i] + k[i + 1]) / 2))
            for i in range(1, len(k) - 2)]
    return min(exps), max(exps)


EXP_SANE = (0.85, 1.30)      # the physical band for a distance exponent


# _healthNote
# Purpose:   One formatted health verdict, used by every fit report.
# Arguments: entry — a fitted curve.
# Output:    the string to print after the pool's fit line.
def _healthNote(entry):
    tag = "" if _isHealthy(entry) else \
          "  <<< UNPHYSICAL — do not trust this curve"
    lo, hi = _curveHealth(entry)
    return f"local exp range [{lo:.3f}, {hi:.3f}]{tag}"


# _isHealthy
# Purpose:   The health verdict as a single predicate — the one place
#            the EXP_SANE comparison lives (the gate, the note, and the
#            stability check all ask the same question).
# Arguments: entry — a fitted curve.
# Output:    True when the whole interior sits inside EXP_SANE.
def _isHealthy(entry):
    lo, hi = _curveHealth(entry)
    return EXP_SANE[0] <= lo and hi <= EXP_SANE[1]


# _stabilityGrid
# Purpose:   The probe distances for the stability comparison: log-
#            spaced INSIDE the entry's own support (outside it, both
#            fits run the linear-extension policy — a choice, not data).
# Arguments: entry — the baseline curve (its span rules).
# Output:    list of 7 probe distances in meters.
def _stabilityGrid(entry):
    lo, hi = entry["span"]
    return [math.exp(x)
            for x in np.linspace(math.log(lo), math.log(hi), 7)]


# _stabilityShift
# Purpose:   The measurement behind the stability gate: refit the pool
#            at each probe Tukey constant — eps PINNED to the baseline's
#            (stage 1 never touches Tukey, so pinning isolates pure
#            shape/basin sensitivity) — and read the worst local-exp
#            shift plus any health-verdict flip. A flip is the loudest
#            possible instability: the same evidence, judged physical or
#            unphysical depending on a constant.
# Arguments: pairs — the pool's pairs; entry — the shipped-knob fit.
# Output:    (max_shift, verdict_flipped).
def _stabilityShift(pairs, entry):
    grid = _stabilityGrid(entry)
    base_ok = _isHealthy(entry)
    worst, flipped = 0.0, False
    for c in STABILITY_TUKEY_PROBES:
        # The probe must refit the SAME MODEL, only the knob may move —
        # so the entry's degree is pinned (like its eps). Before the
        # demotion ladder this held by accident (degree was a pure
        # function of the pair count, identical pairs -> identical
        # degree); a DEMOTED entry breaks that accident — an unpinned
        # probe would refit at the natural degree and the "shift" would
        # measure cubic-vs-quadratic disagreement, not knob sensitivity,
        # auto-failing every demotion. Pinning makes the invariant
        # explicit instead of accidental.
        alt = _fitOnePotential(pairs, eps_fixed=entry["eps"], tukey_c=c,
                               degree_cap=entry["degree"])
        worst = max(worst,
                    max(abs(_localExponent(alt, d)
                            - _localExponent(entry, d)) for d in grid))
        flipped = flipped or (_isHealthy(alt) != base_ok)
    return worst, flipped


# _applyStabilityGate
# Purpose:   The gate: a curve is SAVED only if its verdict and shape
#            are knob-independent. Runs AFTER the health gate (no point
#            probing a curve already dropped) and prints its reading
#            either way — "holds" with the measured shift, or the loud
#            drop. The consequence of a drop is the fallback chain
#            doing its job: the pool's rows ride the sport global,
#            where the pool's own evidence still votes but as a weight-
#            minority that cannot preside over the consensus.
# Arguments: label — for the report; pairs — the pool's pairs;
#            entry — the health-approved fit, or None (passed through).
# Output:    the entry, or None (stability-gated).
def _applyStabilityGate(label, pairs, entry):
    if entry is None:
        return None
    shift, flipped = _stabilityShift(pairs, entry)
    if shift > STABILITY_TOL or flipped:
        why = "verdict FLIPS" if flipped else f"shift {shift:.4f}"
        # Verdict only — no disposition claim. The demotion ladder may
        # still rescue a pool at lower degree, so "falls to its sport
        # global" is no longer this gate's to announce; the ladder (or
        # its exhaustion line) prints the true final disposition.
        print(f"    STABILITY-GATED: {label} — {why} across Tukey "
              f"{STABILITY_TUKEY_PROBES}")
        return None
    print(f"    stability: shift {shift:.4f} across Tukey "
          f"{STABILITY_TUKEY_PROBES} — holds")
    return entry


# _refitEpsArg
# Purpose:   The eps argument a demotion REFIT should pass — preserving
#            each rung's provenance. Stage 1 never touches the
#            polynomial (contrasts are degree-independent), so an "own"
#            pool re-derives the bit-identical eps naturally and a
#            "none" pool stays at 0.0 — passing None keeps both the
#            value AND the eps_source label truthful. Only a rung-2
#            ("level") pool must PIN its borrowed value: re-deriving
#            would silently drop the borrow it was granted in pass 2.
# Arguments: entry — the certification-failed fit being demoted.
# Output:    entry["eps"] for a borrowed pool, else None (self-derive).
def _refitEpsArg(entry):
    return entry["eps"] if entry["eps_source"] == "level" else None


# _certifyEntry
# Purpose:   One certification rung, as a single named step: the health
#            gate (via _gatePrint, which also prints the fit line) then
#            the stability gate. Every candidate curve — first fit or
#            demotion — faces the SAME two gates in the same order;
#            this helper is the one place that sequence lives.
# Arguments: label — for the report; pairs — the pool's pairs;
#            entry — a fitted entry or None (passed through).
# Output:    the entry if both gates pass, else None.
def _certifyEntry(label, pairs, entry):
    return _applyStabilityGate(label, pairs, _gatePrint(label, entry))


# _demoteUntilCertified
# Purpose:   The demotion ladder: a pool that failed certification at
#            its count-earned degree is offered strictly LESS freedom —
#            degree-1 steps down to a straight line — and each rung must
#            pass the same health + stability gates to ship. This is
#            the count rule's missing half: count grants freedom, but
#            only the gates can CERTIFY it, and a pool whose transitions
#            sit in a span too narrow to condition a cubic (college_f's
#            failure) may still testify honestly to a quadratic or a
#            single exponent. NOT knob-tuning: the gates never move; the
#            MODEL gets more rigid, and "rigidity is structural — a
#            cubic cannot zigzag" applies one degree harder at each
#            rung. Only when every rung fails does the pool fall to its
#            sport global — the old behavior, now a last resort instead
#            of the only resort.
# Arguments: label — for the report; pairs — the pool's pairs;
#            entry — the failed full-freedom fit (NOT None; caller
#            guards) whose degree sets the ladder's top.
# Output:    the first certified demoted entry, or None (exhausted).
def _demoteUntilCertified(label, pairs, entry):
    for cap in range(entry["degree"] - 1, 0, -1):
        print(f"    DEMOTION: refitting {label} at degree {cap}")
        demoted = _fitOnePotential(pairs, eps_fixed=_refitEpsArg(entry),
                                   degree_cap=cap)
        certified = _certifyEntry(label, pairs, demoted)
        if certified is not None:
            return certified
    print(f"    no certifiable degree: {label} falls to its sport "
          f"global")
    return None


# _epsNote
# Purpose:   One formatted eps verdict for the fit lines: the offset in
#            percent, which rung produced it, and the side counts
#            behind rung 1's grant or denial.
# Arguments: fitted — a fitted entry.
# Output:    the string to print inside the pool's fit line.
def _epsNote(fitted):
    return (f"eps {100 * fitted['eps']:+.2f}% ({fitted['eps_source']}, "
            f"matched {fitted['n_matched']}, "
            f"sf/lf {fitted['n_edges_sf']}/{fitted['n_edges_lf']})")


# _gatePrint
# Purpose:   The print+gate half of a labeled fit: report the fit line
#            (transitions, degree, eps verdict, health) and return the
#            entry ONLY if it's physical. The single place the gate rule
#            lives for named curves.
# Arguments: label — for the report; fitted — entry or None (too thin).
# Output:    the entry, or None (thin OR gated).
def _gatePrint(label, fitted):
    if fitted is None:
        print(f"  {label}: too few edges.")
        return None
    print(f"  {label}: {fitted['n_edges']:,} transitions, degree "
          f"{fitted['degree']}, {_epsNote(fitted)}.  {_healthNote(fitted)}")
    if fitted.get("n_robust_down"):       # the reweight fired: count it
        print(f"    robust solve silenced {fitted['n_robust_down']} "
              f"cells (weight < {ROBUST_AUDIT_W})")
    if not _isHealthy(fitted):
        print(f"    GATED: {label} not saved.")
        return None
    return fitted


# _fitGated
# Purpose:   Fit + print + gate in one call, for curves with no second
#            pass (the globals). Pools go through fitAllPotentials' own
#            two-pass flow so rung 2 can intervene between fit and gate.
# Arguments: label — for the report; pairs — the pair list.
# Output:    the fitted entry, or None (thin OR gated).
def _fitGated(label, pairs):
    if len(pairs) < MIN_PAIRS_FOR_POOL_SPLINE:
        print(f"  {label}: {len(pairs):,} pairs — below threshold.")
        return None
    return _gatePrint(label, _fitOnePotential(pairs))


# _levelOfPool
# Purpose:   The competitive level a pool name encodes — the borrow
#            neighborhood for rung 2. Pool names are level_gender
#            ("college_f", "ms_unknown_gender"), so the level is the
#            piece before the first underscore.
# Arguments: pool — the pool name string.
# Output:    "college" | "hs" | "ms" (or whatever prefix appears).
def _levelOfPool(pool):
    return pool.split("_", 1)[0]


# _levelEpsTable
# Purpose:   Rung 2's lookup: per (sport, level), the pair-weighted mean
#            of the eps values its pools measured for THEMSELVES (rung 1
#            only — a borrowed or zero eps is not evidence and must not
#            circulate back in as if it were).
#            Weighted by pair count: college_f|XC's 131K-pair eps should
#            out-vote a sibling's 2K-pair eps.
# Arguments: first_pass — {(sport, pool): (entry_or_None, n_pairs)}.
# Output:    {(sport, level): eps}.
def _levelEpsTable(first_pass):
    acc = {}                     # (sport, level) -> [(eps, n_pairs), ...]
    for (sport, pool), (entry, n_pairs) in first_pass.items():
        if entry is None or entry["eps_source"] != "own":
            continue
        acc.setdefault((sport, _levelOfPool(pool)), []) \
           .append((entry["eps"], n_pairs))
    return {key: sum(e * n for e, n in votes) / sum(n for _e, n in votes)
            for key, votes in acc.items()}


# _printLevelEps
# Purpose:   The eps findings table — the number the direction census
#            predicted (~0.3% TF, ~1% HS/college XC, ~2% MS): read it
#            against those predictions before trusting the refit.
# Arguments: level_eps — _levelEpsTable's output.
def _printLevelEps(level_eps):
    if not level_eps:
        print("  (no level eps identified — every pool one-sided?)")
        return
    print("  level eps table (calendar inflation of the earlier race):")
    for (sport, level), eps in sorted(level_eps.items()):
        print(f"    {level}|{sport}: {100 * eps:+.2f}%")


# fitAllPotentials
# Purpose:   The whole artifact, PER SPORT (the two-laws verdict stands),
#            now fit in TWO PASSES so the eps hierarchy can act:
#              pass 1 — every pool fits itself; pools with both time-
#                       order sides solve their OWN eps (rung 1);
#              between — rung-1 verdicts aggregate into the level table;
#              pass 2 — pools that could NOT self-identify are REFIT
#                       with their level's borrowed eps (rung 2), else
#                       stand undebiased (rung 3); THEN the health gate
#                       runs, so a pool whose unphysical shape was the
#                       calendar leak itself gets its rescue attempt
#                       BEFORE being dropped.
#            Globals fit once with own eps (their huge two-sided mass
#            always clears the guard; their single eps is the mixture
#            average across levels — acceptable for what is already a
#            last-resort fallback, and labeled by _epsNote like all).
#            Keys unchanged: "pool|SPORT", "global|SPORT", "global".
#            Every named curve passes the health gate or is dropped,
#            loudly. The consumer walks most-specific-first.
# Arguments: xc_by_pool, tf_by_pool — {pool: [pairs]} per sport;
#            report_residuals — True prints the STEP 4a instrument per
#            pool (gated pools included — the pre-gate entry is what the
#            instrument diagnoses).
# Output:    the kind-tagged artifact dict the consumer dispatches on.
def fitAllPotentials(xc_by_pool, tf_by_pool, report_residuals=False):
    all_xc = [p for ps in xc_by_pool.values() for p in ps]
    all_tf = [p for ps in tf_by_pool.values() for p in ps]
    print(f"Fitting potentials: {len(all_xc):,} XC / {len(all_tf):,} TF "
          f"pairs...")
    art = {"kind": "distance_potential",
           "target": TARGET_DISTANCE_METERS,
           "global": _fitOnePotential(all_xc + all_tf),
           "global_by_sport": {}, "pools": {}}
    assert art["global"] is not None, "combined global must always fit"
    print(f"  global (combined): {_epsNote(art['global'])}.  "
          f"{_healthNote(art['global'])}")

    # PASS 1 — every pool fits itself (rung 1 where the guard clears).
    first = {}
    for sport, by_pool in (("XC", xc_by_pool), ("TF", tf_by_pool)):
        for pool, pairs in sorted(by_pool.items()):
            if len(pairs) < MIN_PAIRS_FOR_POOL_SPLINE:
                continue                     # reported in pass 2's walk
            first[(sport, pool)] = (_fitOnePotential(pairs), len(pairs))

    level_eps = _levelEpsTable(first)
    _printLevelEps(level_eps)

    # PASS 2 — borrow where needed, then gate and store, in the same
    # report order as before (global|sport first, then its pools).
    for sport, by_pool in (("XC", xc_by_pool), ("TF", tf_by_pool)):
        sport_pairs = [p for ps in by_pool.values() for p in ps]
        g = _applyStabilityGate(f"global|{sport}", sport_pairs,
                                _fitGated(f"global|{sport}", sport_pairs))
        if g is not None:
            art["global_by_sport"][sport] = g
        for pool, pairs in sorted(by_pool.items()):
            if (sport, pool) not in first:
                print(f"  {pool}|{sport}: {len(pairs):,} pairs — below "
                      f"threshold.")
                continue
            entry, _n = first[(sport, pool)]
            level_key = (sport, _levelOfPool(pool))
            if (entry is not None and entry["eps_source"] == "none"
                    and level_key in level_eps):
                entry = _fitOnePotential(pairs,
                                         eps_fixed=level_eps[level_key])
            fitted = _certifyEntry(f"{pool}|{sport}", pairs, entry)
            # The demotion ladder: POOLS ONLY. A gated pool retries at
            # strictly lower degrees before exiling its rows to the
            # global; a gated GLOBAL is a five-alarm fire the ladder
            # must not paper over (the globals above stay ladder-free).
            # entry None (too thin) never ladders — no fit to demote.
            if fitted is None and entry is not None:
                fitted = _demoteUntilCertified(f"{pool}|{sport}", pairs,
                                               entry)
            if report_residuals:
                _reportResiduals(f"{pool}|{sport}", pairs, entry)
            if fitted is not None:
                # ★ THE ANCHOR IS STAMPED, NOT REFITTED. The shape solve
                #   uses only DIFFERENCES -- _buildShapeRow is
                #   x2^k - x1^k -- so every residual is invariant to
                #   where g is pinned to zero. Inference is a difference
                #   too: exp(g(target) - g(d)). So the fitted `values`
                #   need no change at all; the anchor only decides which
                #   g is subtracted, and that is a property of the entry
                #   rather than of the fit.
                fitted["target"] = _targetFor(pool, sport)
                art["pools"][f"{pool}|{sport}"] = fitted
    # ⚠ THE WHOLE MAP, NOT JUST THE ENTRIES THAT GOT A CURVE. A pool below
    #   MIN_PAIRS_FOR_POOL_SPLINE has no entry of its own and falls back to
    #   "global" -- and a global entry carries no anchor, so the consumer
    #   silently reverted to the 5000 default. Measured: every elem_* pool
    #   is absent from art["pools"], so 100% of elementary rows normalised
    #   at 5000 instead of 2414.
    #
    # ★ THE ANCHOR IS A PROPERTY OF THE POOL, NOT OF THE CURVE THAT HAPPENED
    #   TO ANSWER FOR IT. Writing the map lets the consumer resolve it from
    #   the pool name, whichever entry the fallback chain lands on.
    art["pool_targets"] = dict(POOL_TARGET_METERS)
    return art
# ------------------------------------------------------------------ #
# STEP 4 — EVALUATION + THE XC-vs-TF COMPARISON
# ------------------------------------------------------------------ #

# _evalPotential                       !! KEEP IN SYNC — normalize_distance
# Purpose:   g at any log-distance.    carries a byte-equivalent mirror
#            Inside the knots: linear  (_evalDistancePotential); the two
#            interpolation. BEYOND     files cannot import each other
#            them: the BOUNDARY        (the fitter imports the consumer),
#            SEGMENT'S SLOPE extended  so this is a deliberate marked
#            — constant local exponent mirror, verify/merge-style. !!
#            past the data, never an oscillating tail.
# Arguments: entry — {"knots","values"}; ld — a log-distance.
# Output:    g(ld) as float.
def _evalPotential(entry, ld):
    k, v = entry["knots"], entry["values"]
    if ld <= k[0]:
        slope = (v[1] - v[0]) / (k[1] - k[0])
        return v[0] + slope * (ld - k[0])
    if ld >= k[-1]:
        slope = (v[-1] - v[-2]) / (k[-1] - k[-2])
        return v[-1] + slope * (ld - k[-1])
    return float(np.interp(ld, k, v))            # inside: plain linear


# _predictFromPotential
# Purpose:   T at dist given a known time at the TARGET distance:
#            T_dist = T_target * exp(g(ld_dist) - g(ld_target)).
#            (The anchor puts g(target) ~ 0; the difference form doesn't
#            depend on that and stays honest if the anchor ever moves.)
# Arguments: entry; dist — meters; reference_time — seconds at TARGET.
# Output:    predicted seconds at dist.
def _predictFromPotential(entry, dist, reference_time):
    g_d = _evalPotential(entry, math.log(dist))
    g_t = _evalPotential(entry, math.log(TARGET_DISTANCE_METERS))
    return reference_time * math.exp(g_d - g_t)


# _localExponent
# Purpose:   The physically meaningful readout: dg/d(log d) at a distance
#            — the power-law exponent AS IT VARIES along the curve. This
#            is the number the whole fit exists to measure; 1.06 was its
#            placeholder.
# Arguments: entry; dist — meters; h — the centered-difference half-step.
# Output:    the local exponent as float.
def _localExponent(entry, dist, h=0.02):
    ld = math.log(dist)
    return (_evalPotential(entry, ld + h)
            - _evalPotential(entry, ld - h)) / (2 * h)

# ------------------------------------------------------------------ #
# STEP 4a — THE RESIDUAL INSTRUMENT  (read-only; measures the fit's
#            remaining disagreement with its own debiased evidence)
# ------------------------------------------------------------------ #
# Motivation (2026-07-05, post-eps): with eps clean, saved XC curves
# still sit ~0.08-0.09 in local-exp BELOW their own debiased pairwise
# midpoints at canonical transitions (hs_m 3200->5000: curve 1.01 vs
# evidence 1.10 — a ~4% conversion-error class). Hypothesis: near-5K
# label-fuzz transitions (4700->5000 etc.) demand the potential be flat
# AT 5K while canonical transitions demand the integral across the same
# region be steep — a kink a cubic cannot hold, so it compromises
# everywhere. This instrument does NOT test the hypothesis by name: the
# buckets are SPAN-ONLY (neutral), and the hypothesis must predict the
# pattern (short-span residuals negative, long-span positive, TF ~0).
# Curve deltas are read via _evalPotential ON THE ENTRY — residuals are
# measured against what inference actually serves, clamps and all.

# _residualRows
# Purpose:   Per matched transition: the debiased evidence exponent, the
#            fitted curve's exponent over the same span, their gap, and
#            the TENSION (weight x |delta misfit|) — the force this
#            transition exerts on the solve, which is the honest sort
#            key for "worst offenders".
# Arguments: entry — a fitted curve dict; mids — _matchedMidpoints output.
# Output:    list of row dicts {d1, d2, span, n, w, ev_exp, curve_exp,
#            res_exp, tension}.
def _residualRows(entry, mids):
    rows = []
    for ld1, ld2, fair, w, n in mids:
        span = ld2 - ld1
        curve = _evalPotential(entry, ld2) - _evalPotential(entry, ld1)
        rows.append({"d1": math.exp(ld1), "d2": math.exp(ld2),
                     "span": span, "n": n, "w": w,
                     "ev_exp": fair / span, "curve_exp": curve / span,
                     "res_exp": (fair - curve) / span,
                     "tension": w * abs(fair - curve)})
    return rows


# _bucketLine
# Purpose:   One span-bucket's summary: how many transitions, and the
#            weighted median + mean of the exponent residual. Median and
#            mean together on purpose — agreement says the bucket is
#            uniform; a gap says a few heavy voices drive it.
# Arguments: name — the printed tag; rows — the bucket's row dicts.
# Output:    the formatted line (caller prints).
def _bucketLine(name, rows):
    if not rows:
        return f"      {name}: no matched transitions"
    wmed = _weightedPercentile([r["res_exp"] for r in rows],
                               [r["w"] for r in rows], 50)
    wmean = (sum(r["res_exp"] * r["w"] for r in rows)
             / sum(r["w"] for r in rows))
    return (f"      {name}: {len(rows)} transitions, res exp "
            f"wmedian {wmed:+.3f}, wmean {wmean:+.3f}")


# _reportResiduals
# Purpose:   The instrument's printout for one pool: the two neutral
#            span buckets, then the RESIDUAL_TOP_N worst-tension
#            transitions with evidence-vs-curve exponents side by side.
#            Called for gated pools too — a gated curve's residual
#            pattern is exactly the diagnostic a gate line can't give.
# Arguments: label — pool tag; pairs — the pool's pairs (edges are
#            re-aggregated here: cheap, and keeps the fit path's
#            signature untouched); entry — the fitted curve, or None
#            (silently skipped: nothing to compare against).
def _reportResiduals(label, pairs, entry):
    if entry is None:
        return
    rows = _residualRows(entry, _matchedMidpoints(_aggregatePairEdges(pairs)))
    down = entry.get("robust_down") or []
    if down:
        # the reweight's audit, straight off the entry: the biggest
        # silenced cells, named — exclusion stays VISIBLE (top 4 by n)
        worst = sorted(down, key=lambda r: -r[3])[:4]
        cells = ", ".join(f"{d1:.0f}->{d2:.0f}m (exp {e:+.2f}, n {n:,}, "
                          f"w {w:.2f})" for d1, d2, e, n, w in worst)
        print(f"    robust solve silenced {len(down)}: {cells}")
    if not rows:
        print(f"    residuals — {label}: no matched transitions")
        return
    print(f"    residuals — {label} (debiased evidence vs fitted curve):")
    print(_bucketLine(f"short-span (<{SHORT_SPAN_LOG:.0%})",
                      [r for r in rows if r["span"] < SHORT_SPAN_LOG]))
    print(_bucketLine(f"long-span (>={SHORT_SPAN_LOG:.0%})",
                      [r for r in rows if r["span"] >= SHORT_SPAN_LOG]))
    print(f"      {'transition':>16} {'n':>9} {'ev exp':>7}"
          f" {'curve':>7} {'resid':>7}")
    for r in sorted(rows, key=lambda r: -r["tension"])[:RESIDUAL_TOP_N]:
        label_t = f"{r['d1']:.0f}->{r['d2']:.0f}m"
        print(f"      {label_t:>16} {r['n']:>9,} {r['ev_exp']:>7.3f}"
              f" {r['curve_exp']:>7.3f} {r['res_exp']:>+7.3f}")


# _distanceSupport
# Purpose:   The distance range a sport's pairs actually COVER — outside
#            it a curve extrapolates by policy, and a cross-sport 'diff'
#            there is policy about policy, not evidence.
# Arguments: pairs — one sport-pool's pair list.
# Output:    (lo, hi) in meters (5th/95th pct of all pair endpoints).
def _distanceSupport(pairs):
    ds = np.array([p["distance1"] for p in pairs]
                  + [p["distance2"] for p in pairs])
    return float(np.percentile(ds, 5)), float(np.percentile(ds, 95))


# compareXCvsTF
# Purpose: Fit separate XC-only and TF-only potentials per pool and
#          compare inside the OVERLAP of their supports. DIAGNOSTIC only —
#          the combined curves are what get saved. >2% in-support
#          divergence => the sports' scaling laws differ; revisit.
# Arguments: xc_by_pool, tf_by_pool — {pool: [pairs]}.
def compareXCvsTF(xc_by_pool, tf_by_pool):
    print("\n" + "=" * 70)
    print("XC vs TF POTENTIAL COMPARISON")
    print("=" * 70)
    print("Threshold: >2% IN-SUPPORT divergence suggests separate curves.\n")
    for pool in sorted(set(xc_by_pool) | set(tf_by_pool)):
        xc_pairs = xc_by_pool.get(pool, [])
        tf_pairs = tf_by_pool.get(pool, [])
        if min(len(xc_pairs), len(tf_pairs)) < MIN_PAIRS_FOR_POOL_SPLINE:
            print(f"  {pool}: one side thin "
                  f"(XC {len(xc_pairs):,} / TF {len(tf_pairs):,}) — skipped.")
            continue
        xc_pot, tf_pot = _fitOnePotential(xc_pairs), _fitOnePotential(tf_pairs)
        if xc_pot is None or tf_pot is None:
            print(f"  {pool}: too few edges on one side — skipped.")
            continue
        print(f"  {pool} — XC: {len(xc_pairs):,}, TF: {len(tf_pairs):,}")
        print(f"    XC {_healthNote(xc_pot)}   TF {_healthNote(tf_pot)}")
        # the debias verdicts: this table is now built from eps-corrected
        # curves wherever the pool self-identified — read the two-laws
        # question against THESE lines, not the 7/04 undebiased run
        print(f"    XC {_epsNote(xc_pot)}   TF {_epsNote(tf_pot)}")
        _printComparisonTable(xc_pot, tf_pot,
                              _distanceSupport(xc_pairs),
                              _distanceSupport(tf_pairs))


# _printComparisonTable
# Purpose: The side-by-side conversions from a 1000s 5K, flagged only
#          inside BOTH supports; outside, the row shows the extrapolation
#          POLICY at work and says so instead of crying wolf.
# Arguments: xc_pot, tf_pot — fitted entries; xc_sup, tf_sup — (lo, hi).
def _printComparisonTable(xc_pot, tf_pot, xc_sup, tf_sup):
    print(f"    {'Distance':>10} {'XC (s)':>10} {'TF (s)':>10}"
          f" {'Diff':>8} {'Diff%':>8}")
    for dist in (800, 1500, 1600, 3000, 3200, 8000, 10000):
        xc_time = _predictFromPotential(xc_pot, dist, 1000.0)
        tf_time = _predictFromPotential(tf_pot, dist, 1000.0)
        diff = xc_time - tf_time
        diff_pct = (diff / tf_time) * 100 if tf_time > 0 else 0.0
        in_xc = xc_sup[0] <= dist <= xc_sup[1]
        in_tf = tf_sup[0] <= dist <= tf_sup[1]
        if not (in_xc and in_tf):
            missing = "XC" if not in_xc else "TF"
            print(f"    {dist:>10.0f} {xc_time:>10.1f} {tf_time:>10.1f}"
                  f" {diff:>+8.1f} {diff_pct:>+7.1f}%"
                  f"   (outside {missing} data — extrapolation)")
            continue
        flag = " <<<" if abs(diff_pct) > 2.0 else ""
        print(f"    {dist:>10.0f} {xc_time:>10.1f} {tf_time:>10.1f}"
              f" {diff:>+8.1f} {diff_pct:>+7.1f}%{flag}")
    print()

# ------------------------------------------------------------------ #
# STEP 5 — SAVE + SANITY CHECK
# ------------------------------------------------------------------ #

# savePotentials
# Purpose: Pickle the kind-tagged artifact to the EXACT path the consumer
#          loads (dispatch on data: legacy pool-dict pickles have no
#          "kind" and route to the old spline path unchanged).
# Arguments: art — fitAllPotentials' output.
def savePotentials(art):
    lo, hi = _curveHealth(art["global"])
    if lo < EXP_SANE[0] or hi > EXP_SANE[1]:
        print(f"\nSAVE REFUSED: the GLOBAL curve is unphysical "
              f"(local exp [{lo:.3f}, {hi:.3f}]). No pickle written — a "
              f"saved artifact is a promise, and this one would lie.")
        return False
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "wb") as f:
        pickle.dump(art, f)
    print(f"\nDistance potentials ({len(art['pools'])} pools + global) "
          f"saved to {OUTPUT_FILE}")
    return True


# sanityCheck / _spotCheck
# Purpose: The instrument panel: convert a 1000s 5K along the GLOBAL
#          curve vs the old 1.06 exponent, plus the local exponent at
#          each distance — the fit's actual finding, printed plainly.
# Arguments: art — the artifact.
def sanityCheck(art):
    print("\n" + "=" * 60)
    print("POTENTIAL SANITY CHECK")
    print("=" * 60)
    print(f"  Pools fitted:  {sorted(art['pools'])}")
    print("  Global curve:  yes\n")
    _spotCheck(art["global"], reference_time=1000.0)


def _spotCheck(entry, reference_time):
    print(f"  Reference: {reference_time:.0f}s at 5000m\n")
    print(f"  {'Distance':>10} {'Potential (s)':>14} {'1.06 exp (s)':>14}"
          f" {'Diff':>8} {'local exp':>10}")
    for dist in (800, 1500, 1600, 3000, 3200, 5000, 8000, 10000):
        got = _predictFromPotential(entry, dist, reference_time)
        old = reference_time * (dist / TARGET_DISTANCE_METERS) ** 1.06
        print(f"  {dist:>10.0f} {got:>14.1f} {old:>14.1f}"
              f" {got - old:>+8.1f} {_localExponent(entry, dist):>10.3f}")

# ------------------------------------------------------------------ #
# MAIN
# ------------------------------------------------------------------ #

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Fit the per-pool distance splines")
    parser.add_argument("--fresh", action="store_true",
                        help="ignore the pair cache and re-stream the DB")
    parser.add_argument("--residuals", action="store_true",
                        help="print the per-pool residual instrument "
                             "(debiased evidence vs fitted curve)")
    args = parser.parse_args()

    print("=== fit_distance_exponent.py (rewrite) ===\n")

    xc_by_pool, tf_by_pool = loadAllPairs(use_cache=not args.fresh)
    total_xc = sum(len(p) for p in xc_by_pool.values())
    total_tf = sum(len(p) for p in tf_by_pool.values())
    if total_xc + total_tf == 0:
        print("No valid pairs found — read the ledgers above.")
        return

    print(f"XC pools:  {sorted(xc_by_pool)}")
    print(f"TF pools:  {sorted(tf_by_pool)}")
    print(f"Total XC pairs: {total_xc:,}   Total TF pairs: {total_tf:,}\n")

    compareXCvsTF(xc_by_pool, tf_by_pool)

    # PER-SPORT is the saved methodology (measured: XC exp ~0.95-1.05
    # vs TF ~1.06-1.22 — two laws; the old combined fit gated itself).
    print("Fitting per-sport potentials...\n")
    art = fitAllPotentials(xc_by_pool, tf_by_pool,
                           report_residuals=args.residuals)
    savePotentials(art)
    sanityCheck(art)


if __name__ == "__main__":
    main()