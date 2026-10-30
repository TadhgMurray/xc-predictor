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
import os
import time

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
# ★ ELEVEN DAYS BY DEFAULT, THE DEEP PASS ON A FLAG (owner, 2026-10-15: "it
#   should only do the past week and only do more if I pass a flag"). The
#   re-ask passes reached back 120 days by date and 30,000 ids with no date
#   at all, and nearly everything they woke came back empty. A normal launch
#   now re-asks only meets DATED in the last RECENT_DAYS; ANET_DEEP_RECHECK=1
#   (launcher) / --deep (this script) runs the old sweep: DEEP_RECENT_DAYS by
#   date, plus the undated and unrecorded id sweeps.
RECENT_DAYS = 11          # owner, 2026-10-15: "can we make it 11?"
DEEP_RECENT_DAYS = 120     # the owner's "past 3-4 months", on --deep
# ★ HOW LONG A RE-ASKED EMPTY MEET RESTS (owner, 2026-10-13: "the scraper
#   keeps doing a ton of 0 results races again each time it restarts").
#   Every launch runs the recent pass, and every pass re-queued every empty
#   meet it found -- so a meet asked ten minutes ago and still empty was
#   asked again on each restart. requeue() stamps meet_queue.requeued_at;
#   the passes skip a meet stamped within this many hours. A meet that is
#   empty because its results are not up yet gets one ask a day.
REASK_HOURS = float(os.environ.get("SEED_REASK_HOURS", "24"))
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


def frontierLeft(cur, sport, source="anet"):
    """Ids above the watermark still due (0) or in flight (3): the part of
    the walk's block the scrape has not finished."""
    mark = watermark(cur, sport, source)
    if mark is None:
        return 0
    cur.execute("""SELECT count(*) FROM meet_queue
                   WHERE source = %s AND sport = %s AND scraped IN (0, 3)
                     AND meet_id > %s""", (source, sport, mark))
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



# ★ TFRRS'S HOLES BELOW THE WATERMARK (owner, 2026-10-30: "it still hasn't
#   scraped the Mike Woods Classic" -- XC 28447, 571 ids under the watermark
#   and in no queue state at all). TFRRS hands out an id when a meet is
#   created and shows nothing until its results are posted; the drain called
#   that "not a meet" and DELETED the row, and the forward walk only ever
#   looks above the watermark. So a meet created early or posted late was
#   never asked again. run_tfrrs now keeps a cross country "not a meet" as
#   state 4 with its requeued_at, and this re-asks those below the watermark,
#   at most once per TFRRS_HOLE_REST_HOURS, within TFRRS_HOLE_SPAN ids of it;
#   ids in the span with no row (deleted before this) are queued once. Holes
#   are rare in the XC id space (track's ids are their own, ~97k against
#   ~29k), so a night asks a handful.
# ! CROSS COUNTRY ONLY. Track's id space is shared with its event pages, which
#   are deleted on purpose (run_tfrrs._deleteQueueRow).
TFRRS_HOLE_SPAN = int(os.environ.get("SEED_TFRRS_HOLE_SPAN", "1500"))
TFRRS_HOLE_REST_HOURS = float(os.environ.get("SEED_TFRRS_HOLE_REST_HOURS", "20"))


