# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Speed Rating Engine
# Date: 6/17/2026
# File Title: fit_distance_exponent.py
# Purpose: Fit per-pool natural cubic splines that replace the placeholder
#          DISTANCE_EXPONENT_BY_POOL constants in normalize_distance.py.
#
# Background:
#   normalize_distance.py currently uses a fixed exponent (1.06) per pool:
#     normalized_time = time * (5000 / distance) ^ exponent
#
#   A single global exponent assumes the distance-time relationship is a
#   straight line in log-log space. In reality it's a curve — different
#   energy systems dominate at different distances (anaerobic at 800m,
#   mixed at 3K, purely aerobic at 8K+). A spline captures this curve.
#
# What changes in normalize_distance.py:
#   Instead of looking up a constant exponent per pool, normalizeTime()
#   loads the fitted splines from disk and calls:
#     log_x = log(distance / 5000)          — distance ratio
#     log_z = log((distance + 5000) / 2)    — absolute distance location
#     log_ratio = spline(log_x, log_z)
#     normalized_time = time * exp(-log_ratio)
#
# Data sources:
#   - XC pairs:          results JOIN meets JOIN athletes
#                        Always outdoor, always clean.
#   - Outdoor TF pairs:  results_tf JOIN meets_tf JOIN athletes
#                        Filter meets_tf.is_indoor = 0.
#   - Indoor TF pairs:   EXCLUDED.
#                        TODO (V2): refit including indoor once
#                        BANKED_TRACK_CORRECTIONS are applied to normalized_time
#                        in normalize_distance.py. Indoor times are currently
#                        biased and would corrupt the spline.
#
# Pool derivation:
#   Pool = buildPool(grade, gender) from feature_extraction.py.
#   e.g. "hs_m", "college_f", "ms_unknown_gender".
#   Both XC and TF need JOIN athletes to get gender.
#
# Output:
#   engine/data/distance_spline.pkl — dict mapping pool -> fitted spline.
#   Also contains "global" key for pools with insufficient data.
#   normalize_distance.py loads this file at startup.
#
# Minimum pairs per pool:
#   MIN_PAIRS_FOR_POOL_SPLINE = 500.
#   Pools below this threshold fall back to the global spline.
#   Rationale: natural cubic spline has ~5-7 parameters. 500 pairs gives
#   ~70+ observations per parameter — enough for noise to average out.

import sys
import os
import math
import pickle
import numpy as np
import psycopg2.extras
from scipy.interpolate import SmoothBivariateSpline
 
sys.path.insert(0, "scripts")
sys.path.insert(0, "model")
from database import getConn
from feature_extraction import buildPool

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #
 
# Maximum days between two races in a pair.
MAX_DAYS_APART = 21
 
# Minimum distance difference between two races in a pair.
# Guards against floating point equality on same-distance pairs.
MIN_DISTANCE_DIFF_METERS = 10
 
# Minimum distance to include.
# Power law breaks down below 800m (anaerobic systems dominate).
MIN_DISTANCE_METERS = 800
 
# Miles to meters conversion.
# XC meets.distance stores some values in miles (e.g. 1.0, 2.0, 3.1).
# Any distance < 100 is treated as miles.
MILES_TO_METERS = 1609.34
 
# Target distance for normalization (5K).
TARGET_DISTANCE_METERS = 5000.0
 
# Minimum pairs a pool needs to get its own spline.
# Below this falls back to global spline.
MIN_PAIRS_FOR_POOL_SPLINE = 500
 
# Output paths.
OUTPUT_DIR  = "engine/data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "distance_spline.pkl")

# ------------------------------------------------------------------ #
# STEP 1 — LOAD PAIRS
# ------------------------------------------------------------------ #


