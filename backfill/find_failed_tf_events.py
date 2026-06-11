# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Backfill
# Date: 6/8/2026
# File Title: find_failed_tf_events.py
# Purpose: Finds TF event/div combos that exist in meets_tf but have no
#          matching row in tf_scraped_events — meaning they were planned
#          but never successfully scraped. Inserts them into
#          tf_recovery_queue so recover_tf_events.py can retry them.

import sys
import os

# Add the scripts/ directory to the path so we can import databse.py.
# We go up one level from this directory and then enter the scripts folder to our path.
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from database import initPool, closePool, getConn, populateRecoveryQueue, countRecoveryRemaining

# HOW MANY ROWS TO FETCH AT ONCE FROM THE GAP QUERY.
# The gap could be millions of rows. We fetch in chunks to avoid
# loading everything into memory at once and to give us progress logs.
CHUNK_SIZE = 10000

# _fetchFailedEventsChunk
# Purpose: Queries one chunk of (meet_id, event_short, div_id) tuples
#          that exist in meets_tf but NOT in tf_scraped_events.
#          Uses keyset pagination instead of OFFSET — passes the last
#          row seen as a cursor so Postgres can seek directly to it
#          via the index rather than reading and discarding all prior rows.
# Arguments:
#           last_seen: tuple of (meet_id, event_short, div_id) from the
#                      last row of the previous chunk. None on first call.
#           limit: how many rows to return.
# Output: List of (meet_id, event_short, div_id) tuples.
def _fetchFailedEventsChunk(last_seen: tuple | None, limit: int) -> list[tuple]:

    with getConn() as conn:
        cursor = conn.cursor()

        if last_seen is None:
            # First chunk — no cursor yet, start from the beginning.
            cursor.execute("""
                SELECT
                    m.meet_id,
                    m.event_short,
                    m.div_id
                FROM meets_tf m
                -- For every row in meet_tf, Postgres tried to find
                -- a matching one in tf_scraped_events and it joins them.
                -- If no matching row is found it fills tf_scraped_events
                -- with NULL.
                LEFT JOIN tf_scraped_events s
                    ON  m.meet_id     = s.meet_id
                    AND m.event_short = s.event_short
                    AND m.div_id      = s.div_id
                -- Then it finds these events with no matching
                -- columns and those are the meets we're recovering.
                WHERE s.meet_id IS NULL
                ORDER BY m.meet_id, m.event_short, m.div_id
                LIMIT %s
            """, (limit,))
        else:
            # Subsequent chunks — start after the last row we saw.
            # (m.meet_id, m.event_short, m.div_id) > (%s, %s, %s) is a
            # row comparison — Postgres evaluates left to right, so it
            # first checks meet_id, then event_short, then div_id.
            # This is fast because it can use the index to seek directly
            # to that position instead of scanning from the beginning.
            last_meet_id, last_event_short, last_div_id = last_seen
            cursor.execute("""
                SELECT
                    m.meet_id,
                    m.event_short,
                    m.div_id
                FROM meets_tf m
                LEFT JOIN tf_scraped_events s
                    ON  m.meet_id     = s.meet_id
                    AND m.event_short = s.event_short
                    AND m.div_id      = s.div_id
                WHERE s.meet_id IS NULL
                AND (m.meet_id, m.event_short, m.div_id) > (%s, %s, %s)
                ORDER BY m.meet_id, m.event_short, m.div_id
                LIMIT %s
            """, (last_meet_id, last_event_short, last_div_id, limit))

        return cursor.fetchall()

# _findAndQueueFailedEvents
# Purpose: Orchestrates teh gap query and queue population.
#          Fetches failed events in chunks and inserts them into
#          tf_recovey_queue.
# Arguments: None.
# Output: Total number of newly inserted rows across all chunks.
def _findAndQueueFailedEvents() -> int:

    total_inserted = 0
    last_seen = None  # Keyset cursor — None means start from beginning

    print("[Recovery] Scanning for failed TF events...")

    while True:

        #Fetch one chunk of failed events from the DB.
        chunk = _fetchFailedEventsChunk(last_seen, CHUNK_SIZE)
 
        # Empty chunk means we've processed everything.
        if not chunk:
            break

        # Insert into tf_recovery_queue. populateRecoveryQueue returns
        # the number of rows actually inserted (skipping duplicates).
        inserted = populateRecoveryQueue(chunk)
        total_inserted += inserted
        
        # Last row of this chunk becomes the cursor for the next chunk
        last_seen = chunk[-1]
 
        print(f"[Recovery] Processed {total_inserted} gaps so far "
                f"({inserted} new in this chunk)...")
        
    return total_inserted

# main
# Purpose: Finds failed track and field events and queues them.
def main():

    # Opens connections pool for cursor.
    initPool()

    try:
        total_inserted = _findAndQueueFailedEvents()
        remaining = countRecoveryRemaining()
        print(f"\n[Recovery] Done. {total_inserted} new events queued.")
        print(f"[Recovery] Total unscraped in tf_recovery_queue: {remaining}")

    except Exception as e:
        print(f"[Recovery] ERROR: {e}")
        raise
 
    finally:
        closePool()

if __name__ == "__main__":
    print("[Recovery] Starting...")
    main()