def tfrrsHoles(cur, top, write=False, span=TFRRS_HOLE_SPAN,
               rest_hours=TFRRS_HOLE_REST_HOURS):
    """(queued, re-asked) for TFRRS cross country ids in [top - span, top]:
    ids with no queue row are queued, and state-4 rows not asked within
    rest_hours go back to due. With write=False, the counts only."""
    lo = max(0, int(top) - int(span))
    if not write:
        cur.execute("""
            SELECT count(*) FROM generate_series(%s, %s) g
            WHERE NOT EXISTS (SELECT 1 FROM meet_queue q WHERE q.meet_id = g
                              AND q.sport = 'XC' AND q.source = 'tfrrs')
        """, (lo, top))
        missing = cur.fetchone()[0]
        cur.execute("""
            SELECT count(*) FROM meet_queue
            WHERE source = 'tfrrs' AND sport = 'XC' AND scraped = 4
              AND meet_id BETWEEN %s AND %s
              AND (requeued_at IS NULL OR requeued_at < now() - %s * interval '1 hour')
        """, (lo, top, rest_hours))
        return missing, cur.fetchone()[0]
    cur.execute("""
        INSERT INTO meet_queue (meet_id, sport, source, scraped, requeued_at)
        SELECT g, 'XC', 'tfrrs', 0, now() FROM generate_series(%s, %s) g
        ON CONFLICT (meet_id, sport, source) DO NOTHING
    """, (lo, top))
    queued = cur.rowcount
    cur.execute("""
        UPDATE meet_queue SET scraped = 0, requeued_at = now()
        WHERE source = 'tfrrs' AND sport = 'XC' AND scraped = 4
          AND meet_id BETWEEN %s AND %s
          AND (requeued_at IS NULL OR requeued_at < now() - %s * interval '1 hour')
    """, (lo, top, rest_hours))
    return queued, cur.rowcount


# ------------------------------------------------------------------ the work

def ensureRequeuedAt(cur):
    """Add meet_queue.requeued_at if it is missing. Looks before it alters:
    ADD COLUMN IF NOT EXISTS still queues for ACCESS EXCLUSIVE, behind every
    scraper holding the queue."""
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'meet_queue' AND column_name = 'requeued_at'""")
    if cur.fetchone() is None:
        cur.execute("ALTER TABLE meet_queue ADD COLUMN IF NOT EXISTS "
                    "requeued_at timestamptz")


# ★ AN EMPTY MEET WAITS FOR ITS OWN DATE (owner, 2026-10-15: "the queue has
#   barely gone down bcs most of the meets are skips ... there must be some
#   way to tell apart"). The launcher now keeps what an empty meet's own meet
#   info said -- its date and athletic.net's HasResults
#   (scrape_results.EMPTY_FACTS) -- on its queue row, and every re-ask pass
#   reads it:
#     - a meet dated AFTER TODAY is not asked: it has not been run, and the
#       daily ask was the bulk of the "skips";
#     - a meet more than STALE_EMPTY_DAYS past its date that athletic.net
#       itself says has NO results is asked once a week, not once a day --
#       most of those never post;
#     - HasResults true with nothing saved is a real miss and keeps the
#       daily ask.
#   A row asked before this existed has no date and is asked as before.
STALE_EMPTY_DAYS = int(os.environ.get("SEED_STALE_EMPTY_DAYS", "21"))
STALE_REASK_HOURS = float(os.environ.get("SEED_STALE_REASK_HOURS", "168"))


def ensureEmptyColumns(cur):
    """Add meet_queue.empty_date / empty_has_results if missing (looks
    first, like ensureRequeuedAt)."""
    ensureRequeuedAt(cur)
    for col, typ in (("empty_date", "date"), ("empty_has_results", "boolean")):
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'meet_queue' AND column_name = %s""", (col,))
        if cur.fetchone() is None:
            cur.execute(f"ALTER TABLE meet_queue ADD COLUMN IF NOT EXISTS {col} {typ}")


def _emptyClause(a="q"):
    """The SQL for the rule above (needs requeued_at and the two columns)."""
    return (f"AND NOT ({a}.empty_date IS NOT NULL AND {a}.empty_date > current_date) "
            f"AND NOT ({a}.empty_date IS NOT NULL "
            f"AND {a}.empty_date < current_date - {int(STALE_EMPTY_DAYS)} "
            f"AND {a}.empty_has_results IS NOT TRUE "
            f"AND {a}.requeued_at IS NOT NULL "
            f"AND {a}.requeued_at > now() - interval '{float(STALE_REASK_HOURS)} hours')")


