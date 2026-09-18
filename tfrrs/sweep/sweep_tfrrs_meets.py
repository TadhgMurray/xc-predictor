# Project: xc-predictor
# File:    tfrrs/sweep/sweep_tfrrs_meets.py
# Purpose: Discover TFRRS meet ids by walking the /results/<id> space and writing
#          real meets into meet_queue (source='tfrrs') for the driver to drain.
#          TFRRS shares ONE id space between meets and events, so past the meet
#          range the same URLs resolve to single-event pages (HTTP 200, not 404).
#          The hard id ceiling is the stop condition; a content classifier is the
#          insurance that drops any event-id sitting INSIDE the meet range.

import re
import sys
import os
import psycopg2
import psycopg2.extras

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "parser"))         # parse_* helpers
sys.path.insert(0, os.path.join(_HERE, "..", "fetch"))          # fetchTFPage
sys.path.insert(0, os.path.join(_HERE, "..", "..", "scripts"))  # database, scraper

from bs4 import BeautifulSoup
# ! fetch_tfrrs, NOT fetch_tf. The module was renamed and this import was
#   not, so importing this file raised ModuleNotFoundError -- found by
#   walking every third-party import in the repo to write the
#   requirements files, not by anyone running it.
from fetch_tfrrs import fetchTFPage
from database import getConn
from scraper import CloudflareException, RateLimitException

# Real meet ids top out around here; everything above is event-id territory we
# never fetch. Tight enough that the ceiling IS the stop condition.
ID_CEILING = 100_000

# Both sports share the same /results/<id> space; the page doesn't say which
# sport has data (the driver finds that when it scrapes). Queue a row per sport.
SPORTS = ("XC", "TF")

# meet_queue rows carry source so the TFRRS driver's claim filter finds them.
SOURCE_TFRRS = "tfrrs"

# Commit after this many QUEUED meets, so a mid-sweep crash keeps progress
# without holding one giant transaction.
COMMIT_EVERY = 500

# A blocked id (Cloudflare/429) might be a real meet we just couldn't see -
# retry this many times before giving up and skipping it.
BLOCK_RETRIES = 3

# The meet/event discriminator regex (panel-title link's meet id).
_MEET_LINK_RE = re.compile(r"/results/(\d+)/")


# _isMeetPage
# Purpose: Distinguish a real MEET landing page from a single-EVENT leaf page.
#          Keyed on the panel-title link: a meet's title links to its own id, an
#          event's title links to its (different) parent meet id.
# Arguments:
#           html:         fetched page HTML.
#           requested_id: the id we asked for in the URL.
# Output:   True if this page IS the meet for requested_id; False for an event
#           leaf or an unrecognizable page.
def _isMeetPage(html, requested_id):
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one("h3.panel-title a")
    if title is None or not title.has_attr("href"):
        return False
    match = _MEET_LINK_RE.search(title["href"])
    if match is None:
        return False
    return int(match.group(1)) == int(requested_id)


# _classifyId
# Purpose: Fetch one /results/<id> page and decide what it is. The sweep's core
#          per-id step. Never raises - a block, 404, or parse error comes back as
#          a value so one bad id can't end the walk.
# Arguments:
#           meet_id: the id to fetch and classify.
#           page:    optional Playwright page for fetchTFPage's tier-3 fallback.
# Output:   one of "meet" | "event" | "empty" | "blocked":
#             meet    -> a real meet page, queue it
#             event   -> an event leaf inside the meet range, skip
#             empty   -> 404 / nothing there, skip
#             blocked -> Cloudflare/429, the caller may retry this id
def _classifyId(meet_id, page=None):
    url = f"https://www.tfrrs.org/results/{meet_id}"
    try:
        html = fetchTFPage(url, page=page)
    except (CloudflareException, RateLimitException):
        return "blocked"
    except Exception:
        return "empty"          # 404s and dead ids land here

    if not html:
        return "empty"
    if _isMeetPage(html, meet_id):
        return "meet"
    return "event"

