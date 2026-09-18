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
    # results: where a finish lands.  meets: where the MEET lands, which a
    # scheduled meet with no results still gets.  dates: where its date lands.
    "XC": {"results": "results",    "meets": "meets",    "dates": "meets"},
    "TF": {"results": "results_tf", "meets": "meets_tf", "dates": "meets_tf_meta"},
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


# A block of ids, and how many real meets a block needs before it counts as
# part of the corpus rather than noise. One stray id cannot make a block.
FRONTIER_BLOCK = 1000
FRONTIER_MIN_PER_BLOCK = 5


def denseFrontier(cur, sport, block=FRONTIER_BLOCK,
                  min_per_block=FRONTIER_MIN_PER_BLOCK):
    """The top of the highest BLOCK of ids that holds real meets.

    ★ THE OWNER'S ASK (2026-09-18): "can we start at the highest batch of
      meet ids we've scraped, so one or two crazy ids don't fuck us? (also
      670k shouldn't even be possible as the max we scraped was like 270k)".
      A max() is decided by its single largest value, so one bogus row -- a
      mis-keyed meet, a stray id from the other sport's space -- moves the
      frontier by 400,000 and the walk starts in empty space.

    ! REAL MEETS, NOT RESULTS. A scheduled meet is a real meet with no
      results, and it marks the corpus just as well, so this counts `meets` /
      `meets_tf` rows. The block only needs min_per_block of them: a genuine
      stretch of the id space has hundreds, and a single outlier has one.

    Returns the TOP of that block (block_start + block), so the walk starts
      inside the tail of real ids rather than skipping it.
    """
    mt = SPORTS[sport]["meets"]
    cur.execute(f"""
        SELECT (meet_id / %(block)s) * %(block)s AS blk,
               count(DISTINCT meet_id)           AS n
        FROM   {mt}
        WHERE  source = 'anet' AND meet_id IS NOT NULL
        GROUP  BY 1
        HAVING count(DISTINCT meet_id) >= %(minimum)s
        ORDER  BY 1 DESC
        LIMIT  1
    """, {"block": block, "minimum": min_per_block})
    row = cur.fetchone()
    return (row[0] + block) if row else None


def askedFrontier(cur, sport):
    """The highest id we have EVER queued for this sport, or None.

    ⚠ THE FORWARD WALK STARTED AT THE WRONG NUMBER (owner, 2026-09-18:
      "where does it actually start the forward walk"). It started at the
      watermark -- the last id that produced RESULTS -- and for cross country
      that is 275,585 while the old blind prefill queued every id up to
      670,000 under both sports. So the walk seeded into ids we had already
      asked about, where ON CONFLICT DO NOTHING correctly leaves them alone,
      added nothing, and would have reported the corpus exhausted on its
      first extension. The genuinely never-asked ids begin ABOVE here.

    ! THE TWO FRONTIERS ANSWER DIFFERENT QUESTIONS, and both are needed.
      This one is "what have we never looked at" -- where the forward walk
      belongs. The watermark is "what has ever given us data" -- what
      scheduled-above-the-watermark is measured from. Below this id and above
      the watermark is the interspersed scheduled population, which is the
      recent pass's job, not the walk's.
    """
    cur.execute("""SELECT max(meet_id) FROM meet_queue
                   WHERE source = 'anet' AND sport = %s""", (sport,))
    return cur.fetchone()[0]


def trailingMisses(cur, sport, top):
    """Consecutive ids at the top that anet says are NOT MEETS.

    ★ scraped = 4 IS THE 404, AND IT HAS BEEN RECORDED ALL ALONG.
      launcher._processMeetResult writes status=4 for `not exists` -- "no meet
      exists at this (meet_id, sport)" -- and its own comment says "if a
      future full pass is run it will check this combo again". Nothing ever
      did. So the authoritative answer to "where does the corpus end" was
      sitting in the queue while this function guessed at it from missing
      result rows, and then from missing meet rows.

    ⚠ IT COUNTED scraped IN (1, 2) ONLY, WHICH IS WHY IT SAID 0 (owner,
      2026-09-18: "0 real meets with no results yet, and 0 ids in a row at
      the top that are not meets at all", over a queue whose highest id is
      656,607). Every id above the real corpus is at 4, and 4 was in neither
      set it looked at.

    ! STATE 1 WITH NO MEET ROW STILL COUNTS, for rows written before status 4
      existed or by a path that did not use it.
    """
    mt = SPORTS[sport]["meets"]
    cur.execute(f"""
        SELECT q.scraped,
               EXISTS (SELECT 1 FROM {mt} m
                       WHERE m.meet_id = q.meet_id AND m.source = 'anet')
        FROM   meet_queue q
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2, 4) AND q.meet_id > %s
        ORDER  BY q.meet_id DESC
    """, (sport, top))
    run = 0
    for scraped, meet_exists in cur.fetchall():
        if scraped != 4 and meet_exists:
            break                      # a real meet: the corpus reaches here
        run += 1
    return run


