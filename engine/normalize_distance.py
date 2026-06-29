# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Speed Rating Engine
# Date: 6/2/2026
# File Title: normalize_distance.py
# Purpose: Classifies Rresults into competitive pools and normalizes time
#          to a 5k equivalent for comparison across distances (not terrain).
#
# Update 6/18/2026:
#   normalizeTime() now uses per-pool 2D cubic splines fitted by
#   fit_distance_exponent.py instead of the placeholder 1.06 exponent.
#   Splines are loaded ONCE at module level (when this file is first
#   imported) so millions of normalizeTime() calls share one in-memory
#   object rather than reading from disk every call.
#   If the spline file doesn't exist yet, falls back to DISTANCE_EXPONENT_BY_POOL.

import os
import math
import pickle


# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# Normalizes the distance by these exponents per pool. Now they are set
# at a default 1.06, but will be changed later.
# Formula: T2 = T1 * (D2 / D1) ^ exponent.
# T2 time of 5k normalization, T1 is time of original race, D2 is 5k distance
# D1 is original distance, exponent is based on pool.
# TODO: fit these empirically per pool from athletes who raced multiple
# distances in the same season. Once track data is available, refit
# using the full aerobic range (800m to 10K) for a more reliable curve.
# TODO: era adjustment (inflation) is a separate multiplier applied
# later — not part of the exponent. See V3 scope notes.
DISTANCE_EXPONENT_BY_POOL = {
    "college_m": 1.06,
    "college_f": 1.06,
    "hs_m":      1.06,
    "hs_f":      1.06,
    "ms_m":      1.06,
    "ms_f":      1.06,
    "unknown":   1.06,
    # Athletes where level is known but gender wasn't recorded.
    # Using same placeholder exponent — will be fitted later if enough
    # data exists, otherwise falls back to DEFAULT_EXPONENT.
    "college_unknown_gender": 1.06,
    "hs_unknown_gender":      1.06,
    "ms_unknown_gender":      1.06,
}

# Maps athletic.net eventShort strings to distance in meters.
# Only events 800m and above — shorter distances extrapolate
# too badly with the power law formula to be useful for XC prediction.
# Events not in this dict get stored in results_tf but normalized_time
# stays NULL.
# Steeplechase distances are approximate — the barriers add effort
# beyond the flat distance, so treat normalization as a rough estimate.
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

    # Indoor variants
    "1000m":    1000,
}

# Manually curated list of known banked indoor tracks and their
# conversion factors relative to a flat outdoor track.
# A banked 200m indoor track is ~0.5-1% faster than flat outdoor
# due to the banked turns reducing energy loss.
# Source: empirical data from elite performances, updated manually.
# Key is the facility name as it appears in athletic.net Location.Name.
# Value is a multiplier — 0.995 means 0.5% faster than flat.
# TODO: fit these empirically once TF data is in.
BANKED_TRACK_CORRECTIONS = {
    "Reggie Lewis Track and Athletic Center":   0.990,
    "Ocean Breeze Athletic Complex":            0.992,
    "Albuquerque Convention Center":            0.993,
    "Findlay Toyota Center":                    0.993,
    "Sportsplex at Utica":                      0.994,
    "Washington Convention Center":             0.993,
    "JDL Fast Track":                           0.992,
    "New Balance Track and Field Center":       0.990,
    "Armory Track":                             0.991,
    "Dempsey Indoor":                           0.993,
}

# Default exponenet if pool isn't found in dictionary.
DEFAULT_EXPONENT = 1.06

# 5k distance normalization, flat normalization is 0 dificulty.
TARGET_DISTANCE_METERS = 5000
# TODO: plug in track conversion anchor here once track scraper is built.
# For each athlete, bracket their XC season with the spring track season
# before and after, average those performances, and use that as a
# calibration anchor for their personal distance curve.
FLAT_COURSE_DIFFICULTY = 0.0

# Path to the fitted spline file produced by fit_distance_exponent.py.
_SPLINE_FILE = os.path.join(os.path.dirname(__file__), "..", "engine", "data", "distance_spline.pkl")

# ------------------------------------------------------------------ #
# MODULE-LEVEL SPLINE LOADING
# ------------------------------------------------------------------ #


