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
#   vestigial. So both halves of the owner's ask are seeding jobs, which is
#   why this is a small script and not a scraper change.
#
# ★ XC AND TF ARE SEPARATE ID SPACES ON ANET, so everything here is per
#   sport. A late-2025 cross country meet is id ~275,585; the track meet the
#   same week is ~670,828. The standing SCAN_START_ID=1..670000 spans both
#   and costs roughly 1.3M fetches of which nearly half can never exist.
#
# ! "ACTUALLY REAL" MEANS IT PRODUCED RESULTS. A queue row proves we asked;
#   a results row proves there was something there. The watermark is the
#   highest meet_id with RESULTS, per sport, so a block of empty ids at the
#   top of the last run cannot push the start past real meets.
#
# ! "RECENT" MEANS THE MEET'S OWN DATE. Not a percentile of its id, not a
#   window of ids, not a cap -- the date, which we store. An earlier version
#   of this file inferred the window from id order because cross country had
#   no meet-level date column; the answer was to add the column (see
#   meets.meet_date and saveMeet), not to do statistics on ids.
#
# ⚠ THIS SCRIPT ONLY EVER WRITES meet_queue.scraped. It inserts queue rows
#   and resets flags; it never touches results, meets, or anything a scrape
#   produced. The worst it can do is cause re-fetching.

import sys
import argparse
import datetime

sys.path.insert(0, "scripts")

from database import getConn, ensureCoreColumns         # noqa: E402

SPORTS = {
    "XC": {"results": "results",    "dates": "meets"},
    "TF": {"results": "results_tf", "dates": "meets_tf_meta"},
}

# How far past the watermark to seed in one go. Small on purpose: the
# ceiling is not knowable in advance, so the loop is "seed a chunk, run it,
# look at what came back, seed the next".
AHEAD = 2000

# Consecutive empty ids at the top before we call it the end of the corpus.
# anet ids have real gaps -- "until 404" cannot be one 404.
STOP_AFTER_MISSES = 500

RECENT_DAYS = 120          # the owner's "past 3-4 months"


def watermark(cur, sport):
    """The highest anet meet_id that actually produced results."""
    cur.execute(f"SELECT max(meet_id) FROM {SPORTS[sport]['results']} "
                f"WHERE source = 'anet'")
    return cur.fetchone()[0]


def trailingMisses(cur, sport, top):
    """How many ids above `top` we ASKED about and got nothing from.

    ! ASKED, not merely absent. An id we never queued is not evidence of a
      ceiling; an id we queued, scraped and got no results from is.
    """
    res = SPORTS[sport]["results"]
    cur.execute(f"""
        SELECT EXISTS (SELECT 1 FROM {res} r
                       WHERE r.meet_id = q.meet_id AND r.source = 'anet')
        FROM   meet_queue q
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2) AND q.meet_id > %s
        ORDER  BY q.meet_id DESC
    """, (sport, top))
    run = 0
    for (has_results,) in cur.fetchall():
        if has_results:
            break
        run += 1
    return run


def hasDateColumn(cur, sport):
    """Does this sport's date table actually carry meet_date yet?

    ⚠ DECLARING A COLUMN IN THE DDL DOES NOT CREATE IT. `CREATE TABLE IF
      NOT EXISTS` never adds a column to a table that already exists -- the
      lesson that cost a day on venue_name and status, and that this script
      then walked straight into by querying meets.meet_date the moment it
      was added to the CREATE TABLE. ensureCoreColumns() in main() adds it;
      this is the guard for the case where it could not (a busy table, an
      older database), so the script degrades instead of crashing.
    """
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = 'meet_date'""",
                (SPORTS[sport]["dates"],))
    return cur.fetchone() is not None


def emptyRecent(cur, sport, since):
    """Meets DATED in the window that we finished and got nothing from.

    The owner's ask, literally: recent meets with 0 results. A meet is
    recent if its own meet_date is in the window, not if its id looks it.
    """
    res, dates = SPORTS[sport]["results"], SPORTS[sport]["dates"]
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        JOIN   {dates} d ON d.meet_id = q.meet_id
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2)
          AND  d.meet_date >= %s
          AND  NOT EXISTS (SELECT 1 FROM {res} r
                           WHERE r.meet_id = q.meet_id AND r.source = 'anet')
        ORDER  BY q.meet_id
    """, (sport, since))
    return [r[0] for r in cur.fetchall()]


