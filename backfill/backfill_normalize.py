# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database for Scraper
# Date: 6/2/2026
# File Title: backfill_normalize.py
# Purpose: Reads every result from the db, computes its normalized flat
#          5k flat equivalent time, and writes it back to the db, specifically
#          to the `normalized_time` column in the `results` table. Resume-safe.

import sys
import time
import psycopg2.extras

# Add the engine folder to the path so we can import normalize_distance.py.
# sys.path is the list of folders Python searches when you do "import X".
# We insert at position 0 so it's checked first before anything else.
sys.path.insert(0, "engine")
from normalize_distance import normalizeResult
 
# Add scripts folder so we can import database.py.
sys.path.insert(0, "scripts")
from database import getConn, initPool

# How many rows to process in each batch. More means faster but more memory
# because most of the time is spent talking to the database.
BATCH_SIZE = 1000000


# fetchBatch
# Purpose: Fetches a batch of results that haven't been normalize yet.
# Arguments:
#           cursor: the databse cursor to execute the query.
#           last_id: last processed meet id to skip to to continue normalizing.
# Output: returns a list of tuples, one per result row. Each tuple contains:
#         result_id: the id of the result, used to write back to the db.
#         time_seconds: the original time in seconds.
#         grade: the grade of the result.
#         meet_id: used to look up distance from meet_distances dictionary.
#         athlete_id: used to look up gender from athlete_genders dictionary.
def fetchBatch(cursor, last_id: int) -> list:

    # WHERE normalize_time is NULL skips already-processed row.
    # Uses last_id as a cursor instead of OFFSET so the query stays
    # fast regardless of how many rows have already been processed.
    # OFFSET scans and discards all previous rows on every call —
    # at millions of rows that gets very slow. result_id > last_id
    # jumps straight to the right place using the primary key index
    # LIMIT lets us page through in batches - LIMIT says how many
    # rows to return(batch size).
    cursor.execute("""
        SELECT
            r.result_id,
            r.time_seconds,
            r.grade,
            -- These are now div_id and athlete_id, not distance and gender.
            -- Distance and gender come from the in-memory dictionaries instead
            -- of the other tables joining.
            r.div_id,
            r.athlete_id
        FROM results r
        WHERE r.normalized_time is NULL
        -- result_id > last_id is the cursor — skips rows already processed.
        -- Faster than OFFSET because it uses the primary key index directly.
        AND r.result_id > %s
        -- Filter out '-' grades at the SQL level so SQLite never returns
        -- them. 352K rows would otherwise be fetched and immediately dropped
        -- in Python — wasteful and slow as fuck.
        AND r.grade != '-'
        -- ORDER BY is required for the cursor pattern to work correctly.
        -- Without it SQLite returns rows in unpredictable order and
        -- last_id might skip rows
        ORDER BY r.result_id
        LIMIT %s
    """, (last_id, BATCH_SIZE))

    return cursor.fetchall()

# ------------------------------------------------------------------ #
# LOOKUP TABLES
# ------------------------------------------------------------------ #

 
# loadLookups
# Purpose: Loads all meet distances and athlete genders into memory once
#          at startup. Faster than joining on every batch.
# Arguments:
#           cursor: open psycopg2 cursor.
# Output: Tuple of (meet_distances dict, athlete_genders dict, xc_div_ids set).
#         meet_distances: {div_id: distance}
#         athlete_genders: {athlete_id: gender}
#         xc_div_ids: set of div_ids from XC meets only
def loadLookups(cursor) -> tuple[dict, dict, set]:

    # Distance keyed by div_id — we use div_id not meet_id because
    # div_id is unique per division, meet_id is not. Creates a
    # dict with div_id as key and distance as value.
    cursor.execute("SELECT div_id, distance FROM meets")
    meet_distances = {row[0]: row[1] for row in cursor.fetchall()}
 
    cursor.execute("SELECT athlete_id, gender FROM athletes")
    athlete_genders = {row[0]: row[1] for row in cursor.fetchall()}


    # Only normalize XC meets — TF uses a separate backfill script.
    # JOIN with meet_queue to filter by sport = 'XC'.
    cursor.execute("""
        SELECT m.div_id
        FROM meets m
        JOIN meet_queue mq ON m.meet_id = mq.meet_id
        WHERE mq.sport = 'XC'
    """)
    xc_div_ids = {row[0] for row in cursor.fetchall()}
 
    print(f"Loaded {len(meet_distances):,} meets, "
          f"{len(athlete_genders):,} athletes, "
          f"{len(xc_div_ids):,} XC div IDs")
 
    return meet_distances, athlete_genders, xc_div_ids

# ------------------------------------------------------------------ #
# NORMALIZE ONE BATCH
# ------------------------------------------------------------------ #

