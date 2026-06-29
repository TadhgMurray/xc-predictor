# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Model
# Date: 6/11/2026
# File Title: feature_extraction.py
# Purpose: Builds the training dataset for the transformer model.
#          For every result in the DB where the athlete has prior results,
#          creates one training example:
#            - sequence: all prior results as feature vectors
#            - context:  features about the target race
#            - target:   normalized_time of the target result
#
# Output files (all saved to model/data/):
#            - sequences.pt   — padded sequence tensors
#            - masks.pt       — attention masks (True = real, False = padding)
#            - context.pt     — context feature tensors
#            - targets.pt     — target normalized_time values
#            - encoders.pkl   — saved label encoders for use at inference time
#
# System design:
#   1. Load all results from DB in one big query
#   2. Group by athlete_id
#   3. For each athlete, sort results by date
#   4. For each result, build (sequence, context, target) from prior results
#   5. Encode categorical features (grade, pool, school) to integers
#   6. Pad sequences to uniform length within each batch
#   7. Save everything to disk as PyTorch tensors

import os
import sys
import pickle
import torch
import psycopg2.extras
from collections import defaultdict
from datetime import datetime, date
from sklearn.preprocessing import LabelEncoder
import re
import statistics
 
sys.path.insert(0, "scripts")
from database import initPool, closePool, getConn

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# Where to save the output tensors and encoders.
OUTPUT_DIR = "model/data"
 
# Default race hours for weather lookup.
# We don't store actual race times, so we use typical start times
# per sport. At inference time the user can specify the exact hour.
# XC races almost always start 9-10am.
# TF distance events typically run in the afternoon session.
XC_DEFAULT_HOUR  = 9
TF_DEFAULT_HOUR  = 15
 
# Minimum normalized_time to be considered a valid result.
# Matches the filter in the speed ratings engine.
MIN_NORMALIZED_TIME = 600

# Number of examples per chunk file.
CHUNK_SIZE = 10_000

# TODO: replace with real pass 1 once TF scraping completes and we
# can check realistic max_len from actual data.
MAX_SEQ_LEN_PLACEHOLDER = 500

# ------------------------------------------------------------------ #
# CHUNK 1 — DATABASE QUERIES
# ------------------------------------------------------------------ #
#
# We load everything in two queries:
#   1. All XC results with meet, athlete, course difficulty, weather
#   2. All TF results with meet, athlete, course difficulty, weather
#
# Then we merge them into one list in Python.
#
# Why two queries instead of one?
# XC and TF results live in different tables (results vs results_tf)
# and join to different meet tables (meets vs meets_tf). It's cleaner
# to query them separately and merge in Python than to write one
# massive UNION query.
#
# Why LEFT JOIN on weather and course_difficulties?
# Not every meet has weather data yet (backfill not complete) and not
# every course has a difficulty rating (thin courses get 0.0). LEFT JOIN
# means we still get the result row even if weather/difficulty is missing
# — the missing columns just come back as NULL, which we handle in Python.


# loadXCResults
# Purpose: Loads all XC resutls with all features need for training.
# Arguments: None.
# Output: List of dicts, one per result.
def loadXCResults() -> list[dict]:

    with getConn() as conn:
        
        # RealDictCursor makes rows come back as dicts (row["column_name"])
        # instead of tuples (row[0]). Much easier to work with.
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cursor.execute("""
            SELECT
                -- Result identifiers.
                r.result_id,
                r.athlete_id,
                r.date,
                       
                -- The value we're trying to predict.
                r.normalized_time,
                       
                -- Per-race features that go into the sequence.
                r.grade,
                r.time_seconds,
                a.gender,
                
                -- Meet features.
                m.meet_id,
                m.course_name,
                -- AS renames the column in the query result, 
                -- from distance to distance_meters.
                m.distance      AS distance_meters,
                m.gps_lat,
                m.gps_long,
                
                -- Course difficulty - how much harder/easier than flat.
                -- LEFT JOIN means NULL if course not yet rated, which COALESCE
                -- makes 0.0.
                COALESCE(cd.difficulty, 0.0) AS course_difficulty,
                
                -- Weather features - NULL if backfill not yet run for this meet.
                -- COALESCE won't help here since we wanted to know if it's missing.
                -- We handle NULLs in Python by substituting 0.0.)
                w.temp_c,
                w.dew_point_c,
                w.humidity,
                w.apparent_temp_c,
                w.precipitation_mm,
                w.pressure_hpa,
                w.cloud_cover,
                w.wind_speed_km,
                w.wind_dir,
                       
                -- Altitude of the meet location.
                -- NULL until elevation backfill runs.
                m.altitude_meters,
 
                -- School name for school encoding.
                a.school,
 
                -- Flag so the model knows this is an XC result.
                TRUE AS is_xc,
                FALSE AS is_indoor
        
        FROM results r
        JOIN athletes a ON r.athlete_id = a.athlete_id
        JOIN meets     m  ON r.div_id       = m.div_id
                       
        -- LEFT JOIN: keeps result even if no course difficulty yet.
        LEFT JOIN course_difficulties cd ON m.course_name = cd.course_name
        
        -- LEFT JOIN weather at the default XC race hour (9am local time).
        -- meet_id matches, hour matches our XC default.
        LEFT JOIN weather w
            ON  m.meet_id = w.meet_id
            AND w.hour    = %s
                       
        WHERE r.normalized_time IS NOT NULL
        -- Makes normalized time be a reasonable value.
        AND   r.normalized_time > %s
        AND   r.date IS NOT NULL
        AND   r.date != ''
        -- Drop profile-less meet entries (AAU/junior/user-uploaded) saved
        -- with NULL athlete_id — they have no stable identity, can't be
        -- tracked across races, and are useless/polluting to a per-athlete
        -- sequence model. (See 6/25 diagnostics: ~2.5M such TF rows.)
        AND   r.athlete_id IS NOT NULL
                       
        -- ORDER BY date so when we group by athlete later,
        -- the races are already in chronological order.
        ORDER BY r.date ASC
    """, (XC_DEFAULT_HOUR, MIN_NORMALIZED_TIME))

    # Converts each row dict from Postgres to a Python dict.
    rows = [dict(row) for row in cursor.fetchall()]
 
    print(f"Loaded {len(rows):,} XC results")
    return rows