# _queueMeet
# Purpose: Insert a discovered meet's queue rows - one per sport - so the TFRRS
#          driver can claim them. Idempotent: ON CONFLICT DO NOTHING means a
#          re-run of the sweep won't duplicate rows or reset a meet that's
#          already been scraped (scraped stays whatever it was).
# Arguments:
#           conn:    open DB connection (caller commits).
#           meet_id: the discovered meet id.
# Output:   none. Writes up to len(SPORTS) rows.
def _queueMeet(conn, meet_id):
    rows = []
    for sport in SPORTS:
        rows.append((meet_id, sport, SOURCE_TFRRS, 0))   # scraped=0 -> pending

    cursor = conn.cursor()
    psycopg2.extras.execute_values(cursor, """
        INSERT INTO meet_queue (meet_id, sport, source, scraped)
        VALUES %s
        ON CONFLICT (meet_id, sport) DO NOTHING
    """, rows)

# runSweep
# Purpose: Walk the meet-id space and queue every real meet found. The hard
#          ceiling is the stop; the classifier drops event-ids that fall inside
#          the range. Blocks are retried; 404s/events are skipped. Commits in
#          batches so a crash mid-sweep keeps the meets found so far.
# Arguments:
#           page:       optional Playwright page for fetchTFPage's tier-3 fallback.
#           start_id:   first id to sweep (default 1; lets you resume a partial run).
# Output:   none. Writes pending meet rows into meet_queue as it goes.
def runSweep(page=None, start_id=1):

    counts = {"meet": 0, "event": 0, "empty": 0, "blocked": 0}
    since_commit = 0

    conn = _openConn()
    try:
        for meet_id in range(start_id, ID_CEILING + 1):
            kind = _classifyWithRetry(meet_id, page)
            counts[kind] += 1

            if kind == "meet":
                _queueMeet(conn, meet_id)
                since_commit += 1

            # Batch commits so progress survives a crash without one giant txn.
            if since_commit >= COMMIT_EVERY:
                conn.commit()
                since_commit = 0

            _logProgress(meet_id, counts)

        conn.commit()          # final partial batch
    finally:
        _closeConn(conn)

    print(f"[SWEEP] done. meets={counts['meet']} events={counts['event']} "
          f"empty={counts['empty']} blocked={counts['blocked']}", flush=True)
    
# _classifyWithRetry
# Purpose: Classify one id, retrying ONLY the blocked case (Cloudflare/429) a few
#          times before giving up. A "blocked" that never clears is downgraded to
#          "empty" (skip) so the sweep doesn't stall forever on one bad id - but
#          we tried, so we won't silently lose a real meet to a transient block.
# Arguments:
#           meet_id: id to classify.
#           page:    optional Playwright page for the tier-3 fallback.
# Output:   "meet" | "event" | "empty" (never "blocked" - that's resolved here).
def _classifyWithRetry(meet_id, page):
    for attempt in range(BLOCK_RETRIES):
        kind = _classifyId(meet_id, page)
        if kind != "blocked":
            return kind
        time.sleep(2 * (attempt + 1))      # simple backoff between block retries
    # Still blocked after all retries: skip it, but say so loudly.
    print(f"[SWEEP] id {meet_id} still blocked after {BLOCK_RETRIES} tries, skipping", flush=True)
    return "empty"


# _openConn / _closeConn
# Purpose: The sweep holds ONE connection for its whole run (unlike the per-meet
#          getConn() pattern) because it commits in batches across thousands of
#          ids. getconn()/putconn() directly, since we're managing the lifetime
#          ourselves rather than per-with-block.
def _openConn():
    from database import _pool, initPool
    if _pool is None:
        initPool()
    from database import _pool as pool_now
    return pool_now.getconn()


def _closeConn(conn):
    from database import _pool
    conn.rollback()              # clear any uncommitted tail before returning it
    _pool.putconn(conn)


# _logProgress
# Purpose: Periodic one-line progress, every 1000 ids, so a long sweep shows life
#          without spamming a line per id.
# Arguments:
#           meet_id: current id.
#           counts:  the running tally dict.
# Output:   none (prints on the cadence).
def _logProgress(meet_id, counts):
    if meet_id % 1000 == 0:
        print(f"[SWEEP] at {meet_id}: {counts['meet']} meets, "
              f"{counts['event']} events, {counts['empty']} empty", flush=True)


# main
# Purpose: Run the sweep end to end. Opens one headful browser page for
#          fetchTFPage's tier-3 fallback (Cloudflare blocks headless), sweeps,
#          tears down.
# Arguments: none.
# Output:   none.
def main():
    handle = _openPage()
    try:
        runSweep(page=handle["page"])
    finally:
        _closePage(handle)


if __name__ == "__main__":
    main()