# _loadSplines
# Purpose: Load the spline file from disk. Returns None if not found
#          so the rest of the module can fall back gracefully.
# Arguments: None (uses module-level _SPLINE_FILE path).
# Output: dict of splines, or None.
def _loadSplines():
 
    if not os.path.exists(_SPLINE_FILE):
        print(
            f"[normalize_distance] Spline file not found at {_SPLINE_FILE}. "
            "Falling back to placeholder exponent. "
            "Run engine/fit_distance_exponent.py to generate splines."
        )
        return None
 
    with open(_SPLINE_FILE, "rb") as f:
        splines = pickle.load(f)
 
    print(f"[normalize_distance] Splines loaded from {_SPLINE_FILE}. "
          f"Pools: {[k for k in splines if k != 'global']}")
    return splines

# Load splines ONCE when this module is first imported.
# Why module level: normalizeTime() is called millions of times during
# an engine run. Loading from disk inside the function would mean
# millions of file reads + pickle deserializations. Loading here means
# one read, one deserialization, then the object lives in memory for
# the entire run.
#
# _SPLINES is either:
#   - A dict mapping pool -> fitted SmoothBivariateSpline (normal case)
#   - None if the spline file doesn't exist yet (falls back to exponent)
_SPLINES = _loadSplines()

# ------------------------------------------------------------------ #
# POOL CLASSIFICATION
# ------------------------------------------------------------------ #


# GRADE_TO_LEVEL maps grade values from the database to a competition
# level string. Grades not in this map get classified as "unknown_level".
# College grades are strings (Fr/So/Jr/Sr/RS), HS/MS are integers as strings.
GRADE_TO_LEVEL = {
    "1": "ms", "2": "ms", "3": "ms", "4": "ms",
    "5": "ms", "6": "ms", "7": "ms", "8": "ms",
    "9": "hs", "10": "hs", "11": "hs", "12": "hs",
    "Fr": "college", "So": "college", "Jr": "college",
    "Sr": "college", "RS": "college",
    "7-8": "ms", "9-10": "hs", "11-12": "hs"
}

# getPool
# Purpose: Gets the level/gender pool the athlete attatched to the result
#          is in. e.g "hs_f".
# Arguments: 
#           grade: the grade of the athlete attatched to the result.
#           gender: the gender of the athlete attatched to the result.
# Output: Returns a string containing the level/gender pool the athlete
#          attatched to the result is in.
def getPool(grade: str, gender: str) -> str:

    #  Gets competitive level from grade level with stripped whitespace.
    level = GRADE_TO_LEVEL.get(str(grade).strip())

    # If grade isn't in our map, try to infer from context later.
    # For now return "unknown_level" so we can handle these separately
    # rather than dropping them entirely.
    if level is None:
        return "unknown_level"
    
    # Normalize gender to uppercase for comparison.
    g = str(gender).strip().upper()
    
    if g == "M":
        return f"{level}_m"
    elif g == "F":
        return f"{level}_f"
    # Miscellaneous pool for results with missing or nonbinary gender.
    # We can still normalize this time using the default exponent.
    else:
        return f"{level}_unknown_gender"

# metersFromDistance
# Purpose: Converts a distance value from the database into meters for normalization.
# Arguments:
#           distance: the original distance value, which may be in miles or meters.
# Output: Returns the distance in meters as a float, or None if the input is missing or invalid.
def metersFromDistance(distance) -> float | None:

    # Convert to float so we can do math on it.
    # try/except catches cases where distance is None, empty string,
    # or some other non-numeric value that can't be converted.
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return None
    
    # Probably already in meters if it's a large number, return as is.
    if d >= 50:
        return d
    # Probably in miles if it's a small number, convert to meters.
    if d > 0:
        return round(d * 1609.34, 1)
    
    return None

# ------------------------------------------------------------------ #
# TIME NORMALIZATION
# ------------------------------------------------------------------ #