def emptyUndated(cur, sport, above):
    """Finished-but-empty meets with NO stored date, above an id.

    ! THE HONEST REMAINDER. Until today an empty cross country meet stored
      no date at all -- anet's MeetDate was parsed by saveMeet and dropped,
      landing only on result rows -- so those meets cannot be tested against
      the window. They are not in it and not out of it; they are unknown.
      Counted separately, and only re-asked ABOVE the watermark, where
      "these are the newest ids" is the one thing we do know about them.
      This shrinks to nothing as meets are re-scraped with meets.meet_date.
    """
    res, dates = SPORTS[sport]["results"], SPORTS[sport]["dates"]
    # ! NULL WHEN THE COLUMN IS NOT THERE, so "undated" stays answerable on
    #   a database that has not run the migration yet.
    datecol = "d.meet_date" if hasDateColumn(cur, sport) else "NULL::text"
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        LEFT   JOIN {dates} d ON d.meet_id = q.meet_id
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2) AND q.meet_id > %s
          AND  {datecol} IS NULL
          AND  NOT EXISTS (SELECT 1 FROM {res} r
                           WHERE r.meet_id = q.meet_id AND r.source = 'anet')
        ORDER  BY q.meet_id
    """, (sport, above))
    return [r[0] for r in cur.fetchall()]


def seedForward(cur, sport, lo, hi):
    """Queue ids lo..hi for one sport."""
    cur.execute("""
        INSERT INTO meet_queue (meet_id, sport, source, scraped)
        SELECT g, %s, 'anet', 0 FROM generate_series(%s, %s) g
        ON CONFLICT (meet_id, sport, source) DO NOTHING
    """, (sport, lo, hi))
    inserted = cur.rowcount
    # ! AND UNSTICK WHAT IS ALREADY THERE. A row left at 2 (failed) or 3 (a
    #   session that died mid-claim) is never claimed again, so a forward
    #   seed that ignored them would walk straight past the ids most likely
    #   to be missing data. Rows at 1 belong to the recent pass.
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
    ap.add_argument("--stop-after-misses", type=int, default=STOP_AFTER_MISSES)
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--recent-only", action="store_true")
    ap.add_argument("--sport", choices=("XC", "TF"))
    args = ap.parse_args()

    sports = [args.sport] if args.sport else list(SPORTS)
    since = (datetime.date.today()
             - datetime.timedelta(days=args.recent_days)).isoformat()

    # ★ CREATE THE COLUMN BEFORE QUERYING IT. meets.meet_date is declared in
    #   database.py's DDL, and a declaration is not a column -- CREATE TABLE
    #   IF NOT EXISTS never adds one. ensureCoreColumns reads
    #   information_schema first and takes a lock only for what is genuinely
    #   missing, so on a database that already has it this is a catalogue
    #   read and nothing else.
    ensureCoreColumns(verbose=True)

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

                if not args.recent_only:
                    if misses >= args.stop_after_misses:
                        print(f"  ✓ {args.stop_after_misses}+ empty in a row "
                              f"-- treating that as the end of the corpus. "
                              f"Nothing seeded forward.")
                    else:
                        lo, hi = top + 1, top + args.ahead
                        print(f"  seeding forward {lo:,}..{hi:,}")
                        if args.write:
                            ins, woke = seedForward(cur, sport, lo, hi)
                            print(f"    {ins:,} new queue rows, "
                                  f"{woke:,} failed/stuck rows reset")

                if not args.new_only:
                    if not hasDateColumn(cur, sport):
                        # Every empty meet is then undated, which is the
                        # truth about this database rather than a crash.
                        dated = []
                        print(f"  {SPORTS[sport]['dates']}.meet_date does "
                              f"not exist yet -- every empty meet counts as "
                              f"undated below. It is created by "
                              f"createTables(), i.e. by the next scraper "
                              f"start, and this gets exact after that.")
                    else:
                        dated = emptyRecent(cur, sport, since)
                    undated = emptyUndated(cur, sport, top)
                    print(f"  meets dated since {since} with 0 results: "
                          f"{len(dated):,}")
                    if dated[:10]:
                        print(f"    e.g. {dated[:10]}")
                    if undated:
                        print(f"  plus {len(undated):,} above the watermark "
                              f"with no stored date (they predate "
                              f"{SPORTS[sport]['dates']}.meet_date)")
                    todo = sorted(set(dated) | set(undated))
                    if args.write and todo:
                        print(f"    {requeue(cur, sport, todo):,} "
                              f"reset to scraped=0")

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
