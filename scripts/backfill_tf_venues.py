#!/usr/bin/env python3
"""
backfill_tf_venues.py -- give every anet TRACK meet its venue's NAME.

    python scripts/backfill_tf_venues.py            # dry run: the census only
    python scripts/backfill_tf_venues.py --write

★ THE SYMPTOM (owner, 2026-09-17): the engine labels a LOCATION ID as a
  venue, because meets_tf.venue_name is NULL and the id is the only other
  thing on the row.

⚠ AND THE FIX HAS BEEN IN THE REPO, UNCALLED. database.backfillMeetsTFVenueNames
  was written, committed and wired to nothing -- so the names sat in
  meets_tf_meta while the engine printed ids. (school_team_link spent a day
  in the same state.) This is that function with an entry point.

Three passes, each filling only a NULL and never overwriting a name a meet
stated for itself:

  1. the meet's own meets_tf_meta row;
  2. OTHER MEETS AT THE SAME location_id -- the owner's ask: two meets at
     location 8891 are at the same venue whether or not both said so;
  3. the same coordinates, rounded to five places, for rows carrying no
     location id at all.

Needs no scrape. Idempotent: a second run finds nothing.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def census(cur):
    cur.execute("""
        SELECT count(*)                                        AS n,
               count(*) FILTER (WHERE venue_name IS NULL
                                  OR btrim(venue_name) = '')   AS unnamed,
               count(*) FILTER (WHERE (venue_name IS NULL
                                   OR btrim(venue_name) = '')
                                  AND location_id IS NOT NULL) AS unnamed_with_loc
        FROM   meets_tf
    """)
    n, unnamed, with_loc = cur.fetchone()
    print(f"  meets_tf: {n:,} rows, {unnamed:,} with no venue name "
          f"({100.0 * unnamed / max(1, n):.1f}%), of which {with_loc:,} do "
          f"carry a location id")
    # how many of those could be named from a neighbour at the same location
    cur.execute("""
        SELECT count(*) FROM meets_tf t
        WHERE  (t.venue_name IS NULL OR btrim(t.venue_name) = '')
          AND  t.location_id IS NOT NULL
          AND  EXISTS (SELECT 1 FROM meets_tf s
                       WHERE s.location_id = t.location_id
                         AND s.venue_name IS NOT NULL
                         AND btrim(s.venue_name) <> '')
    """)
    print(f"  {cur.fetchone()[0]:,} of them are at a location another meet "
          f"HAS named -- that is what pass 2 fills")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="without this, print the census and change nothing")
    a = ap.parse_args()

    from database import getConn, backfillMeetsTFVenueNames
    with getConn() as conn, conn.cursor() as cur:
        census(cur)
        if not a.write:
            print("\n  DRY RUN -- nothing written. --write to fill them.\n")
            return 0
        backfillMeetsTFVenueNames(conn)
        census(cur)
    return 0


if __name__ == "__main__":
    sys.exit(main())