# loadTFResults
# Purpose: Loads all TF distance results with all features need for training.
#          Only loads events with a normalized_time (distance events 800m+).
# Arguments: None.
# Output: List of dicts, one per result.
def loadTFResults() -> list[dict]:

    with getConn() as conn:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
 
        cursor.execute("""
            SELECT
                r.result_id,
                r.athlete_id,
                r.date,
                r.normalized_time,
                r.grade,
                r.time_seconds,
                a.gender,
 
                -- TF meets table has event-level info
                m.meet_id,
                m.event_short    AS course_name,
                m.distance_meters,
                m.gps_lat,
                m.gps_long,
                m.is_indoor,
 
                -- TF events don't have course difficulties —
                -- the "course" is a standard track. Use 0.0.
                0.0              AS course_difficulty,
 
                -- Weather at default TF hour (3pm local time).
                w.temp_c,
                w.dew_point_c,
                w.humidity,
                w.apparent_temp_c,
                w.precipitation_mm,
                w.pressure_hpa,
                w.cloud_cover,
                w.wind_speed_km,
                w.wind_dir,
 
                -- Altitude
                m.altitude_meters,
 
                a.school,

                -- Creates a new column in the query result not in any table.
                -- Hardcodes the values FALSE for every TF row.
                FALSE AS is_xc
 
            FROM results_tf r
            JOIN athletes  a  ON r.athlete_id = a.athlete_id
            JOIN meets_tf  m  ON r.meet_id    = m.meet_id
                             AND r.div_id     = m.div_id
                             AND r.event_id   = m.event_id
 
            LEFT JOIN weather w
                ON  m.meet_id = w.meet_id
                AND w.hour    = %s
 
            WHERE r.normalized_time IS NOT NULL
            AND   r.normalized_time > %s
            AND   r.date IS NOT NULL
            AND   r.date != ''
            AND   r.is_relay = 0
            -- Drop NULL-athlete_id profile-less entries (see XC note above).
            AND   r.athlete_id IS NOT NULL
 
            ORDER BY r.date ASC
        """, (TF_DEFAULT_HOUR, MIN_NORMALIZED_TIME))
 
        rows = [dict(row) for row in cursor.fetchall()]
 
    print(f"Loaded {len(rows):,} TF results")
    return rows


# loadAllResults
# Purpose: Loads XC and TF results and merges them into one list.
#          Sorted by date so the athlete grouping step sees results
#          in chronological order
def loadAllResults() -> list[dict]:

    xc = loadXCResults()
    tf = loadTFResults()

    # Combines XC and TF results into one list.
    all_results = xc + tf

    # Sort by date. This ensure that when we group by athlete
    # and iterate through their resutls, we always see them in order.
    # strptime parses "YYYY-MM-DD" into a comparable date object.
    # key= tells sort waht value to sort by, lambda r is an anonymous
    # func that takes one arg (the results dict) and returns the date string.
    all_results.sort(key=lambda r: r["date"])

    print(f"Total results loaded: {len(all_results):,}")
    return all_results


# groupByAthlete
# Purpose: Groups the flat list of results into a dict keyed by athlete_id.
#          Each value is the athlete's results in chronological order.
#          This is the structure we iterate over to build training examples.
# Arguments:
#           results: flat list of result dicts from loadAllResults.
# Output: {athlete_id: [result, result, ...]} sorted by date.
def groupByAthlete(results: list) -> dict:
 
    # defaultdict(list) creates an empty list for any new key automatically.
    by_athlete = defaultdict(list)
 
    for r in results:
        by_athlete[r["athlete_id"]].append(r)
 
    print(f"Grouped into {len(by_athlete):,} athletes")
    return by_athlete

# ------------------------------------------------------------------ #
# CHUNK 2 — ENCODERS
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   The model only understands numbers. Every categorical field
#   (grade, pool, school) needs to become an integer before it can
#   be turned into a tensor.
#
#   We use sklearn's LabelEncoder for this. It's a simple lookup table:
#       fit()       — scans all the data, assigns each unique string
#                      an integer (e.g. "Amherst" -> 47, "Tufts" -> 112)
#       transform() — converts strings to their assigned integers
#
#   We fit these encoders ONCE on the full training set, then save
#   them to disk (encoders.pkl). At inference time we LOAD the same
#   encoders and call transform() — this guarantees "Amherst" always
#   maps to 47, whether it's training or a live prediction.
#
# WHAT THIS CHUNK ADDS:
#   1. normalizeGrade — cleans messy grade strings into canonical labels
#   2. buildPool — combines normalized grade + gender into a pool string,
#      e.g. "FR-M", "11-F", "Unknown-M"
#   3. buildEncoders — fits LabelEncoders for grade, pool, and school
#      across the whole dataset and saves them to disk
# ------------------------------------------------------------------ #
# Grade level classification
# ------------------------------------------------------------------ #

# College grades are letter codes, not numbers.
# Stored as a set for fast "in" checks.
COLLEGE_GRADES = {"FR", "SO", "JR", "SR"}

# MS/HS grades that pass through unchanged when seen alone (no range).
SCHOOL_GRADES = {"6", "7", "8", "9", "10", "11", "12"}