# loadAllPairs
# Purpose: Load race pairs from XC and outdoor TF, derive pool for each,
#          return grouped by pool.
# Arguments: None.
# Returns: Dict mapping pool -> list of pair dicts.
#          Each pair dict has: pool, distance1, distance2, time1, time2.
def loadAllPairs() -> tuple[list[dict], list[dict]]:

    # Returns two dicts: xc_by_pool, tf_by_pool.
    # Kept separate so we can compare XC vs TF splines before merging.

    print("Loading XC pairs...")
    xc_pairs = _loadXCPairs()
    print(f"  {len(xc_pairs):,} valid XC pairs")

    print("Loading TF pairs...")
    tf_pairs = _loadTFPairs()
    print(f"  {len(tf_pairs):,} valid TF pairs")

    # Group each sport's pairs by pool separately.
    xc_by_pool = _groupByPool(xc_pairs)
    tf_by_pool = _groupByPool(tf_pairs)

    return xc_by_pool, tf_by_pool

# _loadXCPairs
# Purpose: Load XC pairs from results + meets tables.
#          Converts miles to meters where needed.
#          Filters out junk distances and pairs that are too similar.
# Arguments: None.
# Returns: List of dicts.
def _loadXCPairs() -> list[dict]:

    rows = _queryXCPairs()
    return _filterAndConvertPairs(rows, is_xc=True)

# _queryXCPairs
# Purpose: Raw DB query for XC pairs. Self-joins results on athlete_id.
#          Joins meets to get distance for each race.
# Arguments: None.
# Returns: List of raw row dicts.
def _queryXCPairs() -> list[dict]:

    with getConn() as conn:

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(_buildXCPairsQuery())
        return [dict(row) for row in cursor.fetchall()]
    
# _queryTFPairs
# Purpose: Raw DB query for TF pairs. Self-joins results_tf on athlete_id.
#          Joins meets_tf to get distance_meters for each race.
# Arguments: None.
# Returns: List of raw row dicts.
def _queryTFPairs() -> list[dict]:

    with getConn() as conn:

        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(_buildTFPairsQuery())
        return [dict(row) for row in cursor.fetchall()]
    
# _buildXCPairsQuery
# Purpose: SQL code to query for XC pairs.
# Arguments: None.
# Returns: SQL string.
def _buildXCPairsQuery() -> str:

    return f"""
        SELECT
            r1.athlete_id,
            r1.date            AS date1,
            r2.date            AS date2,
            m1.distance        AS distance1,
            m2.distance        AS distance2,
            r1.normalized_time AS time1,
            r2.normalized_time AS time2
        FROM results r1
        -- Self-join: match each race with every later race by the same athlete.
        -- r2.date > r1.date ensures we only get each pair once.
        JOIN results r2
            ON r1.athlete_id = r2.athlete_id
            AND r2.date > r1.date
        -- Get distance for each race from meets.
        JOIN meets m1 ON r1.div_id = m1.div_id
        JOIN meets m2 ON r2.div_id = m2.div_id
        WHERE
            r1.normalized_time IS NOT NULL
            AND r2.normalized_time IS NOT NULL
            AND r1.normalized_time > 0
            AND r2.normalized_time > 0
            AND m1.distance IS NOT NULL
            AND m2.distance IS NOT NULL
            AND m1.distance > 0
            AND m2.distance > 0
            AND r1.date IS NOT NULL AND r1.date != ''
            AND r2.date IS NOT NULL AND r2.date != ''
            AND (r2.date::date - r1.date::date) <= {MAX_DAYS_APART}
    """

