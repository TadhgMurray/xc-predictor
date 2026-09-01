# Project: xc-predictor
# Author:  Tadhg Murray
# Subset:  Speed Rating Engine
# File:    normalize_distance.py
# Purpose: Classifies results into competitive pools and normalizes time to a 5k
#          equivalent for comparison across distances. As of this update it also
#          applies the two physical/temporal corrections that follow distance in
#          the chain, so a single call returns a fully comparable value:
#
#              distance  ->  geometry (track length + banking)  ->  era (season)
#
# Update 6/18/2026:
#   normalizeTime() uses per-pool 2D cubic splines fitted by
#   fit_distance_exponent.py instead of the placeholder 1.06 exponent. Splines
#   load ONCE at module level (first import) so millions of calls share one
#   in-memory object. Falls back to DISTANCE_EXPONENT_BY_POOL if absent.
#
# Update (this change): GEOMETRY + ERA wired in.
#   - GEOMETRY: fit_geometry_correction.py's geometry_spline.pkl (length potential
#     + banking surface) replaces the hand-curated BANKED_TRACK_CORRECTIONS dict.
#     Applied ONLY to results that carry track geometry (TF track subset). A result
#     with no track_length/track_type defaults to the REFERENCE track (400m flat),
#     which is the geometry zero — i.e. "no basis to correct" == "leave at the
#     reference", NOT "we believe it was 400m flat". Same math, honest framing.
#   - ERA: fit_era_correction.py's era_curve.pkl (per-season factor) applied
#     whenever a race date/season is available (every result has one).
#   - Both load ONCE at module level with graceful fallback: if a pickle is
#     missing, that correction is a clean no-op and behaviour is exactly as before.
#
#   *** VALIDATE ERA BEFORE TRUSTING IT IN TRAINING ***
#   Geometry self-validates (composition law, checked in its fitter). ERA does NOT
#   — a contaminated era curve injects BIAS into normalized_time that the model
#   will learn as signal. Only enable the era pickle after its cross-check gap
#   (fit_era_correction.py measure pass) looks clean. Train with/without and
#   compare val loss to confirm it helps rather than biases.
#
# NOTE: course_difficulty is a SEPARATE correction applied in the speed-ratings
#   engine (race_time = normalized_time * (1+course_difficulty) * (D/5000)^exp).
#   It is NOT applied here and must not be — geometry/era and course_difficulty
#   are different effects; doing either twice double-corrects.

import os
import re
import math
import pickle

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# Per-pool distance exponents (fallback when the distance spline is absent).
# Formula: T2 = T1 * (D2 / D1) ^ exponent  (T2 = 5k-normalized, D2 = 5000).
# TODO: fit empirically per pool from multi-distance same-season athletes.
# TODO: era adjustment is a SEPARATE multiplier — now implemented (see era wiring
#       below), no longer just a scope note.
DISTANCE_EXPONENT_BY_POOL = {
    "college_m": 1.06,
    "college_f": 1.06,
    "hs_m":      1.06,
    "hs_f":      1.06,
    "ms_m":      1.06,
    "ms_f":      1.06,
    "unknown":   1.06,
    "college_unknown_gender": 1.06,
    "hs_unknown_gender":      1.06,
    "ms_unknown_gender":      1.06,
}

# athletic.net eventShort -> distance in meters. 800m+ only (shorter extrapolates
# badly under the power law). Events not here keep normalized_time NULL.
EVENT_DISTANCES_TF = {
    "800m":     800,
    "1500m":    1500,
    "1600m":    1600,
    "1mile":    1609.34,
    "3000m":    3000,
    "3200m":    3200,
    "2mile":    3218.69,
    "5000m":    5000,
    "10000m":   10000,
    "3000mSC":  3000,
    "2000mSC":  2000,
    "1000m":    1000,
}

# NOTE: the hand-curated BANKED_TRACK_CORRECTIONS facility->multiplier dict is
# RETIRED. Banking is now the data-fit geometry banking SURFACE (keyed by
# track_length + event_distance), loaded from geometry_spline.pkl below. Keyed by
# physical geometry, not facility name — so callers correct by length/distance,
# not by looking a venue up by name.

DEFAULT_EXPONENT = 1.06
TARGET_DISTANCE_METERS = 5000
FLAT_COURSE_DIFFICULTY = 0.0

# The geometry REFERENCE track: a standard flat 400m outdoor oval. This is the
# zero of the geometry potential (g(400) = 0, banking factor = 1). A result
# lacking track geometry is treated as this reference -> no geometry correction.
REFERENCE_TRACK_LENGTH = 400.0

# Pickle paths (siblings of the distance spline, all under engine/data/).
_DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "engine", "data")
_SPLINE_FILE   = os.path.join(_DATA_DIR, "distance_spline.pkl")
_SCHOOL_FILE   = os.path.join(_DATA_DIR, "school_levels.pkl")
_GEOMETRY_FILE = os.path.join(_DATA_DIR, "geometry_spline.pkl")
_ERA_FILE      = os.path.join(_DATA_DIR, "era_curve.pkl")


# ------------------------------------------------------------------ #
# MODULE-LEVEL ARTIFACT LOADING  (each loads ONCE, graceful fallback)
# ------------------------------------------------------------------ #

# _loadPickle
# Purpose:   Load a pickle if present, else return None so the caller can fall
#            back. One helper for all three artifacts — same pattern the distance
#            spline already used, generalised.
# Arguments: path  — the pickle path.
#            label — name for the log line.
# Output:    the unpickled object, or None if the file is absent.
def _loadPickle(path, label):
    if not os.path.exists(path):
        print(f"[normalize_distance] {label} not found at {path}. "
              f"That correction is a no-op until the pickle exists.")
        return None
    with open(path, "rb") as f:
        obj = pickle.load(f)
    print(f"[normalize_distance] {label} loaded from {path}.")
    return obj


# Load all three ONCE at import. Each is either its object or None (-> no-op).
#   _SPLINES   : {pool -> SmoothBivariateSpline} | None   (distance)
#   _GEOMETRY  : {"length": {...}, "banking": {...}} | None
#   _ERA       : one of two generations (dispatch on "kind" — see _applyEra):
#                  legacy: {"curve": {season: log_level}, "reference": int}
#                  banded: {"kind": "era_banded",
#                           "curves": {"SPORT|band|gender": {curve, reference}}}
#                | None
_SPLINES  = _loadPickle(_SPLINE_FILE, "Distance splines")
_GEOMETRY = _loadPickle(_GEOMETRY_FILE, "Geometry spline")
_ERA      = _loadPickle(_ERA_FILE, "Era curve")

# WEATHER: the race-day correction, the LAST link in the chain (after era). One
# artifact PER SPORT (XC and TF have different betas / curves), written by
# fit_weather_correction.py. Each is either its dict or None (-> a clean no-op,
# exactly like a missing era/geometry pickle). Shape:
#   {"reference": {feat: ref}, "betas": {wind/precip/snow: b},
#    "splines": {"apparent_temp": {knots,coef,ref}, "soil": {knots,coef,ref}},
#    "linear_features": [...], "soil_sensitivity": {course: {"s","n","mv"}}}
# Unlike the other corrections weather is PER-RACE (continuous inputs), so it is
# applied OUTSIDE the cached factor -- see _applyWeather / normalizeTime.
_WEATHER = {sp: _loadPickle(os.path.join(_DATA_DIR, f"weather_correction_{sp}.pkl"),
                            f"Weather correction {sp}")
            for sp in ("XC", "TF")}

# School -> level map, built once by scripts/build_school_levels.py from anet's
# REAL grades (the ground truth tfrrs lacks). None -> levelForSchool() is a
# clean no-op and pooling behaves exactly as it did before this change.
_SCHOOL_LEVELS = (_loadPickle(_SCHOOL_FILE, "School levels") or {}).get("levels")

from functools import lru_cache

# _factorKey
# Purpose:   Normalize the cache key so trivially-different inputs share a slot.
#            Floats are ROUNDED (track_length/distance can carry DB noise or
#            Decimals -> without this, 200.0 and 200.00000001 are different keys
#            and the cache never hits). Rounding to 3 dp is far finer than any
#            real distinct track length or event distance, so it merges noise
#            without merging genuinely different values.
# Arguments: the non-time args of normalizeTime.
# Output:    a hashable tuple safe to use as an lru_cache key.
def _factorKey(distance_meters, pool, season, track_length, track_type,
               sport, event_short):
    dm = round(float(distance_meters), 3)
    tl = None if track_length is None else round(float(track_length), 3)
    return (dm, pool, season, tl, track_type, sport, event_short)


# _normalizationFactor
# Purpose:   The CACHED heart of the speedup: compute the pure multiplier that
#            turns a raw time into a normalized one, for a given set of
#            NON-TIME args. Because time_seconds is not an argument, every row
#            sharing an event/pool/track hits the same cached result — the 3
#            spline evaluations run once per DISTINCT combo (a few hundred),
#            not once per row (~109M).
#
#            HOW a "multiplier" is extracted from functions written in terms of
#            time: each step is  out = in * f(...)  with f independent of the
#            time. So feeding the chain a probe time of 1.0 second makes the
#            output EQUAL to the product of all the multipliers — factor =
#            normalize(1.0). One clean trick, no reaching into each step.
# Arguments: distance_meters, pool, season, track_length, track_type, sport,
#            event_short — exactly normalizeTime's non-time args.
# Output:    the combined distance*geometry*era multiplier as a float.
@lru_cache(maxsize=100_000)
def _normalizationFactorCached(distance_meters, pool, season, track_length,
                               track_type, sport, event_short):
    # 1. DISTANCE multiplier (spline or exponent) — evaluated at a probe time
    #    of 1.0, so the returned value IS the multiplier.
    if _SPLINES is not None:
        factor = _normalizeWithSpline(1.0, distance_meters, pool, sport)
    else:
        factor = _normalizeWithExponent(1.0, distance_meters, pool)

    # 2. GEOMETRY — same chain as before, still starting from our running factor.
    factor = _applyGeometry(factor, distance_meters, track_length, track_type)

    # 3. ERA — build the key exactly as the original did, then apply.
    era_key = None
    if sport is not None:
        band = classifyEraBand(sport, event_short, distance_meters)
        if band is not None:
            era_key = (f"{str(sport).strip().upper()}|{band}|"
                       f"{_genderFromPool(pool)}")
    factor = _applyEra(factor, season, era_key)
    return factor


# ------------------------------------------------------------------ #
# POOL CLASSIFICATION
# ------------------------------------------------------------------ #

# --- school -> level lookup (tfrrs's missing grade, recovered from anet) ----
#
# WHY: poolFor never saw the school, and for tfrrs grade is always NULL, so one
# line ("tfrrs -> college") pooled EVERY tfrrs row as college. Measurement says
# ~60% of tfrrs rows are youth. Consequence: youth rows were judged against the
# COLLEGE raw-time floor (720s/5k) rather than hs 780 / ms 840-960 -- so the
# youth floor never fired on a tfrrs row at all.
#
# HOW: anet rows carry a real grade. Both sources name the same schools. So a
# school's level is learned from anet's grades and transferred to its tfrrs rows.
# NO voting, NO inference: a row's level comes from its OWN school, and only when
# that school is unambiguous. Ambiguous/unknown -> None -> today's behaviour.
#
# The key must match build_school_levels.py EXACTLY or every lookup misses.
_SCHOOL_ABBREV = [
    (re.compile(r"\bwis\.?\s*-\s*"),               "wisconsin-"),
    (re.compile(r"\bu\.\s*of\s+"),                 "university of "),
    (re.compile(r"\bu\.\s*$"),                      " university"),
    (re.compile(r"\b(st\.?|state)\s*$"),            " state"),
    (re.compile(r"^\s*(st\.?|saint)\s+"),           "st "),
    (re.compile(r"\b(st\.?|saint)\s+(?=[a-z])"),    "st "),
]
_SCHOOL_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

