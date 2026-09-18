#!/usr/bin/env python3
# Project: xc-predictor
# File:    scripts/queue_meets.py
# Purpose: Decide WHAT a scraper should do next, and put it in meet_queue.
#          ONE implementation for BOTH feeds (owner, 2026-09-18: "Just make it
#          exactly like anet").
#
#     python scripts/queue_meets.py                     # anet, both sports
#     python scripts/queue_meets.py --source tfrrs
#     python scripts/queue_meets.py --source tfrrs --write
#     python scripts/queue_meets.py --sport XC --write
#
# The owner's spec, identical for either feed:
#   "scrape new meets linearly starting from our last collected meet id (that
#    was actually real). Keep going until 404. Also scrape any recent meets
#    (like past 3-4 months) that have 0 results."
#
# ★ THE LAUNCHERS DRAIN A QUEUE, THEY DO NOT WALK A RANGE. Both anet's
#   runSession and tfrrs's runDrain claim meet_queue rows at scraped=0 for
#   their own source, so both halves of the spec are seeding jobs -- which is
#   why this is a small script rather than a scraper change, and why both
#   launchers call seedAll().
#
# ★ EVERY SPORT IS ITS OWN ID SPACE, IN BOTH FEEDS (owner: "once again tf and
#   xc do not share an id space so need to sweep separately"). anet: XC tops
#   out near 276,000 while TF is near 671,000. tfrrs: XC near 27,000 while TF
#   is near 96,000. Nothing here is ever measured across sports.
#
# ! "ACTUALLY REAL" MEANS IT PRODUCED RESULTS. A queue row proves we asked; a
#   results row proves something was there.
#
# ! "RECENT" MEANS THE MEET'S OWN DATE, not a percentile of its id. An earlier
#   version inferred the window from id order because anet cross country had
#   no meet-level date column; the answer was to add the column, not to do
#   statistics on ids.
#
# ⚠ THIS SCRIPT ONLY EVER WRITES meet_queue.scraped. The worst it can do is
#   cause re-fetching.

import sys
import argparse
import datetime

sys.path.insert(0, "scripts")

from database import getConn, ensureCoreColumns         # noqa: E402

# Where each (feed, sport) keeps its three facts. Predicates are templated on
# `{a}`, the table alias, so they can be dropped into any query.
#
#   results  -- where a finish lands. Always also filtered by source.
#   meets    -- where the MEET lands, which a SCHEDULED meet with no results
#               still gets. This is what tells "no such meet" apart from
#               "meet exists, results not posted yet".
#   dates    -- the meet's own date, and the column it lives in.
#
# ⚠ THE TWO FEEDS DISAGREE ON ALL THREE, AND THAT IS ALL THEY DISAGREE ON.
#   `meets` / `meets_tf` are anet-only; tfrrs meet metadata lives in
#   meets_tfrrs, keyed (meet_id, sport), with the date column called `date`
#   rather than `meet_date`. A table map, not a different algorithm -- which is
#   what the first tfrrs attempt got wrong by treating it as a bigger job.
FEEDS = {
    "anet": {
        "XC": {"results": "results",     "meets": "meets",
               "meets_where": "{a}.source = 'anet'",
               "dates": "meets",         "date_col": "meet_date",
               "dates_where": "{a}.source = 'anet'"},
        "TF": {"results": "results_tf",  "meets": "meets_tf",
               "meets_where": "{a}.source = 'anet'",
               "dates": "meets_tf_meta", "date_col": "meet_date",
               "dates_where": ""},
    },
    "tfrrs": {
        "XC": {"results": "results",     "meets": "meets_tfrrs",
               "meets_where": "{a}.sport = 'XC'",
               "dates": "meets_tfrrs",   "date_col": "date",
               "dates_where": "{a}.sport = 'XC'"},
        "TF": {"results": "results_tf",  "meets": "meets_tfrrs",
               "meets_where": "{a}.sport = 'TF'",
               "dates": "meets_tfrrs",   "date_col": "date",
               "dates_where": "{a}.sport = 'TF'"},
    },
}

SPORTS = ("XC", "TF")
SOURCES = tuple(FEEDS)

AHEAD = 500                # ids seeded per block
RECENT_DAYS = 120          # the owner's "past 3-4 months"
FRONTIER_BLOCK = 1000      # ids per density bucket
FRONTIER_MIN_PER_BLOCK = 5 # real meets a bucket needs to count as corpus


def _t(source, sport):
    return FEEDS[source][sport]


def _clause(where, alias):
    """' AND <predicate>' for a query, or '' when the feed needs none."""
    return f" AND {where.format(a=alias)}" if where else ""