# _extractFirstNumber
# Purpose: Pulls the first integer out of a grade string. Handles
#          plain numbers ("11"), ranges ("11-12"), and "19+".
#          Returns None if no number is found (e.g. "-", "", "FR").
# Arguments:
#           grade_str: the raw grade string from the DB.
# Output: An int, or None if no digits are present.
def _extractFirstNumber(grade_str: str) -> int | None:

    # re.match looks for a pattern at the START of the string.
    # \d+ means "one or more digits". We capture them with ().
    # For "11-12" this matches "11". For "19+" this matches "19".
    # For "FR" or "-" this matches nothing -> returns None.
    # This is a Regex, a pattern-matching language for strings.
    # It's a way to describe "shapes" of text so code can find/extract
    # them. Here out "shape" is one or more digits in a row.
    match = re.match(r"(\d+)", grade_str)

    if match is None:
        return None
 
    # group(1) is the captured digits as a string; int() converts it.
    return int(match.group(1))

# normalizeGrade
# Purpose: Cleans a raw grade string into a canonical label used for
#          the pool feature. Preserves the FR/SO/JR/SR and 9-12
#          distinctions (unlike a broad MS/HS/College bucket), while
#          collapsing ranges and junk values down to something usable.
# Arguments:
#           grade_str: raw grade value from the DB (e.g. "11", "FR",
#                      "11-12", "-", "", "19+", None).
# Output: A canonical grade label:
#         "6".."12", "FR","SO","JR","SR", or "Unknown".
def normalizeGrade(grade_str: str) -> str:

    # Handle None / NULL from the DB.
    if grade_str is None:
        return "Unknown"
 
    # Strip whitespace so " 11 " doesn't break comparisons.
    grade_str = grade_str.strip()
 
    # College grades pass through unchanged — check this first since
    # they're letter codes with no leading digit.
    if grade_str in COLLEGE_GRADES:
        return grade_str
 
    # Plain MS/HS grades pass through unchanged.
    if grade_str in SCHOOL_GRADES:
        return grade_str
 
    # Everything else (ranges like "11-12", "19+", "-", "") — try to
    # pull a leading number and re-check it against SCHOOL_GRADES.
    num = _extractFirstNumber(grade_str)
    
    # Rechecks against MS and HS grades to see if the leading
    # num is there.
    if num is not None and 6 <= num <= 12:
        # e.g. "11-12" -> 11 -> "11"
        return str(num)
 
    # No usable number (ages 13+, "-", "", anything unexpected).
    return "Unknown"

# ------------------------------------------------------------------ #
# Pool derivation
# ------------------------------------------------------------------ #


# buildPool
# Purpose: Combines normalized grade + gender into a single pool string.
#          This is the "pool_encoded" context feature from MODEL.md —
#          it groups athletes into comparable cohorts, e.g. "FR-M"
#          (college freshman men) vs "SR-M" (college senior men), which
#          the model can learn have different improvement curves.
# Arguments:
#           grade_str: raw grade value from the DB.
#           gender: "M" or "F" (or whatever athletes.gender contains).
# Output: A pool string like "FR-M", "11-F", "Unknown-M".
def buildPool(grade_str: str, gender: str) -> str:

    grade = normalizeGrade(grade_str)
 
    # f-string glues grade and gender together with a dash.
    # e.g. grade="FR", gender="M" -> "FR-M"
    return f"{grade}-{gender}"

# ------------------------------------------------------------------ #
# Encoder fitting
# ------------------------------------------------------------------ #


# _collectValues
# Purpose: Walks every result and builds three parallel lists —
#          one value per result for grade, pool, and school.
#          Separated from buildEncoders so each piece is testable
#          and buildEncoders itself stays short.
# Arguments:
#           results: flat list of result dicts from loadAllResults.
# Output: A tuple of three lists (grades, pools, schools), all the
#         same length as results.
def _collectValues(results: list[dict]) -> tuple[list, list, list]:

    grades  = []
    pools   = []
    schools = []
 
    for r in results:
        # raw grade string, e.g. "11", "FR", "-"
        # NOTE: kept raw (not normalized) for the sequence feature —
        # LabelEncoder just needs unique strings, doesn't need them clean.
        grades.append(r["grade"])
 
        # pool combines normalized grade + gender, e.g. "FR-M"
        pools.append(buildPool(r["grade"], r["gender"]))
 
        # school name, e.g. "Amherst Regional"
        schools.append(r["school"])
 
    return grades, pools, schools

# _fitEncoder
# Purpose: Fits a single LabelEncoder on a list of values.
#          Tiny helper so buildEncoders doesn't repeat this 3x.
# Arguments:
#           values: list of strings (or None) to fit on.
# Output: A fitted LabelEncoder.
def _fitEncoder(values: list) -> LabelEncoder:

    # LabelEncoder.fit_transform doesn't like None - replace with
    # the string "None" so it's treated as it's own category.
    cleaned = [v if v is not None else "None" for v in values]

    encoder = LabelEncoder()

    # fit() scans all values and builds the string -> int mapping.
    # We don't need the transformed output here, just the fitted
    # encoder itself (used in Chunk 3 to transform).
    encoder.fit(cleaned)

    return encoder

# buildEncoders
# Purpose: Fits LabelEncoders for grade, pool, and school across the
#          entire dataset, prints how many categories each found,
#          and returns them as a dict ready to be saved to disk.
# Arguments:
#           results: flat list of result dicts from loadAllResults.
# Output: dict with keys "grade", "pool", "school", each mapping to
#         a fitted LabelEncoder.
def buildEncoders(results: list[dict]) -> dict:

    grades, pools, schools = _collectValues(results)

    # For each list of string values creates an string -> int
    # mapping for later use in Chunk 3 to transform.
    encoders = {
        "grade":  _fitEncoder(grades),
        "pool":   _fitEncoder(pools),
        "school": _fitEncoder(schools),
    }

    # encoder.classes_ is the array of unique values the encoder learned.
    # len() of that tells us how many categories each field has.
    for name, encoder in encoders.items():
        print(f"  {name}: {len(encoder.classes_)} categories")
 
    return encoders

