# Project: xc-predictor
# File:    backfill/fill_meets_tf_event_short.py
# Purpose: After the meta-only re-scrape lays down meets_tf rows, fill the two
#          per-event fields GetMeetData does NOT carry -- event_short and
#          distance_meters -- from results_tf, which already has event_short on
#          every result. Matches on the full key (meet_id, div_id, event_id) (the
#          widened PK), so no cross-meet contamination. distance_meters is then
#          derived from event_short via the same EVENT_DISTANCES_TF map the
#          scraper uses, in Python, so the logic lives in one place.
#          Idempotent: only touches rows where event_short IS NULL. Batched.

import sys
sys.path.insert(0, "engine")

from database import initPool, getConn, closePool
from normalize_distance import EVENT_DISTANCES_TF


# fillEventShortFromResults
# Purpose: Copy event_short into meets_tf from results_tf on the full key. One
#          UPDATE; results_tf has an index on meet_id so the join is cheap
#          per-meet. Only fills rows still NULL (resumable / re-runnable).
# Arguments:
#           conn: open connection (caller commits)
# Output:  int rows updated
def fillEventShortFromResults(conn):
    # DISTINCT ON picks one event_short per (meet_id, div_id, event_id) -- they're
    # all identical for a given event-div, so any one is correct.
    sql = """
        UPDATE meets_tf m
        SET    event_short = src.event_short
        FROM (
            SELECT DISTINCT ON (meet_id, div_id, event_id)
                   meet_id, div_id, event_id, event_short
            FROM   results_tf
            WHERE  source = 'anet' AND event_short IS NOT NULL
        ) src
        WHERE m.meet_id  = src.meet_id
          AND m.div_id   = src.div_id
          AND m.event_id = src.event_id
          AND m.event_short IS NULL
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount


# distinctEventShorts
# Purpose: Every event_short now present in meets_tf with a NULL distance, so we
#          can derive distance_meters for the mapped ones in Python.
# Arguments:
#           conn: open connection
# Output:  list[str] event_short values needing a distance
def distinctEventShorts(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT event_short
            FROM   meets_tf
            WHERE  event_short IS NOT NULL AND distance_meters IS NULL
        """)
        return [r[0] for r in cur.fetchall()]


# fillDistanceForEvent
# Purpose: Set distance_meters for all meets_tf rows of one event_short, using
#          the EVENT_DISTANCES_TF map. Events not in the map are left NULL (by
#          design -- sprints/field events don't get normalized).
# Arguments:
#           conn:        open connection (caller commits)
#           event_short: the event code
#           distance:    meters from the map
# Output:  int rows updated
def fillDistanceForEvent(conn, event_short, distance):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE meets_tf
            SET    distance_meters = %s
            WHERE  event_short = %s AND distance_meters IS NULL
        """, (distance, event_short))
        return cur.rowcount


# main
# Purpose: Run the event_short copy, then the distance derivation. Read-then-write
#          with explicit commits. Prints what it filled.
# Output:  none (prints)
def main():
    initPool()
    try:
        with getConn() as conn:
            print("Filling event_short from results_tf ...")
            n = fillEventShortFromResults(conn)
            conn.commit()
            print(f"  event_short filled on {n:,} meets_tf rows")

            print("\nDeriving distance_meters from EVENT_DISTANCES_TF ...")
            total = 0
            for ev in distinctEventShorts(conn):
                dist = EVENT_DISTANCES_TF.get(ev)
                if dist is None:
                    continue               # unmapped (sprint/field) -> leave NULL
                changed = fillDistanceForEvent(conn, ev, dist)
                conn.commit()
                total += changed
                print(f"  {ev:12} -> {dist:>8}  ({changed:,} rows)")
            print(f"\n  distance_meters filled on {total:,} rows total "
                  f"(unmapped events left NULL by design)")

    finally:
        closePool()


if __name__ == "__main__":
    main()