def _meetExistsSql(source, sport, outer="q", inner="_m"):
    """SQL asserting `outer.meet_id` is a real meet in this feed."""
    t = _t(source, sport)
    return (f"EXISTS (SELECT 1 FROM {t['meets']} {inner}"
            f" WHERE {inner}.meet_id = {outer}.meet_id"
            f"{_clause(t['meets_where'], inner)})")


# ------------------------------------------------------------------ frontiers

def watermark(cur, sport, source="anet"):
    """The highest meet_id in this feed and sport that produced results."""
    cur.execute(f"SELECT max(meet_id) FROM {_t(source, sport)['results']} "
                f"WHERE source = %s", (source,))
    return cur.fetchone()[0]


def askedFrontier(cur, sport, source="anet"):
    """The highest id ever queued for this feed and sport. Context only."""
    cur.execute("""SELECT max(meet_id) FROM meet_queue
                   WHERE source = %s AND sport = %s""", (source, sport))
    return cur.fetchone()[0]


def denseFrontier(cur, sport, source="anet", block=FRONTIER_BLOCK,
                  min_per_block=FRONTIER_MIN_PER_BLOCK):
    """The top of the highest BLOCK of ids holding real meets.

    ★ A GUARD AGAINST ONE BOGUS ROW (owner: "can we start at the highest batch
      of meet ids we've scraped, so one or two crazy ids don't fuck us?"). A
      max() is decided by its single largest value; a block of 1,000 needs
      min_per_block real meets, and an outlier is one.
    """
    t = _t(source, sport)
    cur.execute(f"""
        SELECT (meet_id / %(block)s) * %(block)s AS blk,
               count(DISTINCT meet_id)           AS n
        FROM   {t['meets']} m
        WHERE  m.meet_id IS NOT NULL
               {_clause(t['meets_where'], 'm')}
        GROUP  BY 1
        HAVING count(DISTINCT meet_id) >= %(minimum)s
        ORDER  BY 1 DESC
        LIMIT  1
    """, {"block": block, "minimum": min_per_block})
    row = cur.fetchone()
    return (row[0] + block) if row else None


def trailingMisses(cur, sport, top, source="anet"):
    """Consecutive ids above `top` that the feed says are NOT MEETS.

    ★ scraped = 4 IS THE 404, RECORDED WHEN WE ASKED. Both launchers write it
      for "no meet exists at this (meet_id, sport)".

    ! REPORTED, AND GATES NOTHING. These answers can be months old, and an id
      that held no meet then is exactly the kind worth re-asking now -- the
      stop comes from each launcher's own fresh evidence instead.
    """
    cur.execute(f"""
        SELECT q.scraped, {_meetExistsSql(source, sport)}
        FROM   meet_queue q
        WHERE  q.source = %s AND q.sport = %s
          AND  q.scraped IN (1, 2, 4) AND q.meet_id > %s
        ORDER  BY q.meet_id DESC
    """, (source, sport, top))
    run = 0
    for scraped, meet_exists in cur.fetchall():
        if scraped != 4 and meet_exists:
            break
        run += 1
    return run


def scheduledAbove(cur, sport, top, source="anet"):
    """Ids above `top` that ARE real meets but produced no results."""
    t = _t(source, sport)
    cur.execute(f"""
        SELECT count(DISTINCT q.meet_id)
        FROM   meet_queue q
        WHERE  q.source = %s AND q.sport = %s
          AND  q.scraped IN (1, 2) AND q.meet_id > %s
          AND  {_meetExistsSql(source, sport)}
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id AND r.source = %s)
    """, (source, sport, top, source))
    return cur.fetchone()[0]


# ------------------------------------------------------------------ the work

def hasDateColumn(cur, sport, source="anet"):
    """Does this feed and sport's date table carry its date column yet?

    ⚠ DECLARING A COLUMN IN THE DDL DOES NOT CREATE IT. CREATE TABLE IF NOT
      EXISTS never adds one to a table that already exists -- the lesson that
      cost a day on venue_name and status, and that this script then walked
      into by querying meets.meet_date the moment it was declared.
    """
    t = _t(source, sport)
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = %s AND column_name = %s""",
                (t["dates"], t["date_col"]))
    return cur.fetchone() is not None


def emptyRecent(cur, sport, since, source="anet"):
    """Meets DATED in the window that we finished and got nothing from.

    The owner's ask, literally: recent meets with 0 results.
    """
    t = _t(source, sport)
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        JOIN   {t['dates']} d ON d.meet_id = q.meet_id
                                 {_clause(t['dates_where'], 'd')}
        WHERE  q.source = %s AND q.sport = %s
          AND  q.scraped IN (1, 2)
          AND  d.{t['date_col']} >= %s
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id AND r.source = %s)
        ORDER  BY q.meet_id
    """, (source, sport, since, source))
    return [r[0] for r in cur.fetchall()]