# ------------------------------------------------------------------ #
# CHUNK 3 — PER-ATHLETE SEQUENCE BUILDER
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   For each athlete, walk through their races in chronological order.
#   Every race AFTER the first becomes one training example:
#     - "sequence" = feature vectors for every race BEFORE it
#     - "target_result" = the race itself (Chunk 4 turns this into
#                          the context vector)
#     - "target" = that race's normalized_time (what we're predict)
#
#   This is a "growing window" — race 2's history is just race 1,
#   race 4's history is races 1-3, etc. The first race has no history,
#   so it can't be a training example itself — but it still appears
#   INSIDE every later example's sequence


# _parseDate
# Purpose: Converts a "YYYY-MM-DD" string from the DB into a Python
#          date object so we can subtract dates to get day counts.
# Arguments:
#           date_str: date string, e.g. "2025-10-04"
# Output: a datetime.date object
def _parseDate(date_str: str) -> date:
    # strptime parses a string into a datetime according to the format
    # string. .date() drops the time-of-day part, leaving just the date.
    return datetime.strptime(date_str, "%Y-%m-%d").date()

# _daysAgo
# Purpose: Computes how many days before the target race a prior race
#          happened. This becomes the "days_ago" sequence feature —
#          it tells the model how recent each prior race is relative
#          to the race it's predicting.
# Arguments:
#           prior_date_str: date string of the prior race
#           target_date_str: date string of the target race
# Output: int number of days (>= 0) between the two dates
def _daysAgo(prior_date_str: str, target_date_str: str) -> int:
    prior_date  = _parseDate(prior_date_str)
    target_date = _parseDate(target_date_str)
 
    # Subtracting two date objects gives a timedelta object.
    # .days extracts the whole number of days as an int.
    return (target_date - prior_date).days

# _encodeGrade
# Purpose: Converts a raw grade string ("11", "FR", None, ...) into
#          the integer assigned by the Chunk 2 grade LabelEncoder.
# Arguments:
#           grade_raw: the raw "grade" value from a result dict
#           grade_encoder: the fitted LabelEncoder for "grade"
#                          (encoders["grade"] from Chunk 2)
# Output: float — the encoded grade, ready to go straight into a
#         feature vector
def _encodeGrade(grade_raw, grade_encoder: LabelEncoder) -> float:
 
    # buildEncoders fit on "None" (the string) wherever grade was None,
    # so we have to match that exact substitution here at transform time.
    if grade_raw is None:
        grade_raw = "None"
    
    # Transforms the raw grade string into an integer based on the label
    # it was encoded into in chunk 2.
    # transform() expects a LIST of values, even for a single item,
    # and returns a list/array back — [0] grabs the first (only) result.
    encoded = grade_encoder.transform([grade_raw])[0]
 
    return float(encoded)

# _orZero
# Purpose: Converts a possibly-NULL numeric value into a float,
#          substituting 0.0 for NULL — per MODEL.md's "missing weather
#          -> NULL -> 0.0" rule. Defined here (Chunk 3) since sequence
#          vectors now carry weather; Chunk 4 reuses this same helper
#          for the context vector's weather fields — only define it
#          once in your combined file.
# Arguments:
#           value: a number, or None (if RealDictCursor returned NULL)
# Output: float — value as a float, or 0.0 if value was None
def _orZero(value) -> float:
 
    # Postgres NULL comes back as Python None via RealDictCursor.
    # "is None" specifically checks for that — 0 or 0.0 from the DB
    # would NOT match this and would pass through unchanged.
    if value is None:
        return 0.0
 
    return float(value)

# _altitudeDelta
# Purpose: Computes altitude_delta = this race's altitude minus the
#          MEDIAN altitude of every race the athlete ran BEFORE it.
#          Captures sea-level vs altitude effects on performance —
#          same idea as weather: a slow time at 8,000ft is a different
#          signal than the same time at sea level.
#          Returns 0.0 (neutral) if either the race's own altitude is
#          unknown, or there are no prior races with known altitude to
#          compare against — both common until the elevation backfill
#          (MODEL.md, V2) has run.
# Arguments:
#           altitude: this race's altitude_meters (float or None)
#           races_before: list of result dicts for every race the
#                          athlete ran BEFORE this one (chronological,
#                          may be empty for the athlete's first race)
# Output: float — altitude_delta, or 0.0 if not computable
def _altitudeDelta(altitude, races_before: list[dict]) -> float:

    # Collect altitudes from prior races, skipping any that are still
    # NULL (None).
    prior_altitudes = [
        r["altitude_meters"] for r in races_before
        if r["altitude_meters"] is not None
    ]

    # Nothing to compare against, in either direction -> neutral 0.0.
    if altitude is None or len(prior_altitudes) == 0:
        return 0.0

    # This returns the difference in altitude between the current result
    # and the median result of the athlete's previous races.
    # statistics.median sorts the values and returns the middle one
    # (or the average of the two middle values for an even-length
    # list) — robust to a single outlier altitude in a small history.
    return float(altitude) - statistics.median(prior_altitudes)