def scheduledAbove(cur, sport, top):
    """Ids above `top` that ARE real meets but have produced no results.

    The scheduled meets. Reported so the two numbers are never confused
    again: these are work that will pay off later, not a ceiling.
    """
    mt, res = SPORTS[sport]["meets"], SPORTS[sport]["results"]
    cur.execute(f"""
        SELECT count(DISTINCT q.meet_id)
        FROM   meet_queue q
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.scraped IN (1, 2) AND q.meet_id > %s
          -- never 4: that is "no such meet", not a scheduled one
          AND  EXISTS (SELECT 1 FROM {mt} m
                       WHERE m.meet_id = q.meet_id AND m.source = 'anet')
          AND  NOT EXISTS (SELECT 1 FROM {res} r
                           WHERE r.meet_id = q.meet_id AND r.source = 'anet')
    """, (sport, top))
    return cur.fetchone()[0]


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


def scheduledToRetry(cur, sport, above, has_date):
    """The scheduled meets worth asking again: real meets above the
    watermark with no results, minus any we know are still in the future.

    ★ THIS IS THE ACTUAL WORK (owner, 2026-09-18: "there's like 10k meets
      that are scheduled with no results, and they're interspersed between
      the last like 30k ids"). They are not a ceiling and they are not
      failures -- they are meets we reached before the results were posted.
      Being above the watermark is what marks them out, and it needs no date.

    ! A DATE STILL HELPS WHEN WE HAVE IT: a meet scheduled for next month
      will be just as empty today, so asking is a wasted fetch. meets_tf_meta
      carries the date for track; meets.meet_date does for cross country from
      today on. Without one, every scheduled meet is asked -- which is right,
      because the alternative is never asking.
    """
    mt, res = SPORTS[sport]["meets"], SPORTS[sport]["results"]
    dates = SPORTS[sport]["dates"]
    future = ""
    if has_date:
        future = f"""
          AND NOT EXISTS (SELECT 1 FROM {dates} d
                          WHERE d.meet_id = q.meet_id
                            AND d.meet_date > %(today)s)"""
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        WHERE  q.source = 'anet' AND q.sport = %(sport)s
          AND  q.scraped IN (1, 2) AND q.meet_id > %(above)s
          AND  EXISTS (SELECT 1 FROM {mt} m
                       WHERE m.meet_id = q.meet_id AND m.source = 'anet')
          AND  NOT EXISTS (SELECT 1 FROM {res} r
                           WHERE r.meet_id = q.meet_id AND r.source = 'anet')
          {future}
        ORDER  BY q.meet_id
    """, {"sport": sport, "above": above,
          "today": datetime.date.today().isoformat()})
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
    #   to be missing data.
    cur.execute("""
        UPDATE meet_queue SET scraped = 0
        WHERE  source = 'anet' AND sport = %s
          AND  meet_id BETWEEN %s AND %s AND scraped IN (2, 3)
    """, (sport, lo, hi))
    woken = cur.rowcount

    # ★ AND RE-ASK THE ONES THAT HELD NO MEET, which is the half that makes a
    #   forward walk work at all. The blind prefill asked every id to 670,000
    #   long ago and marked them done; the ids just above the real corpus
    #   answered "no such meet" because anet HAD NOT CREATED THEM YET. That
    #   answer expires. Without this, ON CONFLICT DO NOTHING leaves them at 1
    #   forever and the walk can never reach a new meet.
    #
    # ! ONLY THE ONES WITH NO MEET ROW. An id that did come back as a real
    #   meet with no results is a SCHEDULED meet, and re-asking those is the
    #   recent pass's job (scheduledToRetry), measured from the watermark. Two
    #   passes, two populations, no overlap.
    # ★ STATE 4 IS THE ONE TO RE-ASK. It means "no meet exists at this id",
    #   recorded when we asked -- and anet creates ids over time, so that
    #   answer expires. launcher._processMeetResult has written it all along
    #   and its own comment expected a future pass to re-check; this is that
    #   pass. Without it, ON CONFLICT DO NOTHING leaves 380,000 ids at 4
    #   forever and the walk can never reach a new meet.
    #
    # ! PLUS STATE 1 WITH NO MEET ROW, for rows written before status 4
    #   existed. And nothing with a meet row: a real meet with no results is
    #   SCHEDULED, and re-asking those is scheduledToRetry's job. Two passes,
    #   two populations, no overlap.
    mt = SPORTS[sport]["meets"]
    cur.execute(f"""
        UPDATE meet_queue q SET scraped = 0
        WHERE  q.source = 'anet' AND q.sport = %s
          AND  q.meet_id BETWEEN %s AND %s
          AND  (q.scraped = 4
                OR (q.scraped = 1
                    AND NOT EXISTS (SELECT 1 FROM {mt} m
                                    WHERE m.meet_id = q.meet_id
                                      AND m.source = 'anet')))
    """, (sport, lo, hi))
    return inserted, woken + cur.rowcount


def requeue(cur, sport, ids, chunk=5000):
    n = 0
    for i in range(0, len(ids), chunk):
        cur.execute("""
            UPDATE meet_queue SET scraped = 0
            WHERE source = 'anet' AND sport = %s AND meet_id = ANY(%s)
        """, (sport, ids[i:i + chunk]))
        n += cur.rowcount
    return n


def seedSport(cur, sport, write=False, ahead=AHEAD,
              recent_days=RECENT_DAYS, stop_after_misses=None,
              do_new=True, do_recent=True, verbose=True):
    # stop_after_misses is accepted and ignored: the stop now comes from this
    # run's own evidence in launcher._extendFrontier, not from how many ids
    # were empty the last time anyone asked. Kept so existing callers and the
    # CLI flag do not break.
    """Seed one sport. Returns a dict of what it found and did.

    ★ CALLED BY THE LAUNCHER TOO (owner, 2026-09-18: "can you just make the
      launcher handle the queue itself"). One implementation, so a scrape
      night cannot get a different answer than the dry run did.
    """
    def say(msg):
        if verbose:
            print(msg, flush=True)

    out = {"sport": sport, "top": None, "misses": 0, "seeded": 0,
           "woken": 0, "dated": 0, "scheduled_retry": 0, "requeued": 0}
    top = watermark(cur, sport)
    if top is None:
        say(f"  [{sport}] no anet results at all -- nothing to walk from.")
        return out
    out["top"] = top
    out["misses"] = misses = trailingMisses(cur, sport, top)
    out["scheduled"] = sched = scheduledAbove(cur, sport, top)
    say(f"  [{sport}] last id that produced results: {top:,}")
    say(f"  [{sport}] above it: {sched:,} real meets with no results yet "
        f"(scheduled), and {misses:,} ids in a row at the top that anet says "
        f"are not meets (queue state 4)")

    # ⚠ NOT askedFrontier, AND NOT THE WATERMARK EITHER. The old blind
    #   prefill queued every id to 670,000, so the asked frontier is 670,000
    #   for a sport whose real ids stop near 270,000 -- starting there walks
    #   400,000 ids of empty space. And the watermark is a max(), so one bogus
    #   row moves it just as far. The dense frontier is where the real ids
    #   actually run out.
    asked = askedFrontier(cur, sport)
    dense = denseFrontier(cur, sport)
    # ★ THE WALK STARTS JUST ABOVE THE WATERMARK, NOT ABOVE THE DENSE BLOCK
    #   (owner, 2026-09-18). A new meet takes the next id anet has free, so
    #   the ids that matter most are the few hundred immediately above the
    #   last one that produced results -- starting at the top of the dense
    #   block skipped 275,658..276,000, which is exactly where this week's
    #   meets are.
    #
    # ! THE DENSE BLOCK IS THE GUARD, NOT THE START. Its job is to stop one
    #   bogus id inflating the watermark: a watermark far above where real
    #   meets actually stop is not a watermark, it is an outlier, and the walk
    #   falls back to the dense block in that case.
    frontier = top if (dense is None or top <= dense) else dense
    out["asked"], out["dense"], out["frontier"] = asked, dense, frontier
    say(f"  [{sport}] highest block of real meets ends at {frontier:,} "
        f"(watermark {top:,}, highest id ever queued "
        f"{asked if asked is None else format(asked, ',')})")
    if asked and asked > frontier + ahead:
        say(f"  [{sport}]   ids {frontier + 1:,}..{asked:,} were asked before "
            f"and recorded as 'no such meet' (state 4) -- re-asked as the "
            f"walk reaches them, because anet creates ids over time and that "
            f"answer expires.")

    if do_new:
        # ⚠ HISTORICAL state 4 IS NOT A CEILING, AND USING IT AS ONE STOPPED
        #   THE WALK DEAD (owner, 2026-09-18: 31,198 in a row, so nothing was
        #   seeded). Those ids were asked MONTHS ago. That they were not meets
        #   then is exactly why they are worth asking now -- anet creates ids
        #   over time. Treating a stale answer as evidence of the present is
        #   the same mistake in a new place.
        #
        # ★ THE STOP COMES FROM THIS RUN'S OWN EVIDENCE INSTEAD. The launcher
        #   seeds a block, drains it, and stops when a freshly-asked block
        #   moves the watermark nowhere -- see launcher._extendFrontier. The
        #   count below is reported because it is interesting, and used for
        #   nothing.
        lo, hi = frontier + 1, frontier + ahead
        say(f"  [{sport}] seeding forward {lo:,}..{hi:,}")
        if write:
            ins, woke = seedForward(cur, sport, lo, hi)
            out["seeded"], out["woken"] = ins, woke
            say(f"  [{sport}]   {ins:,} new rows, {woke:,} re-asked "
                f"(state 4, failed or stuck)")

    if do_recent:
        since = (datetime.date.today()
                 - datetime.timedelta(days=recent_days)).isoformat()
        if hasDateColumn(cur, sport):
            dated = emptyRecent(cur, sport, since)
        else:
            dated = []
            say(f"  [{sport}] {SPORTS[sport]['dates']}.meet_date does not "
                f"exist yet -- every empty meet counts as undated.")
        sched = scheduledToRetry(cur, sport, top, hasDateColumn(cur, sport))
        out["dated"], out["scheduled_retry"] = len(dated), len(sched)
        say(f"  [{sport}] dated since {since} with 0 results: {len(dated):,}")
        say(f"  [{sport}] scheduled above the watermark, worth asking again: "
            f"{len(sched):,}")
        todo = sorted(set(dated) | set(sched))
        if write and todo:
            out["requeued"] = requeue(cur, sport, todo)
            say(f"  [{sport}]   {out['requeued']:,} reset to scraped=0")
    return out


def seedAll(conn, write=False, sports=None, verbose=True, **kw):
    """Seed every sport and commit once. Returns [per-sport dict].

    ! TAKES A CONNECTION, so the launcher can seed on the pool it already
      opened rather than standing up a second one.
    """
    results = []
    with conn.cursor() as cur:
        for sport in (sports or list(SPORTS)):
            results.append(seedSport(cur, sport, write=write,
                                     verbose=verbose, **kw))
    if write:
        conn.commit()
    else:
        conn.rollback()
    return results


def dueCounts(conn):
    """{sport: rows at scraped=0} for anet."""
    with conn.cursor() as cur:
        cur.execute("""SELECT sport, count(*) FROM meet_queue
                       WHERE source = 'anet' AND scraped = 0
                       GROUP BY sport""")
        out = dict(cur.fetchall())
    conn.rollback()
    return out


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

    ensureCoreColumns(verbose=True)

    with getConn() as conn:
        seedAll(conn, write=args.write,
                sports=[args.sport] if args.sport else None,
                ahead=args.ahead, recent_days=args.recent_days,
                stop_after_misses=args.stop_after_misses,
                do_new=not args.recent_only, do_recent=not args.new_only)
        if args.write:
            print("\n  queue now due:")
            for sp, n in sorted(dueCounts(conn).items()):
                print(f"    {sp}  {n:,}")
        else:
            print("\n  DRY RUN -- pass --write to seed the queue.")


if __name__ == "__main__":
    main()