def scheduledToRetry(cur, sport, above, has_date, source="anet"):
    """Scheduled meets worth asking again: real meets above the watermark with
    no results, minus any we know are still in the future.

    ★ THIS IS THE BULK OF THE WORK (owner: "there's like 10k meets that are
      scheduled with no results, and they're interspersed between the last like
      30k ids"). Not a ceiling and not failures -- meets we reached before the
      results were posted.
    """
    t = _t(source, sport)
    future = ""
    if has_date:
        future = f"""
          AND NOT EXISTS (SELECT 1 FROM {t['dates']} d
                          WHERE d.meet_id = q.meet_id
                            {_clause(t['dates_where'], 'd')}
                            AND d.{t['date_col']} > %(today)s)"""
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        WHERE  q.source = %(source)s AND q.sport = %(sport)s
          AND  q.scraped IN (1, 2) AND q.meet_id > %(above)s
          AND  {_meetExistsSql(source, sport)}
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id
                             AND r.source = %(source)s)
          {future}
        ORDER  BY q.meet_id
    """, {"source": source, "sport": sport, "above": above,
          "today": datetime.date.today().isoformat()})
    return [r[0] for r in cur.fetchall()]


def seedForward(cur, sport, lo, hi, source="anet"):
    """Queue ids lo..hi. Returns (inserted, re-asked)."""
    cur.execute("""
        INSERT INTO meet_queue (meet_id, sport, source, scraped)
        SELECT g, %s, %s, 0 FROM generate_series(%s, %s) g
        ON CONFLICT (meet_id, sport, source) DO NOTHING
    """, (sport, source, lo, hi))
    inserted = cur.rowcount

    # ★ AND RE-ASK WHAT THE FEED CALLED "NOT A MEET". State 4 was recorded when
    #   we asked, and feeds create ids over time, so that answer EXPIRES.
    #   Without this, ON CONFLICT DO NOTHING leaves those ids at 4 forever and
    #   the walk can never reach a new meet however it is aimed. State 2
    #   (failed) and 3 (session died mid-claim) go too -- neither is ever
    #   re-claimed otherwise.
    #
    # ! NOTHING WITH A MEET ROW. A real meet with no results is SCHEDULED, and
    #   re-asking those is scheduledToRetry's job, measured from the watermark.
    #   Two passes, two populations, no overlap.
    cur.execute(f"""
        UPDATE meet_queue q SET scraped = 0
        WHERE  q.source = %s AND q.sport = %s
          AND  q.meet_id BETWEEN %s AND %s
          AND  (q.scraped IN (2, 3, 4)
                OR (q.scraped = 1 AND NOT {_meetExistsSql(source, sport)}))
    """, (source, sport, lo, hi))
    return inserted, cur.rowcount


# ★ THE FAILURES, WHEREVER THEY ARE (owner, 2026-09-18: "rerun the scrapers
#   to do the failed ones without the forwards pass and stuff"). Nothing else
#   reaches them: seedForward re-asks states 2, 3 and 4 but only inside the
#   block it is walking, so a meet that failed below the watermark -- the 62
#   that died on the Unicode jsonb bug, for instance -- is never re-claimed by
#   any pass. resetInProgress covers state 3 at start-up; this covers 2.
#
# ! STATE 4 IS NOT A FAILURE. It is the feed saying "no meet here", and
#   re-asking every one of them across the whole id space is the forward
#   walk's job, aimed and bounded. This is the meets we broke, not the ids
#   that do not exist.
def retryFailed(cur, sport, source="anet"):
    """Reset every failed or stranded claim to due. Returns how many."""
    cur.execute("""
        UPDATE meet_queue SET scraped = 0
        WHERE  source = %s AND sport = %s AND scraped IN (2, 3)
    """, (source, sport))
    return cur.rowcount


def countFailed(cur, sport, source="anet"):
    cur.execute("""
        SELECT count(*) FROM meet_queue
        WHERE  source = %s AND sport = %s AND scraped IN (2, 3)
    """, (source, sport))
    return cur.fetchone()[0]


def requeue(cur, sport, ids, source="anet", chunk=5000):
    n = 0
    for i in range(0, len(ids), chunk):
        cur.execute("""
            UPDATE meet_queue SET scraped = 0
            WHERE source = %s AND sport = %s AND meet_id = ANY(%s)
        """, (source, sport, ids[i:i + chunk]))
        n += cur.rowcount
    return n


# ------------------------------------------------------------------ the plan

def seedSport(cur, sport, source="anet", write=False, ahead=AHEAD,
              recent_days=RECENT_DAYS, do_new=True, do_recent=True,
              do_failed=False, verbose=True, **_ignored):
    """Seed one (feed, sport). Returns a dict of what it found and did.

    ! CALLED BY BOTH LAUNCHERS, so a scrape night cannot get a different answer
      than a dry run did.
    """
    def say(msg):
        if verbose:
            print(msg, flush=True)

    tag = f"{source}/{sport}"
    out = {"source": source, "sport": sport, "top": None, "misses": 0,
           "seeded": 0, "woken": 0, "dated": 0, "scheduled_retry": 0,
           "requeued": 0, "failed": 0}

    # ! BEFORE THE WATERMARK GUARD. A failed meet is worth re-claiming whether
    #   or not this feed has produced results yet.
    if do_failed:
        n = countFailed(cur, sport, source)
        say(f"  [{tag}] failed or stranded claims: {n:,}")
        if write and n:
            out["failed"] = retryFailed(cur, sport, source)
            say(f"  [{tag}]   {out['failed']:,} reset to scraped=0")

    top = watermark(cur, sport, source)
    if top is None:
        say(f"  [{tag}] no results at all -- nothing to walk from.")
        return out
    out["top"] = top
    out["misses"] = misses = trailingMisses(cur, sport, top, source)
    out["scheduled"] = sched = scheduledAbove(cur, sport, top, source)
    say(f"  [{tag}] last id that produced results: {top:,}")
    say(f"  [{tag}] above it: {sched:,} real meets with no results yet "
        f"(scheduled), and {misses:,} ids in a row at the top the feed says "
        f"are not meets (queue state 4)")

    asked = askedFrontier(cur, sport, source)
    dense = denseFrontier(cur, sport, source)
    # ★ THE WALK STARTS JUST ABOVE THE WATERMARK: a new meet takes the next
    #   free id. The dense block is the GUARD -- a watermark far above where
    #   real meets stop is an outlier, not a watermark.
    frontier = top if (dense is None or top <= dense) else dense
    out["asked"], out["dense"], out["frontier"] = asked, dense, frontier
    say(f"  [{tag}] highest block of real meets ends at "
        f"{dense if dense is None else format(dense, ',')}; walking from "
        f"{frontier:,} (highest id ever queued "
        f"{asked if asked is None else format(asked, ',')})")

    if do_new:
        lo, hi = frontier + 1, frontier + ahead
        say(f"  [{tag}] seeding forward {lo:,}..{hi:,}")
        if write:
            ins, woke = seedForward(cur, sport, lo, hi, source)
            out["seeded"], out["woken"] = ins, woke
            say(f"  [{tag}]   {ins:,} new rows, {woke:,} re-asked "
                f"(state 4, failed or stuck)")

    if do_recent:
        since = (datetime.date.today()
                 - datetime.timedelta(days=recent_days)).isoformat()
        has_date = hasDateColumn(cur, sport, source)
        if has_date:
            dated = emptyRecent(cur, sport, since, source)
        else:
            dated = []
            t = _t(source, sport)
            say(f"  [{tag}] {t['dates']}.{t['date_col']} does not exist yet "
                f"-- no dated pass; the scheduled pass below still runs.")
        sched_retry = scheduledToRetry(cur, sport, top, has_date, source)
        out["dated"] = len(dated)
        out["scheduled_retry"] = len(sched_retry)
        say(f"  [{tag}] dated since {since} with 0 results: {len(dated):,}")
        say(f"  [{tag}] scheduled above the watermark, worth asking again: "
            f"{len(sched_retry):,}")
        todo = sorted(set(dated) | set(sched_retry))
        if write and todo:
            out["requeued"] = requeue(cur, sport, todo, source)
            say(f"  [{tag}]   {out['requeued']:,} reset to scraped=0")
    return out


def seedAll(conn, source="anet", write=False, sports=None, verbose=True, **kw):
    """Seed every sport for one feed and commit once."""
    results = []
    with conn.cursor() as cur:
        for sport in (sports or list(SPORTS)):
            results.append(seedSport(cur, sport, source=source, write=write,
                                     verbose=verbose, **kw))
    conn.commit() if write else conn.rollback()
    return results


def dueCounts(conn, source="anet"):
    """{sport: rows at scraped=0} for one feed."""
    with conn.cursor() as cur:
        cur.execute("""SELECT sport, count(*) FROM meet_queue
                       WHERE source = %s AND scraped = 0
                       GROUP BY sport""", (source,))
        out = dict(cur.fetchall())
    conn.rollback()
    return out


# ------------------------------------------------------------- the walk itself

class ForwardWalk:
    """Seeds the next block when a drain empties, and decides when to stop.

    ★ ONE IMPLEMENTATION FOR BOTH LAUNCHERS. anet's runSession and tfrrs's
      _sessionWorker have the same `if not batch: break` shape, and the anet
      one grew this logic inline. A second copy in run_tfrrs would be a second
      set of stopping rules to keep in step.

    ★ AND THE STOP COMES FROM THIS RUN'S OWN EVIDENCE. An earlier version
      stopped when enough ids were already recorded as "not a meet", and
      stopped dead on 31,198 of them -- answers from months ago, about the very
      ids being re-asked. A block counts as dry only when WE just asked it and
      the watermark did not move; a watermark only moves when a real meet was
      found.

    ! PER SPORT. A shared counter kept seeding dead ids for one sport for as
      long as the other was productive, and the finished one was never marked
      done.

    ! SYNCHRONOUS ON PURPOSE. Both callers are asyncio and both already have a
      way to run blocking DB work off the loop; a coroutine here would need
      one of their event loops.
    """

    def __init__(self, source="anet", sports=None, ahead=AHEAD,
                 dry_blocks=2, recent_days=RECENT_DAYS):
        self.source = source
        self.sports = list(sports or SPORTS)
        self.ahead = ahead
        self.dry_blocks = dry_blocks
        self.recent_days = recent_days
        self.dry = {}
        self.last_top = {}
        self.done = set()

    def live(self):
        return [sp for sp in self.sports if sp not in self.done]

    def extend(self):
        """(has_more, [lines to print]). False means every sport is finished."""
        lines = []
        live = self.live()
        if not live:
            return False, lines

        with getConn() as conn:
            with conn.cursor() as cur:
                tops = {sp: watermark(cur, sp, self.source) for sp in live}
            conn.rollback()
            seedAll(conn, source=self.source, write=True, do_recent=False,
                    verbose=False, sports=live, ahead=self.ahead)
            due = dueCounts(conn, self.source)
        due = {sp: due.get(sp, 0) for sp in live}

        for sp in live:
            last = self.last_top.get(sp, "unset")
            moved = last == "unset" or tops.get(sp) != last
            self.last_top[sp] = tops.get(sp)
            self.dry[sp] = 0 if moved else self.dry.get(sp, 0) + 1

            if self.dry.get(sp, 0) >= self.dry_blocks or not due.get(sp):
                self.done.add(sp)
                why = ("nothing left to seed" if not due.get(sp) else
                       f"{self.dry_blocks} blocks of {self.ahead} in a row "
                       f"found no new meet")
                lines.append(f"{self.source}/{sp} forward walk finished: "
                             f"{why} (watermark {tops.get(sp)})")

        still = {sp: n for sp, n in due.items()
                 if sp not in self.done and n}
        if still:
            parts = ", ".join(
                f"{sp} {n:,}"
                + (f" [{self.dry[sp]}/{self.dry_blocks} dry]"
                   if self.dry.get(sp) else "")
                for sp, n in sorted(still.items()))
            lines.append(f"queue drained -- seeded the next block: {parts}")
            return True, lines

        lines.append(f"every {self.source} sport's forward walk is finished.")
        return False, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=SOURCES, default="anet")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--ahead", type=int, default=AHEAD)
    ap.add_argument("--recent-days", type=int, default=RECENT_DAYS)
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--recent-only", action="store_true")
    ap.add_argument("--sport", choices=SPORTS)
    args = ap.parse_args()

    # ★ CREATE DECLARED-BUT-MISSING COLUMNS BEFORE QUERYING THEM.
    ensureCoreColumns(verbose=True)

    with getConn() as conn:
        seedAll(conn, source=args.source, write=args.write,
                sports=[args.sport] if args.sport else None,
                ahead=args.ahead, recent_days=args.recent_days,
                do_new=not args.recent_only, do_recent=not args.new_only)
        if args.write:
            print(f"\n  {args.source} queue now due:")
            for sp, n in sorted(dueCounts(conn, args.source).items()):
                print(f"    {sp}  {n:,}")
        else:
            print("\n  DRY RUN -- pass --write to seed the queue.")


if __name__ == "__main__":
    main()
