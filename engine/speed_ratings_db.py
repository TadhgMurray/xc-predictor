# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Speed Rating Engine
# Date: 6/3/2026
# File Title: speed_ratings_db.py
# Purpose: Database read and write functions for the speed rating engine.
#          Separate from scripts/database.py which handles scraper writes.

import psycopg2 
import sys
import psycopg2.extras
from datetime import date

# Add scripts folder so we can import DB_PATH from databse.py.
sys.path.insert(0, "scripts")
from database import getConn

# ------------------------------------------------------------------ #
# LOAD
# ------------------------------------------------------------------ #

# loadResults
# Purpose: Loads all normalized results from the DB that the engine needs
#          to compute speed ratings and course difficulties.
# Arguments: None.
# Output: Returns a list of dicts, one per result. Each dic has keys:
#         result_id, athlete_id, course_name, normalized_time, date, pool.
def loadResults() -> list[dict]:

    # RealDictCursor makes every row come back as a dict instead of a tuple.
    # Without it: row[0] is result_id, row[1] is athlete_id — hard to read.
    # With it: row["result_id"] and row["athlete_id"] work instead.
    with getConn() as conn:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(_buildLoadQuery())

        # fetchall() returns all rows at once as a list.
        # dict(row) converts each RealDictRow to a plain Python dict.
        rows = [dict(row) for row in cursor.fetchall()]
 
    print(f"Loaded {len(rows):,} results from database")
    return rows

# _buildLoadQuery
# Purpose: Returns the SQL string for loadResults.
#          Extracted as a helper so loadResults stays short and readable.
# Arguments: None.
# Output: SQL string.
def _buildLoadQuery() -> str:
    # JOIN results with meets to get course_name and date.
    # JOIN with normalize_distance pool classification happens
    # in Python since pool is computed from grade + gender, not stored in DB.
    # We load grade and gender here so Python can classify the pool.
     return """
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
        -- Skips impossible values.
        AND r.normalized_time > 600
        -- Only load results with a valid date for decay weighting
        AND r.date IS NOT NULL
        AND r.date != ''
        AND m.course_name IS NOT NULL
        AND m.course_name != ''
        -- Order by older dates first.
        ORDER BY r.date ASC
    """

# ------------------------------------------------------------------ #
# SAVE — COURSE DIFFICULTIES
# ------------------------------------------------------------------ #

# saveCourseDifficulties
# Purpose: Writes computed course difficulties to the DB.
#          Always does a full replace — deletes all existing rows
#          then inserts fresh ones, because the engine always recomputes
#          everything from scratch.
# Arguments:
#           difficulties: dict mapping course_name -> dict with keys:
#                         difficulty (float), n_results (int), n_athletes (int).
#           e.g. {"Detweiller Park": {"difficulty": 0.03, "n_results": 4200, "n_athletes": 1100}}
# Output: None.
def saveCourseDifficulties(difficulties: dict):
 
    rows = _buildDifficultyRows(difficulties)
 
    with getConn() as conn:
        cursor = conn.cursor()

        # TRUNCATE drops all rows instantly with no per-row overhead.
        # Safe here because we always recompute everything from scratch.
        # RESTART IDENTITY resets any sequences (not needed here but
        # good practice when truncating).
        cursor.execute("TRUNCATE course_difficulties")

        psycopg2.extras.execute_values(cursor, """
            INSERT INTO course_difficulties
                (course_name, difficulty, n_results, n_athletes, last_updated)
            VALUES %s
        """, rows)
        conn.commit()
 
    print(f"Saved {len(rows):,} course difficulties to database")

# _buildDifficultyRows
# Purpose: Converts the difficulties dict into a list of tuples for
#          execute_values. Extracted to keep saveCourseDifficulties short.
# Arguments:
#           difficulties: same dict as saveCourseDifficulties.
# Output: List of (course_name, difficulty, n_results, n_athletes, today) tuples.
def _buildDifficultyRows(difficulties: dict) -> list[tuple]:
    today = date.today().isoformat()
    # Build a list of tuples to insert in one batch.
    # Each tuples matches the column order in the INSER statement.
    return [
        (
            course_name,
            data["difficulty"],
            data["n_results"],
            data["n_athletes"],
            today
        )
        # .items() returns (key, value) pairs from the dict.
        for course_name, data in difficulties.items()
    ]

# ------------------------------------------------------------------ #
# SAVE — ATHLETE RATINGS
# ------------------------------------------------------------------ #

