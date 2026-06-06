# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database for Scraper
# Date: 6/2/2026
# File Title: backfill_normalize.py
# Purpose: Reads every result from the db, computes its normalized flat
#          5k flat equivalent time, and writes it back to the db, specifically
#          to the `normalized_time` column in the `results` table. Resume-safe.

import sqlite3
import sys
import time

# Add the engine folder to the path so we can import normalize.py.
# sys.path is the list of folders Python searches when you do "import X".
# We insert at position 0 so it's checked first before anything else.
sys.path.insert(0, "engine")

from normalize_distance import normalizeResult

# Add scripts folder so we can import database.py
sys.path.insert(0, "scripts")
from database import DB_PATH

# How many rows to process in each batch. More means faster but more memory
# because most of the time is spent talking to the database.
BATCH_SIZE = 10000

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
        AND r.result_id > ?
        -- Filter out '-' grades at the SQL level so SQLite never returns
        -- them. 352K rows would otherwise be fetched and immediately dropped
        -- in Python — wasteful and slow as fuck.
        AND r.grade != '-'
        -- ORDER BY is required for the cursor pattern to work correctly.
        -- Without it SQLite returns rows in unpredictable order and
        -- last_id might skip rows
        ORDER BY r.result_id
        LIMIT ?
    """, (last_id, BATCH_SIZE))

    return cursor.fetchall()

def main():

    # Connect to the database.
    conn = sqlite3.connect(DB_PATH)
    # WAL mode lets the scraper and normalizer write simultaneously
    # without blocking each other. Default mode locks the entire file
    # on any write — WAL uses a separate log file instead.
    conn.execute("PRAGMA journal_mode=WAL")
    # Get a cursor for executing queries.
    cursor = conn.cursor()

    # Load all XC div IDs from meets into a set for fast lookup.
    # Only normalize results from XC meets — track distances will be
    # normalized separately once exponents are fitted empirically.
    # set() means lookup is O(1) — instant regardless of size.
    # This connects the meet_queue and meets tables using JOIN where
    # the meet ids are the same so we can select all the ids in meet_queue
    # and get the divisions from there.
    cursor.execute("SELECT m.div_id FROM meets m JOIN meet_queue mq ON m.meet_id = mq.meet_id WHERE mq.sport = 'XC'")
    xc_div_ids = {row[0] for row in cursor.fetchall()}
    print(f"Loaded {len(xc_div_ids):,} XC div IDs")

    # Count how mant results still need to be normalized.
    cursor.execute("SELECT COUNT(*) FROM results WHERE normalized_time is NULL")
    total = cursor.fetchone()[0]
    # :, is a format specifier, tells Python how to format number into a string.
    # : means formatting instructions follow, 
    # , means to include commas every 3 digits.
    print(f"Rows to process: {total:,}")

    # Track progress.
    processed = 0
    skipped = 0
    # Tracks the highest result_id we've seen — next batch starts after this.
    last_id = 0
    # Gets start time to track elapsed time later.
    start_time = time.time()

    # Load all meet distances into a dictionary keyed by div_id.
    # {div_id: distance} — instant lookup instead of a JOIN every batch.
    # We do this once at startup rather than per-batch because meets
    # never change while the normalizer is running. We use div_id so
    # it takes the division distance, not the meet distance (which is
    # the last division's distance).
    cursor.execute("SELECT div_id, distance FROM meets")
    # row 0 the key(meet_id), row 1 value(diustance).
    meet_distances = {row[0]: row[1] for row in cursor.fetchall()}

    # Load all athlete genders into a dictionary keyed by athlete_id.
    # {athlete_id: gender} — same idea.
    cursor.execute("SELECT athlete_id, gender FROM athletes")
    athlete_genders = {row[0]: row[1] for row in cursor.fetchall()}

    print(f"Loaded {len(meet_distances):,} meets and {len(athlete_genders):,} athletes into memory")

    while True:

        # Fetch the next batch of unprocessed rows.
        batch = fetchBatch(cursor, last_id)
        
        # If the batch is empty, the queue is empty and we're done.
        if not batch:
            break
        
        # Build a list of (normalized_time, result_id) tuples to update in the db.
        # We collect all updates first, then write them at once.
        updates = []

        # Normalize each result row in batch, add to updates list.
        for result_id, time_seconds, grade, div_id, athlete_id  in batch:
            
            # Advance last_id for every row — skipped or not.
            # If this only ran for processed rows, skipped rows at the
            # end of a batch would cause the next batch to re-fetch
            # the same rows forever, hanging the normalizer.
            last_id = max(last_id, result_id)

            # Skip sentinel values — athletic.net uses 999999 and similar large
            # numbers to indicate DNF, DNS, or DQ. Not real times.
            if time_seconds is None or time_seconds > 100000:
                skipped += 1
                continue

            # Look up distance and gender from in-memory dictionaries.
            # .get() returns None if the key isn't found — safe default.
            distance = meet_distances.get(div_id)
            gender = athlete_genders.get(athlete_id)


            # Skip non-XC meets — track distances use placeholder exponents
            # until empirically fitted from TF data.
            if div_id not in xc_div_ids:
                skipped += 1
                continue
    
            # Run the normalization.
            result = normalizeResult(time_seconds, distance, grade, gender)

            # Can't normalize this row - leave it NULL and skip it.
            if result["drop"] or result["normalized_time"] is None:
                skipped += 1
                continue
            
            # Add to the update list as a tuple. Value first, then the WHERE id.
            updates.append((result["normalized_time"], result_id))
            processed += 1


        # Write all updates in one batch using executemany.
        # executemany runs the same SQL once per tuple in the list —
        # much faster than calling execute() in a loop since it's
        # one database round-trip instead of 10,000.
        if updates:
            cursor.executemany("""
                UPDATE results
                SET normalized_time = ?
                WHERE result_id = ?
            """, updates)
            
            conn.commit()

        # Re-query remaining count each batch — accurate even as scraper adds rows
        cursor.execute("SELECT COUNT(*) FROM results WHERE normalized_time IS NULL")
        remaining = cursor.fetchone()[0]

        # Print progress every batch.
        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        eta = remaining / rate if rate > 0 else 0
        # :.0f means float with 0 decimal places, : tells Python 
        # that formatting instructions follow.
        print(f"Processed: {processed:,} | Skipped: {skipped:,} | Remaining: {remaining:,} | Rate: {rate:.0f}/s | ETA: {eta:.0f}s")

    
    conn.close()
    print(f"\nDone. Processed: {processed:,}, Skipped: {skipped:,}")
    # :.1f means float with 1 decimal place, : tells Python 
    # that formatting instructions follow.
    print(f"Total time: {time.time() - start_time:.1f}s")

if __name__ == "__main__":
    main()

