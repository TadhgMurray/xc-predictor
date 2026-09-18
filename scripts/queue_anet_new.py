#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/queue_anet_new.py
# Purpose: Decide WHAT the anet scraper should do next, and put it in
#          meet_queue (owner, 2026-09-17): "scrape new meets linearly
#          starting from our last collected meet id (that was actually
#          real). Keep going until 404. Also scrape any recent meets (like
#          past 3-4 months) that have 0 results."
#
#     python scripts/queue_anet_new.py                  # show the plan
#     python scripts/queue_anet_new.py --write          # seed the queue
#     python scripts/queue_anet_new.py --write --new-only
#     python scripts/queue_anet_new.py --write --recent-only
#
# ★ THE LAUNCHER DRAINS A QUEUE, IT DOES NOT WALK A RANGE. scripts/launcher
#   takes SCAN_START_ID/SCAN_END_ID and passes them into runSession, but
#   runSession's loop calls getBatchUnscrapedMeets -- it claims meet_queue
#   rows with scraped=0 and source='anet'. The stride arithmetic is
#   vestigial. So "scrape forward from the last real id" is a seeding job,
#   which is also why it can be a small script instead of a scraper change.
#
# ★ XC AND TF ARE SEPARATE ID SPACES ON ANET, and this is the reason the
#   script is per-sport everywhere. A late-2025 cross country meet is id
#   ~271,911; the track meet the same week is ~665,606. Seeding one range
#   for both sports is the current SCAN_START_ID=1..670000 arrangement, and
#   it costs roughly 1.3M fetches of which nearly half can never exist.
#
# ! "ACTUALLY REAL" MEANS IT PRODUCED RESULTS. A queue row proves we asked;
#   a results row proves there was something there. The watermark is the
#   highest meet_id with RESULTS, per sport, so a block of empty ids at the
#   top of the last run does not push the start past real meets.
#
# ⚠ THIS SCRIPT ONLY EVER WRITES meet_queue.scraped. It inserts queue rows
#   and resets flags; it never touches results, meets, or anything a scrape
#   produced. The worst it can do is cause re-fetching.

import sys
import argparse
import datetime

sys.path.insert(0, "scripts")

from database import getConn                            # noqa: E402

# One row per sport: which tables hold its results and its geometry.
SPORTS = {
    "XC": {"results": "results",    "meets": "meets"},
    "TF": {"results": "results_tf", "meets": "meets_tf"},
}

# How far past the watermark to seed in one go. Small on purpose: the
# ceiling is not knowable in advance, so the loop is "seed a chunk, run it,
# look at what came back, seed the next" rather than one guess at the end
# of the id space.
AHEAD = 2000

# Consecutive empty ids at the top before we call it the end of the corpus.
# anet ids have real gaps -- a single miss is not a ceiling, which is why
# "until 404" cannot be one 404.
STOP_AFTER_MISSES = 500

RECENT_DAYS = 120          # the owner's "past 3-4 months"


def watermark(cur, sport):
    """The highest anet meet_id that actually produced results."""
    t = SPORTS[sport]["results"]
    cur.execute(f"SELECT max(meet_id) FROM {t} WHERE source = 'anet'")
    return cur.fetchone()[0]


def trailingMisses(cur, sport, top):
    """How many ids below `top` we have ASKED about and got nothing from.

    ! ASKED, not merely absent. An id we never queued is not evidence of a
      ceiling; an id we queued, scraped and got no results from is. So this
      counts queue rows that were completed (scraped 1 or 2) and have no
      results, walking down from the top of the queued range.
    """
    t = SPORTS[sport]["results"]
    cur.execute(f"""
        SELECT q.meet_id,
               EXISTS (SELECT 1 FROM {t} r
                       WHERE r.meet_id = q.meet_id AND r.source = 'anet')
        FROM   meet_queue q
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2)
          AND  q.meet_id > %s
        ORDER  BY q.meet_id DESC
    """, (sport, top))
    run = 0
    for _mid, has_results in cur.fetchall():
        if has_results:
            break
        run += 1
    return run


# A handful of meets carry a date that is not their own -- the corpus has
# rows dated 2222 -- so the floor is a low percentile, not a minimum.
FLOOR_PCT = 0.02