# processBatch
# Purpose: Normalizes one batch of raw result rows.
# Arguments:
#           batch: list of (result_id, time_seconds, grade, div_id, athlete_id)
#           meet_distances: {div_id: distance} lookup dict
#           athlete_genders: {athlete_id: gender} lookup dict
#           xc_div_ids: set of valid XC div_ids
# Output: Tuple of (updates, n_skipped).
#         updates: list of (normalized_time, result_id) tuples ready to write.
#         n_skipped: count of rows that couldn't be normalized.
def processBatch(batch, meet_distances, athlete_genders, xc_div_ids) -> tuple[list, int]:

    updates = []
    n_skipped = 0
 
    for result_id, time_seconds, grade, div_id, athlete_id in batch:
 
        # Skip sentinel values — athletic.net uses large numbers for DNF/DNS/DQ.
        if time_seconds is None or time_seconds > 100000:
            n_skipped += 1
            continue
 
        # Skip non-XC meets — TF backfill handles those separately.
        if div_id not in xc_div_ids:
            n_skipped += 1
            continue
 
        # Look up distance and gender from in-memory dicts.
        # .get() returns None safely if the key isn't found.
        distance = meet_distances.get(div_id)
        gender = athlete_genders.get(athlete_id)
 
        # Normalize — returns dict with normalized_time, pool, drop keys.
        result = normalizeResult(time_seconds, distance, grade, gender)
 
        if result["drop"] or result["normalized_time"] is None:
            n_skipped += 1
            continue
 
        # (value, id) order matches the SET/WHERE in the UPDATE statement.
        updates.append((result["normalized_time"], result_id))
 
    return updates, n_skipped

# ------------------------------------------------------------------ #
# WRITE BATCH
# ------------------------------------------------------------------ #

# writeBatch
# Purpose: Writes normalized times back to the results table in one query.
#          Uses execute_values UPDATE pattern — much faster than executemany
#          because it sends all rows in one round trip instead of one per row.
# Arguments:
#           conn: open psycopg2 connection.
#           updates: list of (normalized_time, result_id) tuples.
# Output: None.
def writeBatch(conn, updates: list):
 
    if not updates:
        return
 
    cursor = conn.cursor()
 
    # UPDATE ... FROM (VALUES %s) is Postgres's bulk update pattern.
    # execute_values fills the VALUES clause with all tuples at once.
    # The AS data(normalized_time, result_id) gives the values table
    # column names so the WHERE clause can reference them. We create
    # another table with the normalize time and result id, and then join
    # it to the results table to update in one go.
    psycopg2.extras.execute_values(cursor, """
        UPDATE results
        SET normalized_time = data.normalized_time
        FROM (VALUES %s) AS data(normalized_time, result_id)
        WHERE results.result_id = data.result_id
    """, updates)
 
    conn.commit()

# ------------------------------------------------------------------ #
# PROGRESS
# ------------------------------------------------------------------ #

# printProgress
# Purpose: Prints a progress line after each batch.
# Arguments:
#           processed: total rows normalized so far.
#           skipped: total rows skipped so far.
#           conn: open connection for querying remaining count.
#           start_time: time.time() from start of run.
# Output: None.
def printProgress(processed: int, skipped: int, conn, start_time: float):
 
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM results WHERE normalized_time IS NULL")
    remaining = cursor.fetchone()[0]
 
    elapsed = time.time() - start_time
    rate = processed / elapsed if elapsed > 0 else 0
    eta = remaining / rate if rate > 0 else 0
 
    print(f"Processed: {processed:,} | Skipped: {skipped:,} | "
          f"Remaining: {remaining:,} | Rate: {rate:.0f}/s | ETA: {eta:.0f}s")
    
# ------------------------------------------------------------------ #
# MAIN
# ------------------------------------------------------------------ #
 

def main():

    initPool()
 
    with getConn() as conn:
 
        cursor = conn.cursor()
 
        # Load lookups once at startup.
        meet_distances, athlete_genders, xc_div_ids = loadLookups(cursor)
 
        # Count total rows to normalize for initial progress display.
        cursor.execute("SELECT COUNT(*) FROM results WHERE normalized_time IS NULL")
        total = cursor.fetchone()[0]
        print(f"Rows to process: {total:,}")
 
        # Tracks highest result_id seen — next batch starts after this.
        # Starts at 0 so first batch fetches from the beginning.
        last_id = 0
        processed = 0
        skipped = 0
        start_time = time.time()

        while True:
 
            # Fetch next batch of unnormalized rows.
            batch = fetchBatch(cursor, last_id)
 
            # Empty batch means we're done.
            if not batch:
                break
 
            # Advance last_id to the highest result_id in this batch.
            # max() of the first element of each tuple.
            last_id = max(row[0] for row in batch)
 
            # Normalize the batch.
            updates, n_skipped = processBatch(
                batch, meet_distances, athlete_genders, xc_div_ids
            )
 
            # Write normalized times back to DB.
            writeBatch(conn, updates)
 
            processed += len(updates)
            skipped += n_skipped
 
            printProgress(processed, skipped, conn, start_time)
 
    print(f"\nDone. Processed: {processed:,} | Skipped: {skipped:,}")
    print(f"Total time: {time.time() - start_time:.1f}s")

if __name__ == "__main__":
    main()