# _buildTFPairsQuery
# Purpose: Builds SQL query copde for TF pairs.
# Arguments: None.
# Returns: SQL string.
def _buildTFPairsQuery() -> str:

    return f"""
        SELECT
            r1.athlete_id,
            r1.date                AS date1,
            r2.date                AS date2,
            m1.distance_meters     AS distance1,
            m2.distance_meters     AS distance2,
            r1.normalized_time     AS time1,
            r2.normalized_time     AS time2
        FROM results_tf r1
        JOIN results_tf r2
            ON r1.athlete_id = r2.athlete_id
            AND r2.date > r1.date
        JOIN meets_tf m1 ON r1.div_id = m1.div_id
        JOIN meets_tf m2 ON r2.div_id = m2.div_id
        WHERE
            r1.normalized_time IS NOT NULL
            AND r2.normalized_time IS NOT NULL
            AND r1.normalized_time > 0
            AND r2.normalized_time > 0
            AND m1.distance_meters IS NOT NULL
            AND m2.distance_meters IS NOT NULL
            AND m1.distance_meters > 0
            AND m2.distance_meters > 0
            AND r1.date IS NOT NULL AND r1.date != ''
            AND r2.date IS NOT NULL AND r2.date != ''
            AND (r2.date::date - r1.date::date) <= {MAX_DAYS_APART}
    """

# _filterAndConvertPairs
# Purpose: Shared filtering logic for both XC and TF pairs.
#          1. Converts XC miles to meters where distance < 100 (TF pre-converted).
#          2. Filters out distances below MIN_DISTANCE_METERS (sprints, junk).
#          3. Filters out pairs where distances are too similar.
# Arguments:
#   rows   — raw list of dicts from a query function.
#   is_xc  — True for XC (needs miles conversion), False for TF.
# Returns: Filtered list of dicts with distances always in meters.
def _filterAndConvertPairs(rows: list[dict], is_xc: bool) -> list[dict]:

    valid = []

    for row in rows:

        # Converts distance of each reace results to meters.
        d1 = _toMeters(row["distance1"], is_xc)
        d2 = _toMeters(row["distance2"], is_xc)

        # Skip distances below minimum (sprints, field events, junk).
        if d1 < MIN_DISTANCE_METERS or d2 < MIN_DISTANCE_METERS:
            continue

        # Skip pairs where distances are too similar — no signal.
        if abs(d2 - d1) < MIN_DISTANCE_DIFF_METERS:
            continue

        # Derive pool from grade and gender.
        # grade1 is from r1 — both races are by the same athlete so
        # grade may differ slightly but pool will be the same.
        pool = getPool(row["grade1"], row["gender"])

        # Drop unknown_level — can't classify, not useful for fitting.
        if pool == "unknown_level":
            continue

        # Write converted distances back into the row.
        row["distance1"] = d1
        row["distance2"] = d2
        row["pool"]      = pool
        valid.append(row)
 
    return valid

# _toMeters
# Purpose: Convert a distance value to meters.
#          For TF: already meters, return as-is.
#          For XC: if distance < 100, treat as miles and convert.
#                  if distance >= 100, already meters, return as-is.
# Arguments:
#   distance — raw distance value from DB.
#   is_xc    — True if this is an XC distance (may be in miles).
# Returns: Distance in meters as a float.
def _toMeters(distance: float, is_xc: bool) -> float:

    # TF distance pre-converted.
    if not is_xc:
        return float(distance)
    
    if distance < 100:
        # Miles to meters.
        return distance * MILES_TO_METERS
    
    # Already meters.
    return float(distance)

# _groupByPool
# Purpose: Group a flat list of pairs into a dict keyed by pool.
# Arguments: pairs — flat list of pair dicts each containing a pool key.
# Returns: Dict mapping pool -> list of pair dicts.
def groupByPool(pairs: list[dict]) -> dict[str, list[dict]]:

    # Grouped is a dict with string as value, list[dict] as pairs.
    grouped: dict[str, list[dict]] = {}

    # Groups each pair into it's pool
    for pair in pairs:

        # Get's pairs pool
        pool = pair["pool"]

        # If first entry into pool creates list.
        if pool not in grouped:
            grouped[pool] = []

        grouped[pool].append(pair)

    return grouped

# ------------------------------------------------------------------ #
# STEP 2 — LOG RATIOS
# ------------------------------------------------------------------ #