def recentFloor(cur, sport, days, pct=FLOOR_PCT):
    """The lowest anet meet_id that honestly belongs to the window.

    ★ AN ID FLOOR, NOT A DATE FILTER, because a meet with NO results has no
      date anywhere for cross country -- `meets` has div_id, meet_id,
      meet_name, course_name, distance, gps and state, and no date column at
      all. anet ids are chronological, so the id of the oldest recent result
      is the honest boundary for "recent" and it covers empty meets too.

    ⚠ A PERCENTILE, NOT min(). The first version took min(meet_id) over
      recent-dated rows and TF came back with "the last four months" meaning
      ids 317,500..670,828 -- 353,328 ids, more than half the whole TF id
      space, for four months. One meet with a bad date drags a minimum all
      the way down, and this corpus has rows dated 2222. Two per cent of
      recent meets is a floor that a few liars cannot move.

    ! PER MEET, NOT PER ROW, so a meet with ten thousand results does not
      outvote one with six when the percentile is taken.
    """
    t = SPORTS[sport]["results"]
    since = (datetime.date.today()
             - datetime.timedelta(days=days)).isoformat()
    cur.execute(f"""
        WITH m AS (
            SELECT meet_id, max(date) AS d
            FROM   {t} WHERE source = 'anet'
            GROUP  BY meet_id
        )
        SELECT percentile_disc(%s) WITHIN GROUP (ORDER BY meet_id),
               min(meet_id), count(*)
        FROM   m WHERE d >= %s
    """, (pct, since))
    floor_id, lowest, n_meets = cur.fetchone()
    return floor_id, lowest, n_meets, since


# ⚠ anet ASSIGNS AN ID WHEN A MEET IS CREATED, NOT WHEN IT IS RACED, so a
#   meet entered in spring and run in autumn carries a spring id. Id order
#   is only ROUGHLY chronological and no percentile gives a clean four-month
#   boundary -- XC's floor landed exactly on the single lowest recent meet
#   because only 39 meets have results in the window at all (May to
#   September is the cross country off-season). So the window is a soft
#   hint and this cap is the real bound on the work.
RECENT_CAP = 25000


def emptyRecent(cur, sport, floor_id):
    """Queue rows from the floor UPWARD that we finished and got nothing from.

    These are the owner's "recent meets that have 0 results": we asked, the
    scrape completed, and nothing landed -- a meet whose results were not
    posted yet when we passed, or one we failed on quietly.

    ⚠ NO UPPER BOUND, AND THE FIRST VERSION STOPPED AT THE WATERMARK. The
      ids just ABOVE the last id that produced results are the newest meets
      in the corpus and the likeliest of all to have been empty when we
      passed and to have results now -- TF had 147 of them sitting at
      scraped=1, which is precisely the case this pass exists for, and they
      were the ones it excluded.
    """
    t = SPORTS[sport]["results"]
    cur.execute(f"""
        SELECT q.meet_id
        FROM   meet_queue q
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2)
          AND  q.meet_id >= %s
          AND  NOT EXISTS (SELECT 1 FROM {t} r
                           WHERE r.meet_id = q.meet_id AND r.source = 'anet')
        ORDER  BY q.meet_id
    """, (sport, floor_id))
    return [r[0] for r in cur.fetchall()]


def seedForward(cur, sport, lo, hi):
    """Queue ids lo..hi for one sport. Returns how many rows are now due."""
    cur.execute("""
        INSERT INTO meet_queue (meet_id, sport, source, scraped)
        SELECT g, %s, 'anet', 0 FROM generate_series(%s, %s) g
        ON CONFLICT (meet_id, sport, source) DO NOTHING
    """, (sport, lo, hi))
    inserted = cur.rowcount
    # ! AND UNSTICK WHAT WE ALREADY HAVE IN THAT RANGE. A row left at 2
    #   (failed) or 3 (in-progress, i.e. a session that died mid-claim) is
    #   never claimed again, so a forward seed that ignored them would walk
    #   straight past the ids most likely to be missing data. Rows at 1 are
    #   left alone: those were scraped and are handled by the recent pass.
    cur.execute("""
        UPDATE meet_queue SET scraped = 0
        WHERE  source = 'anet' AND sport = %s
          AND  meet_id BETWEEN %s AND %s AND scraped IN (2, 3)
    """, (sport, lo, hi))
    return inserted, cur.rowcount