# saveAthleteRatings
# Purpose: Writes compute athlete speed ratings back to the db.
#          Replaces all existing rows — engine always does a full recompute.
# Arguments:
#           ratings: dict mapping (athlete_id, pool) -> dict with values:
#                    speed_rating, n_races.
# e.g. {(10234, "college_m"): {"speed_rating": 127.3, "n_races": 14}}
def saveAthleteRatings(ratings: dict):

    rows = _buildRatingRows(ratings)
 
    with getConn() as conn:
        cursor = conn.cursor()
        
        # TRUNCATE is significantly faster than DELETE for 4M rows.
        # Safe because the engine always does a full recompute.
        cursor.execute("TRUNCATE athlete_ratings")

        psycopg2.extras.execute_values(cursor, """
            INSERT INTO athlete_ratings
                (athlete_id, pool, speed_rating, n_races, last_updated)
            VALUES %s
        """, rows)
        conn.commit()
 
    print(f"Saved {len(rows):,} athlete ratings to database")

# _buildRatingRows
# Purpose: Converts the ratings dict into a list of tuples for execute_values.
# Arguments:
#           ratings: same dict as saveAthleteRatings.
# Output: List of (athlete_id, pool, speed_rating, n_races, today) tuples.
def _buildRatingRows(ratings: dict) -> list[tuple]:
    today = date.today().isoformat()
    return [
        (
            athlete_id,
            pool,
            data["speed_rating"],
            data["n_races"],
            today
        )
        # Key is a tuple (athlete_id, pool) — unpack it directly.
        for (athlete_id, pool), data in ratings.items()
    ]

# ------------------------------------------------------------------ #
# SAVE — PER-RESULT SPEED RATINGS
# ------------------------------------------------------------------ #

# saveResultSpeedRatings
# Purpose: Writes per-result speed ratings back to the results table.
#          Uses a temp table pattern instead of batched UPDATE loops —
#          one bulk insert into a temp table, then one single UPDATE
#          joining against it. Much faster than 400 separate execute_values
#          UPDATE calls each scanning the full results table.
# Arguments:
#           result_ratings: dict mapping result_id -> speed_rating float.
#           e.g. {10234: 118.4, 10235: 102.1}
# Output: None.
def saveResultSpeedRatings(result_ratings: dict):
 
    if not result_ratings:
        return

    # Convert dict to list of (result_id, speed_rating) tuples.
    # Note: value first in old version was (speed_rating, result_id) to
    # match SET/WHERE order — now we use named columns in the temp table
    # so order doesn't matter, but (result_id, speed_rating) is clearer.
    all_rows = [
        (result_id, speed_rating)
        for result_id, speed_rating in result_ratings.items()
    ]
 
    print(f"Saving {len(all_rows):,} result speed ratings via temp table...")

    with getConn() as conn:

        cursor = conn.cursor()

        # Step 1 — create a temp table to stage the new ratings.
        # TEMP means it's only visible to this connection — no other
        # session can see or interfere with it.
        # ON COMMIT DROP means Postgres destroys it automatically when
        # the transaction commits, so no cleanup needed even if an
        # exception fires mid-save.
        cursor.execute("""
            CREATE TEMP TABLE tmp_speed_ratings (
                result_id    BIGINT,
                speed_rating REAL
            ) ON COMMIT DROP
        """)

        # Step 2 — bulk insert all rows into the temp table in one shot.
        # execute_values sends rows in pages of 50,000 — each page is
        # one round trip to Postgres. For 20M rows that's 400 inserts
        # into a tiny temp table with no indexes, which is very fast.
        # Inserting into a temp table is faster than updating the real
        # table directly because there are no indexes to maintain and
        # no MVCC overhead from updating existing rows.
        psycopg2.extras.execute_values(cursor, """
            INSERT INTO tmp_speed_ratings (result_id, speed_rating)
            VALUES %s
        """, all_rows, page_size=50000)

        print("Temp table loaded — running bulk UPDATE...")

        # Step 3 — single UPDATE joining the temp table to results.
        # FROM tmp_speed_ratings tells Postgres to join the two tables
        # and update every results row whose result_id matches a row
        # in the temp table.
        # This is ONE pass over the results table instead of 400 separate
        # UPDATE calls each doing their own full table scan.
        cursor.execute("""
            UPDATE results
            SET speed_rating = t.speed_rating
            FROM tmp_speed_ratings t
            WHERE results.result_id = t.result_id
        """)

        # Step 4 — commit triggers ON COMMIT DROP, destroying the temp
        # table automatically. The UPDATE is also committed here.
        conn.commit()

    print(f"Saved {len(all_rows):,} result speed ratings")