# toLogRatios
# Purpose: Convert a list of pairs to (log_x, log_z, log_y) arrays.
#
#          Why log ratios?
#          Power law: T = C * D^exponent, C cancels for same athlete:
#            T2/T1 = (D2/D1)^exponent
#          Taking logs:
#            log(T2/T1) = exponent * log(D2/D1)
#
#          Why two inputs (log_x AND log_z)?
#          A 1D spline only sees log(D2/D1) — the distance ratio.
#          So 800m vs 1600m and 2500m vs 5000m look identical (both
#          have ratio = 2). But they're in different physiological zones —
#          anaerobic vs aerobic — and the conversion factor is different.
#
#          Adding log_z = log(D_avg) where D_avg = (D1+D2)/2 tells the
#          spline WHERE on the distance spectrum this pair sits.
#          Now 800m vs 1600m gives (log(2), log(1131)) and 2500m vs
#          5000m gives (log(2), log(3536)) — same ratio, different zone.
#          The 2D spline can give different predictions for each.
#
# Arguments: pairs — list of pair dicts.
# Returns: Tuple of (log_x, log_z, log_y) numpy arrays where:
#          log_x = log(D2/D1)       — distance ratio
#          log_z = log((D1+D2)/2)   — absolute distance location
#          log_y = log(T2/T1)       — time ratio (target)
def toLogRatios(pairs: list[dict]) -> tuple[np.ndarray, np.ndarray]:

    log_x = []
    log_z = []
    log_y = []

    for pair in pairs:
        d1, d2 = pair["distance1"], pair["distance2"]
        t1, t2 = pair["time1"], pair["time2"]
 
        # Skip degenerate values.
        if d1 <= 0 or d2 <= 0 or t1 <= 0 or t2 <= 0:
            continue
        
        log_x.append(math.log(d2 / d1))
        log_z.append(math.log((d1 + d2) / 2))   # average distance location
        log_y.append(math.log(t2 / t1))
 
    return np.array(log_x), np.array(log_z), np.array(log_y)


# ------------------------------------------------------------------ #
# STEP 3 — FIT SPLINES
# ------------------------------------------------------------------ #


# fitAllSplines
# Purpose: Fit one spline per pool plus a global spline.
#          Pools below MIN_PAIRS_FOR_POOL_SPLINE are skipped —
#          normalize_distance.py falls back to "global" for those.
# Arguments: pairs_by_pool — dict from loadAllPairs.
# Returns: Dict mapping pool -> fitted spline, always including "global".
def fitAllSplines(pairs_by_pool: dict[str, list[dict]]) -> dict:
    
    splines = {}

    # Fit global spline first from all pairs combined
    all_pairs = [p for pairs in pairs_by_pool.values() for p in pairs]
    print(f"Fitting global spline from {len(all_pairs):,} total pairs...")
    splines["global"] = _fitOneSpline(all_pairs)
    print(f"  Global spline fitted.\n")

    # Fit per-pool splines.
    for pool, pairs in sorted(pairs_by_pool.items()):

        if len(pairs) < MIN_PAIRS_FOR_POOL_SPLINE:
            print(f"  {pool}: {len(pairs):,} pairs — below threshold, will use global.")
            continue

        print(f"  {pool}: fitting spline from {len(pairs):,} pairs...")
        splines[pool] = _fitOneSpline(pairs)

        print(f"  {pool}: done.")
 
    return splines