def requeue(cur, sport, ids, chunk=5000):
    n = 0
    for i in range(0, len(ids), chunk):
        cur.execute("""
            UPDATE meet_queue SET scraped = 0
            WHERE source = 'anet' AND sport = %s AND meet_id = ANY(%s)
        """, (sport, ids[i:i + chunk]))
        n += cur.rowcount
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ahead", type=int, default=AHEAD)
    ap.add_argument("--recent-days", type=int, default=RECENT_DAYS)
    ap.add_argument("--recent-cap", type=int, default=RECENT_CAP,
                    help="at most this many empty meets per sport, newest "
                         "first (0 = no cap)")
    ap.add_argument("--stop-after-misses", type=int, default=STOP_AFTER_MISSES)
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--recent-only", action="store_true")
    ap.add_argument("--sport", choices=("XC", "TF"))
    args = ap.parse_args()

    sports = [args.sport] if args.sport else list(SPORTS)
    do_new = not args.recent_only
    do_recent = not args.new_only

    with getConn() as conn:
        with conn.cursor() as cur:
            for sport in sports:
                print(f"\n=== {sport} ===")
                top = watermark(cur, sport)
                if top is None:
                    print("  no anet results at all -- nothing to walk from.")
                    continue
                misses = trailingMisses(cur, sport, top)
                print(f"  last id that produced results: {top:,}")
                print(f"  ids asked about above it with nothing back: "
                      f"{misses:,}")

                if do_new:
                    if misses >= args.stop_after_misses:
                        print(f"  ✓ that is {args.stop_after_misses}+ empty "
                              f"in a row -- treating it as the end of the "
                              f"corpus. Nothing seeded forward.")
                    else:
                        lo, hi = top + 1, top + args.ahead
                        print(f"  seeding forward {lo:,}..{hi:,}")
                        if args.write:
                            ins, woke = seedForward(cur, sport, lo, hi)
                            print(f"    {ins:,} new queue rows, "
                                  f"{woke:,} failed/stuck rows reset")

                if do_recent:
                    floor_id, lowest, n_meets, since = recentFloor(
                        cur, sport, args.recent_days)
                    if floor_id is None:
                        print(f"  no results since {since} -- no recent "
                              f"range to check.")
                    else:
                        empty = emptyRecent(cur, sport, floor_id)
                        # ! NEWEST FIRST, THEN CAPPED. "Recent" is what the
                        #   owner asked for, and when the id cannot prove a
                        #   date the newest ids are the best available
                        #   answer to it. Ordering by id descending and
                        #   taking the top N is a bound you can reason
                        #   about; a percentile on 39 meets is not.
                        if args.recent_cap and len(empty) > args.recent_cap:
                            empty = sorted(empty)[-args.recent_cap:]
                            capped = True
                        else:
                            capped = False
                        span = top - floor_id
                        print(f"  {n_meets:,} meets have results since "
                              f"{since}")
                        print(f"  recent range: {floor_id:,} and up "
                              f"({span:,} ids to the watermark; the single "
                              f"lowest recent-dated meet is {lowest:,}, "
                              f"which is why this is a percentile)")
                        print(f"  finished meets at or above it with 0 "
                              f"results: {len(empty):,}"
                              + (f"  (capped at the newest "
                                 f"{args.recent_cap:,})" if capped else ""))
                        if empty:
                            print(f"  ids {min(empty):,}..{max(empty):,}")
                        if empty[:10]:
                            print(f"    e.g. {empty[:10]}")
                        above = [m for m in empty if m > top]
                        if above:
                            print(f"    {len(above):,} of them are ABOVE the "
                                  f"watermark -- the newest ids, the ones "
                                  f"most likely to have results now")
                        if args.write and empty:
                            n = requeue(cur, sport, empty)
                            print(f"    {n:,} reset to scraped=0")

            if args.write:
                conn.commit()
                cur.execute("""SELECT sport, count(*) FROM meet_queue
                               WHERE source = 'anet' AND scraped = 0
                               GROUP BY sport ORDER BY sport""")
                print("\n  queue now due:")
                for sp, n in cur.fetchall():
                    print(f"    {sp}  {n:,}")
            else:
                conn.rollback()
                print("\n  DRY RUN -- pass --write to seed the queue.")


if __name__ == "__main__":
    main()
