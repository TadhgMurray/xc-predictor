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
                       
                - Altitude of the meet location.
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
            JOIN meets_tf  m  ON r.div_id     = m.div_id
                             AND r.event_id   = m.event_id
 
            LEFT JOIN weather w
                ON  m.meet_id = w.meet_id
                AND w.hour    = %s
 
            WHERE r.normalized_time IS NOT NULL
            AND   r.normalized_time > %s
            AND   r.date IS NOT NULL
            AND   r.date != ''
            AND   r.is_relay = 0
 
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
 
        # Subsequent chunks will add:
        #   - buildEncoders(results)
        #   - buildTrainingExamples(by_athlete, encoders)
        #   - saveDataset(examples)
 
        print("Chunk 1 complete — DB load and grouping working.")
 
    finally:
        closePool()