# _fitOneSpline
# Purpose: Fit a 2D cubic smoothing spline through (log_x, log_z, log_y).
#
# --- WHY A SPLINE INSTEAD OF A POLYNOMIAL ---
#
#   A degree-3 polynomial fits ONE cubic equation across the entire distance
#   range. If the curve has a different shape at 800m-1600m (anaerobic zone)
#   vs 5K-10K (aerobic zone), the polynomial has to compromise — pulled toward
#   fitting both zones with one equation and does neither perfectly.
#
#   A spline instead:
#     1. Divides the input space into segments at "knot" points.
#     2. Fits a separate cubic polynomial in each segment.
#     3. Forces segments to connect smoothly at knots — matching
#        value, slope, and curvature at each junction.
#
#   Each zone gets its own curve fitted locally, without being pulled
#   by data from other zones. The result looks like one continuous surface.
#
# --- WHY 2D (log_x AND log_z) ---
#
#   A 1D spline only sees log(D2/D1) — the distance ratio.
#   800m vs 1600m and 2500m vs 5000m both have ratio=2, so the 1D
#   spline treats them identically. But they're in different physiological
#   zones. Adding log_z = log(D_avg) tells the spline WHERE on the
#   distance spectrum this pair sits, so it can give different predictions
#   for the same ratio at different absolute distances.
#
# --- WHY k=3 (CUBIC) SPECIFICALLY ---
#
#   At each knot we enforce 3 smoothness conditions:
#     - Same value      (no jump)
#     - Same slope      (no corner)
#     - Same curvature  (no sudden bend)
#
#   Degree 3 is the minimum that satisfies all three. Degree 4+ risks
#   Runge's phenomenon: wild oscillation between knots.
#
# --- SMOOTHING PARAMETER s ---
#
#   s=0 fits through every point exactly — overfits noise.
#   s=len(pairs)*0.1 allows light smoothing — averages noise while
#   preserving the true curve shape.
#
# Arguments: pairs — list of pair dicts for one pool.
# Returns: Fitted SmoothBivariateSpline object.
def _fitOneSpline(pairs: list[dict]):
    
    log_x, log_z, log_y = toLogRatios(pairs)

    smoothing = len(pairs) * 0.1
 
    # SmoothBivariateSpline takes two input arrays (x, y in scipy's notation,
    # which we call log_x and log_z) and one output array (log_y).
    # kx=3, ky=3: cubic in both dimensions.
    # No sorting needed — SmoothBivariateSpline handles scattered data.
    return SmoothBivariateSpline(log_x, log_z, log_y, kx=3, ky=3, s=smoothing)

# ------------------------------------------------------------------ #
# STEP 4 — COMPARE XC VS TF
# ------------------------------------------------------------------ #
 
# compareXCvsTF
# Purpose: Fit separate XC-only and TF-only splines per pool, then
#          compare their predictions at reference distances side by side.
#          If predictions diverge by more than ~2%, XC and TF should
#          have separate splines. If they're close, one combined spline
#          is fine.
#
#          This is a DIAGNOSTIC only — the combined splines from
#          fitAllSplines() are what get saved to disk. If divergence
#          is large, we'll revisit the methodology.
#
# Arguments:
#   xc_by_pool — dict from loadAllPairs (XC pairs grouped by pool).
#   tf_by_pool — dict from loadAllPairs (TF pairs grouped by pool).
# Returns: None. Prints comparison table.
def compareXCvsTF(xc_by_pool: dict, tf_by_pool: dict):
 
    print("\n" + "=" * 70)
    print("XC vs TF SPLINE COMPARISON")
    print("=" * 70)
    print("Threshold: >2% divergence suggests separate splines may be needed.\n")
 
    # Find pools that have enough data in BOTH XC and TF to compare.
    # | combines two sets and removes duplicates.
    all_pools = set(xc_by_pool.keys()) | set(tf_by_pool.keys())
 
    for pool in sorted(all_pools):
 
        xc_pairs = xc_by_pool.get(pool, [])
        tf_pairs = tf_by_pool.get(pool, [])
 
        # Need minimum pairs in both to make comparison meaningful.
        if len(xc_pairs) < MIN_PAIRS_FOR_POOL_SPLINE:
            print(f"  {pool}: XC only {len(xc_pairs):,} pairs — skipping comparison.")
            continue
        if len(tf_pairs) < MIN_PAIRS_FOR_POOL_SPLINE:
            print(f"  {pool}: TF only {len(tf_pairs):,} pairs — skipping comparison.")
            continue
 
        print(f"  {pool} — XC: {len(xc_pairs):,} pairs, TF: {len(tf_pairs):,} pairs")
 
        # Fit separate splines for this pool.
        xc_spline = _fitOneSpline(xc_pairs)
        tf_spline = _fitOneSpline(tf_pairs)
 
        # Compare predictions at reference distances.
        _printSplineComparison(xc_spline, tf_spline, pool)
 
 