# Roster statuses, not institutions -- 'unattached' was the single worst
# ambiguous key (42,128 tfrrs rows). HS and college runners both race unattached.
_NON_SCHOOLS = {"unattached", "unattached runner", "unat", "independent",
                "individual", "alumni", "open", "none", "na", "n a", "guest"}


# Countries and pro clubs. NOT roster statuses -- these are real, identifiable
# organisations, and an athlete racing for one is a PROFESSIONAL.
#
# ★ WHY THIS OVERRIDES THE GRADE FIELD. A pro's grade is stale: Eduardo Herrera
#   reads grade 12 while racing for Mexico. Under the old precedence his grade
#   won and he was pooled as a high schooler, dragging hs_m's pool mean and
#   corrupting the difficulty of every course he raced. Pro detection therefore
#   runs BEFORE the grade -- the one place in this chain where the row's own
#   field is not the most trustworthy signal.
#
# ★ TWO MATCH MODES, AND THE SPLIT IS THE WHOLE POINT.
#   The first version matched EXACT strings only, so "saucony" was caught and
#   "Saucony Freedom Track Club" walked straight through. Real team names are
#   brand + qualifier, so brands have to match as SUBSTRINGS.
#
#   But a substring sweeps up real schools if it is short or common. "on" would
#   catch every school with those two letters; bare "brooks" would catch Brooks
#   High School. Those stay EXACT.
#
# THIS IS THE SSOT. panels.py carried its own copy and used it only to hide
# pros from leaderboards, which left them mis-pooled in the engine. panels.py
# should import isProSchool from here and delete _NON_SCHOOL,
# _NON_SCHOOL_FRAGMENTS and _is_non_school.

# EXACT match only. Short, ambiguous, or a real English word -- any of these as
# a substring would catch genuine schools.
_PRO_EXACT = frozenset(s.lower() for s in {
    # --- countries / national teams that appear in distance results ---
    "mexico", "ireland", "belgium", "kenya", "netherlands", "canada", "japan",
    "new zealand", "australia", "great britain", "united states", "usa",
    "united states of america", "spain", "france", "germany", "italy",
    "ethiopia", "uganda", "norway", "sweden", "guatemala", "portugal",
    "uruguay", "poland", "eritrea", "morocco", "south africa", "brazil",
    "colombia", "chile", "denmark", "finland", "switzerland", "austria",
    "czech republic", "hungary", "romania", "turkey", "israel", "china",
    "korea", "south korea", "india", "philippines", "puerto rico", "jamaica",
    "trinidad and tobago", "bahamas", "nigeria", "tanzania", "burundi",
    "rwanda", "algeria", "tunisia", "egypt",
    # --- bare brand names: safe exact, dangerous as substrings ---
    "on", "brooks", "joma", "boss", "team boss", "roots", "empire",
})

# SUBSTRING match. Distinctive enough that a real school will not contain them,
# which is what lets "Saucony Freedom Track Club" and "Team Saucony" both hit.
#
# ⚠ EVERY ADDITION IS A FALSE-POSITIVE RISK. Before adding one, run
#   test_pools.py --review, which prints every school a fragment would newly
#   catch. A fragment that sweeps up one real school is worse than a brand that
#   slips through: the school's athletes get pooled as pros, and pro is a tiny
#   pool with a wildly different mean.
_PRO_FRAGMENTS = (
    # shoe brands -- long enough to be safe anywhere in a string
    "saucony", "adidas", "asics", "reebok", "altra", "hoka", "nike",
    "new balance", "lululemon", "under arm", "under armor", "satisfy",
    # brand + qualifier, for brands too short to match bare
    "puma elite", "on athletics", "on running", "on track club",
    "brooks beasts", "hansons", "hanson's",
    # named pro teams
    "bowerman", "tinman", "naz elite", "dark sky", "zap endurance",
    "atlanta track club", "minnesota distance elite", "oregon track club",
    "union athletics", "swoosh", "very nice track club",
    "district track club", "empire elite", "golden coast track club",
    "roots running", "furman elite", "b.a.a.", "baa high performance",
    "team usa arizona", "team indiana elite", "mission run",
    "aggie running club", "athletics tauranga", "nn running",
    "adizero", "rabbit elite", "hoka one one",
    # generic pro-team suffixes: distinctive in combination
    "distance project", "elite development", "professional track",
)


# isProSchool
# Purpose: is this "school" actually a country or a pro club?
# Arguments: school -- the raw school string from the result row.
# Output: bool.
# Detail:  Lowercased and stripped, then exact set first (cheap) and fragments
#          second. Returns False for blank -- an unknown school is not evidence
#          of anything, so it falls through to the normal precedence rather than
#          being guessed at.
def isProSchool(school):
    if not school:
        return False
    t = str(school).strip().lower()
    if t in _PRO_EXACT:
        return True
    return any(frag in t for frag in _PRO_FRAGMENTS)