# normalizeTime
# Purpose: Convert a race time at any distance to a flat 5K equivalent.
#
#   If splines are loaded (normal case after fit_distance_exponent.py runs):
#     Uses 2D cubic spline — more accurate, captures physiological zones.
#     Formula:
#       log_x     = log(5000 / distance)           — ratio to 5K
#       log_z     = log((distance + 5000) / 2)     — absolute location
#       log_ratio = spline(log_x, log_z)            — predicted log(T_5k/T_dist)
#       normalized = time * exp(log_ratio)
#
#   If splines are not loaded (fallback):
#     Uses per-pool power law exponent (placeholder 1.06).
#     Formula: normalized = time * (5000 / distance) ^ exponent
#
# Arguments:
#   time_seconds:    original time in seconds.
#   distance_meters: original distance in meters.
#   pool:            competitive pool, e.g. "college_m", "hs_f".
# Returns: Normalized flat 5K equivalent time in seconds, or None if invalid.
def normalizeTime(time_seconds: float, distance_meters: float, pool: str) -> float | None:

    if not time_seconds or not distance_meters:
        return None
    if time_seconds <= 0 or distance_meters <= 0:
        return None
 
    # Use splines if available, otherwise fall back to exponent.
    if _SPLINES is not None:
        return _normalizeWithSpline(time_seconds, distance_meters, pool)
    else:
        return _normalizeWithExponent(time_seconds, distance_meters, pool)
    
# _normalizeWithSpline
# Purpose: Normalize using the fitted 2D spline for this pool.
#          Falls back to global spline if pool has no dedicated spline.
# Arguments:
#   time_seconds:    original time in seconds.
#   distance_meters: original distance in meters.
#   pool:            competitive pool string.
# Returns: Normalized time in seconds as float.
def _normalizeWithSpline(time_seconds: float, distance_meters: float, pool: str) -> float:
 
    # Use pool-specific spline if available, otherwise global.
    spline = _SPLINES.get(pool) or _SPLINES["global"]
 
    # Distance ratio: how far is this race relative to 5K?
    # log(5000/distance): positive if race is shorter than 5K, negative if longer.
    log_x = math.log(TARGET_DISTANCE_METERS / distance_meters)
 
    # Absolute location: where on the distance spectrum is this pair?
    log_z = math.log((distance_meters + TARGET_DISTANCE_METERS) / 2)
 
    # spline() returns a 2D numpy array even for scalar inputs — float() extracts the value.
    # log_ratio = log(T_5k / T_dist), so T_5k = T_dist * exp(log_ratio).
    log_ratio = float(spline(log_x, log_z))
 
    normalized = time_seconds * math.exp(log_ratio)
    return round(normalized, 2)
 
 
# _normalizeWithExponent
# Purpose: Fallback normalization using the placeholder power law exponent.
#          Used when spline file hasn't been generated yet.
# Arguments:
#   time_seconds:    original time in seconds.
#   distance_meters: original distance in meters.
#   pool:            competitive pool string.
# Returns: Normalized time in seconds as float.
def _normalizeWithExponent(time_seconds: float, distance_meters: float, pool: str) -> float:
 
    # dict.get(key, default) returns the default if key not in dict.
    exponent = DISTANCE_EXPONENT_BY_POOL.get(pool, DEFAULT_EXPONENT)
 
    normalized = time_seconds * (TARGET_DISTANCE_METERS / distance_meters) ** exponent
    return round(normalized, 2)


# ------------------------------------------------------------------ #
# MAIN ENTRY POINT
# ------------------------------------------------------------------ #

# normalizeResults
# Purpose: Takes a raw result row from the db and return a normalized
#          result the speed rating engine can deal with.
# Arguments:
#           time_seconds: raw finish time in seconds
#           distance: raw distance from meets table
#           grade: grade string from results table
#           gender: gender string from athletes table
# Output: Returns a dict with keys:
#         "normalized_time" (float or None),
#         "pool" (string),
#         "drop" (bool — True means exclude from speed rating engine)
def normalizeResult(time_seconds, distance, grade, gender) -> dict:

    # Classify into a pool first.
    pool = getPool(grade, gender)

    # Drop results where we can't classify pool.
    if pool == "unknown_level":
        # TODO: infer pool from school name as fallback before dropping.
        # Use known college suffixes (University, College, State, CC) to
        # classify as college, known club keywords (Track Club, TC, Elite)
        # to drop, everything else as hs. Affects ~60K rows (<1% of data).
        # Not worth building now — revisit before V1 ships.
        return {"normalized_time": None, "pool": "unknown_level", "drop": True}
    
    # Convert distance to meters.
    distance_meters = metersFromDistance(distance)

    # Normalize the time.
    normalized_time = normalizeTime(time_seconds, distance_meters, pool)

    # If normalization failed for any reason, mark as a drop.
    if normalized_time is None:
        # Dictionary with key-value pairs.
        return {"normalized_time": None, "pool": pool, "drop": True}
    
    return {
        "normalized_time": normalized_time,
        "pool": pool,
        "drop": False
    }