def _rested(cur, a="q"):
    """The SQL clause: not re-asked within REASK_HOURS, and not an empty meet
    waiting for its date (above). Empty when the columns are not there yet
    (a dry run against an old schema)."""
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'meet_queue'
                     AND column_name IN ('requeued_at', 'empty_date', 'empty_has_results')""")
    cols = {r[0] for r in cur.fetchall()}
    if "requeued_at" not in cols:
        return ""
    out = ""
    if REASK_HOURS > 0:
        out = (f"AND ({a}.requeued_at IS NULL OR {a}.requeued_at < "
               f"now() - interval '{float(REASK_HOURS)} hours')")
    if {"empty_date", "empty_has_results"} <= cols:
        out += " " + _emptyClause(a)
    return out


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

    ★ NOT AFTER TODAY (owner, 2026-10-12, the side-computer run: page after
      page of "saved 0 results"). athletic.net lists a meet weeks ahead --
      this autumn's later XC, next spring's track -- and the window had no
      end, so every launch re-asked every scheduled meet. One that has not
      been run yet has nothing to give; it is asked again once its date
      has passed.
    """
    import datetime as _dt
    t = _t(source, sport)
    rested = _rested(cur)
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        JOIN   {t['dates']} d ON d.meet_id = q.meet_id
                                 {_clause(t['dates_where'], 'd')}
        WHERE  q.source = %s AND q.sport = %s
          AND  q.scraped IN (1, 2)
          AND  d.{t['date_col']} >= %s
          AND  d.{t['date_col']} <= %s
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id AND r.source = %s)
        {rested}
        ORDER  BY q.meet_id
    """, (source, sport, since, _dt.date.today().isoformat(), source))
    return [r[0] for r in cur.fetchall()]


UNDATED_SPAN = int(os.environ.get("SEED_UNDATED_SPAN", "30000"))


def emptyUndated(cur, sport, top, span=UNDATED_SPAN, source="anet"):
    """Meets we finished with 0 results, whose row has NO date, within
    `span` ids below the watermark.

    ★ THE GAP emptyRecent CANNOT SEE (owner, 2026-09-26: "find out why St.
      Mary's Invite wasn't scraped"). An anet meet is asked while still
      scheduled, saved with 0 results, and marked done; newer ids post
      results and the watermark passes it. After that only emptyRecent can
      wake it -- and that pass needs the meet's date, a column that only
      exists since 2026-09-18. Every empty meet saved before then has none,
      and stayed done for ever.
    ! BOUNDED BY ID, since there is no date to bound it by: anet ids are
      handed out as meets are created, so the last SEED_UNDATED_SPAN ids
      below the watermark are this season's and last."""
    t = _t(source, sport)
    rested = _rested(cur)
    cur.execute(f"""
        SELECT DISTINCT q.meet_id
        FROM   meet_queue q
        JOIN   {t['dates']} d ON d.meet_id = q.meet_id
                                 {_clause(t['dates_where'], 'd')}
        WHERE  q.source = %s AND q.sport = %s
          AND  q.scraped IN (1, 2)
          AND  q.meet_id BETWEEN %s AND %s
          AND  d.{t['date_col']} IS NULL
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id AND r.source = %s)
        {rested}
        ORDER  BY q.meet_id
    """, (source, sport, max(0, top - span), top, source))
    return [r[0] for r in cur.fetchall()]


def emptyUnrecorded(cur, sport, top, span=UNDATED_SPAN, source="anet",
                    since=None):
    """Meets marked DONE with 0 results that left NO meet row at all, within
    `span` ids below the watermark.

    ★ THE GAP emptyUndated CANNOT SEE EITHER (owner, 2026-10-08: "I see no
      Purple Valley classic, or woodbridge"). scrapeMeetBySport answers
      (0, True) for a meet that exists but has no divisions yet -- the shape
      of a meet created weeks ahead of race day -- and the launcher writes
      state 1. Nothing is saved: the meets row is written per DIVISION, from
      the results payload, so there is no row to carry a name or a date.
      Every re-ask pass here starts from that row (scheduledToRetry and
      emptyUndated through _meetExistsSql, emptyRecent through the date
      join), and seedForward's "state 1 and no meet row" clause only covers
      the block it is walking. So once the watermark passed such an id, the
      meet stayed "done" for ever, whatever was posted later.

    ! STATE 1, NOT 4. State 4 is the feed saying "no meet here"; this is the
      feed saying "a meet, nothing in it yet". A row with results, or with a
      meet row, belongs to the passes above.
    ! BOUNDED LIKE emptyUndated, by SEED_UNDATED_SPAN ids below the
      watermark, because there is no date to bound it by -- that is the bug.
    """
    t = _t(source, sport)
    rested = _rested(cur)
    # ! since: only the meets whose OWN date (meet_queue.empty_date, kept
    #   since 2026-10-15) is in [since, today] -- the default one-week pass.
    #   None is the deep sweep by id.
    dated, params = "", [source, sport, max(0, top - span), top, source]
    if since is not None:
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'meet_queue' AND column_name = 'empty_date'""")
        if cur.fetchone() is None:
            return []
        dated = "AND q.empty_date BETWEEN %s AND current_date"
        params.append(since)
    cur.execute(f"""
        SELECT q.meet_id
        FROM   meet_queue q
        WHERE  q.source = %s AND q.sport = %s
          AND  q.scraped = 1
          AND  q.meet_id BETWEEN %s AND %s
          AND  NOT {_meetExistsSql(source, sport)}
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id AND r.source = %s)
          {dated}
        {rested}
        ORDER  BY q.meet_id
    """, params)
    return [r[0] for r in cur.fetchall()]


_SANE_DATE = r"^(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}"


def seasonRange(cur, sport, source="anet", recent_days=DEEP_RECENT_DAYS):
    """(lo, hi, how) -- the ids this season's meets occupy in one feed/sport.

    lo is the lowest id of a meet DATED in the last recent_days (the same
    window the recent pass uses); hi is the highest id ever queued. With no
    dated meet in the window it falls back to SEED_UNDATED_SPAN below the
    watermark, the bound emptyUndated uses. Read-only.
    """
    t = _t(source, sport)
    since = (datetime.date.today()
             - datetime.timedelta(days=recent_days)).isoformat()
    lo = None
    if hasDateColumn(cur, sport, source):
        cur.execute(f"""
            SELECT min(d.meet_id) FROM {t['dates']} d
            WHERE  d.{t['date_col']} ~ %s AND d.{t['date_col']} >= %s
                   {_clause(t['dates_where'], 'd')}
        """, (_SANE_DATE, since))
        lo = cur.fetchone()[0]
    hi = askedFrontier(cur, sport, source)
    if lo is not None:
        return lo, hi, f"lowest id dated since {since}"
    mark = watermark(cur, sport, source)
    if mark is None:
        return None, hi, "no results and no dated meets"
    return max(0, mark - UNDATED_SPAN), hi, \
        f"SEED_UNDATED_SPAN ({UNDATED_SPAN:,}) below the watermark"


def deletedIds(cur, sport, lo, hi, source="tfrrs"):
    """Ids in lo..hi with NO queue row and NO meet row for this feed/sport.

    ★ WHAT A tfrrs "event - deleted" LEAVES BEHIND. run_tfrrs.processMeet
      deletes the queue row of any page _isMeetPage does not call a meet, so
      nothing records that the id was ever asked. seedForward re-inserts it
      only while it is inside the walk's block above the watermark; below
      it, the id is never asked again. Read-only.
    """
    if lo is None or hi is None or hi < lo:
        return []
    t = _t(source, sport)
    cur.execute(f"""
        SELECT g FROM generate_series(%s::bigint, %s::bigint) g
        WHERE NOT EXISTS (SELECT 1 FROM meet_queue q
                          WHERE q.meet_id = g AND q.sport = %s
                            AND q.source = %s)
          AND NOT EXISTS (SELECT 1 FROM {t['meets']} m
                          WHERE m.meet_id = g{_clause(t['meets_where'], 'm')})
        ORDER BY g
    """, (lo, hi, sport, source))
    return [r[0] for r in cur.fetchall()]


def scheduledToRetry(cur, sport, above, has_date, source="anet", since=None):
    """Scheduled meets worth asking again: real meets above the watermark with
    no results, minus any we know are still in the future.

    ★ THIS IS THE BULK OF THE WORK (owner: "there's like 10k meets that are
      scheduled with no results, and they're interspersed between the last like
      30k ids"). Not a ceiling and not failures -- meets we reached before the
      results were posted.
    """
    t = _t(source, sport)
    rested = _rested(cur)
    future = ""
    # ! since: only meets DATED in [since, today] (the default one-week
    #   pass); an undated meet waits for --deep
    if since is not None:
        if not has_date:
            return []
        future = f"""
          AND EXISTS (SELECT 1 FROM {t['dates']} d
                      WHERE d.meet_id = q.meet_id
                        {_clause(t['dates_where'], 'd')}
                        AND d.{t['date_col']} >= %(since)s
                        AND d.{t['date_col']} <= %(today)s)"""
    elif has_date:
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
        {rested}
        ORDER  BY q.meet_id
    """, {"source": source, "sport": sport, "above": above, "since": since,
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
    ensureRequeuedAt(cur)
    n = 0
    for i in range(0, len(ids), chunk):
        cur.execute("""
            UPDATE meet_queue SET scraped = 0, requeued_at = now()
            WHERE source = %s AND sport = %s AND meet_id = ANY(%s)
        """, (source, sport, ids[i:i + chunk]))
        n += cur.rowcount
    return n


# ★ SHED THE BACKLOG THE OLD PASSES QUEUED (owner, 2026-10-15: "the queue has
#   barely gone down"). The one-week rule stops the 120-day and id sweeps
#   from queueing more, but what they already set due stays due until a
#   session asks it once more -- thousands of meets that will come back
#   empty. This puts a due re-ask back to done when nothing says it is this
#   week's: re-queued by a pass (requeued_at set), still no results, and
#   neither its meet row's date nor its own kept date (empty_date) in the
#   last `days` days. A never-asked id (no requeued_at: the forward walk, a
#   new block) and a claimed one (state 3) are left alone. The next deep
#   pass (--deep / ANET_DEEP_RECHECK=1) can always queue them again.
# ★ A NAME REPAIR THAT HAS NOTHING LEFT TO REPAIR (owner, 2026-10-15: "we
#   gota be able to shed more"; 54,672 TF and 7,903 XC due rows were saved
#   meets with results). requeue_blank_athletes.py set those due so a
#   re-scrape fills nameless results in -- by a plain UPDATE, no stamp -- and
#   a name is filled across meets (one athletes row per runner), so many of
#   them have no nameless row left. Its own test, here per due meet: a result
#   with no named athletes row for COALESCE(person_id, athlete_id) and no
#   name of its own; track relays never count. A due meet WITH results, not
#   stamped by a re-ask pass, and no nameless row left goes back to done.
def _namedIds(cur):
    """qm_named(id): every id the site can put a name to -- built ONCE per
    connection and indexed, as requeue_blank_athletes does. A per-row probe
    of athletes on person_id (no index there) is what hung the first
    version on results_tf."""
    cur.execute("SELECT to_regclass('pg_temp.qm_named')")
    if cur.fetchone()[0] is not None:
        return
    t0 = time.time()
    print("    building the named-id table from athletes ...", end="", flush=True)
    cur.execute("""
        CREATE TEMP TABLE qm_named AS
        SELECT DISTINCT k AS id FROM (
            SELECT person_id AS k FROM athletes
            WHERE  NULLIF(btrim(concat_ws(' ', first_name, last_name)), '') IS NOT NULL
              AND  person_id IS NOT NULL
            UNION ALL
            SELECT athlete_id FROM athletes
            WHERE  NULLIF(btrim(concat_ws(' ', first_name, last_name)), '') IS NOT NULL) x
        WHERE  k IS NOT NULL;
        CREATE UNIQUE INDEX ON qm_named (id);
        ANALYZE qm_named
    """)
    print(f" {time.time() - t0:.0f}s", flush=True)


def shedRepaired(cur, sport, source="anet", write=False):
    """How many due name-repair re-scrapes have nothing left to repair;
    with write, sets them back to done (scraped = 1)."""
    if source != "anet":
        return 0
    t = _t(source, sport)
    t0 = time.time()
    cur.execute("SET LOCAL statement_timeout = 0")
    cur.execute("SET LOCAL work_mem = '512MB'")
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'meet_queue' AND column_name = 'requeued_at'""")
    unstamped = "AND q.requeued_at IS NULL" if cur.fetchone() is not None else ""
    _namedIds(cur)
    cur.execute("DROP TABLE IF EXISTS qm_repair")
    cur.execute(f"""
        CREATE TEMP TABLE qm_repair AS
        SELECT q.meet_id FROM meet_queue q
        WHERE  q.source = %(source)s AND q.sport = %(sport)s AND q.scraped = 0
          {unstamped}
    """, {"source": source, "sport": sport})
    cur.execute("CREATE UNIQUE INDEX ON qm_repair (meet_id)")
    cur.execute("ANALYZE qm_repair")
    print(f"    {sport}: one pass over {t['results']} for the queued meets' "
          f"rows ...", end="", flush=True)
    # ONE pass: per queued meet, does it have rows, and any nameless one
    cur.execute(f"""
        CREATE TEMP TABLE qm_rows AS
        SELECT r.meet_id,
               bool_or(n.id IS NULL AND NULLIF(btrim(r.athlete_name), '') IS NULL
                       {"AND COALESCE(r.is_relay, 0) = 0" if sport == "TF" else ""})
                   AS nameless
        FROM   {t['results']} r
        JOIN   qm_repair m ON m.meet_id = r.meet_id
        LEFT   JOIN qm_named n ON n.id = COALESCE(r.person_id, r.athlete_id)
        WHERE  r.source = %(source)s
        GROUP  BY r.meet_id
    """, {"source": source})
    print(f" {time.time() - t0:.0f}s", flush=True)
    cur.execute("SELECT count(*) FROM qm_rows WHERE NOT nameless")
    n = cur.fetchone()[0]
    if write and n:
        cur.execute("""UPDATE meet_queue q SET scraped = 1 FROM qm_rows m
                       WHERE q.meet_id = m.meet_id AND NOT m.nameless
                         AND q.source = %s AND q.sport = %s AND q.scraped = 0""",
                    (source, sport))
        n = cur.rowcount
    cur.execute("DROP TABLE qm_repair; DROP TABLE qm_rows")
    return n


def dueBreakdown(cur, sport, source="anet"):
    """{label: count} of what is due: re-asks, saved meets, and the rest."""
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'meet_queue' AND column_name = 'requeued_at'""")
    stamped = ("q.requeued_at IS NOT NULL" if cur.fetchone() is not None
               else "false")
    cur.execute(f"""
        SELECT count(*) FILTER (WHERE {stamped}),
               count(*) FILTER (WHERE NOT ({stamped}) AND {_meetExistsSql(source, sport)}),
               count(*) FILTER (WHERE NOT ({stamped})
                                  AND NOT {_meetExistsSql(source, sport)})
        FROM   meet_queue q
        WHERE  q.source = %s AND q.sport = %s AND q.scraped = 0
    """, (source, sport))
    a, b, c = cur.fetchone()
    return {"re-asked by a pass": a, "a saved meet, not stamped": b,
            "never saved, not stamped (new ids, or re-queued before 10-13)": c}


def shedStale(cur, sport, source="anet", days=RECENT_DAYS, write=False):
    """How many due re-asks are outside the last `days` days; with write,
    sets them back to done (scraped = 1)."""
    t = _t(source, sport)
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    cur.execute("""SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'meet_queue'
                     AND column_name IN ('requeued_at', 'empty_date')""")
    cols = {r[0] for r in cur.fetchall()}
    if "requeued_at" not in cols:
        return 0
    kept = ("AND NOT (q.empty_date IS NOT NULL "
            "AND q.empty_date BETWEEN %(since)s AND current_date)"
            if "empty_date" in cols else "")
    dated = ""
    if hasDateColumn(cur, sport, source):
        dated = f"""
          AND NOT EXISTS (SELECT 1 FROM {t['dates']} d
                          WHERE d.meet_id = q.meet_id
                            {_clause(t['dates_where'], 'd')}
                            AND d.{t['date_col']} >= %(since)s
                            AND d.{t['date_col']} <= %(today)s)"""
    # ! ASKED BEFORE: stamped by a re-ask pass, OR the meet already has a
    #   meet row -- only a scrape writes one, so it has been asked. A row
    #   with neither (requeued before requeued_at existed and never saved,
    #   or a never-asked id) is left due; --shed-stale prints how many.
    where = f"""
        WHERE  q.source = %(source)s AND q.sport = %(sport)s
          AND  q.scraped = 0
          AND  (q.requeued_at IS NOT NULL OR {_meetExistsSql(source, sport)})
          AND  NOT EXISTS (SELECT 1 FROM {t['results']} r
                           WHERE r.meet_id = q.meet_id AND r.source = %(source)s)
          {dated}
          {kept}"""
    params = {"source": source, "sport": sport, "since": since,
              "today": datetime.date.today().isoformat()}
    if write:
        cur.execute(f"UPDATE meet_queue q SET scraped = 1 {where}", params)
        return cur.rowcount
    cur.execute(f"SELECT count(*) FROM meet_queue q {where}", params)
    return cur.fetchone()[0]


# ------------------------------------------------------------------ the plan

def seedSport(cur, sport, source="anet", write=False, ahead=AHEAD,
              recent_days=None, do_new=True, do_recent=True,
              do_failed=False, verbose=True, deep=False, **_ignored):
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
        if write:
            ensureRequeuedAt(cur)
        if recent_days is None:
            recent_days = DEEP_RECENT_DAYS if deep else RECENT_DAYS
        since = (datetime.date.today()
                 - datetime.timedelta(days=recent_days)).isoformat()
        say(f"  [{tag}] re-asking empty meets dated since {since}"
            + (" (deep: plus the undated and unrecorded id sweeps)" if deep
               else " (ANET_DEEP_RECHECK=1 / --deep for the 120-day sweep)"))
        has_date = hasDateColumn(cur, sport, source)
        if has_date:
            dated = emptyRecent(cur, sport, since, source)
        else:
            dated = []
            t = _t(source, sport)
            say(f"  [{tag}] {t['dates']}.{t['date_col']} does not exist yet "
                f"-- no dated pass; the scheduled pass below still runs.")
        sched_retry = scheduledToRetry(cur, sport, top, has_date, source,
                                       since=None if deep else since)
        undated = (emptyUndated(cur, sport, top, source=source)
                   if has_date and deep else [])
        out["undated"] = len(undated)
        say(f"  [{tag}] undated with 0 results in the last {UNDATED_SPAN:,} ids: "
            f"{len(undated):,}")
        # ★ AND THE EMPTY MEETS THAT LEFT NO ROW (owner, 2026-10-08) -- see
        #   emptyUnrecorded. Not gated on has_date: having no row is exactly
        #   why no date exists for them.
        unrecorded = emptyUnrecorded(cur, sport, top, source=source,
                                     since=None if deep else since)
        out["unrecorded"] = len(unrecorded)
        say(f"  [{tag}] done with 0 results and no meet row in the last "
            f"{UNDATED_SPAN:,} ids: {len(unrecorded):,}")
        dated = sorted(set(dated) | set(undated) | set(unrecorded))
        out["dated"] = len(dated)
        out["scheduled_retry"] = len(sched_retry)
        say(f"  [{tag}] dated since {since} with 0 results: {len(dated):,}")
        say(f"  [{tag}] scheduled above the watermark, worth asking again: "
            f"{len(sched_retry):,}")
        todo = sorted(set(dated) | set(sched_retry))
        if write and todo:
            out["requeued"] = requeue(cur, sport, todo, source)
            say(f"  [{tag}]   {out['requeued']:,} reset to scraped=0")
        if source == "tfrrs" and sport == "XC":
            if write:
                ensureRequeuedAt(cur)
            queued, woken = tfrrsHoles(cur, top, write=write)
            out["holes"] = queued + woken
            say(f"  [{tag}] holes in the last {TFRRS_HOLE_SPAN:,} ids below the watermark: "
                f"{queued:,} never queued, {woken:,} 'not a meet' due a re-ask"
                + ("" if write else " (dry run)"))
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


# ! THE RETRY'S OWN DENOMINATOR. A retry claims states 2 and 3 directly, so
#   "how much is there to do" is not dueCounts -- which counts state 0, the
#   rows a retry deliberately leaves alone.
def failedCounts(conn, source="anet"):
    """{sport: rows at scraped 2 or 3} for one feed."""
    with conn.cursor() as cur:
        cur.execute("""SELECT sport, count(*) FROM meet_queue
                       WHERE source = %s AND scraped IN (2, 3)
                       GROUP BY sport""", (source,))
        out = dict(cur.fetchall())
    conn.rollback()
    return out


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
                 dry_blocks=2, recent_days=DEEP_RECENT_DAYS):
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

    def frontierDrained(self):
        """The live sports whose seeded block is all scraped.

        ★ THE WALK NO LONGER WAITS FOR THE WHOLE QUEUE (owner, 2026-10-07).
          With the claim newest first (database._CLAIM_ORDER), the block
          above the watermark is claimed before anything older, so "nothing
          due or in flight above the watermark" is exactly the moment a
          drained queue used to mean -- for that sport -- while 93k old
          meets are still queued beneath it.
        """
        out = []
        with getConn() as conn:
            with conn.cursor() as cur:
                for sp in self.live():
                    if frontierLeft(cur, sp, self.source) == 0:
                        out.append(sp)
            conn.rollback()
        return out

    def extend(self, sports=None):
        """(has_more, [lines to print]). False means every sport is finished.

        sports= extends only those (a frontier drained while the queue below
        it is not); a sport left out keeps its dry count untouched."""
        lines = []
        live = [sp for sp in self.live() if sports is None or sp in sports]
        if not live:
            return bool(self.live()), lines

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
        if sports is not None:
            # a frontier extension mid-queue: the run goes on whatever this
            # block found, as long as a sport is still walking or rows are due
            if still:
                lines.append("frontier scraped -- seeded the next block: "
                             + ", ".join(f"{sp} {n:,}" for sp, n in sorted(still.items())))
            return True, lines
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
    ap.add_argument("--recent-days", type=int, default=None,
                    help=f"default {RECENT_DAYS}, or {DEEP_RECENT_DAYS} with --deep")
    ap.add_argument("--deep", action="store_true",
                    help="the full re-ask sweep: 120 days, undated and unrecorded ids")
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--recent-only", action="store_true")
    ap.add_argument("--sport", choices=SPORTS)
    ap.add_argument("--shed-stale", action="store_true",
                    help="put due re-asks dated outside the last --recent-days "
                         f"(default {RECENT_DAYS}) back to done; with --write")
    args = ap.parse_args()

    if args.shed_stale:
        days = args.recent_days or RECENT_DAYS
        with getConn() as conn:
            with conn.cursor() as cur:
                for sp in ([args.sport] if args.sport else SPORTS):
                    print(f"  {args.source}/{sp} due now:")
                    for what, k in dueBreakdown(cur, sp, args.source).items():
                        print(f"    {k:>10,}  {what}")
                    n = shedStale(cur, sp, args.source, days, write=args.write)
                    print(f"  {args.source}/{sp}: {n:,} due re-asks dated outside "
                          f"the last {days} days "
                          + ("set back to done" if args.write else "(dry run)"))
                    k = shedRepaired(cur, sp, args.source, write=args.write)
                    print(f"  {args.source}/{sp}: {k:,} due name repairs with no "
                          f"nameless result left "
                          + ("set back to done" if args.write else "(dry run)"),
                          flush=True)
            if args.write:
                conn.commit()
                for sp, n in sorted(dueCounts(conn, args.source).items()):
                    print(f"  {args.source} queue now due: {sp} {n:,}")
            else:
                conn.rollback()
                print("\n  DRY RUN -- pass --write to shed them.")
        return

    # ★ CREATE DECLARED-BUT-MISSING COLUMNS BEFORE QUERYING THEM.
    ensureCoreColumns(verbose=True)

    with getConn() as conn:
        seedAll(conn, source=args.source, write=args.write,
                sports=[args.sport] if args.sport else None,
                ahead=args.ahead, recent_days=args.recent_days, deep=args.deep,
                do_new=not args.recent_only, do_recent=not args.new_only)
        if args.write:
            print(f"\n  {args.source} queue now due:")
            for sp, n in sorted(dueCounts(conn, args.source).items()):
                print(f"    {sp}  {n:,}")
        else:
            print("\n  DRY RUN -- pass --write to seed the queue.")


if __name__ == "__main__":
    main()