# _buildSequenceVector
# Purpose: Converts ONE prior race into the 17-number feature vector
#          for the sequence — the athlete's race AND the conditions
#          they ran it in (weather + altitude). Included here (not
#          just in the target's context) so the model can learn that,
#          e.g., a slow time on a hot/humid day or at high altitude is
#          a noisier signal of true ability than the same time under
#          neutral conditions — it can "de-weight" that data point if
#          it learns to.
# Arguments:
#           prior_result: a result dict for a race the athlete ran
#                          BEFORE the target race
#           target_date_str: date string of the target race, used to
#                          compute days_ago
#           races_before_prior: list of result dicts for every race
#                          the athlete ran BEFORE prior_result — used
#                          to compute prior_result's OWN altitude_delta
#                          (may be empty if prior_result was the
#                          athlete's first-ever race)
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: list of 17 floats, in this fixed order:
#         [normalized_time, course_difficulty, days_ago,
#          distance_meters, grade_encoded, is_xc, is_indoor,
#          temp_c, dew_point_c, humidity, apparent_temp_c,
#          precipitation_mm, pressure_hpa, cloud_cover,
#          wind_speed_km, wind_dir, altitude_delta]
def _buildSequenceVector(prior_result: dict, target_date_str: str,
                        races_before_prior: list[dict],
                        encoders: dict) -> list[float]:

    return [
        float(prior_result["normalized_time"]),
        float(prior_result["course_difficulty"]),
        float(_daysAgo(prior_result["date"], target_date_str)),
        float(prior_result["distance_meters"]),
        _encodeGrade(prior_result["grade"], encoders["grade"]),
 
        # bool -> float: True becomes 1.0, False becomes 0.0
        float(prior_result["is_xc"]),
        float(prior_result["is_indoor"]),

        # Weather this prior race was run in. _orZero handles NULLs
        # for meets where the weather backfill hasn't reached yet.
        _orZero(prior_result["temp_c"]),
        _orZero(prior_result["dew_point_c"]),
        _orZero(prior_result["humidity"]),
        _orZero(prior_result["apparent_temp_c"]),
        _orZero(prior_result["precipitation_mm"]),
        _orZero(prior_result["pressure_hpa"]),
        _orZero(prior_result["cloud_cover"]),
        _orZero(prior_result["wind_speed_km"]),
        _orZero(prior_result["wind_dir"]),
 
        # Altitude relative to THIS race's own prior history — see
        # _altitudeDelta docstring. _orZero handles NULL altitude
        # via the None-check inside _altitudeDelta itself.
        _altitudeDelta(prior_result["altitude_meters"], races_before_prior),
        # Absolute altitude of this race's venue (NEW — index 17).
        # Pairs with altitude_delta above: delta = "how far from your
        # normal", this = "where you are now". Together they let the
        # model learn the non-linear altitude effect neither expresses
        # alone. _orZero -> 0.0 until the elevation backfill populates it.
        _orZero(prior_result["altitude_meters"]),
    ]

# buildAthleteExamples
# Purpose: Builds all training examples for ONE athlete using the
#          growing-window approach: for each race after the first,
#          everything before it becomes "sequence".
# Arguments:
#           athlete_results: this athlete's results, already in
#                          chronological order (one entry from
#                          by_athlete[athlete_id])
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: list of example dicts, each:
#         {
#           "sequence":      [[17 floats], [17 floats], ...],
#           "prior_results": result dicts for every race before the
#                             target (raw, for Chunk 4's altitude_delta),
#           "target_result": result dict (full, for Chunk 4),
#           "target":        float (normalized_time to predict),
#         }
def buildAthleteExamples(athlete_results: list[dict], encoders: dict) -> list[dict]:

    examples = []

    # Builds training examples for an athlete using a growing window
    # approach for every race.
    # Skips athlete's first-ever race because it can't be a training
    # example (as it has no prior races).
    for i in range(1, len(athlete_results)):

        # Gets the result we're predicting
        target_result = athlete_results[i]

        # Gets all the prior results to build the training example.
        # Slicing: everything from index 0 up to (but not including) i.
        prior_results = athlete_results[:i]

        # Builds the sequence vector by building the sequence vector for
        # eahc prior result.
        sequence = [
            _buildSequenceVector(prior, target_result["date"], prior_results[:j], encoders)
            for j, prior in enumerate(prior_results)
        ]

        # Adds the current training examples to our list of training
        # examples. It contains the sequence, the context of the result
        # we're predicting, and the normalize time of the result we're predicting.
        # Creates a dict
        examples.append({
            "sequence": sequence,
            "prior_results": prior_results,
            "target_result": target_result,
            "target": float(target_result["normalized_time"]),
        })

    return examples

# buildAllExamples
# Purpose: Runs buildAthleteExamples for every athlete and flattens
#          the per-athlete lists into one big list — this flat list
#          is the dataset Chunk 5 will pad and save to tensors.
# Arguments:
#           by_athlete: {athlete_id: [results...]} from groupByAthlete
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: flat list of example dicts (see buildAthleteExamples)
def buildAllExamples(by_athlete: dict, encoders: dict):

    all_examples = []

    # For each athlete it builds their training examples.
    for athlete_id, athlete_results in by_athlete.items():
        # .extend() appends every item from this athlete's list
        # onto all_examples, rather than appending the whole list
        # as one nested element.
        all_examples.extend(buildAthleteExamples(athlete_results, encoders))

    print(f"Built {len(all_examples):,} training examples")
    return all_examples

# ------------------------------------------------------------------ #
# CHUNK 4 — CONTEXT FEATURE BUILDER
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   Chunk 3 built the "sequence" (history) for each example. Chunk 4
#   builds the "context" — a single vector describing the TARGET race
#   itself (the one we're predicting normalized_time for).
#
#   Per MODEL.md, the context vector is, in order:
#     [course_difficulty, distance_meters, day_of_year,
#      days_since_last_race, pool_encoded, gender_encoded,
#      school_encoded,
#      temp_c, dew_point_c, humidity, apparent_temp_c,
#      precipitation_mm, pressure_hpa, cloud_cover,
#      wind_speed_km, wind_dir]
#      altitude_delta]
#
#   That's 7 "race info" features + 9 weather features
#   + 1 altitude feature = 17 floats.