# normSchoolKey
# Purpose: canonical school key, so a tfrrs spelling and an anet spelling of the
#          same school collide on purpose ("Wis.-Eau Claire" == "Wisconsin-Eau
#          Claire"). Keeps the trailing "(Tex.)" -- it disambiguates two real
#          institutions and stripping it was measured to merge them.
# Arguments: school — the raw school string from the result row.
# Output: normalized key, or None if this is not a school (roster status/blank).
def normSchoolKey(school):
    if not school:
        return None
    t = str(school).strip().lower()
    m = _SCHOOL_PAREN.search(t)
    suffix = ""
    if m:
        suffix = " " + re.sub(r"[^a-z0-9]+", "", m.group(0))
        t = _SCHOOL_PAREN.sub("", t)
    for rx, repl in _SCHOOL_ABBREV:
        t = rx.sub(repl, t)
    t = re.sub(r"[.\'`]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    key = (re.sub(r"\s+", " ", t) + suffix).strip()
    if not key or key in _NON_SCHOOLS:
        return None
    return key


# levelForSchool
# Purpose: the level ('ms'|'hs'|'college') a school is KNOWN to be, from anet's
#          real grades. Returns None whenever we cannot be sure — an unknown
#          school, a collided name (one name, two institutions), thin evidence,
#          or a missing pickle. None always means "fall back to old behaviour".
# Arguments: school — the raw school string.
# Output: level string, or None.
# ★ THE RACE GRAPH OUTRANKS THE PICKLE.
#
#   school_levels.pkl resolves a name by dictionary lookup, and it is
#   overwhelmingly a high-school dictionary, so an ambiguous short name
#   defaults to hs. Audited against the NCAA/NAIA institution lists it had 128
#   colleges tagged hs or ms -- Cornell, Arkansas, Duke, Alabama, Auburn,
#   Liberty -- 1,642 colleges missing entirely, 'United States' tagged college
#   and 'Unattached' tagged hs.
#
#   level_graph.py resolves the same names by WHO THEY RACE AGAINST, seeded on
#   NCAA/NAIA, NXN/NXR/Foot Locker/state associations, Diamond League, state
#   middle-school championships and Tennessee/CPS elementary, then propagated
#   through races whose teams are >= 90% known and unanimous. Names are never
#   parsed, so a collision is impossible: Cornell in an NCAA regional is the
#   university, Cornell in a New York sectional is the high school.
#
#   Head to head on the 37,794 schools the graph reached:
#       graph college / pickle hs      132     the Cornell class, corrected
#       graph college / pickle ms       51
#       graph pro     / pickle hs       15     national teams
#       graph college / pickle none  2,328     colleges the pickle never had
#       graph hs      / pickle college    7     disagreements the other way
#   198 corrections against 10 reversals, and every hand-checked case right.
#
# ⚠ THE PICKLE IS STILL THE FALLBACK. The graph only reaches schools that
#   raced against a seeded meet, so 610k of 648k teams are outside it. For
#   those, nothing changes.
_GRAPH_LEVELS = None


def loadGraphLevels():
    """school -> level from school_level_graph, or {} if the table is absent."""
    global _GRAPH_LEVELS
    if _GRAPH_LEVELS is not None:
        return _GRAPH_LEVELS
    _GRAPH_LEVELS = {}
    try:
        from database import getConn
        with getConn() as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('school_level_graph')")
            if cur.fetchone()[0] is None:
                return _GRAPH_LEVELS
            cur.execute("SELECT school, level FROM school_level_graph")
            for name, lvl in cur.fetchall():
                k = normSchoolKey(name)
                if k:
                    _GRAPH_LEVELS[k] = lvl
        print(f"[normalize_distance] Race-graph school levels: "
              f"{len(_GRAPH_LEVELS):,} schools (outranks school_levels.pkl).")
    except Exception as exc:
        print(f"[normalize_distance] school_level_graph unavailable ({exc}); "
              f"using school_levels.pkl alone.")
    return _GRAPH_LEVELS


# ★ A SCHOOL THAT NAMES ITSELF A UNIVERSITY IN ANY LANGUAGE BUT ENGLISH IS A
#   UNIVERSITY. Guillaume Tremblay's feed writes "Université Laval Rouge et
#   Or" with grades 5, 6 and 7 -- a Québec programme year, which the grade
#   machinery reads as a middle schooler running 1:54 for 800m. The school
#   string says what the grade cannot, and it says it unambiguously.
#
# ⚠ AND THAT IS EXACTLY WHY PLAIN ENGLISH "University" IS NOT IN HERE. The
#   corpus is full of high schools called University High School -- Normal
#   (University), Urbana (University) and Chicago (University) were all on the
#   Illinois boards last week -- and "Hidalgo Early College" is a Texas high
#   school. An English word-match would move hundreds of teenagers into the
#   college pool to rescue a handful of Québécois, which is the wrong trade.
#   The accented and foreign forms carry no such collision.
#
# ! CÉGEP IS IN, and is the same case one level down: a Québec CEGEP is
#   post-secondary, and "Cegep de Saint-Laurent" was sitting in ms_m.
_FOREIGN_UNIVERSITY = re.compile(
    r"\b("
    r"universit[ée]s?"          # French   (université, universites)
    r"|universidad(es)?"        # Spanish
    r"|universidade"            # Portuguese
    r"|universit[àa]"           # Italian
    r"|universiteit"            # Dutch
    r"|universit[äa]t"          # German
    r"|uniwersytet"             # Polish
    r"|c[ée]gep"                # Québec CEGEP
    r")\b",
    re.IGNORECASE)


def isForeignUniversity(school):
    """True when the school NAMES itself a university in a language that has
    no high-school collision. See _FOREIGN_UNIVERSITY."""
    return bool(school) and bool(_FOREIGN_UNIVERSITY.search(str(school)))


def levelForSchool(school):
    # ! BEFORE THE LOOKUPS, because neither the graph nor the pickle has ever
    #   seen these schools -- they are foreign, so no US grade feed describes
    #   them, which is the whole reason their athletes were being pooled by a
    #   grade that means something else.
    if isForeignUniversity(school):
        return "college"

    key = normSchoolKey(school)
    if not key:
        return None
    lvl = loadGraphLevels().get(key)
    if lvl is not None:
        return lvl
    if not _SCHOOL_LEVELS:
        return None                                # pickle absent -> clean no-op
    return _SCHOOL_LEVELS.get(key)


# ★ GRADES 1-5 ARE NOW "elem", NOT "ms". Elementary and middle-school runners
#   scale across distances differently and race different distances, so one
#   pool fitted across both is a compromise that serves neither. This SPLITS an
#   existing pool: every grade 1-5 row changes pool, which changes its distance
#   spline, its pool mean and its athlete identity in the engine. A ruler change
#   -- it must be followed by a refit, a backfill and an engine run.
#
#   The split is at 5/6 because that is the standard US boundary and because
#   competitive middle-school racing is overwhelmingly 6-8. Ambiguous banded
#   grades ("5-6") stay "ms": conservative, since ms is the existing behaviour.
GRADE_TO_LEVEL = {
    "1": "elem", "2": "elem", "3": "elem", "4": "elem", "5": "elem",
    "6": "ms", "7": "ms", "8": "ms",
    "9": "hs", "10": "hs", "11": "hs", "12": "hs",
    "Fr": "college", "So": "college", "Jr": "college",
    "Sr": "college", "RS": "college",
    "7-8": "ms", "9-10": "hs", "11-12": "hs",
}


# --- grade normalisation ------------------------------------------------- #
#
# THE DOMAIN FACT (owner-confirmed): high school grades are NUMBERS (9-12, and
# 1-8 for ms); college classes are NAMES (FR/SO/JR/SR, plus RS for redshirt).
# There is no overlap, so a class NAME always means college and a NUMBER always
# means a youth grade. GRADE_TO_LEVEL already encodes exactly that.
#
# THE PROBLEM: the sources write the same value many ways. Measured across the
# corpus, ~8.0M rows carried a grade GRADE_TO_LEVEL could not read --
#   'Freshman' 'Sophomore' 'Junior' 'Senior'   (tfrrs, spelled out)
#   'FR' 'SO' 'JR' 'SR'  and  'fr' 'so' 'jr'   (case)
#   'Fr.' 'So.' 'Jr.' 'Sr.'  'Fresh' 'SENIOR'  (punctuation / abbreviation)
#   '6t' '7t' '8t'                             (truncated ordinals)
# ...plus a further ~9.7M in the 'FR-1' / 'SO-2' / 'JR-3' / 'SR-4' form, which
# is tfrrs's CLASS + YEAR-OF-ELIGIBILITY. The class is the part that matters.
#
# Those rows were silently falling into unknown_pool, AND were invisible as
# "graded neighbours" to anything that reads grades. Normalising the key is a
# pure recovery: no inference, no model, no guess.
#
# Values deliberately NOT mapped (they mean nothing we can trust):
#   '-'  '--'  '?'  'NA'  'UN'  '0'  '13'  'A'..'G'  '2002'..'2032'  'RS/Una'
#   '13-14' '15-16' '17-18' '19+' '6U' 'Gr'
# '-' alone is 15.6M anet TF rows: a placeholder for ABSENT, not a grade.

# Spelled-out and abbreviated college classes -> the canonical GRADE_TO_LEVEL key.
_CLASS_ALIASES = {
    "fr": "Fr", "fresh": "Fr", "freshman": "Fr", "freshmen": "Fr",
    "so": "So", "soph": "So", "sophomore": "So",
    "jr": "Jr", "junior": "Jr",
    "sr": "Sr", "senior": "Sr",
    "rs": "RS", "redshirt": "RS",
}


# --- level arbitration ---------------------------------------------------- #
#
# Levels the engine has a pool for. A level outside this set resolves to None
# and the caller ledgers + drops the row.
#
# ★ EXCLUSION STAYS VISIBLE. 'pro' and 'open' have NO pool_mean and NO distance
#   spline. Emitting 'pro_m' would produce a pool string every downstream lookup
#   misses -- a silent NULL bucket, which is exactly the failure mode the
#   12-key EVENT_DISTANCES_TF dict caused (7,157,445 rows, no alarm). Saying
#   "we cannot rate this" out loud is the safe encoding of a gap.
#
# ⚠ OWNER DECISION OUTSTANDING: give pro/open real pools, fold them into an
#   existing pool, or exclude them like relays. 'elem' is IN because
#   GRADE_TO_LEVEL already emits it; whether a spline exists for it is a
#   separate check.
POOLABLE_LEVELS = frozenset({"ms", "hs", "college", "elem"})


# arbitrateLevel
# Purpose: decide one athlete-season's level from two disagreeing witnesses.
# Arguments: grade_level  — what the row's own grade says ('hs' / None)
#            season_level — the season's UNANIMOUS race verdict, or None
# Output: the level to use, or None when neither witness can speak.
#
# ★ THE ASYMMETRY THAT JUSTIFIES THE ORDER. Grade goes STALE; a race verdict
#   cannot. A grade is written once per row by a scraper reading a roster, and
#   nothing updates it when the athlete graduates -- Pieter Heesters finished
#   high school in 2022 and his 2026 road 10 km still carries grade '12',
#   which pools him hs_m and rates 29:43 at 143.65. A race is contemporaneous
#   by construction: a fact about an event on a date.
#
# ★ GRADE REMAINS THE DEFAULT. Deliberately conservative -- this preserves
#   poolFor's "anet behaviour is provably identical to before" property
#   everywhere the two agree, which is the overwhelming majority of rows.
#
# ⚠ season_level MUST BE PER SEASON, NOT PER RACE. The engine keys on
#   (person_id, pool), so a pool that changes race-to-race splits one athlete
#   into two half-solved unknowns. Cooper Lutkenhaus raced Millrose and a Texas
#   UIL district meet six weeks apart as a high school junior; his season is
#   NOT unanimous, so season_level is None and his grade stands. That is the
#   whole safety mechanism -- see season_level.py.
#
# Pure: no DB, no globals. The backfill and any audit call this same function,
# so they cannot disagree.
# Ordered, so "how far apart are these two verdicts" is a subtraction.
_LEVEL_RANK = {"elem": 0, "ms": 1, "hs": 2, "college": 3, "pro": 4}

# A season verdict may move a grade ONE step. Two is not a correction, it is a
# contradiction, and the grade is the better witness there. See the guard.
MAX_LEVEL_JUMP = 1

# Grades that cannot be demoted below high school by any season verdict.
# 9 is deliberately EXCLUDED -- see the guard.
_UPPERCLASS_GRADES = ("10", "11", "12")


def arbitrateLevel(grade_level, season_level, grade=None):
    if season_level is None:            # no usable race evidence
        return grade_level
    if grade_level is None:             # grade unusable -> pure gain
        return season_level
    if grade_level == season_level:     # agreement -> nothing to decide
        return grade_level

    gi = _LEVEL_RANK.get(grade_level)
    si = _LEVEL_RANK.get(season_level)

    # ★ GUARD 1 -- DISTANCE. A season verdict two or more levels from the grade
    #   is not a correction, it is a contradiction, and the grade wins.
    #
    #   MEASURED: 1,315 athletes with grades 3-6 were pooled college_m/f, and
    #   100% of those rows carried season_level='college' -- 5,956 of 5,956.
    #   season_level.py's MIN_DECIDED_RACES is 1, so a single open-meet
    #   level_mask (USATF Junior Olympic and AAU meets carry the college bit)
    #   gives a third grader a unanimous 'college' season. elem -> college is
    #   three steps; nothing real moves that far in one season.
    #
    #   season_level.py anticipated this: "Raise MIN_DECIDED only if the audit
    #   shows one-race overrides going wrong." It does. But raising it to 2 is
    #   the wrong lever -- that is exactly what breaks Pieter Heesters, whose
    #   two 2026 races sit under separate person_ids with one race each. This
    #   rejects IMPLAUSIBLE overrides instead of all one-race ones.
    #
    # ⚠ 'pro' IS EXEMPT, AND MUST STAY EXEMPT. A professional's scraped grade
    #   is stale by definition -- that is the whole reason step 0 exists, and
    #   Fouad Messaoudi's hs -> pro is a legitimate two-step move. Guarding it
    #   would put him back in hs_m at 136.
    if (season_level != "pro" and gi is not None and si is not None
            and abs(si - gi) > MAX_LEVEL_JUMP):
        return grade_level

    # ★ GUARD 2 -- NO DEMOTING AN UPPERCLASSMAN. A trusted grade of 10, 11 or
    #   12 cannot be middle school or elementary, whatever the season votes.
    #
    #   MEASURED: 62,352 rows with grade 10-12 pooled ms_*. Anders Erickson is
    #   the case -- grade 10 at a K-12 school, season voted 'ms', rated against
    #   a middle school mean and filed on the high school board at 147.
    #
    # ⚠ ASYMMETRIC ON PURPOSE. 'ms' overriding UPWARD stays legal: eighth
    #   graders racing varsity is real and common. Only the demotion is
    #   blocked. And grade 9 is excluded -- ninth graders at combined K-12
    #   schools genuinely race both ways, so that call belongs to the season.
    if (grade is not None and si is not None and gi is not None and si < gi
            and normalizeGrade(grade) in _UPPERCLASS_GRADES
            and season_level in ("ms", "elem")):
        return grade_level

    return season_level                 # disagreement within one level, and
                                        # the season is the fresher witness


# levelToPool
# Purpose: level + gender -> pool string, or None when the level has no pool.
# Detail: gender handling is identical to getPool's, so the two can never
#         produce different spellings of the same pool.
def levelToPool(level, gender):
    if level not in POOLABLE_LEVELS:
        return None
    g = str(gender or "").strip().upper()
    if g == "M":
        return f"{level}_m"
    if g == "F":
        return f"{level}_f"
    return f"{level}_unknown_gender"


# normalizeGrade
# Purpose: map a raw grade string onto a key GRADE_TO_LEVEL knows, or return the
#          value unchanged when nothing safe applies. Pure string work.
# Arguments: grade — the raw stored value (any type; may be None).
# Output: a canonical grade key (str), or "" when the value carries no grade.
# Detail: order matters. Exact match first (cheapest, and never wrong), then the
#         class+eligibility split, then aliasing, then ordinal stripping.
def normalizeGrade(grade):
    if grade is None:
        return ""
    s = str(grade).strip()
    if not s or s in GRADE_TO_LEVEL:      # already canonical -> untouched
        return s

    # 'FR-1' / 'SO-2' / 'JR-3' / 'SR-4': tfrrs class + eligibility year. Keep the
    # class; the year says nothing about level. Guarded so '13-14' (an anet age
    # band) and '11-12' (a real GRADE_TO_LEVEL key, caught above) never enter.
    head = s.split("-", 1)[0].strip()
    if head.lower() in _CLASS_ALIASES:
        return _CLASS_ALIASES[head.lower()]

    # 'Freshman' 'FR' 'Fr.' 'SENIOR' 'rs' ... -> canonical class key.
    bare = s.rstrip(". ").lower()
    if bare in _CLASS_ALIASES:
        return _CLASS_ALIASES[bare]

    # '9th' '12th' '7t' '08' -> the number. ONLY when the value is a number plus
    # an ordinal tail, so 'FR-1'/'13-14'/'2002' can never reach here as digits.
    digits = s.rstrip("thstndrdTHSTNDRD. ")
    if digits.isdigit():
        canon = str(int(digits))          # '08' -> '8'
        if canon in GRADE_TO_LEVEL:
            return canon

    return s                               # unknown -> caller sees unknown_level


# getPool
# Purpose: The level/gender pool of the athlete on a result (e.g. "hs_f").
# Arguments: grade, gender — from the result/athlete.
# Output: pool string.
# Detail: grade is NORMALISED first, so every source's spelling of the same
#         class lands on the same key. This is the SSOT for grade reading --
#         anything that parses grades itself will drift from poolFor.
def getPool(grade: str, gender: str) -> str:
    level = GRADE_TO_LEVEL.get(normalizeGrade(grade))
    if level is None:
        return "unknown_level"
    g = str(gender).strip().upper()
    if g == "M":
        return f"{level}_m"
    elif g == "F":
        return f"{level}_f"
    else:
        return f"{level}_unknown_gender"


# poolFor
# Purpose: getPool, plus a level recovered from the SCHOOL when the grade is
#          unusable, plus the old tfrrs->college fallback as the last resort.
#          SINGLE SOURCE OF TRUTH: the era and distance fitters both import
#          this, so the spline/curve pickle keys and the lookup keys here can
#          never drift apart. Gender is cleaned internally (idempotent for
#          callers that already cleaned it).
#
#   PRECEDENCE (most trustworthy first):
#     1. The row's own GRADE. Ground truth; anet rows always take this path,
#        so anet behaviour is provably identical to before.
#     2. The SCHOOL's known level, from anet's grades (school_levels.pkl).
#        Only set for schools that are unambiguous: not collided (one name,
#        two institutions), not a roster status ('unattached'), not thin.
#        This is what rescues tfrrs, whose grade is always NULL.
#     3. The old admitted domain fact: an unresolved tfrrs row is a college
#        athlete. Kept, because it is right for the rows school lookup can't
#        reach — but it now applies to far fewer of them.
#
#   BACKWARD COMPATIBILITY: with school=None (the default) this function is
#   byte-for-byte the old one. Callers that have not been updated, and every
#   anet row, behave exactly as before. That is deliberate: poolFor is imported
#   by both fitters, and a silent divergence between them and the backfill is
#   the precise failure this SSOT exists to prevent.
#
# Arguments: grade — raw grade string; gender — raw gender string;
#            source — 'anet' | 'tfrrs' (decides whether the fallback applies);
#            school — raw school string, optional. Pass it to enable step 2.
#            season_level — the athlete-season's UNANIMOUS race verdict from
#                athlete_season_level, optional. Pass it to enable step 0.
# Output: pool string, or None = level unknowable (caller ledgers + drops).
def poolFor(grade, gender, source, school=None, season_level=None):
    # ★ PRO DETECTION IS DELIBERATELY NOT HERE. isProSchool still exists and
    #   panels.py should use it -- hiding a pro from a leaderboard costs one
    #   hidden row if it is wrong. Setting a POOL from it costs a whole roster.
    #
    #   Measured: matching on the school string caught 113 strings and 34,942
    #   races, and essentially none was professional --
    #       Denmark (3,732 races, grade 10)   Denmark High School
    #       Mexico  (1,866 races, grade 11)   Mexico High School, Missouri
    #       Bowerman Track (9,105, grade 8)   a youth club, not Bowerman TC
    #       Tahoka  (555, grade 12)           caught by the 'hoka' substring
    #       Central Oregon Running Klub       'on running' inside 'Oregon Running'
    #   A pro types the same string a rural high school is named. There is no
    #   matching rule that separates them because there is no difference to match.
    #
    #   The meet does not rescue it either: Cooper Lutkenhaus raced Millrose in
    #   February and a Texas UIL district meet six weeks later, as a high school
    #   junior. A pro MEET does not imply a pro ATHLETE, and pooling by race
    #   would split one athlete across two pools mid-season -- the engine keys on
    #   (person_id, pool), so he would become two half-solved athletes.
    #
    #   The only thing that works is a hand-curated PERSON-level list with date
    #   ranges. Perhaps 200 athletes. Until that exists, a pro stays pooled by
    #   grade, which is wrong for a handful of people and right for 35,000 races.
    #
    # ★ STEP 0 IS THE ANSWER TO THAT LAST PARAGRAPH, AND IT OBEYS ITS WARNING.
    #   season_level is a PER-SEASON verdict, never per-race, so the Lutkenhaus
    #   failure mode above cannot occur: his season is not unanimous, so his
    #   caller passes None and nothing below changes. Only a season that is
    #   UNANIMOUSLY something other than the grade moves. See season_level.py.
    #
    # ⚠ BACKWARD COMPATIBILITY IS EXACT. With season_level=None (the default)
    #   arbitrateLevel returns grade_level unchanged and this function is
    #   byte-for-byte the old one, including for every unmodified caller and
    #   every anet row. That is deliberate: poolFor is imported by both fitters,
    #   and a silent divergence between them and the backfill is the precise
    #   failure this SSOT exists to prevent.
    # ★ A FOREIGN UNIVERSITY OUTRANKS THE GRADE, AND ONLY THIS DOES.
    #   Everything else here treats the grade as the strongest evidence, for
    #   good reason. But "Université Laval Rouge et Or" with grade 7 is not a
    #   seventh grader: the number is a Québec programme year, and the school
    #   string is the one field in the row that says so. A US high school is
    #   never called université, so nothing else can be caught by this.
    #
    #   isForeignUniversity is deliberately narrow -- no English "University",
    #   because University High School is a real and common name. See it.
    if isForeignUniversity(school):
        return levelToPool("college", gender)

    grade_level = GRADE_TO_LEVEL.get(normalizeGrade(grade))
    # grade is passed too: guard 2 needs the raw grade, because 9 and 12
    # both map to 'hs' and only one of them may be demoted.
    level = arbitrateLevel(grade_level, season_level, grade)  # 0) arbitration

    if level is not None:
        # levelToPool returns None for a level with no pool (pro / open), which
        # is a DROP, not a fallthrough -- falling through would let a stale
        # grade re-claim an athlete the race evidence just took away.
        return levelToPool(level, gender)

    g = str(gender or "").strip().upper()

    level = levelForSchool(school) if school is not None else None
    if level:                                      # 2) the school's known level
        if g == "M":
            return f"{level}_m"
        if g == "F":
            return f"{level}_f"
        return f"{level}_unknown_gender"

    # 3) the old last-resort fact: an unresolved tfrrs row is a college
    #    athlete. Still right for rows the school lookup cannot reach -- and it
    #    can no longer swallow pros, because step 0 already claimed them.
    if source == "tfrrs" and g in ("M", "F"):
        return "college_m" if g == "M" else "college_f"
    return None


# metersFromDistance
# Purpose: Convert a stored distance (miles or meters) into meters.
# Arguments: distance — raw value.
# Output: meters as float, or None.
def metersFromDistance(distance) -> "float | None":
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return None
    if d >= 50:
        return d
    if d > 0:
        return round(d * 1609.34, 1)
    return None


# ------------------------------------------------------------------ #
# TIME NORMALIZATION  (distance — unchanged core, now followed by corrections)
# ------------------------------------------------------------------ #

# normalizeTime
# Purpose: Convert a race time into a fully comparable value by running it through
#          the whole chain: distance -> geometry -> era. Distance puts it on a flat
#          5K scale; geometry removes track-shape effects; era removes year effects.
#
#   THE CHAIN (each step a multiplier; each is 1.0 at its reference so it no-ops
#   when its data is absent):
#       raw time --[distance: 5K-equiv]--> --[geometry: 400m flat]--> --[era: ref year]--> out
#
# Arguments:
#   time_seconds:    the raw finish time in seconds (> 0).
#   distance_meters: the race distance in metres (> 0); distance normalizes to 5K.
#   pool:            competitive pool string, e.g. "college_m" — selects the
#                    per-pool distance spline / exponent.
#   season:          the race year (int) for the era step; None -> era is a no-op.
#                    OPTIONAL so existing callers that don't pass it still work.
#   track_length:    the track length in metres for geometry; None -> the 400m
#                    reference (no length correction). OPTIONAL.
#   track_type:      the track type string ("Banked"/"Flat"/...) for the banking
#                    correction; only "Banked" triggers it. OPTIONAL.
#   sport:           "XC" | "TF", for the BANDED era artifact — picks which
#                    band's curve applies. OPTIONAL: None -> no banded era
#                    correction (we can't pick a curve without knowing the
#                    sport; legacy single-curve pickles still apply).
#   event_short:     the event string (TF), for band classification (hurdles/
#                    steeple need the string, not just metres). OPTIONAL:
#                    None -> the band is classified from distance_meters alone.
# Returns: the fully normalized time in seconds (rounded to 2 dp), or None if the
#          inputs are missing/non-positive.
def normalizeTime(time_seconds, distance_meters, pool,
                  season=None, track_length=None, track_type=None,
                  sport=None, event_short=None, weather=None, course=None):
    if not time_seconds or not distance_meters:
        return None
    if time_seconds <= 0 or distance_meters <= 0:
        return None

    # normalize the key (round floats), then fetch the cached multiplier. Weather
    # and course are NOT part of the key: they are per-race (continuous), so
    # caching on them would make every row a unique key and defeat the speedup.
    dm, pool_k, season_k, tl, tt, sport_k, ev = _factorKey(
        distance_meters, pool, season, track_length, track_type,
        sport, event_short)
    factor = _normalizationFactorCached(dm, pool_k, season_k, tl, tt,
                                        sport_k, ev)

    normalized = time_seconds * factor
    # WEATHER: the last link, applied per-race outside the cache. distance_meters
    # feeds the distance interaction (heat scales with race length). No-op when the
    # artifact/weather is absent, so existing callers are byte-for-byte unchanged.
    normalized = _applyWeather(normalized, weather, course, sport,
                               distance_m=distance_meters)
    return round(normalized, 2)


# _evalDistancePotential          !! KEEP IN SYNC with the fitter's
# Purpose: g at any log-distance   _evalPotential — a deliberate marked
#          for a "distance_potential" artifact: linear interpolation
#          inside the knots, the BOUNDARY SEGMENT'S SLOPE extended beyond
#          them (constant local exponent past the data — an extrapolation
#          POLICY, never an oscillating tail). Mirrored, not imported:
#          the fitter imports THIS module, so importing back is circular.
# Arguments: entry — {"knots": [...], "values": [...]}; ld — log-distance.
# Output: g(ld) as float.
def _evalDistancePotential(entry, ld):
    k, v = entry["knots"], entry["values"]
    if ld <= k[0]:
        slope = (v[1] - v[0]) / (k[1] - k[0])
        return v[0] + slope * (ld - k[0])
    if ld >= k[-1]:
        slope = (v[-1] - v[-2]) / (k[-1] - k[-2])
        return v[-1] + slope * (ld - k[-1])
    lo = 0
    hi = len(k) - 1                      # binary search: no numpy import
    while hi - lo > 1:                   # in this module, and 8 lines of
        mid = (lo + hi) // 2             # bisect beat adding a dependency
        if k[mid] <= ld:
            lo = mid
        else:
            hi = mid
    t = (ld - k[lo]) / (k[hi] - k[lo])
    return v[lo] * (1 - t) + v[hi] * t


# _distancePotentialEntry
# Purpose: The lookup chain, most-specific first. XC and TF carry
#          measurably different scaling laws (XC local exponent
#          ~0.95-1.05, TF ~1.06-1.22), so the artifact keys curves
#          "pool|SPORT" with per-sport globals behind them and the
#          combined global as the last resort for sportless callers.
# Arguments: pool — from getPool; sport — "XC"/"TF"/None.
# Output: the curve entry to evaluate.
def _distancePotentialEntry(pool, sport):
    pools = _SPLINES["pools"]
    if sport is not None:
        s = str(sport).strip().upper()          # same normalization as era
        entry = (pools.get(f"{pool}|{s}")
                 or _SPLINES.get("global_by_sport", {}).get(s))
        if entry is not None:
            return entry
    return pools.get(pool) or _SPLINES["global"]


# _normalizeWithPotential
# Purpose: Distance normalization via the 1-D potential artifact:
#          T_5k_equiv = T_d * exp(g(log target) - g(log d)). Difference
#          form on purpose — correct whatever the anchor convention.
# Arguments: as _normalizeWithSpline, plus the optional sport.
# targetFor
# Purpose:   The distance a pool is normalised TO. Public because callers
#            outside this module need it to reason about the SCALE of a
#            normalized_time, which stopped being one number when per-pool
#            anchors landed.
# Arguments: pool -- bare or sport-suffixed ('ms_f' or 'ms_f|XC').
# Output:    metres as float; the artifact's global target when unknown.
def targetFor(pool, sport=None):
    """The anchor for a pool -- the distance its normalized_time is expressed at.

    ! THE FULL KEY IS TRIED FIRST -- NO LONGER, AND THE SPORT IS NOW IGNORED
      WHEN THE BARE POOL HAS AN ANCHOR. It used to prefer 'hs_m|TF' over
      'hs_m', which is not what the normaliser does:

          _normalizeWithPotential:
              target = pool_targets[base] or entry['target'] or global

      -- it strips the suffix deliberately (see the comment there) and reads
      the BARE key. So the artifact's 'hs_m|TF' = 1600 was never applied to a
      single row, while every caller reasoning about SCALE was told it had
      been. hs_m TF normalized_times are 5000m-equivalents; targetFor said
      1600.

    ⚠ WHAT THAT COST, and it is the reason this docstring's own warning
      existed. poolBandFor multiplies the anchor by PACE_FLOOR/PACE_CEIL, so
      the boards and the solve gated high-school TF rows at (192, 1152) --
      the band for a 1600m-equivalent -- against numbers written on a 5000m
      scale. A 5:29 1600, an 11:53 3200 and a 2:27 800 all normalise past
      1152 and were refused a place on every board; below 192 nothing exists,
      so the floor that should have been 600 caught nothing and impossibly
      fast rows passed. That is the same failure the old docstring named --
      "the two stages write and read on different scales" -- with the
      spellings swapped.

      The bare key is therefore authoritative, exactly as in the normaliser.
      A sport-suffixed anchor is still honoured when the bare pool has none,
      so an older artifact that only carried suffixed keys still resolves.
    """
    # ! NO ARTIFACT IS NOT AN EXCEPTION. _SPLINES is None until the pickle
    #   loads, and every other reader in this module already degrades to a
    #   no-op rather than raising -- this one raised AttributeError, so a
    #   caller reasoning about SCALE crashed on a machine that simply had not
    #   built the splines yet. The documented fallback is the global target.
    targets = (_SPLINES or {}).get("pool_targets", {})
    base = (pool or "").split("|")[0]
    if base in targets:
        return float(targets[base])
    sp = sport or ((pool or "").split("|")[1] if "|" in (pool or "") else None)
    if sp and f"{base}|{sp}" in targets:
        return float(targets[f"{base}|{sp}"])
    return float((_SPLINES or {}).get("target", 5000.0))


# ★ THE ENGINE'S SANITY BAND, DEFINED ONCE. speed_ratings.packResults keeps a
#   row out of the SOLVE when its normalized_time falls outside this pace
#   band; since every row now still receives a rating (fill_ratings), the
#   band is ALSO what the boards gate on -- build_ranking_results and
#   panels.py refuse out-of-band rows a place in a ranking. Both readers and
#   the engine must agree on the numbers or a row could be solved on but
#   unrankable, or ranked on a rating the solve refused to stand behind.
#
#     0.12 s/m = 2:00/km   -- faster than any human over any distance
#     0.72 s/m = 12:00/km  -- slower than walking
PACE_FLOOR = 0.12
PACE_CEIL = 0.72


# poolBandFor
# Purpose:   (lo, hi) normalized_time bounds for one pool.
# Arguments: pool -- bare or sport-suffixed; sport -- optional, as targetFor.
# Output:    tuple of floats.
def poolBandFor(pool, sport=None):
    t = targetFor(pool, sport)
    return (PACE_FLOOR * t, PACE_CEIL * t)


def _normalizeWithPotential(time_seconds, distance_meters, pool, sport=None):
    entry = _distancePotentialEntry(pool, sport)
    # ★ THE ENTRY'S OWN ANCHOR, FALLING BACK TO THE ARTIFACT'S GLOBAL ONE.
    #   A pool is normalised to a distance its athletes actually race, so
    #   g(target) is read from inside the fitted span instead of from a
    #   linear extrapolation off its edge. ms_* spans (800, 3200) and was
    #   being asked for g(log 5000) on every single row.
    #
    # ⚠ BACKWARD COMPATIBLE BY CONSTRUCTION. An artifact written before
    #   the fitter stamped anchors has no "target" on its entries, so
    #   every pool falls back to _SPLINES["target"] and this function is
    #   byte-for-byte what it was. The two can be mixed during a rollout.
    # ! POOL NAME FIRST, THEN THE ENTRY, THEN THE GLOBAL DEFAULT.
    #   `entry` may be the "global" fallback, shared by every pool that did
    #   not earn its own curve -- it cannot carry one pool's anchor. The map
    #   is keyed by pool, so it answers correctly no matter which entry the
    #   fallback chain returned.
    #
    # ⚠ base pool only: `pool` arrives as e.g. "ms_f", not "ms_f|XC". If a
    #   caller ever passes the sport-suffixed form, strip it here rather
    #   than letting the lookup miss silently.
    base = (pool or "").split("|")[0]
    target = (_SPLINES.get("pool_targets", {}).get(base)
              or entry.get("target")
              or _SPLINES["target"])
    g_d = _evalDistancePotential(entry, math.log(distance_meters))
    g_t = _evalDistancePotential(entry, math.log(target))
    return time_seconds * math.exp(g_t - g_d)


# _normalizeWithSpline
# Purpose: Distance normalization — DISPATCH on the artifact's kind first
#          (dispatch on data, not deployment: the new potential artifact
#          carries kind="distance_potential"; legacy pool-dict pickles
#          have no "kind" key and fall through to the 2D-spline path
#          below, unchanged).
def _normalizeWithSpline(time_seconds: float, distance_meters: float,
                         pool: str, sport=None) -> float:
    if _SPLINES.get("kind") == "distance_potential":
        return _normalizeWithPotential(time_seconds, distance_meters,
                                       pool, sport)
    spline = _SPLINES.get(pool) or _SPLINES["global"]
    log_x = math.log(TARGET_DISTANCE_METERS / distance_meters)
    log_z = math.log((distance_meters + TARGET_DISTANCE_METERS) / 2)
    # .ev = point evaluation (one value per point). The grid API,
    # spline(x, y), returns a 2-D array even for scalars, and float()
    # on a (1,1) array raises on modern numpy. Same fix as the fitter's
    # _predictFromSpline — the two sides evaluate identically.
    # .item(): shape-proof scalar extraction — .ev returns a 0-d
    # array on some scipy versions and a (1,)-array on others, and
    # float() only accepts 0-d. Same fix as the fitter, executed on
    # both shapes before shipping.
    log_ratio = spline.ev(log_x, log_z).item()
    return time_seconds * math.exp(log_ratio)


# _normalizeWithExponent
# Purpose: Distance normalization fallback (placeholder power-law). Unchanged.
def _normalizeWithExponent(time_seconds: float, distance_meters: float, pool: str) -> float:
    exponent = DISTANCE_EXPONENT_BY_POOL.get(pool, DEFAULT_EXPONENT)
    return time_seconds * (TARGET_DISTANCE_METERS / distance_meters) ** exponent


# ------------------------------------------------------------------ #
# GEOMETRY CORRECTION  (length potential + banking surface)
# ------------------------------------------------------------------ #
#
# Two independent corrections from geometry_spline.pkl, both multiplicative:
#   LENGTH  — convert the time off the track's length onto the 400m reference,
#             via the potential g (lengthToReference: time * exp(-g(length))).
#   BANKING — if the track is banked, divide out the banking advantage so a
#             banked time reads like its flat equivalent.
# A row with no track geometry defaults to the reference (400m flat) -> both are
# 1.0 -> no change. This is the safe no-op: we don't invent a correction we can't
# support.
#
#   THE PICTURE — what a raw track time passes through:
#
#       raw time on a 200m banked indoor track
#            |
#            |  LENGTH  : * exp(-g(200))   -> "as if it were a 400m oval"
#            v
#       length-corrected time
#            |
#            |  BANKING : * bankingFactor  -> "as if the turns weren't banked"
#            v
#       fully geometry-normalized time  (comparable to a flat 400m race)
#
#   Each factor is 1.0 at the reference (400m, flat), so a standard track — or a
#   row we have no geometry for — passes through unchanged.


# _resolveTrackLength
# Purpose: The length to feed geometry: the row's track_length, or the 400m
#          reference when absent/unparseable. Centralised so "missing -> reference"
#          (the safe no-op) lives in exactly one place.
# Arguments: track_length — a length in metres, or None, or a non-numeric value.
# Output: a float length: the parsed track_length, or REFERENCE_TRACK_LENGTH
#         (400.0) when it's None or can't be turned into a number.
def _resolveTrackLength(track_length):
    if track_length is None:
        return REFERENCE_TRACK_LENGTH
    try:
        return float(track_length)              # may be a str/Decimal from the DB
    except (TypeError, ValueError):
        return REFERENCE_TRACK_LENGTH           # unparseable -> reference (no-op)
    

# _clamp
# Purpose:   Pin a query into a fitted range [lo, hi], so a spline is never
#            asked to EXTRAPOLATE past its data — outside the range it holds
#            the boundary value instead ("we know nothing past here, so we
#            hold the last thing we knew"). One helper because three
#            different corrections need the identical guard.
# Arguments: x — the query value; lo, hi — the fitted bounds.
# Output:    x if inside, else the nearer bound.
def _clamp(x, lo, hi):
    # chained conditional expression: value-if-true IF test ELSE value-if-false,
    # nested once — reads as "lo when below, else (hi when above, else x)".
    return lo if x < lo else (hi if x > hi else x)


# _applyGeometry
# Purpose: Apply the length + banking corrections to a distance-normalized time.
# Arguments:
#   normalized:   the distance-normalized time in seconds (output of step 1).
#   distance_m:   the event distance in metres. BOTH corrections now use it:
#                 the rank-1 length correction scales g1 by the distance shape
#                 s(d), and the banking model is evaluated at d. (Under an old
#                 1D pickle, the length step ignores it — see _lengthToReference.)
#   track_length: the row's track length in metres, or None -> the 400m
#                 reference, i.e. no length correction.
#   track_type:   the row's track type string; only the literal "Banked"
#                 triggers the banking correction.
# Output: the geometry-corrected time. Returned UNCHANGED when the geometry
#         pickle is absent — the graceful fallback.
def _applyGeometry(normalized, distance_m, track_length, track_type):
    if _GEOMETRY is None:                       # no pickle -> correction off
        return normalized

    length = _resolveTrackLength(track_length)  # real length or 400m reference

    length_art = _GEOMETRY.get("length")        # the length half (or None)
    if length_art is not None:
        normalized = _lengthToReference(length_art, normalized,
                                        length, distance_m)

    if track_type == "Banked":                  # only positively-known banked
        banking_art = _GEOMETRY.get("banking")
        if banking_art is not None:
            normalized = normalized * _bankingFactor(banking_art,
                                                     length, distance_m)
    return normalized

# _lengthToReference
# Purpose: Convert a time run on a track of `length` onto the 400m reference.
#          Handles BOTH artifact generations (mirror of the fitter's
#          lengthToReference — !! KEEP IN SYNC !!):
#            kind "potential_1d"    : factor = exp(-g1(L))         (distance ignored)
#            kind "potential_rank1" : factor = exp(-g1(L) * s(d))  (distance scales it)
# Arguments:
#   length_art   — the "length" sub-dict of geometry_spline.pkl:
#                    always:  "spline" (g1), "min_len"/"max_len" (clamp bounds)
#                    rank1 +: "kind", "shape_spline" (s over distance),
#                             "min_dist"/"max_dist" (its clamp bounds)
#   time_seconds — the time to convert (already distance-normalized).
#   length       — the track's length in metres.
#   distance_m   — the RACE distance in metres, or None. None -> s stays 1.0,
#                  i.e. the all-distance-average correction — exactly the old
#                  1D behavior, the honest neutral when no distance is known.
# Output: the reference-equivalent time in seconds.
def _lengthToReference(length_art, time_seconds, length, distance_m=None):
    L = _clamp(length, length_art["min_len"], length_art["max_len"])
    g = float(length_art["spline"](L))          # g1(L): the all-distance average

    # .get, not [key]: an OLD pickle has no "kind" key at all — .get returns
    # None instead of raising, so the comparison is simply False and the old
    # pickle flows through the 1D path untouched.
    if length_art.get("kind") == "potential_rank1" and distance_m is not None:
        d = _clamp(distance_m, length_art["min_dist"], length_art["max_dist"])
        g *= float(length_art["shape_spline"](d))   # scale by this race's share

    return time_seconds * math.exp(-g)

# _bankingFactor
# Purpose: DISPATCHER — the banking multiplier for (length, distance), routed
#          by which generation of artifact is loaded (mirror of the fitter's
#          bankingFactor — !! KEEP IN SYNC !!):
#            kind "banking_by_distance" -> per-length distance models (new)
#            no kind / anything else    -> the old tier artifact (legacy)
# Arguments: banking_art — the "banking" sub-dict of geometry_spline.pkl.
#            length      — the banked track's length in metres.
#            distance    — the event distance in metres.
# Output: the multiplier to scale a banked time up to its flat equivalent.
def _bankingFactor(banking_art, length, distance):
    if banking_art.get("kind") == "banking_by_distance":
        return _bankingFactorByDistance(banking_art, length, distance)
    return _bankingFactorLegacy(banking_art, length, distance)


# _bankingFactorByDistance
# Purpose: Evaluate the NEW artifact: pick the nearest fitted track length,
#          then evaluate that length's own distance model.
# Arguments:
#   banking_art — {"kind": "banking_by_distance", "models": {length: model}}
#                 where each model is ONE of:
#                   {"form": "spline", "spline": s, "lo": .., "hi": ..}
#                       s(d) = log banking benefit at race distance d
#                       (fitted through per-band medians; lo/hi = the fitted
#                        distance range, the clamp bounds)
#                   {"form": "median", "z": ..}
#                       one log benefit for every distance (thin lengths: Iowa)
#   length, distance — as above.
# Output: the multiplier exp(z).
def _bankingFactorByDistance(banking_art, length, distance):
    models = banking_art["models"]
    # min() over a dict iterates its KEYS (the fitted lengths); key=lambda
    # ranks each by distance-to-our-length, so min returns the nearest
    # fitted length — same snap-to-nearest the old binned tier used.
    nearest = min(models, key=lambda L: abs(L - length))
    m = models[nearest]

    if m["form"] == "spline":
        # Clamp BEFORE evaluating: the spline was fitted on band medians out
        # to ~5000m; past its last knot a cubic keeps following its
        # polynomial and can swing to absurd values. Clamping holds the
        # boundary value instead — a 10K query gets the 3500m+ band's answer.
        z = float(m["spline"](_clamp(distance, m["lo"], m["hi"])))
    else:                                       # "median": one number, any d
        z = m["z"]
    return math.exp(z)                          # log benefit -> multiplier


# _bankingFactorLegacy
# Purpose: Evaluate the OLD tier artifact ("2d"/"1d"/"binned") — the current
#          _bankingFactor body verbatim, renamed. Kept so a pre-restructure
#          pickle on disk can never crash inference.
# (body: exactly the existing tier if/elif/else, unchanged, with the 1d
#  branch's inline clamp swapped for _clamp)
def _bankingFactorLegacy(banking_art, length, distance):
    tier = banking_art["tier"]
    if tier == "2d":
        # .ev(x, y) evaluates the bivariate surface at (length, distance).
        z = float(banking_art["model"].ev(length, distance))
    elif tier == "1d":
        lo, hi = banking_art["min_len"], banking_art["max_len"]
        L = lo if length < lo else (hi if length > hi else length)   # clamp
        z = float(banking_art["model"](L))      # spline over length only
    else:  # binned: no interpolation, snap to the nearest sampled length
        binned = banking_art["model"]           # dict {length: log_ratio}
        nearest = min(binned, key=lambda k: abs(k - length))
        z = binned[nearest]
    return math.exp(z)                           # log-benefit -> multiplier


# ------------------------------------------------------------------ #
# EVENT PARSING + ERA BANDS  (SHARED: fit_era_correction.py imports these)
# ------------------------------------------------------------------ #
#
# The band an event falls in must mean EXACTLY the same thing at fit time and
# at inference time, so the classifier lives here — once — and the fitter
# imports it. (Not a KEEP-IN-SYNC mirror: the fitter already imports this
# module for normalizeTime, so importing the classifier adds zero coupling and
# deletes a whole class of drift bug. Mirrors are for verifier-vs-audited
# independence; a fitter and its consumer are two callers of one definition.)
#
#   THE BANDS (why they exist: the era effect differs by event — super-shoes
#   hit distance ~2017-19, sprints barely and later — so each band gets its
#   own independently-fitted curve, and sprints double as the placebo):
#
#     xc            everything in `results` (all ~5K, one locomotion)
#     sprint        flat <= 250m       (55/60/100/200 — the placebo band)
#     m400          flat 251-500m      (300/400/500 — the anaerobic/aerobic seam)
#     m800          flat 501-1099m     (600/800/1000)
#     mile          flat 1100-2399m    (1500/1600/mile)
#     m3200         flat 2400-3999m    (3000/3200/2-mile)
#     m5000         flat >= 4000m      (5000/10000)
#     hurdles_short hurdles < 250m     (55H/60H/100H/110H)
#     hurdles_long  hurdles >= 250m    (300H/400H)
#     steeple       any steeplechase
#     None          relays, racewalks, unparseable — excluded by rule
#
#   Hurdles/steeple are NOT pooled with flat neighbours: their times carry a
#   systematic offset on the normalized scale, so pooling would let event-mix
#   drift across seasons masquerade as an era change. Thin bands BORROW a
#   fitted neighbour's curve at inference instead (future borrow map), keeping
#   every fitted curve clean.

_MILE_METERS = 1609.34
_YARD_METERS = 0.9144

# first number in the string, integer or decimal (e.g. "1.5" in "1.5mile").
_EVENT_NUM_RE  = re.compile(r"(\d+(?:\.\d+)?)")
# a digit then optional 'm' then 'h' at a word end: "110mh", "300h", "60mh".
_HURDLE_RE     = re.compile(r"\dm?h\b")
# a digit then 'k'/'km' at a word end: "5k", "8k" (XC-style shorthands).
_KILO_RE       = re.compile(r"\d\s*(?:k|km)\b")
# a digit then a yards unit at a word end: "600y", "440yd".
_YARDS_RE      = re.compile(r"\d\s*(?:y|yd|yds|yards)\b")


# parseEventShort
# Purpose: One event_short string -> what was run: how far, and what KIND of
#          locomotion/format. Kind decides band family and exclusions; meters
#          is the fallback distance when meets_tf.distance_meters is NULL
#          (~70% of rows — the parser is the workhorse, per the geometry fit).
# Arguments:
#   event_short: the raw event string (e.g. "1600m", "110mH", "3000mSC",
#                "4x400m", "1mile", "5k"). None/blank tolerated.
# Output: {"meters": float|None, "kind": str|None} where kind is one of
#         "flat" | "hurdles" | "steeple" | "relay" | "racewalk" | None (blank).
#         Relays/racewalks return meters=None — they are excluded wholesale,
#         so a distance would only invite misuse.
def parseEventShort(event_short) -> dict:
    s = str(event_short or "").strip().lower()
    if not s:
        return {"meters": None, "kind": None}

    # format exclusions first — a "4x400m" must never parse as a 4m flat race.
    if ("4x" in s or "relay" in s or "dmr" in s or "smr" in s
            or "medley" in s or "shuttle" in s):
        return {"meters": None, "kind": "relay"}
    # racewalk: explicit rule + ledger downstream (the geometry lesson — the
    # old accidental hyphen-strip guard died when a regex was extended).
    if "walk" in s or s.endswith("rw"):
        return {"meters": None, "kind": "racewalk"}

    if "sc" in s or "steeple" in s:            # "3000mSC", "2000msc"
        kind = "steeple"
    elif "hurdle" in s or _HURDLE_RE.search(s):
        kind = "hurdles"
    else:
        kind = "flat"

    m = _EVENT_NUM_RE.search(s)
    if m is None:                              # named-but-numberless oddity
        return {"meters": None, "kind": kind}
    value = float(m.group(1))

    if "mile" in s:                            # "1mile", "2mile" -> miles
        meters = value * _MILE_METERS
    elif _KILO_RE.search(s):                   # "5k", "8k" -> kilometres
        meters = value * 1000.0
    elif _YARDS_RE.search(s):                  # "600y" -> yards
        meters = value * _YARD_METERS
    else:                                      # bare number = metres
        meters = value
    return {"meters": meters, "kind": kind}


# classifyEraBand
# Purpose: The era band for one result — THE shared definition (fit + inference).
# Arguments:
#   sport:       "XC" | "TF" (any case). XC is one band; anything else -> None.
#   event_short: the event string (TF). None is tolerated (falls back to
#                distance_m, classified as flat).
#   distance_m:  the race distance in metres, used when the event string
#                carries no number (or for event-less callers).
# Output: band string (see the table above), or None = this result has no era
#         band (relay/racewalk/unparseable) and must not be era-corrected.
def classifyEraBand(sport, event_short=None, distance_m=None):
    sp = str(sport or "").strip().upper()
    if sp == "XC":
        return "xc"
    if sp != "TF":
        return None

    parsed = parseEventShort(event_short) if event_short else \
        {"meters": None, "kind": "flat"}
    kind = parsed["kind"]
    if kind in ("relay", "racewalk", None):
        return None
    meters = parsed["meters"] if parsed["meters"] else distance_m
    if not meters or meters <= 0:
        return None

    if kind == "steeple":
        return "steeple"
    if kind == "hurdles":
        return "hurdles_short" if meters < 250 else "hurdles_long"
    # flat, banded by the standard-event families:
    if meters < 45:
        return None                            # sub-55m oddities: no band
    if meters <= 250:
        return "sprint"
    if meters <= 500:
        return "m400"
    if meters <= 1099:
        return "m800"
    if meters <= 2399:
        return "mile"
    if meters <= 3999:
        return "m3200"
    return "m5000"


# _genderFromPool
# Purpose: The gender leg of the era key, read off the pool suffix so callers
#          don't need to thread gender separately (the pool already encodes it).
# Arguments: pool — a getPool string ("hs_f", "college_m", ...).
# Output: "M" | "F" | "U" (unknown — such rows get no banded era correction,
#         because no curve was fitted for them).
def _genderFromPool(pool):
    if pool.endswith("_m"):
        return "M"
    if pool.endswith("_f"):
        return "F"
    return "U"


# ------------------------------------------------------------------ #
# ERA CORRECTION  (per-season factor)
# ------------------------------------------------------------------ #

# _applyEra
# Purpose: Scale a distance+geometry-normalized time onto the reference season —
#          the LAST correction in the chain. DISPATCHER over both artifact
#          generations (mirror-free: the fitter imports this module):
#            kind "era_banded"       -> per-(sport|band|gender) curves; the
#                                       era_key picks which one. No key / key
#                                       not fitted -> no-op (a band we never
#                                       fitted must never be corrected).
#            no kind / anything else -> the old single-curve artifact (legacy).
#          No-op when the pickle is missing, season is None, or the season
#          isn't in the chosen curve — an absent or not-yet-validated curve
#          never silently biases the value.
# Arguments:
#   normalized — the time so far (already distance + geometry corrected), seconds.
#   season     — the race's season (calendar year), or None.
#   era_key    — "SPORT|band|gender" for the banded artifact, or None. Built by
#                normalizeTime from (sport, event_short/distance, pool); a
#                caller that doesn't say what sport it is gets no banded
#                correction (honest: we can't pick a curve we can't identify).
# Output: the era-corrected time in seconds, or the input unchanged (no-op paths).
def _applyEra(normalized, season, era_key=None):
    if _ERA is None or season is None:          # era off, or no season to look up
        return normalized

    if _ERA.get("kind") == "era_banded":        # .get: legacy pickles have no
        entry = _ERA["curves"].get(era_key) if era_key else None   # "kind" key
        if entry is None:                       # unknown/unfitted band -> no-op
            return normalized
        level = entry["curve"].get(season)      # that band's own curve
    else:                                       # legacy single-curve artifact
        level = _ERA["curve"].get(season)

    if level is None:                           # season not in the curve -> no-op
        return normalized
    # reference season has level 0 -> exp(-0)=1 (unchanged); a faster season has a
    # NEGATIVE level -> exp(-level)>1, scaling it UP to the slower reference scale.
    return normalized * math.exp(-level)


# ------------------------------------------------------------------ #
# WEATHER CORRECTION  (race-day; the LAST link in the chain)
# ------------------------------------------------------------------ #
#
# Applied as a per-race multiplier AFTER distance/geometry/era. Unlike those it
# cannot be cached (temp/soil/... are continuous, unique per race), so it lives
# outside _normalizationFactorCached and is a plain post-multiply on the result.
#
# Direction: bad conditions (hot/muddy/windy) slow a race -> the fit's terms are
# POSITIVE there -> wmult = exp(sum) > 1 -> normalized_time / wmult is SMALLER
# (faster), crediting the athlete for the conditions. At the reference weather
# every term is 0 -> wmult = 1 -> exact no-op. A missing weather field is treated
# as the reference (that term drops out), so partial weather still corrects what
# it can.

# _rcsValue
# Purpose: evaluate a restricted cubic spline (the fitter's _nsBasis . coef) at a
#          single x, in PURE PYTHON -- this module intentionally imports no numpy.
#          MUST mirror fit_weather_correction._nsBasis exactly: dedupe+sort knots,
#          linear fallback under 3 knots, else basis = [x, term_0 .. term_{K-3}].
# Arguments: spline -- {"knots":[...], "coef":[...], "ref": float}; x -- the value.
# Output: the spline's log-effect at x (a float).
def _rcsValue(spline, x, dc=0.0):
    # coefficients shift with distance (dc) so the whole curve SHAPE changes:
    # c_effective = coef + coef_dist * dc.
    k = sorted(set(spline["knots"]))
    coef = spline["coef"]
    cdist = spline.get("coef_dist")
    if cdist is not None and dc != 0.0:
        coef = [coef[i] + cdist[i] * dc for i in range(len(coef))]
    if len(k) < 3:                                   # degenerate -> linear column
        return coef[0] * x
    K = len(k)
    denom = (k[-1] - k[0]) ** 2

    def cube(u):
        return u * u * u if u > 0 else 0.0

    val = coef[0] * x                                # the linear (x) basis column
    for j in range(K - 2):                           # the K-2 cubic columns
        tj = k[j]
        term = (cube(x - tj)
                - cube(x - k[-2]) * (k[-1] - tj) / (k[-1] - k[-2])
                + cube(x - k[-1]) * (k[-2] - tj) / (k[-1] - k[-2])) / denom
        val += coef[j + 1] * term
    return val


# _weatherArtifactFor
# Purpose: the loaded weather artifact for a sport, or None (no-op) when the
#          sport is unknown or its pickle is absent.
def _weatherArtifactFor(sport):
    if sport is None:
        return None
    return _WEATHER.get(str(sport).strip().upper())


# ---------------------------------------------------------------------------
# CELL REALISM -- the 7/16 Juneau finding.
#
# MAX_RACE_SNOW_M
# The deepest snowpack a race can actually be RUN on.
#
# WHY THIS CONSTANT EXISTS (measured, not theorised):
#   ERA5 is a 0.25 deg grid -- ~25 km cells -- so a cell's avg(snow_depth)
#   describes THE CELL, not the course. Thunder Mountain High School, Juneau AK
#   (lat 58.379658, lon -134.59125, altitude_meters 10) snaps to cell
#   (58.5, 225.5). That cell also contains the Juneau Icefield: 6.795 m of
#   permanent snow, year round -- which is why it reads the same in September as
#   in April. The snow beta is 0.748411 per metre, so:
#       0.748411 * 6.63 = 4.96   ->   exp(4.96) = 143x
#   Every runner in that division was divided by 143. A 1007-second race
#   normalized to 6.81 seconds.
#
#   MEASURED 7/16, same 39,288,362 rows, one variable:
#       weather pickle live      -> XC fastest floor    6.81s
#       weather pickle .OFF      -> XC fastest floor  714.86s
#   And 714.86 is arithmetically right: 1198.8s over 8000 m at b~=1.08 gives
#   1198.8 * (5000/8000)^1.08 = 717s.
#
#   NOTHING WAS BROKEN. The grid is correct (Juneau really does have 6.8 m of
#   snow up there). The GPS is correct. _rcsValue is correct -- traced by hand at
#   every observed extreme, no term exceeds ~0.1. The snow beta is correct
#   (3.4 documents +3.8%/5cm = 0.76/m). The soil clip is correct ([0,1], zero
#   courses outside). Every component is right and the answer is 143, because a
#   25 km average is not a venue measurement, and snow is the one feature with a
#   linear beta big enough to turn that into a catastrophe. Temp and soil are
#   splines and stay bounded; wind and precip have tiny betas. Snow was the only
#   unguarded door.
#
#   0.10: CORRECTIONS HAVE A POINT OF APPLICATION -- if the bucket holds more
#   than the detector measured, the fix's scope exceeds its evidence. The bucket
#   held a glacier.
#
# WHY 0.5 m:
#   Nobody races a 5K under 6.6 metres of snow. Above roughly half a metre the
#   meet is cancelled. So a larger value is not a race condition -- it is the
#   grid telling you the venue was not resolved. 14: JUDGE WITH PHYSICS WHEN
#   STATISTICS MIGHT BE POISONED. A physical bound needs no clean neighbours.
MAX_RACE_SNOW_M = 0.5


def isRaceWeatherPlausible(weather):
    # Purpose   : could a race have been HELD in these conditions?
    # Arguments : weather -- the race-day dict {apparent_temp, wind, precip,
    #                        soil, snow}; any key may be missing or None.
    # Output    : bool. False -> this cell is describing terrain, not a course.
    # ★ WHY THIS NO-OPS THE WHOLE CORRECTION, NOT JUST THE SNOW TERM:
    #   a cell that reports a glacier is not describing this venue's temperature
    #   or soil moisture either. Same cell, same 25 km average, same lie. Zeroing
    #   only the snow term would keep the other three terms sourced from an
    #   icefield and call it a repair.
    # Note      : `snow > MAX` is False for None-free NaN too, so a NaN snow value
    #   (7 rows exist in weather_grid) would PASS here. That is deliberate: NaN
    #   propagates to exp(NaN)=NaN downstream, which the fitter's own finite gate
    #   catches. This function answers ONE question -- is the venue resolved --
    #   and must not quietly grow into a data-quality checker.
    # ★ THIS IS THE SHARED DEFINITION. fit_weather_correction IMPORTS it rather
    #   than mirroring it: a fitter and its consumer are two callers of ONE
    #   definition -> import. Mirrors are for verifier-vs-audited independence,
    #   which this is not (14: MIRRORS vs IMPORTS).
    snow = weather.get("snow")
    if snow is not None and snow > MAX_RACE_SNOW_M:
        return False
    return True


# _applyWeather
# Purpose: scale a distance+geometry+era-normalized time onto REFERENCE weather.
# Arguments:
#   normalized -- time so far (seconds).
#   weather    -- dict of race-day values {apparent_temp, wind, precip, soil,
#                 snow}; any key may be missing/None (-> that term is skipped).
#   course     -- the course key for per-course mud sensitivity (course_name for
#                 XC, "TF:loc:<id>:<in|out>" for TF). Unknown course -> s_c = 1.0.
#   sport      -- "XC"|"TF"; selects the per-sport artifact.
#   distance_m -- race distance (m) for the distance interaction: each feature's
#                 effect scales with dc = distance/dist_ref - 1 (0 at the ref
#                 distance), so heat bites harder on a 10k than a 100m.
# Output: the weather-corrected time (seconds), or the input unchanged (no-op).
# ---------------------------------------------------------------------------
# THE MULTIPLIER SANITY BAND -- the guard for the CLASS, not the cause.
#
# MAX_RACE_SNOW_M above fixes the bug we FOUND. This fixes the bug we HAVEN'T.
#
# Weather cannot plausibly move a race time by more than ~25%. Not heat, not
# mud, not a blizzard -- past that the meet is cancelled. So a wmult outside
# this band is not a weather effect: it is arithmetic that has come off its
# rails, and we have now watched that happen once at 143x.
#
# ★ IT REFUSES, IT DOES NOT CLAMP. 14: "A GUARD THAT REWRITES IS NOT A GUARD"
#   -- np.maximum(wsum, 1e-12) reads as protection and silently changes the
#   answer. Clamping 143x to 1.25x yields a WRONG number wearing a plausible
#   face, and nothing downstream can tell. Refusing yields the UNCORRECTED
#   number, which is a known quantity, plus a ledger line.
#
# ★ IT ANNOUNCES. An alarm that doesn't gate is decoration -- a gate that
#   doesn't announce is worse, because it hides the bug it just caught. The
#   Juneau divide-by-143 sat in the sanity panel for weeks reading "6.81s" and
#   nothing counted it. Every refusal is counted here; weatherRefusalCensus()
#   is how that count reaches a human without reading 39M rows.
#
# ★ IT CANNOT KNOW WHY. That is the point. A future unit bug in soil_moisture,
#   a refit that extrapolates somewhere nobody probed, a spline past its last
#   knot -- this fires on all of them, because it audits the CODOMAIN (what a
#   weather correction is ALLOWED to be) instead of the domain (every way an
#   input can go wrong, which is unbounded). 14: AUDIT THE CODOMAIN.
_WMULT_MIN = 0.75
_WMULT_MAX = 1.25
_WMULT_REFUSED = {"n": 0, "worst": 1.0}       # module-level ledger


def _wmultPlausible(wmult):
    # Purpose   : is this multiplier physically a weather effect?
    # Arguments : wmult -- exp(sum of terms).
    # Output    : bool. False -> the caller must return the row UNCORRECTED.
    # Note      : remembers the WORST, not just a tally. 300 refusals at 1.26x
    #             is a threshold argument; 300 at 143x is a bug. The count alone
    #             cannot tell those apart, so it does not travel alone.
    #             math.log() on a non-positive wmult would raise -- exp() can
    #             only produce positives, so the guard below is unreachable in
    #             practice and cheap insurance if that ever stops being true.
    if _WMULT_MIN <= wmult <= _WMULT_MAX:
        return True
    _WMULT_REFUSED["n"] += 1
    if wmult > 0 and abs(math.log(wmult)) > abs(math.log(_WMULT_REFUSED["worst"])):
        _WMULT_REFUSED["worst"] = wmult
    return False


def weatherRefusalCensus():
    # Purpose   : the line the backfill prints beside no_distance / insane_pace.
    # Output    : a string, or None if nothing was refused.
    # Why       : EVERY ARTIFACT MUST OUTLIVE ITS CONSOLE (14). Without this,
    #             the only evidence of a broken artifact is a 6.81 buried in a
    #             20-row sanity panel -- which is exactly how the last one
    #             survived for weeks.
    n = _WMULT_REFUSED["n"]
    if not n:
        return None
    return (f"  weather refused  {n:>9,}  (wmult outside "
            f"[{_WMULT_MIN}, {_WMULT_MAX}]; worst {_WMULT_REFUSED['worst']:.1f}x"
            f" -- NO-OP on these rows, not a clamp)")


# _weatherEventKey
# Purpose:   rebuild the fitter's _eventKey for one race.
# Arguments: course -- venue name as stored on the meets row;
#            doy    -- day of year (1-366).
# Output:    the string key, or None when either input is missing.
#
# MUST MATCH fit_weather_correction._eventKey EXACTLY:
#       (coalesce(course, '') || '|f' || floor(doy / 14))
#   A mismatch does NOT raise -- every lookup simply misses and the correction
#   silently falls back to the global reference, i.e. the old behaviour. So a
#   typo here reverts the fix invisibly. weatherNormalCensus() is what catches
#   it; check the miss rate after any change to either side.
def _weatherEventKey(course, doy):
    if course is None or doy is None:
        return None
    return f"{course}|f{int(doy) // 14}"


# Hit/miss census for the venue-normal lookup. A high miss rate means the key
# does not match the fitter's and the whole venue-normal path is doing nothing.
_WNORM_CENSUS = {"hit": 0, "miss": 0}


def weatherNormalCensus():
    hit, miss = _WNORM_CENSUS["hit"], _WNORM_CENSUS["miss"]
    total = hit + miss
    if not total:
        return None
    return (f"  weather normals  {hit:>9,} hit  {miss:>9,} miss "
            f"({100.0 * miss / total:.1f}% fell back to the global reference)")


# _weatherReference
# Purpose:   the baseline this race's weather is measured against.
# Arguments: art -- loaded artifact; course, doy -- this race.
# Output:    {feature: value}
# Detail:    This venue's own normal for this slot of the calendar when we have
#            it, the global constant otherwise.
#
#            THE FALLBACK IS THE OLD BEHAVIOUR, NOT A NEUTRAL DEFAULT. A race
#            at an unseen venue is corrected against 55F exactly as before --
#            wrong in the way this change exists to fix, but wrong in a KNOWN
#            direction, which beats refusing to correct at all.
def _weatherReference(art, course, doy):
    norms = art.get("venue_norms")
    if norms:
        key = _weatherEventKey(course, doy)
        if key is not None:
            hit = norms["by_event"].get(key)
            if hit is not None:
                _WNORM_CENSUS["hit"] += 1
                return hit
    _WNORM_CENSUS["miss"] += 1
    return art["reference"]


def _applyWeather(normalized, weather, course, sport, distance_m=None):
    art = _weatherArtifactFor(sport)
    if art is None or not weather:
        return normalized
    if not isRaceWeatherPlausible(weather):     # the cell holds a mountain; the
        return normalized                       # course does not. NO-OP, not a
                                                # partial correction. See above.
    # THE CHANGE: the baseline is this venue's NORMAL for this fortnight -- the
    # same group beta was fitted within -- instead of a single global constant.
    # doy rides in the weather dict, so no signature in the chain had to change.
    ref = _weatherReference(art, course, weather.get("doy"))
    dist_betas = art.get("dist_betas", {})
    dist_ref = art.get("dist_ref", 5000.0)
    dc = (distance_m / dist_ref - 1.0) if distance_m else 0.0   # 0 -> base effect
    total = 0.0

    # temperature: the spline effect, its CURVE SHAPE adjusted for distance (dc).
    tsp = art["splines"].get("apparent_temp")
    t = weather.get("apparent_temp")
    if tsp is not None and t is not None:
        # The spline's own "ref" is replaced by the venue normal for the same
        # reason: it is the x the curve is measured FROM.
        base = ref.get("apparent_temp", tsp["ref"])
        total += _rcsValue(tsp, t, dc) - _rcsValue(tsp, base, dc)

    # mud: soil spline (distance-adjusted shape), scaled by course s_c.
    ssp = art["splines"].get("soil")
    soil = weather.get("soil")
    if ssp is not None and soil is not None:
        s_c = art.get("soil_sensitivity", {}).get(course, {}).get("s", 1.0)
        # SOIL MAY NOW BE DOUBLE-BASELINED. s_c already encodes "how muddy
        # does this course get", so subtracting a venue soil normal removes the
        # same venue property twice. Using the normal and leaving s_c alone is
        # the conservative reading -- measure after a run, since it may want
        # s_c refitting.
        base = ref.get("soil", ssp["ref"])
        total += s_c * (_rcsValue(ssp, soil, dc) - _rcsValue(ssp, base, dc))

    # wind / precip / snow: linear beta + scalar distance interaction.
    for f in art.get("linear_features", ()):
        v = weather.get(f)
        if v is not None:
            total += (art["betas"][f] + dist_betas.get(f, 0.0) * dc) * (v - ref[f])

    if total == 0.0:                                 # reference weather -> exact no-op
        return normalized
    wmult = math.exp(total)
    if not _wmultPlausible(wmult):
        return normalized                     # uncorrected beats wrong
    return normalized / wmult


# ------------------------------------------------------------------ #
# MAIN ENTRY POINT
# ------------------------------------------------------------------ #

# normalizeResult
# Purpose: Take a raw result row and return the normalized result the speed-rating
#          engine consumes. Now threads the OPTIONAL date + track geometry through
#          so geometry and era apply when their data is present.
# Arguments:
#   time_seconds: raw finish time in seconds.
#   distance:     raw distance from the meets table.
#   grade:        grade string (results table).
#   gender:       gender string (athletes table).
#   date:         race date (for era's season). OPTIONAL — None disables era.
#   track_length: track length in metres (for geometry). OPTIONAL — None ->
#                 400m-flat reference (no length correction).
#   track_type:   track type string (for banking). OPTIONAL — non-"Banked" -> no
#                 banking correction.
#   sport:        "XC" | "TF" for the banded era artifact. OPTIONAL — None ->
#                 no banded era correction (legacy pickles still apply).
#   event_short:  the event string (TF) for band classification. OPTIONAL.
# Output: dict {"normalized_time", "pool", "drop"}.
def normalizeResult(time_seconds, distance, grade, gender,
                    date=None, track_length=None, track_type=None,
                    sport=None, event_short=None,
                    weather=None, course=None, pool=None) -> dict:
    # ★ AN ALREADY-RESOLVED POOL WINS. getPool(grade, gender) reads the RAW
    #   grade and nothing else -- not the school, not the source, not the
    #   season verdict, not grade_fix. It is the weakest of the pool
    #   derivations in this codebase and it was the one deciding the
    #   NORMALISATION, which since per-pool anchors also decides the SCALE.
    #
    #   Measured: person 26055806 carries raw grade 7 and a corroborated
    #   verdict of 9. backfill_normalize resolved him hs correctly for its
    #   own census, then passed the raw grade here; getPool said ms, his
    #   5000s were written at the 3200 anchor (factor 0.605), and the engine
    #   read them as hs. He finished the season at 196.3 and topped the high
    #   school board as a middle schooler.
    #
    # ⚠ OPTIONAL, AND DEFAULTING TO THE OLD PATH ON PURPOSE. Callers that do
    #   not resolve pools -- and there are several -- behave exactly as they
    #   did. Only a caller that has done the work gets to override.
    pool = pool or getPool(grade, gender)
    if pool == "unknown_level":
        return {"normalized_time": None, "pool": "unknown_level", "drop": True}

    distance_meters = metersFromDistance(distance)

    # Derive the season from the date for era (calendar year; matches the era
    # fitter's _seasonOf). None date -> season None -> era no-op.
    season = date.year if date is not None else None

    normalized_time = normalizeTime(
        time_seconds, distance_meters, pool,
        season=season, track_length=track_length, track_type=track_type,
        sport=sport, event_short=event_short,
        weather=weather, course=course,          # per-race weather (None -> no-op)
    )

    if normalized_time is None:
        return {"normalized_time": None, "pool": pool, "drop": True}

    return {"normalized_time": normalized_time, "pool": pool, "drop": False}