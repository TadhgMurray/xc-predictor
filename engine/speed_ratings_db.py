# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Speed Rating Engine
# Date: 6/3/2026
# File Title: speed_ratings_db.py
# Purpose: Database read and write functions for the speed rating engine.
#          Separate from scripts/database.py which handles scraper writes.

import sqlite3
import sys
from datetime import date

# Add scripts folder so we can import DB_PATH from databse.py.
sys.path.insert(0, "scripts")
from database import getConn

# loadResults
# Purpose: Loads all normalized results from the DB that the engine needs
#          to compute speed ratings and course difficulties.
# Arguments: None.
# Output: Returns a list of dicts, one per result. Each dic has keys:
#         result_id, athlete_id, course_name, normalized_time, date, pool.
def loadResults() -> list[dict]:

    conn = getConn

    # row_factory makes each row come back as a dict instead of a tuple/
    # Without this, row[0] is result_id, row[1] is athlete_id etc - which
    # is head to read. With it, row["result_id"] and row["athlte_id"] work instead.
    conn.row_factory = sqlite3.Row
    
    cursor = conn.cursor()

    # JOIN results with meets to get course_name and date.
    # JOIN with normalize_distance pool classification happens
    # in Python since pool is computed from grade + gender, not stored in DB.
    # We load grade and gender here so Python can classify the pool.
    cursor.execute("""
        SELECT
            r.result_id,
            r.athlete_id,
            r.normalized_time,
            r.grade,
            r.date,
            m.course_name,
            a.gender
        FROM results r
        -- For every row in results finds the row in meets where the meet_id
        -- matches and then glue them together into a wider row.
        -- Use div_id to join results to meets — div_id is the primary key
        -- of meets so each result matches exactly one row.
        -- meet_id is NOT unique in meets — one meet has multiple divisions,
        -- so joining on meet_id multiplies every result by the number of divisions.
        JOIN meets m ON r.div_id = m.div_id
        -- For every row in results finds the row in athletes where the athlete_id
        -- matches and then glue them together into a wider row.
        JOIN athletes a ON r.athlete_id = a.athlete_id
        -- Only load results that have been normalized
        WHERE r.normalized_time IS NOT NULL
        -- Skip sentinel values and unreasonably large times.
        -- 100000 seconds is over 27 hours — no real race result.
        AND r.normalized_time < 100000
        -- Only load results with a valid date for decay weighting
        AND r.date IS NOT NULL
        AND r.date != ''
        AND m.course_name IS NOT NULL
        AND m.course_name != ''
        -- Order by older dates first.
        ORDER BY r.date ASC
    """)

    # Convert each sqlite3.Row object to a plain dict. dict(row) unpacks
    # all columns,
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # :, means separate entries by commas.
    print(f"Loaded {len(rows):,} results from database")
    return rows

# saveCourseDifficulties
# Purpose: Writes computed course difficulties back to the db.
#          Replaces all existing rows - engine always does a full recompute.
# Arguments:
#           difficulties: dict mapping course_name -> dict with keys:
#                         difficulty, n_results, n_athletes.
def saveCourseDifficulties(difficulties: dict):

    conn = getConn()
    cursor = conn.cursor()

    # Today's date as a string for last_updated column.
    today = date.today().isoformat()

    # DELETE then INSERT is simpler than INSERT OR REPLACE for this case
    # because we're always recompujting everything from scratch.
    # If we used INSERT OR REPLACE we'd leave stale rows for
    # courses that no longer exist in the data.
    cursor.execute("DELETE FROM course_difficulties")

    # Build a list of tuples to insert in one batch.
    # Each tuples matches the column order in the INSER statement.
    rows = [
        # Makes this tuple for each course.
        (
            course_name,
            data["difficulty"],
            data["n_results"],
            data["n_athletes"],
            today
        )
        # .items() returns (key, value) pairs from the dictionary.
        # course_name is the key, data is the valuie dict.
        for course_name, data in difficulties.items()
    ]

    # executemany inserts all rows in one db round-trip.
    cursor.executemany("""
        INSERT INTO course_difficulties (
            course_name, difficulty, n_results, n_athletes, last_updated
        ) VALUES (?, ?, ?, ?, ?)
    """, rows)

    conn.commit()
    conn.close()
    print(f"Saved {len(rows):,} course difficulties to database")


# saveAthleteRatings
# Purpose: Writes compute athlete speed ratings back to the db.
#          Replaces all existing rows — engine always does a full recompute.
# Arguments:
#           ratings: dict mapping (athlete_id, pool) -> dict with values:
#                    speed_rating, n_races.
# e.g. {(10234, "college_m"): {"speed_rating": 127.3, "n_races": 14}}
def saveAthleteRatings(ratings: dict):

    conn = getConn()
    cursor = conn.cursor()

    today = date.today().isoformat()

    cursor.execute("DELETE FROM athlete_ratings")

    rows = [
        (
            athlete_id,
            pool,
            data["speed_rating"],
            data["n_races"],
            today
        )
        # key is a tuple (athlete_id, pool) — we unpack it directly
        # in the for loop using (athlete_id, pool), data syntax.
        for (athlete_id, pool), data in ratings.items()
    ]

    cursor.executemany("""
        INSERT INTO athlete_ratings (
            athlete_id, pool, speed_rating, n_races, last_updated
        ) VALUES (?, ?, ?, ?, ?)
    """, rows)

    conn.commit()
    conn.close()
    print(f"Saved {len(rows):,} athlete ratings to database")

# saveResultSpeedRatings
# Purpose: Writes per-result speed ratings back to the results table.
# Arguments:
#           result_ratings: dict mapping result_id -> speed_rating float.
#           e.g. {10234: 118.4, 10235: 102.1, ...}
# Output: None.
def saveResultSpeedRatings(result_ratings: dict):

    conn = getConn()
    cursor = conn.cursor()

    # Build list of (speed_rating, result_id) tuples for executemany.
    # Value first, then the WHERE id, matches the UPDATE statement below.
    rows = [
        (speed_rating, result_id)
        for result_id, speed_rating in result_ratings.items()
    ]

    # executemany runs the UPDATE once per tuple in one db round-trip.
    cursor.executemany("""
        UPDATE results
        SET speed_rating = ?
        WHERE result_id = ?
    """, rows)

    conn.commit()
    conn.close()
    print(f"Saved {len(rows):,} per-result speed ratings to database")