# ------------------------------------------------------------------ #
# Gender encoding
# ------------------------------------------------------------------ #
#
# Unlike grade/pool/school, gender is a clean, fixed "M"/"F" straight
# from the athletes table — no messy variants to clean up. A small
# hardcoded mapping is simpler than a full LabelEncoder and doesn't
# need to be saved/loaded in encoders.pkl.

GENDER_MAP = {"M": 0.0, "F": 1.0}

# _encodeGender
# Purpose: Converts a gender string to a float using GENDER_MAP.
# Arguments:
#           gender: "M", "F", or possibly something unexpected/None.
# Output: float — 0.0 for "M", 1.0 for "F", -1.0 for anything else
#         (so unexpected values are visible/debuggable downstream
#         rather than silently colliding with "M").
def _encodeGender(gender: str) -> float:
 
    # dict.get(key, default) returns the value for key if present,
    # otherwise returns default — avoids a KeyError for unexpected
    # gender values while still flagging them (-1.0 stands out).
    return GENDER_MAP.get(gender, -1.0)

# ------------------------------------------------------------------ #
# CHUNK 5 — PADDING, TENSORS, AND SAVING
# ------------------------------------------------------------------ #
#
# BIG IDEA:
#   PyTorch needs every training example to be the same shape so they
#   can be stacked into a batch. Our sequences have variable length —
#   an athlete with 2 races has a sequence of length 1, an athlete
#   with 20 races has a sequence of length 19.
#
#   The solution is PADDING: find the longest sequence in the dataset,
#   then pad every shorter sequence with rows of 0.0 up to that length.
#
#   But we need to tell the Transformer which positions are real and
#   which are padding — otherwise it'll "attend" to the zero rows and
#   corrupt the computation. That's what the ATTENTION MASK is for:
#     True  = real race (attend to this)
#     False = padding  (ignore this)
#
#   Final output — 5 files saved to model/data/:
#     sequences.pt  — shape [N, max_seq_len, 17]  (float32)
#     masks.pt      — shape [N, max_seq_len]       (bool)
#     context.pt    — shape [N, 17]                (float32)
#     targets.pt    — shape [N]                    (float32)
#     encoders.pkl  — fitted LabelEncoders (for inference)
#
#   Where N = total number of training examples across all athletes.


# _maxSequenceLength
# Purpose: Finds the longest sequence across all examples.
#          This becomes the padded width every example is stretched to.
# Arguments:
#           examples: flat list of example dicts from buildAllExamples.
# Output: int — length of the longest sequence.
def _maxSequenceLength(examples: list[dict]) -> int:

    # len(ex["sequence"]) = number of prior races for that example.
    # max() returns the largest of those counts.
    # Goes through all training examples finding the length
    # of the longest sequence (the most races).
    return max(len(ex["sequence"]) for ex in examples)

# _padSequence
# Purpose: Pads ONE sequence to max_len by appending rows of zeros,
#          and builds the corresponding attention mask.
# Arguments:
#           sequence: list of 17-float vectors (the real race data).
#           max_len:  target length to pad up to.
# Output: tuple of:
#           padded   — list of max_len vectors (real + zero rows)
#           mask     — list of max_len bools (True=real, False=padding)
def _padSequence(sequence: list[list[float]], max_len: int):

    # Length of this specific raw sequence.
    real_len = len(sequence)

    # How many zero rows to add based on this sequence's length
    # and the max sequence length.
    pad_len = max_len - real_len

    # A single zero row — 17 zeros matching the sequence feature width.
    # We build one and reuse it rather than recomputing inside the loop.
    zero_row = [0.0] * 18

     # Concatenate real rows + pad rows.
    # [zero_row] * pad_len creates a list of pad_len zero rows.
    padded = sequence + [zero_row] * pad_len

    # True for every real race, False for every padding row.
    mask = [True] * real_len + [False] * pad_len

    return padded, mask

# _buildTensors
# Purpose: Converts all examples into four PyTorch tensors ready for
#          training. Pads sequences to uniform length and builds masks.
# Arguments:
#           examples: flat list of example dicts (with "sequence",
#                     "context", "target" keys populated by Chunks 3-4).
#           max_len:  padded sequence length from _maxSequenceLength.
# Output: tuple of (sequences_tensor, masks_tensor,
#                   context_tensor, targets_tensor)
def _buildTensors(examples: list[dict], max_len: int):

    # Pre-allocate four Python lists — one entry per example.
    # We'll convert these to tensors in one shot at the end,
    # which is faster than calling torch.tensor() in a loop.
    all_sequences = []
    all_masks     = []
    all_contexts  = []
    all_targets   = []

    # For each example pads it's training sequence and builds
    # it's mask, then appends it to the python lists.
    for ex in examples:

        # Pad this example's sequence and build its mask.
        padded, mask = _padSequence(ex["sequence"], max_len)

        all_sequences.append(padded)
        all_masks.append(mask)
        all_contexts.append(ex["context"])
        all_targets.append(ex["target"])

    # torch.tensor() converts a nested Python list into a tensor.
    # dtype=torch.float32 — standard precision for neural net weights.
    # dtype=torch.bool    — True/False mask, no gradient needed.
    sequences_tensor = torch.tensor(all_sequences, dtype=torch.float32)
    masks_tensor     = torch.tensor(all_masks,     dtype=torch.bool)
    context_tensor   = torch.tensor(all_contexts,  dtype=torch.float32)
    targets_tensor   = torch.tensor(all_targets,   dtype=torch.float32)

    # Print shapes so we can sanity-check before saving.
    # e.g. sequences: [2_400_000, 847, 17]
    #      masks:     [2_400_000, 847]
    #      context:   [2_400_000, 17]
    #      targets:   [2_400_000]
    # This prints as [depth, rows, columns], i.e. # of examples,
    # steps, features.
    print(f"  sequences : {list(sequences_tensor.shape)}")
    print(f"  masks     : {list(masks_tensor.shape)}")
    print(f"  context   : {list(context_tensor.shape)}")
    print(f"  targets   : {list(targets_tensor.shape)}")

    return sequences_tensor, masks_tensor, context_tensor, targets_tensor

