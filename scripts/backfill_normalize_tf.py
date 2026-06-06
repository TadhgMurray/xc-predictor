# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Database for Scraper
# Date: 6/4/2026
# File Title: backfill_normalize_tf.py
# Purpose: Reads every result from the results_tf, computes its normalized flat
#          5k flat equivalent time, and writes it back to the db, specifically
#          to the `normalized_time` column in the `results` table. Resume-safe.
#          Only processes running events 800m and above.
#          TODO: apply banked track correction from BANKED_TRACK_CORRECTIONS.

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
    cursor.execute("""
        SELECT COUNT(*) FROM results_tf
        WHERE normalized_time IS NULL
        AND is_relay = 0
    """)
    total = cursor.fetchone()[0]
    print(f"TF rows to normalize: {total:,}")

    # Load athlete genders into memory for pool classification.
    # Same pattern as backfill_normalize.py — one dict lookup per row
    # is much faster than a JOIN on every batch.
    cursor.execute("SELECT athlete_id, gender FROM athletes")
    athlete_genders = {row[0]: row[1] for row in cursor.fetchall()}
    print(f"Loaded {len(athlete_genders):,} athletes into memory")

    # Track progress.
    processed = 0
    updated = 0
    skipped = 0
    # Tracks the highest result_id we've seen — next batch starts after this.
    last_id = 0
    # Gets start time to track elapsed time later.
    start_time = time.time()

    while True:

        # Cursor-based pagination — same pattern as backfill_normalize.py.
        # Fetches result_id, time_seconds, grade, athlete_id, event_short.
        # We don't need div_id here because distance comes from EVENT_DISTANCES_TF
        # keyed by event_short — not from the meets table like XC.
        batch = cursor.execute("""
            SELECT result_id, time_seconds, grade, athlete_id, event_short
            FROM results_tf
            WHERE normalized_time IS NULL
            AND is_relay = 0
            AND result_id > ?
            ORDER BY result_id
            LIMIT ?
        """, (last_id, BATCH_SIZE)).fetchall()
        
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
            last_id = result_id

            # Skip sentinel values — athletic.net uses 999999 and similar large
            # numbers to indicate DNF, DNS, or DQ. Not real times.
            if time_seconds is None or time_seconds > 100000:
                skipped += 1
                continue

            # Skip events with no distance mapping — field events,
            # short sprints, or unknown event codes.
            distance_meters = EVENT_DISTANCES_TF.get(event_short)
            if distance_meters is None:
                skipped += 1
                continue

            # Look up gender from in-memory dictionaries.
            # .get() returns None if the key isn't found — safe default.
            gender = athlete_genders.get(athlete_id)
    
            # Run the normalization.
            result = normalizeResult(time_seconds, distance, grade, gender)

            # Can't normalize this row - leave it NULL and skip it.
            if result["drop"] or result["normalized_time"] is None:
                skipped += 1
                continue
            
            # Add to the update list as a tuple. Value first, then the WHERE id.
            updates.append((result["normalized_time"], result_id))
            updated += 1


        # Write all updates in one batch using executemany.
        # executemany runs the same SQL once per tuple in the list —
        # much faster than calling execute() in a loop since it's
        # one database round-trip instead of 10,000.
        if updates:
            cursor.executemany("""
                UPDATE results_tf
                SET normalized_time = ?
                WHERE result_id = ?
            """, updates)
            
            conn.commit()

        processed += len(batch)

        # Re-query remaining count each batch — accurate even as scraper adds rows.
        cursor.execute("""
            SELECT COUNT(*) FROM results_tf
            WHERE normalized_time IS NULL
            AND is_relay = 0
        """)
        remaining = cursor.fetchone()[0]

        # Print progress every batch.
        elapsed = time.time() - start_time
        rate = processed / elapsed if elapsed > 0 else 0
        eta_min = int(eta_seconds // 60)
        eta_sec = int(eta_seconds % 60)
        # :.0f means float with 0 decimal places, : tells Python 
        # that formatting instructions follow.
        print(f"Processed {processed:,} | Updated {updated:,} | "
              f"Skipped {skipped:,} | Rate {rate:,.0f}/s | "
              f"ETA {eta_min}m {eta_sec}s")

    
    conn.close()
    print(f"\nDone. Updated : {updated:,}, Skipped: {skipped:,}")
    # :.1f means float with 1 decimal place, : tells Python 
    # that formatting instructions follow.
    print(f"Total time: {time.time() - start_time:.1f}s")

if __name__ == "__main__":
    main()

