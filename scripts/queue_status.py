#!/usr/bin/env python3
"""
queue_status.py -- what is in meet_queue, and what the next run would claim.

    python scripts/queue_status.py
    python scripts/queue_status.py --sample 20     # show some actual ids

★ WHY THIS EXISTS (owner, 2026-09-18): "I don't think you've only reset the
  failed ones. Can we get a script to check the queue?" Both launchers print
  a queue summary at start-up, but only their OWN view of it, only for their
  own feed, and only as they are about to act on it -- so the one question
  that matters, "is this run about to do what I asked", could not be asked
  separately or beforehand.

⚠ AND THE ANSWER IS NOT JUST THE COUNTS. A retry claims states 2 and 3
  DIRECTLY (database._claimMeetBatch); an ordinary run claims state 0. Those
  are different sets, and the earlier retry-only mode reset 2 and 3 to 0 and
  then claimed 0 -- which silently swept in every id a forward walk had ever
  seeded. So this prints what EACH MODE would claim, side by side, rather
  than a table you have to do that arithmetic on yourself.

! READ-ONLY. It never writes, never seeds and never claims.
"""
import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "racecast")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The meaning of each meet_queue.scraped value, in one place. 4 is the one
# that surprises people: it is the feed saying "no meet at this id", recorded
# when we asked, and it EXPIRES -- ids get created over time.
STATES = {
    0: "due (never tried)",
    1: "done",
    2: "failed",
    3: "in-progress / stranded",
    4: "not a meet (feed said 404)",
}

# What each run mode claims. Kept beside STATES so the two cannot drift, and
# so the answer to "what will this run do" is a lookup, not a deduction.
MODES = (
    ("normal run", (0,)),
    ("--retry-failed", (2, 3)),
)


def counts(cur):
    cur.execute("""
        SELECT source, sport, scraped, count(*)
        FROM   meet_queue
        GROUP  BY source, sport, scraped
        ORDER  BY source, sport, scraped
    """)
    return cur.fetchall()


def sample(cur, source, states, n):
    cur.execute("""
        SELECT sport, meet_id FROM meet_queue
        WHERE  source = %s AND scraped = ANY(%s)
        ORDER  BY sport, meet_id
        LIMIT  %s
    """, (source, list(states), n))
    return cur.fetchall()


def span(cur, source, states):
    """(min, max) id in this set -- the cheapest tell that a 'retry' is
    actually sweeping the whole corpus."""
    cur.execute("""
        SELECT min(meet_id), max(meet_id) FROM meet_queue
        WHERE  source = %s AND scraped = ANY(%s)
    """, (source, list(states)))
    return cur.fetchone()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=None,
                    help="anet or tfrrs (default: both)")
    ap.add_argument("--sample", type=int, default=0,
                    help="also print this many actual ids per mode")
    args = ap.parse_args()

    from database import getConn
    with getConn() as conn:
        with conn.cursor() as cur:
            rows = counts(cur)
            if not rows:
                print("meet_queue is empty.")
                return

            by_source = {}
            for source, sport, state, n in rows:
                by_source.setdefault(source, []).append((sport, state, n))

            for source in sorted(by_source):
                if args.source and source != args.source:
                    continue
                print(f"\n=== {source} ===")
                print(f"    {'sport':<6} {'state':<28} {'rows':>12}")
                print(f"    {'-' * 6} {'-' * 28} {'-' * 12}")
                for sport, state, n in by_source[source]:
                    print(f"    {sport:<6} "
                          f"{STATES.get(state, f'? {state}'):<28} {n:>12,}")

                print(f"\n    what each mode would claim right now:")
                for label, states in MODES:
                    cur.execute("""
                        SELECT sport, count(*) FROM meet_queue
                        WHERE  source = %s AND scraped = ANY(%s)
                        GROUP  BY sport ORDER BY sport
                    """, (source, list(states)))
                    got = cur.fetchall()
                    total = sum(n for _s, n in got)
                    per = ", ".join(f"{s} {n:,}" for s, n in got) or "nothing"
                    lo, hi = span(cur, source, states)
                    # ⚠ THE ID SPAN IS THE TELL. A retry over meets we broke
                    #   sits wherever those meets are; a "retry" spanning the
                    #   whole id space is claiming more than the failures.
                    where = (f"  ids {lo:,}..{hi:,}"
                             if lo is not None and hi is not None else "")
                    print(f"      {label:<16} {total:>10,}  ({per}){where}")
                    if args.sample and total:
                        for sport, meet_id in sample(cur, source, states,
                                                     args.sample):
                            print(f"        {sport}  {meet_id}")
        conn.rollback()

    print("\n  a normal run claims state 0; --retry-failed claims 2 and 3 and "
          "resets nothing.\n  If those two numbers are the same, something "
          "reset the failures into state 0.")


if __name__ == "__main__":
    main()