# _saveTensors
# Purpose: Saves the four tensors to disk in model/data/.
#          torch.save uses Python's pickle format — torch.load()
#          at training time will restore the exact tensor.
# Arguments:
#           sequences_tensor, masks_tensor, context_tensor,
#           targets_tensor: the four tensors from _buildTensors.
#           output_dir: directory to save into (OUTPUT_DIR constant).
# Output: None. Prints each file path on save.
def _saveTensors(sequences_tensor, masks_tensor,
                 context_tensor, targets_tensor,
                 output_dir: str) -> None:
    
    # os.path.join glues the directory and filename together correctly
    # on any OS (Windows uses \, Linux uses /).
    files = {
        "sequences.pt": sequences_tensor,
        "masks.pt":     masks_tensor,
        "context.pt":   context_tensor,
        "targets.pt":   targets_tensor,
    }

    # Gets the path to each file, and then saves the tensors
    # to there.
    for filename, tensor in files.items():
        path = os.path.join(output_dir, filename)
        torch.save(tensor, path)
        print(f"  Saved {path}")

# _saveEncoders
# Purpose: Saves the fitted LabelEncoders to disk using pickle.
#          At inference time, the Flask app loads these to transform
#          the same categorical features the same way as training.
# Arguments:
#           encoders: dict of fitted LabelEncoders from buildEncoders.
#           output_dir: directory to save into.
# Output: None.
def _saveEncoders(encoders: dict, output_dir: str) -> None:

    path = os.path.join(output_dir, "encoders.pkl")

    # "wb" = write binary — pickle needs binary mode.
    # Writes the binary encoders for each string we
    # input in the model to this file
    with open(path, "wb") as f:
        pickle.dump(encoders, f)

    print(f"  Saved {path}")

# saveAll
# Purpose: Two-pass chunked save. Pass 1 finds max_len (placeholder
#          for now — TODO: replace with real pass once we know realistic
#          sequence lengths from actual data). Pass 2 builds examples
#          in batches of CHUNK_SIZE, pads each batch to max_len, saves
#          each chunk to model/data/chunk_NNNN.pt immediately, then
#          discards it — keeps memory flat regardless of dataset size.
# Arguments:
#           by_athlete: {athlete_id: [results...]} from groupByAthlete.
#           encoders: dict of fitted LabelEncoders from Chunk 2.
#           output_dir: where to save (OUTPUT_DIR constant).
# Output: None. Saves chunk files + metadata.pkl to output_dir.
def saveAll(examples: list[dict], encoders: dict, output_dir: str) -> None:

    os.makedirs(output_dir, exist_ok=True)

    # TODO: Pass 1 — iterate all athletes to find real max_len.
    # For now use placeholder.
    max_len = MAX_SEQ_LEN_PLACEHOLDER
    print(f"Using placeholder max_len: {max_len} (TODO: fit from data)")

    # Pass 2 — build tensors, pad, save in chunks.
    chunk_idx     = 0
    total_examples = 0
    buffer        = []  # holds up to CHUNK_SIZE examples before flushing

    # For each athlete result builds training examples, context, and saves
    # them in chunks.
    for athlete_results in by_athlete.values():

        # Builds training examples and context, adds to current buffer.
        examples = buildAthleteExamples(athlete_results, encoders)
        addContextToExamples(examples, encoders)
        buffer.extend(examples)

        # Flush whenever buffer hits CHUNK_SIZE by saving chunk and
        # discarding examples.
        while len(buffer) >= CHUNK_SIZE:
            _saveChunk(buffer[:CHUNK_SIZE], max_len, chunk_idx, output_dir)
            chunk_idx     += 1
            total_examples += CHUNK_SIZE
            # Discard the flushed examples — this is what keeps RAM flat.
            buffer = buffer[CHUNK_SIZE:]

    # Flush any remaining examples that didn't fill a full chunk.
    if buffer:
        _saveChunk(buffer, max_len, chunk_idx, output_dir)
        total_examples += len(buffer)
        chunk_idx += 1

    # Save metadata so DataLoader knows how many chunks exist.
    _saveMetadata(max_len, total_examples, chunk_idx, output_dir)
    _saveEncoders(encoders, output_dir)

    print(f"Done. {total_examples:,} examples saved in {chunk_idx} chunks.")

# _saveChunk
# Purpose: Pads one buffer of examples to max_len, converts to tensors,
#          saves to chunk_NNNN.pt.
# Arguments:
#           examples:   list of up to CHUNK_SIZE example dicts.
#           max_len:    padded sequence length.
#           chunk_idx:  chunk number, used for filename.
#           output_dir: directory to save into.
# Output: None.
def _saveChunk(examples: list[dict], max_len: int,
               chunk_idx: int, output_dir: str) -> str:
    
    sequences_t, masks_t, context_t, targets_t = _buildTensors(examples, max_len)

    path = os.path.join(output_dir, f"chunk_{chunk_idx:04d}.pt")

    # Saves tensors for this chunk to file specified in path.
    torch.save({
        "sequences": sequences_t,
        "masks":     masks_t,
        "context":   context_t,
        "targets":   targets_t,
    }, path)

    print(f"  Saved {path} ({len(examples):,} examples)")