# _printSplineComparison
# Purpose: Print a side-by-side table comparing XC spline vs TF spline
#          predictions at several reference distances, using a 1000s 5K
#          time as the baseline for conversion.
# Arguments:
#   xc_spline — fitted spline from XC pairs.
#   tf_spline — fitted spline from TF pairs.
#   pool      — pool name (just for display).
# Returns: None.
def _printSplineComparison(xc_spline, tf_spline, pool: str):
 
    # Reference distances to check.
    check_distances = [800, 1500, 1600, 3000, 3200, 8000, 10000]
 
    # Use 1000s (16:40) as a round reference 5K time.
    reference_time = 1000.0
 
    print(f"    {'Distance':>10} {'XC (s)':>10} {'TF (s)':>10} {'Diff':>8} {'Diff%':>8}")
    print(f"    {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")
 
    for dist in check_distances:
 
        xc_time = _predictFromSpline(xc_spline, dist, reference_time)
        tf_time = _predictFromSpline(tf_spline, dist, reference_time)
 
        diff    = xc_time - tf_time
        diff_pct = (diff / tf_time) * 100 if tf_time > 0 else 0.0
 
        # Flag large divergences.
        flag = " <<<" if abs(diff_pct) > 2.0 else ""
        print(f"    {dist:>10.0f} {xc_time:>10.1f} {tf_time:>10.1f} {diff:>+8.1f} {diff_pct:>+7.1f}%{flag}")
 
    print()

# _predictFromSpline
# Purpose: Given a fitted spline and a target distance, predict the
#          time at that distance given a known 5K time.
#
#   Direction: we know T_5k and want T_dist.
#   The spline was fitted on pairs where log_x = log(D2/D1) and
#   log_y = log(T2/T1). We set D1=5000, D2=dist, so:
#     log_x = log(dist / 5000)
#     log_z = log((dist + 5000) / 2)
#     log_ratio = spline(log_x, log_z) = log(T_dist / T_5k)
#     T_dist = T_5k * exp(log_ratio)
#
# Arguments:
#   spline         — fitted SmoothBivariateSpline.
#   dist           — target distance in meters.
#   reference_time — known 5K time in seconds.
# Returns: Predicted time at dist in seconds.
def _predictFromSpline(spline, dist: float, reference_time: float) -> float:
 
    # log of the distance ratio from 5K to dist.
    log_x = math.log(dist / TARGET_DISTANCE_METERS)
    # log of the average of the two distances (absolute location).
    log_z = math.log((dist + TARGET_DISTANCE_METERS) / 2)
 
    # spline() returns a 2D array even for scalar inputs — use float().
    log_ratio = float(spline(log_x, log_z))
 
    # T_dist = T_5k * exp(log(T_dist / T_5k))
    return reference_time * math.exp(log_ratio)


# ------------------------------------------------------------------ #
# STEP 5 — SAVE + SANITY CHECK
# ------------------------------------------------------------------ #

# saveSplines
# Purpose: Save the fitted splines dict to disk as a pickle file.
#          normalize_distance.py loads this at startup.
# Arguments: splines — dict mapping pool -> spline (plus "global").
# Returns: None.
def saveSplines(splines: dict):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "wb") as f:
        pickle.dump(splines, f)
    print(f"\nSplines saved to {OUTPUT_FILE}")
 
# sanityCheck
# Purpose: Print a summary and spot-check reference conversions.
#          Compares spline predictions vs old 1.06 exponent predictions
#          at several distances so you can see if the fit looks reasonable.
# Arguments: splines — dict from fitAllSplines.
# Returns: None.
def sanityCheck(splines: dict):
    print("\n" + "=" * 60)
    print("SPLINE SANITY CHECK")
    print("=" * 60)
    pool_splines = [k for k in splines if k != "global"]
    print(f"  Pools fitted:  {pool_splines}")
    print(f"  Global spline: yes\n")
 
    # Spot-check global spline at reference distances.
    # Using 1000s (16:40) as a round reference 5K time.
    _spotCheck(splines["global"], reference_time=1000.0)
 
# _spotCheck
# Purpose: Convert a reference 5K time to several distances using both
#          the fitted global spline and the old 1.06 exponent.
#          Print side by side so you can see if the spline looks sane.
# Arguments:
#   spline         — fitted global spline.
#   reference_time — a 5K time in seconds used as the baseline.
# Returns: None.
def _spotCheck(spline, reference_time: float):
    check_distances = [800, 1500, 1600, 3000, 3200, 5000, 8000, 10000]
 
    print(f"  Reference: {reference_time:.0f}s ({reference_time/60:.2f}min) at 5000m\n")
    print(f"  {'Distance':>10} {'Spline (s)':>12} {'1.06 exp (s)':>14} {'Diff':>8}")
    print(f"  {'-'*10} {'-'*12} {'-'*14} {'-'*8}")
 
    for dist in check_distances:
        # Spline prediction.
        # For a conversion from dist to 5K, the "pair" is (dist, 5000m).
        # log_x = log(5000/dist), log_z = log of the average of dist and 5000.
        # spline(log_x, log_z) gives log(T_5k / T_dist).
        # We negate because we want T_dist given T_5k (opposite direction).
        log_x_val = math.log(TARGET_DISTANCE_METERS / dist)
        log_z_val = math.log((dist + TARGET_DISTANCE_METERS) / 2)
        log_ratio   = float(spline(log_x_val, log_z_val))
        spline_time = reference_time * math.exp(-log_ratio)
 
        # Old 1.06 exponent prediction.
        old_time = reference_time * (dist / TARGET_DISTANCE_METERS) ** 1.06
 
        diff = spline_time - old_time
        print(f"  {dist:>10.0f} {spline_time:>12.1f} {old_time:>14.1f} {diff:>+8.1f}")
 
# ------------------------------------------------------------------ #
# MAIN
# ------------------------------------------------------------------ #
 
def main():

    print("=== fit_distance_exponent.py ===\n")
 
    # Step 1 — load pairs from XC and outdoor TF, kept separate by sport.
    xc_by_pool, tf_by_pool = loadAllPairs()
 
    total_xc = sum(len(p) for p in xc_by_pool.values())
    total_tf = sum(len(p) for p in tf_by_pool.values())
 
    if total_xc + total_tf == 0:
        print("No valid pairs found. Is TF scraping complete?")
        return
 
    print(f"\nXC pools found:  {sorted(xc_by_pool.keys())}")
    print(f"TF pools found:  {sorted(tf_by_pool.keys())}")
    print(f"Total XC pairs:  {total_xc:,}")
    print(f"Total TF pairs:  {total_tf:,}\n")
 
    # Step 2 — compare XC vs TF splines before merging.
    # If divergence > 2%, we may need separate splines per sport.
    compareXCvsTF(xc_by_pool, tf_by_pool)
 
    # Step 3 — merge XC and TF pairs, fit combined splines per pool.
    # This is what gets saved — combined is the default methodology.
    combined_by_pool: dict[str, list[dict]] = {}
    for pool, pairs in xc_by_pool.items():
        # extend adds all items from one list to another. setdefault returns the
        # existing value if key exists so we don't overwrite.
        combined_by_pool.setdefault(pool, []).extend(pairs)

    for pool, pairs in tf_by_pool.items():
        combined_by_pool.setdefault(pool, []).extend(pairs)
 
    print("Fitting combined splines...\n")
    splines = fitAllSplines(combined_by_pool)
 
    # Step 4 — save and sanity check.
    saveSplines(splines)
    sanityCheck(splines)
 
    print("\nDone. Update normalize_distance.py to load engine/data/distance_spline.pkl.")
 
 
if __name__ == "__main__":
    main()