# _saveMetadata
# Purpose: Saves metadata of all chunks so train.py's DataLoader knows
#          max_len, total examples, and how many chunk files exist.
# Arguments:
#           max_len:        padded sequence length all chunks share, derived from
#                           max results an athlete has.
#           total_examples: total number of training examples across all chunks.
#           num_chunks:     how many chunk_NNNN.pt files were written.
#           output_dir:     directory to save metadata.pkl into.
# Output: None.
def _saveMetadata(max_len: int, total_examples: int,
                  num_chunks: int, output_dir: str) -> None:

    path = os.path.join(output_dir, "metadata.pkl")
    with open(path, "wb") as f:
        pickle.dump({
            "max_len":        max_len,
            "total_examples": total_examples,
            "num_chunks":     num_chunks,
            "chunk_size":     CHUNK_SIZE,
        }, f)
    print(f"  Saved {path}")

# ------------------------------------------------------------------ #
# Date helpers
# ------------------------------------------------------------------ #

# _dayOfYear
# Purpose: Converts a "YYYY-MM-DD" date string into its day-of-year
#          number (1-366). This captures SEASONALITY — e.g. an XC
#          race in early September vs late November is a meaningfully
#          different point in the season, even across different years.
# Arguments:
#           date_str: date string, e.g. "2025-10-04"
# Output: int, 1-366
def _dayOfYear(date_str: str) -> int:
 
    # _parseDate is defined in Chunk 3 — reused here rather than
    # re-implemented, since it's the same "YYYY-MM-DD" -> date parse.
    parsed = _parseDate(date_str)
 
    # .timetuple() converts a date into a time.struct_time, which has
    # a .tm_yday field — the day-of-year count (Jan 1 = 1).
    return parsed.timetuple().tm_yday

# ------------------------------------------------------------------ #
# Context vector builder
# ------------------------------------------------------------------ #

# _buildContextVector
# Purpose: Builds the 17-number context vector for ONE example,
#          describing the target race (course, timing, cohort,
#          weather, altitude). This is the second half of each
#          training example, alongside the "sequence" from Chunk 3.
# Arguments:
#           target_result: the result dict for the race being
#                          predicted (example["target_result"])
#           sequence: this example's sequence from Chunk 3 — used
#                          to read off days_since_last_race without
#                          recomputing it
#           prior_results: result dicts for every race before the
#                          target (example["prior_results"]) — used
#                          to compute the target's altitude_delta
#                          relative to the athlete's full history
#           encoders: dict of fitted LabelEncoders from Chunk 2
# Output: list of 17 floats, in the fixed order documented above
def _buildContextVector(target_result: dict, sequence: list[list[float]], 
                        prior_results: list[dict], encoders: dict) -> list[float]:
    
    # pool combines normalized grade + gender. It is applied to the TARGET
    # race instead of a history race. It encodes it by using the pool
    # transformation to transform it into a float.
    pool_str = buildPool(target_result["grade"], target_result["gender"])
    pool_encoded = float(encoders["pool"].transform([pool_str])[0])

    # School encoder, same "None" substitution pattern as _encodeGrade
    # in Chunk 3 — buildEncoders fit on "None" wherever school was None.
    school_raw = target_result["school"] if target_result["school"] is not None else "None"
    school_encoded = float(encoders["school"].transform([school_raw])[0])

    # sequence[-1] is the most recent prior race (sequence is in
    # chronological order, same as athlete_results). Index [2] of
    # each 17-element vector is days_ago (see Chunk 3's
    # _buildSequenceVector ordering) — for the LAST prior race,
    # "days_ago relative to this target" IS "days since last race".
    days_since_last_race = sequence[-1][2]

    # altitude_delta for the TARGET race, relative to the athlete's
    # FULL prior history.
    altitude_delta = _altitudeDelta(target_result["altitude_meters"], prior_results)

    return [
        float(target_result["course_difficulty"]),
        float(target_result["distance_meters"]),
        float(_dayOfYear(target_result["date"])),
        days_since_last_race,
        pool_encoded,
        _encodeGender(target_result["gender"]),
        school_encoded,
 
        # Weather — each passed through _orZero for NULL -> 0.0.
        _orZero(target_result["temp_c"]),
        _orZero(target_result["dew_point_c"]),
        _orZero(target_result["humidity"]),
        _orZero(target_result["apparent_temp_c"]),
        _orZero(target_result["precipitation_mm"]),
        _orZero(target_result["pressure_hpa"]),
        _orZero(target_result["cloud_cover"]),
        _orZero(target_result["wind_speed_km"]),
        _orZero(target_result["wind_dir"]),
 
        altitude_delta,
        # Absolute altitude of the target race's venue (NEW — index 17).
        # Same pairing rationale as the sequence vector.
        _orZero(target_result["altitude_meters"]),
    ]

def addContextToExamples(examples: list[dict], encoders: dict) -> None:

    for example in examples:
        example["context"] = _buildContextVector(
            example["target_result"],
            example["sequence"],
            example["prior_results"],
            encoders,
        )

    print(f"Added context vectors to {len(examples):,} examples")
 

# ------------------------------------------------------------------ #
# MAIN (placeholder — will be filled in subsequent chunks)
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    
    # Creates a directory and all its parent directories if they don't exist.
    # exist_ok says don't crash if it already exists. It creates model/
    # and model/data/ if they don't exist (OUTPUT_DIR in constants).
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    initPool()
 
    try:
        print("Loading results from database...")
        results = loadAllResults()
        by_athlete = groupByAthlete(results)
 
        print("Building encoders...")
        encoders = buildEncoders(results)

        print("Saving chunked tensors...")
        saveAll(by_athlete, encoders, OUTPUT_DIR)
 
    finally:
        closePool()